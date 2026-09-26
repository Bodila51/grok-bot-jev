"""submit / route / tick / run (+ Jev verification and fix rounds) / confirm."""
import json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
from . import evidence as ev_mod, jev
from .core import (JOBS, RECIPES, HOME, LIMIT_RE, band, cli_model, config, db, enabled_models, env, err_sig, goal_hash, iso, log,
                   now, playbook, record_decisions, refresh_seats, seat_event, worker_env)

PROMPT = """You are a worker in a job directory (your current working directory). Do this job:
GOAL: {goal}
CONSTRAINTS: {constraints}
DONE WHEN: {done_when}
Work only inside the current directory. Never print secrets. Do not send, post, publish, pay, or delete anything
outside this directory. When finished (or if blocked), write
./result.json with exactly: {{"status": "done"|"failed"|"blocked", "summary": "<=3 sentences",
"files": ["relative paths you created/changed"], "open_issues": ["..."]}}. Keep it compact."""

FIX_PROMPT = """A verifier checked your result against DONE WHEN and it did not pass (p={p}).
DONE WHEN: {done_when}
Open issues you reported: {issues}
Fix what is missing so the deliverable fully satisfies DONE WHEN. Stay in this directory, then rewrite ./result.json
(same format: status, summary, files, open_issues)."""


# ---------- recipes ----------
def load_recipe(name):
    p = RECIPES / f"{name}.json"
    if not p.exists(): sys.exit(f"no recipe '{name}' (see: jevopus.py recipes)")
    return json.loads(p.read_text())


def render_recipe(r, pairs):
    vals = {k: (v.get("default") if isinstance(v, dict) else v) for k, v in r.get("vars", {}).items()}
    for kv in pairs or []:
        if "=" not in kv: sys.exit(f"--var expects k=v, got {kv!r}")
        k, v = kv.split("=", 1); vals[k] = v
    missing = [k for k, v in vals.items() if v in (None, "")]
    if missing: sys.exit(f"recipe {r['name']} needs: " + ", ".join(f"--var {k}=..." for k in missing))
    fmt = lambda s: (s or "").format(**vals)
    return vals, fmt(r["goal"]), fmt(r.get("constraints")), fmt(r.get("done_when"))


def check_tools(tools):
    miss = []
    for t in tools or []:
        if t == "render-venv":
            if not (HOME / "tools/render-venv/bin/python").exists(): miss.append("render-venv (reinstall without --no-render)")
        elif not shutil.which(t): miss.append(t)
    return miss


def cmd_submit(a):
    c = db(); cfg = config()
    recipe, vars_, cli_cfg, attach = None, None, [], []
    goal, constraints, done_when, kind, model, effort = a.goal, a.constraints, a.done_when, a.kind, a.model, a.effort
    if a.recipe:
        r = load_recipe(a.recipe); recipe = r["name"]
        vars_, goal, constraints, done_when = render_recipe(r, a.var)
        if a.constraints: constraints = f"{constraints} {a.constraints}".strip()
        if a.done_when: done_when = a.done_when
        kind = a.kind if a.kind != "coding" else r.get("kind", "coding")
        effort = effort or r.get("effort", "")
        cli_cfg, attach = r.get("codex_config", []), r.get("attach", [])
        if r.get("model") and not model: print(f"note: recipe suggests model {r['model']} (Jev still picks unless you pin --model)", file=sys.stderr)
        miss = check_tools(r.get("tools"))
        if miss: print("warning: missing tools for this recipe: " + ", ".join(miss), file=sys.stderr)
    if not goal: sys.exit("--goal or --recipe required")
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    c.execute("INSERT INTO jobs(id,goal,constraints,done_when,kind,status,created_at,model,effort,pin_seat,model_source,"
              "goal_hash,confirmed,from_agent,recipe,recipe_vars,forced,cli_config,attach) "
              "VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (jid, goal, constraints, done_when, kind, iso(), model or None, effort or None, a.seat or None,
               "pinned" if model else None, goal_hash(goal), int(a.confirmed), a.from_agent or None, recipe,
               json.dumps(vars_) if vars_ else None, int(a.force), json.dumps(cli_cfg), json.dumps(attach)))
    c.commit(); (JOBS / jid).mkdir(parents=True, exist_ok=True); print(jid)


def cmd_confirm(a):
    c = db(); c.execute("UPDATE jobs SET confirmed=1 WHERE id=?", (a.job_id,)); c.commit()
    log({"event": "confirm", "job_id": a.job_id}); print(f"{a.job_id}: confirmed by human")


def cmd_cancel(a):
    c = db(); j = c.execute("SELECT * FROM jobs WHERE id=?", (a.job_id,)).fetchone()
    if not j or j["status"] not in ("queued", "leased"): sys.exit(f"only queued/leased jobs can be cancelled")
    if j["status"] == "leased": c.execute("UPDATE seats SET status='idle' WHERE id=?", (j["seat_id"],))
    c.execute("UPDATE jobs SET status='blocked', route_note=COALESCE(route_note,'')||'; cancelled by user', lease_until=NULL "
              "WHERE id=?", (a.job_id,)); c.commit(); log({"event": "cancel", "job_id": a.job_id}); print(f"{a.job_id}: cancelled")


# ---------- route ----------
def _fail_info(c, job, pb):
    rows = c.execute("SELECT id, err_sig, result_path FROM jobs WHERE goal_hash=? AND id!=? AND status IN "
                     "('failed','needs_review') AND err_sig IS NOT NULL ORDER BY created_at DESC",
                     (job["goal_hash"], job["id"])).fetchall()
    if not rows: return None
    n = 0
    for r in rows:
        if r["err_sig"] != rows[0]["err_sig"]: break
        n += 1
    if n < int(pb["limits"].get("max_retries_same_error", 1)): return None
    try: err = json.loads(Path(rows[0]["result_path"]).read_text()); err = (err.get("summary") or "")[:300]
    except Exception: err = "unknown"
    return {"prior_error": err, "same_error_count": n, "prior_job": rows[0]["id"]}


def cmd_route(a):
    c = db(); cfg, pb = config(), playbook()
    job = c.execute("SELECT * FROM jobs WHERE id=?", (a.job_id,)).fetchone()
    if not job: sys.exit(f"no job {a.job_id}")
    pol, th, t0 = pb["policy"], pb["thresholds"], now()
    avail = enabled_models(c, cfg)
    models = {m: cfg["models"][m]["fit"] for m in avail if m in cfg["models"]}
    if job["pin_seat"]:
        models = {m: f for m, f in models.items() if job["pin_seat"] in avail[m]}
    recent = [] if job["forced"] else [(r["id"], r["goal"]) for r in c.execute(
        "SELECT id, goal FROM jobs WHERE status='done' AND id!=? AND result_path IS NOT NULL "
        "AND (verify_status IS NULL OR verify_status='pass') ORDER BY created_at DESC LIMIT ?",
        (job["id"], int(cfg["reuse"]["candidates"])))]
    fail = _fail_info(c, job, pb)
    evid = ev_mod.build(c, job, models) if models and job["model_source"] != "pinned" else None
    kill = not pb.get("enabled", True) or re.search(r"\b(bypass jev|no jev)\b", job["goal"], re.I)
    err = ""
    if kill: j, jev_used, err = jev.fallback_route(job, models, recent, fail), False, "kill switch"
    else:
        try: j, jev_used = jev.ask_route(pb, job, models, recent, fail, evid and evid["lines"]), True
        except Exception as e:
            j, jev_used, err = jev.fallback_route(job, models, recent, fail), False, f"{type(e).__name__}: {str(e)[:200]}"
    farm_conf = max(j["needs_farm_p"], 1 - j["needs_farm_p"])
    fb, tb = band(farm_conf, pol), band(j["tier_conf"], pol)
    dec = [("needs_farm", round(j["needs_farm_p"], 3), farm_conf, fb, True), ("tier", j["tier"], j["tier_conf"], tb, True)]
    route, tier, status, model, msrc, notes, cached_from, result_path = None, None, "queued", job["model"], job["model_source"], [], None, None
    irr = j["irreversible_p"] >= float(cfg["guard"]["confirm_min"])
    dec.append(("guard", j["irreversible_p"], max(j["irreversible_p"], 1 - j["irreversible_p"]),
                band(max(j["irreversible_p"], 1 - j["irreversible_p"]), pol), irr))
    reuse_hit = "reuse" in j and j["reuse"] not in (None, "none") and j["reuse_conf"] >= float(th.get("reuse_min", 0.65))
    if "reuse" in j: dec.append(("reuse", j["reuse"], j["reuse_conf"], band(j["reuse_conf"], pol), reuse_hit))
    stop = fail is not None and j.get("stop_retry_p", 0) >= float(th.get("stop_retry_min", 0.55))
    if fail: dec.append(("stop_retry", j.get("stop_retry_p"), j.get("stop_retry_p"), band(max(j["stop_retry_p"], 1 - j["stop_retry_p"]), pol), stop))
    if reuse_hit:
        src = c.execute("SELECT * FROM jobs WHERE id=?", (j["reuse"],)).fetchone()
        route, status, cached_from, result_path = "cached", "cached", src["id"], src["result_path"]
        notes.append(f"CACHE HIT: reuses {src['id']} (conf {j['reuse_conf']}); use --force to rerun")
    elif stop:
        route, status = "stopped", "blocked"
        notes.append(f"STOP_RETRY: same error x{fail['same_error_count']} (p={j['stop_retry_p']}); not rerunning. "
                     "Change the approach/goal or ask the human.")
    elif j["needs_farm_p"] < 0.5 and not job["pin_seat"]:
        route = "inline"; notes.append(f"dispatcher should do it inline ({fb})")
    else:
        route, tier = "farm", j["tier"]
        notes.append(f"farm/{tier} (farm:{fb}, tier:{tb})")
        if fb == "escalate" or tb == "escalate": notes.append("low confidence - ask the human before spending a seat")
        if job["model_source"] == "pinned":
            if job["model"] not in avail: notes.append(f"UNAVAILABLE: pinned model {job['model']} not on an enabled seat")
        elif not models:
            notes.append("UNAVAILABLE: no enabled seat/model (run jevopus.py doctor / setup-seat); job stays queued")
        elif len(models) == 1:
            model, msrc = next(iter(models)), "only"
        elif j.get("model") and j["model_conf"] >= float(th.get("min_choice_confidence", 0.55)):
            model, msrc = j["model"], "jev"
        else:
            model = cfg["default_model"] if cfg["default_model"] in models else next(iter(models)); msrc = "default"
        if "model" in j:
            dec.append(("model", j["model"], j["model_conf"], band(j["model_conf"] or 0, pol), msrc == "jev",
                        evid and evid["summary"]))
        if model and msrc != "pinned": notes.append(f"model {model} ({msrc})")
    if irr and route == "farm":
        notes.append("GUARD: irreversible/external action suspected (p=%.2f) -> " % j["irreversible_p"]
                     + ("confirmed by human" if job["confirmed"] else "REQUIRES --confirmed (jevopus.py confirm <id>)"))
    if not jev_used: notes.append(f"jev not used ({err or 'error'}) -> fallback rule")
    note = "; ".join(notes)
    c.execute("UPDATE jobs SET route=?, tier=?, route_note=?, status=?, model=?, model_source=?, requires_confirm=?, "
              "cached_from=?, result_path=COALESCE(?, result_path), model_evidence=? WHERE id=?",
              (route, tier, note, status, model, msrc, int(irr), cached_from, result_path,
               json.dumps(evid, ensure_ascii=False) if evid else None, job["id"]))
    c.execute("DELETE FROM decisions WHERE job_id=? AND kind!='verify'", (job["id"],))
    record_decisions(c, job["id"], dec, jev_used); c.commit()
    out = {"job_id": job["id"], "goal": job["goal"][:300], "kind": job["kind"], "jev_used": jev_used, "jev": j,
           "bands": {"farm": fb, "tier": tb}, "route": route, "tier": tier, "model": model, "model_source": msrc,
           "requires_confirm": irr, "status": status, "note": note,
           "model_evidence": evid and {"basis": evid["basis"], "lines": evid["lines"]}, "latency_ms": int((now() - t0) * 1000)}
    if err: out["error"] = err
    log({"event": "route", **out}); print(json.dumps(out, indent=2))
    if reuse_hit and result_path and Path(result_path).exists():
        print(f"\nexisting result ({cached_from}):\n{Path(result_path).read_text()[:1500]}")


# ---------- tick ----------
def cmd_tick(a):
    c = db(); refresh_seats(c); n = 0
    for job in c.execute("SELECT * FROM jobs WHERE status='queued' AND route='farm' ORDER BY created_at").fetchall():
        if job["requires_confirm"] and not job["confirmed"]:
            print(f"{job['id']}: waiting for human confirmation (jevopus.py confirm {job['id']})"); continue
        q = "SELECT * FROM seats WHERE enabled=1 AND status='idle' AND (cooldown_until IS NULL OR cooldown_until<?)"
        seats = c.execute(q, (now(),)).fetchall()
        if job["pin_seat"]: seats = [s for s in seats if s["id"] == job["pin_seat"]]  # never another login
        if job["model"]: seats = [s for s in seats if job["model"] in (s["models"] or s["model"]).split(",")]
        else: seats = [s for s in seats if s["tier"] == job["tier"]]
        if not seats: print(f"{job['id']}: waiting (no free enabled seat for {job['model'] or job['tier']})"); continue
        seat = seats[0]
        c.execute("UPDATE seats SET status='busy' WHERE id=?", (seat["id"],))
        c.execute("UPDATE jobs SET status='leased', seat_id=?, lease_until=? WHERE id=?",
                  (seat["id"], now() + config()["lease_sec"], job["id"])); c.commit(); n += 1
        print(f"{job['id']}: leased to {seat['id']} ({job['model'] or seat['model']})")
    print(f"tick: {n} lease(s)")


# ---------- run ----------
def _argv(seat, model, effort, prompt, cli_cfg, session=None):
    if seat["cli"] == "codex":
        extra = [x for kv in cli_cfg for x in ("-c", kv)] + (["-c", f"model_reasoning_effort={effort}"] if effort else [])
        if session:
            return ["codex", "exec", "resume", session, "--skip-git-repo-check", "-m", model,
                    "-c", "sandbox_mode=workspace-write", *extra, prompt]
        return ["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write", "-m", model, *extra, prompt]
    return ["claude", "-p", prompt, "--model", model, "--output-format", "json", *(["--resume", session] if session else [])]


def _exec(argv, jdir, env, timeout):
    try:
        p = subprocess.run(argv, cwd=jdir, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return (p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""), p.returncode
    except subprocess.TimeoutExpired as e:
        return f"TIMEOUT after {timeout}s\n{e.stdout or ''}", -1


def parse_usage(out):
    """-> (tokens, session_id). Codex prints 'tokens used\\n12,345' and 'session id: X'; claude JSON has usage."""
    toks = sum(int(m.replace(",", "")) for m in re.findall(r"tokens used[:\s]*\n?\s*([\d,]+)", out, re.I))
    sid = re.search(r"session id:\s*([0-9a-f-]{16,})", out)
    sid = sid.group(1) if sid else None
    if not toks:
        try:
            d = json.loads(out.split("\n--- stderr ---")[0]); u = d.get("usage") or {}
            toks = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens"))
            sid = sid or d.get("session_id")
        except Exception: pass
    return toks, sid


def _read_result(jdir):
    try: return json.loads((jdir / "result.json").read_text())
    except Exception: return {}


def verify(pb, job, jdir, log_text):
    res = _read_result(jdir)
    files = []
    for f in (res.get("files") or [])[:30]:
        p = (jdir / f) if not os.path.isabs(f) else Path(f)
        files.append({"path": f, "exists": p.exists(), "bytes": p.stat().st_size if p.exists() and p.is_file() else 0})
    state = {"goal": job["goal"][:1500], "done_when": job["done_when"] or "goal met", "result": res or {"status": "missing"},
             "files": files, "worker_log_tail": log_text[-4000:]}
    try: return jev.ask_verify(pb, state), True, res
    except Exception: return jev.fallback_verify(state), False, res


def cmd_run(a):
    c = db(); cfg, pb = config(), playbook()
    job = c.execute("SELECT * FROM jobs WHERE id=?", (a.job_id,)).fetchone()
    if not job or job["status"] != "leased": sys.exit(f"job {a.job_id} is not leased (status={job and job['status']})")
    if job["requires_confirm"] and not job["confirmed"]: sys.exit("job requires human confirmation first")
    seat = c.execute("SELECT * FROM seats WHERE id=?", (job["seat_id"],)).fetchone()
    jdir = JOBS / job["id"]; jdir.mkdir(parents=True, exist_ok=True)
    for f in json.loads(job["attach"] or "[]"):
        src = RECIPES / f
        if src.exists(): shutil.copy(src, jdir / src.name)
    prompt = PROMPT.format(goal=job["goal"], constraints=job["constraints"] or "none", done_when=job["done_when"] or "goal met")
    model, env, cli_cfg = job["model"] or seat["model"], worker_env(seat), json.loads(job["cli_config"] or "[]")
    timeout = int(env("RUN_TIMEOUT", cfg["run_timeout_sec"]))
    c.execute("UPDATE jobs SET status='running', prompt=?, model=?, started_at=? WHERE id=?", (prompt, model, iso(), job["id"])); c.commit()
    t0 = now(); print(f"running {job['id']} on {seat['id']} ({seat['cli']} {model})...", flush=True)
    out, rc = _exec(_argv(seat, cli_model(cfg, model), job["effort"], prompt, cli_cfg), jdir, env, timeout)
    (jdir / "worker.log").write_text(out)
    tokens, sid = parse_usage(out)
    res = _read_result(jdir)
    limited = rc != 0 and res.get("status") != "done" and bool(LIMIT_RE.search(out))
    vstatus, vp, rounds, vused = None, None, 0, False
    if limited:
        c.execute("UPDATE seats SET status='cooldown', cooldown_until=? WHERE id=?", (now() + cfg["cooldown_sec"], seat["id"]))
        seat_event(c, seat["id"], "cooldown", job["id"], "rate/quota limit"); vstatus = "skipped"
    else:
        while True:
            vp, vused, res = verify(pb, job, jdir, out)
            ok = vp >= float(cfg["verify"]["pass_min"])
            record_decisions(c, job["id"], [("verify", vp, max(vp, 1 - vp), band(max(vp, 1 - vp), pb["policy"]), True)], vused)
            log({"event": "verify", "job_id": job["id"], "p": vp, "pass": ok, "round": rounds, "jev_used": vused})
            print(f"verify: p={vp} -> {'pass' if ok else 'fail'} (round {rounds}, jev_used={vused})", flush=True)
            if ok: vstatus = "pass"; break
            if rounds >= int(cfg["verify"]["max_fix_rounds"]) or not sid:
                vstatus = "fail"; break
            rounds += 1
            fix = FIX_PROMPT.format(p=vp, done_when=job["done_when"] or "goal met",
                                    issues="; ".join(res.get("open_issues") or []) or "none listed")
            out2, rc = _exec(_argv(seat, cli_model(cfg, model), job["effort"], fix, cli_cfg, session=sid), jdir, env, timeout)
            (jdir / f"worker_fix{rounds}.log").write_text(out2); t2, _ = parse_usage(out2); tokens += t2
            log({"event": "run_fix", "job_id": job["id"], "seat": seat["id"], "model": model, "session": sid, "rc": rc, "round": rounds})
            if rc != 0 and LIMIT_RE.search(out2):
                c.execute("UPDATE seats SET status='cooldown', cooldown_until=? WHERE id=?", (now() + cfg["cooldown_sec"], seat["id"]))
                seat_event(c, seat["id"], "cooldown", job["id"], "limit during fix"); limited = True; vstatus = "fail"; break
            out = out + "\n" + out2
        if not limited: c.execute("UPDATE seats SET status='idle' WHERE id=?", (seat["id"],))
    rp = jdir / "result.json"
    if not rp.exists():
        rp.write_text(json.dumps({"status": "failed", "summary": f"worker exited rc={rc} without result.json"
                                  + (" (rate/quota limit; seat cooled down)" if limited else ""),
                                  "files": [], "open_issues": ["see worker.log"]}))
    res = _read_result(jdir)
    status = "done" if vstatus == "pass" else ("needs_review" if vstatus == "fail" and not limited else "failed")
    esig = None if status == "done" else err_sig((res.get("summary") or "") + " ".join(res.get("open_issues") or []))
    secs = int(now() - t0)
    c.execute("UPDATE jobs SET status=?, result_path=?, lease_until=NULL, tokens=?, secs=?, finished_at=?, session_id=?, "
              "verify_p=?, verify_status=?, fix_rounds=?, err_sig=? WHERE id=?",
              (status, str(rp), tokens, secs, iso(), sid, vp, vstatus, rounds, esig, job["id"]))
    seat_event(c, seat["id"], "job_" + status, job["id"]); c.commit()
    log({"event": "run", "job_id": job["id"], "seat": seat["id"], "model": model, "rc": rc, "status": status,
         "limited": limited, "tokens": tokens, "verify_p": vp, "fix_rounds": rounds, "secs": secs})
    print(rp.read_text())
