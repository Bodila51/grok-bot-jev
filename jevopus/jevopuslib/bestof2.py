"""Best of two: run one job on two models (prefer two different seats -> parallel; one seat -> sequential),
verify both against done_when, keep the better one.

Parent job  : route='best_of_two', seat_id NULL, tokens 0 (never counted as a model result or in token totals).
Children    : <id>-a / <id>-b, normal worker jobs with a pinned model, parent_id set, dir jobs/<id>/a|b/.
              Each child is a real result for its model (evidence, usage, weekly).
Winner      : verified pass beats fail; both pass -> blind Jev comparison (applied if confidence >=
              min_choice_confidence), else the verifier's p, then fewer tokens. Reasoning is recorded."""
import json, os, subprocess, sys, time
from pathlib import Path
from . import evidence as ev_mod, jev
from .core import JOBS, band, iso, log, now, playbook, config, record_decisions

TERMINAL = ("done", "failed", "needs_review", "blocked", "cached")


def pick_second(c, cfg, pb, job, first, models, avail):
    """models: {model: fit} usable for this job; avail: model -> [seat ids]. -> dict(model, conf, why, src)."""
    rest = {m: f for m, f in models.items() if m != first}
    if not rest: return {"model": None, "conf": None, "why": "only one model on the enabled seats", "src": None}
    first_seats = set(avail.get(first, []))
    other = {m: f for m, f in rest.items() if set(avail.get(m, [])) - first_seats}
    pool = other or rest
    how = "different seat, runs in parallel" if other else "same seat, runs sequentially"
    if len(pool) == 1:
        m = next(iter(pool)); return {"model": m, "conf": None, "why": f"only other candidate; {how}", "src": "only"}
    th = float(pb["thresholds"].get("min_choice_confidence", 0.55))
    try:
        if not pb.get("enabled", True): raise RuntimeError("kill switch")
        evid = ev_mod.build(c, job, pool)
        m, conf = jev.ask_second(pb, job, pool, first, evid["lines"])
        if m in pool and conf >= th:
            return {"model": m, "conf": conf, "why": f"Jev pick conf {conf}; {how}", "src": "jev"}
        why = f"Jev conf {conf} < {th} -> first listed candidate"
    except Exception as e:
        conf, why = None, f"Jev unavailable ({type(e).__name__}) -> first listed candidate"
    m = next(iter(pool))
    return {"model": m, "conf": conf, "why": f"{why}; {how}", "src": "default"}


def create_children(c, pid, model_a, src_a, b2):
    p = c.execute("SELECT * FROM jobs WHERE id=?", (pid,)).fetchone()
    c.execute("DELETE FROM jobs WHERE parent_id=? AND status='queued'", (pid,))  # re-route replaces unstarted candidates
    for role, m, src in (("a", model_a, src_a), ("b", b2["model"], b2["src"])):
        cid = f"{pid}-{role}"
        if c.execute("SELECT 1 FROM jobs WHERE id=?", (cid,)).fetchone(): continue
        d = JOBS / pid / role; d.mkdir(parents=True, exist_ok=True)
        c.execute("INSERT INTO jobs(id,goal,constraints,done_when,kind,status,created_at,route,tier,route_note,model,effort,"
                  "pin_seat,model_source,goal_hash,requires_confirm,confirmed,from_agent,recipe,recipe_vars,forced,cli_config,"
                  "attach,parent_id,bo2_role,job_dir,complexity,search,best_of_two) "
                  "VALUES(?,?,?,?,?,'queued',?,'farm',?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,0)",
                  (cid, p["goal"], p["constraints"], p["done_when"], p["kind"], iso(), p["tier"],
                   f"best-of-two candidate {role} of {pid}", m, p["effort"], p["pin_seat"], f"best-of-two {role}: {src}",
                   p["goal_hash"], p["requires_confirm"], p["confirmed"], p["from_agent"], p["recipe"], p["recipe_vars"],
                   p["cli_config"], p["attach"], pid, role, str(d), p["complexity"], p["search"]))
    c.execute("UPDATE jobs SET model=? WHERE id=?", (f"{model_a} vs {b2['model']}", pid)); c.commit()


def children(c, pid):
    return c.execute("SELECT * FROM jobs WHERE parent_id=? ORDER BY bo2_role", (pid,)).fetchall()


def run_parent(c, parent, runner_cmd=None):
    """Lease + run both candidates (parallel across seats, sequential on one seat), then pick the winner."""
    from .runner import lease
    cfg, pid = config(), parent["id"]
    runner_cmd = runner_cmd or [sys.executable, str(Path(__file__).resolve().parent.parent / "jevopus.py"), "run"]
    if parent["requires_confirm"] and not parent["confirmed"]: sys.exit("job requires human confirmation first")
    if not parent["started_at"]:
        c.execute("UPDATE jobs SET started_at=? WHERE id=?", (iso(), pid)); c.commit()
    while True:
        kids = children(c, pid)
        if not kids: sys.exit(f"{pid}: no best-of-two candidates (route it first)")
        if all(k["status"] in TERMINAL for k in kids): break
        for k in kids:
            if k["status"] == "queued": lease(c, k, cfg)
        kids = children(c, pid)
        leased = [k for k in kids if k["status"] == "leased"]
        if not leased:
            busy = [k["id"] for k in kids if k["status"] == "running"]
            print(f"{pid}: " + (f"candidate(s) {', '.join(busy)} still running elsewhere" if busy else
                                "waiting: no candidate could be leased now (see messages above)")
                  + f"; re-run `jevopus.py run {pid}` later"); return
        c.execute("UPDATE jobs SET status='running' WHERE id=?", (pid,)); c.commit()
        mode = "in parallel" if len(leased) > 1 else "sequentially"
        print(f"{pid}: running {', '.join(k['id'] + ' (' + k['model'] + ' on ' + k['seat_id'] + ')' for k in leased)} {mode}", flush=True)
        env = {**os.environ, "JEVOPUS_NO_REEXEC": "1"}
        procs = []
        for k in leased:
            out = open(Path(k["job_dir"] or JOBS / k["id"]) / "run_console.log", "w")
            procs.append((k, subprocess.Popen([*runner_cmd, k["id"]], stdout=out, stderr=subprocess.STDOUT, env=env), out))
        for k, pr, out in procs:
            pr.wait(); out.close()
            print(f"  {k['id']} finished rc={pr.returncode} (console: {out.name})", flush=True)
    maybe_finish(c, pid)
    print(f"{pid}: done; see jevopus.py report {pid}")


def _cand_state(k):
    base = Path(k["result_path"]).parent if k["result_path"] else Path(k["job_dir"] or JOBS / k["id"])
    try: res = json.loads(Path(k["result_path"]).read_text())
    except Exception: res = {}
    files = []
    for f in (res.get("files") or [])[:8]:
        p = base / f; item = {"path": f, "exists": p.exists(), "bytes": p.stat().st_size if p.is_file() else 0}
        if p.is_file() and p.suffix.lower() in (".md", ".txt", ".html", ".htm", ".csv", ".json", ".py", ".js", ".css", ".svg"):
            try: item["excerpt"] = p.read_text(errors="replace")[:2500]
            except Exception: pass
        files.append(item)
    return {"summary": res.get("summary"), "status": res.get("status"), "open_issues": res.get("open_issues") or [],
            "files": files, "verify_p": k["verify_p"]}


def decide(kids, pb, compare=None):
    """Pure winner logic. kids: rows/dicts for a and b. compare(state)->(choice, conf) for the both-pass case.
    -> (winner_role, reason, decision_or_None)."""
    a, b = kids
    ok = {k["bo2_role"]: k["status"] == "done" and k["verify_status"] == "pass" for k in kids}
    p = lambda k: k["verify_p"] if k["verify_p"] is not None else -1
    desc = lambda k: f"{k['bo2_role']}={k['model']} {k['status']}" + (f" (verify p={k['verify_p']})" if k["verify_p"] is not None else "")
    by_verifier = lambda: (a if (p(a), -(a["tokens"] or 0)) >= (p(b), -(b["tokens"] or 0)) else b)
    if ok["a"] != ok["b"]:
        w = a if ok["a"] else b; l = b if w is a else a
        return w["bo2_role"], f"only {w['bo2_role']} ({w['model']}) passed verification; {desc(l)}", None
    if not ok["a"]:
        w = by_verifier()
        return w["bo2_role"], f"neither passed verification ({desc(a)}; {desc(b)}); kept the higher verifier score as best attempt", None
    th = float(pb["thresholds"].get("min_choice_confidence", 0.55))
    if compare:
        try:
            ch, conf = compare()
            if ch in ("a", "b") and conf >= th:
                w = a if ch == "a" else b
                return ch, (f"both passed ({desc(a)}; {desc(b)}); blind Jev comparison picked {ch} ({w['model']}) "
                            f"with confidence {conf} >= {th}"), ("bo2_winner", ch, conf, True)
            fb = by_verifier()
            return fb["bo2_role"], (f"both passed ({desc(a)}; {desc(b)}); Jev comparison {ch} conf {conf} < {th} -> "
                                    f"verifier score decides: {fb['bo2_role']} ({fb['model']})"), ("bo2_winner", ch, conf, False)
        except Exception as e:
            err = f"Jev comparison unavailable ({type(e).__name__})"
    else: err = "no comparison"
    w = by_verifier()
    return w["bo2_role"], f"both passed ({desc(a)}; {desc(b)}); {err} -> verifier score decides: {w['bo2_role']} ({w['model']})", None


def maybe_finish(c, pid):
    parent = c.execute("SELECT * FROM jobs WHERE id=?", (pid,)).fetchone()
    kids = children(c, pid)
    if not parent or len(kids) != 2 or parent["winner"] or not all(k["status"] in TERMINAL for k in kids): return None
    cur = c.execute("UPDATE jobs SET winner='pending' WHERE id=? AND winner IS NULL", (pid,)); c.commit()
    if cur.rowcount != 1: return None  # the sibling's process is already judging
    try: return _finish(c, parent, kids)
    except BaseException:
        c.execute("UPDATE jobs SET winner=NULL WHERE id=? AND winner='pending'", (pid,)); c.commit(); raise


def _finish(c, parent, kids):
    pid = parent["id"]; pb = playbook()
    both_pass = all(k["status"] == "done" and k["verify_status"] == "pass" for k in kids)
    compare = None
    if both_pass and pb.get("enabled", True):
        state = {"goal": parent["goal"][:1500], "done_when": parent["done_when"] or "goal met",
                 "candidates": {k["bo2_role"]: _cand_state(k) for k in kids}}
        compare = lambda: jev.ask_compare(pb, state)
    role, reason, d = decide(kids, pb, compare)
    w = next(k for k in kids if k["bo2_role"] == role)
    if d:
        conf = d[2]; record_decisions(c, pid, [(d[0], d[1], conf, band(conf, pb["policy"]), d[3], reason[:600])], True)
    if w["status"] == "done": status = "done"
    elif any(k["status"] == "needs_review" for k in kids): status = "needs_review"
    else: status = "failed"
    secs = None
    if parent["started_at"]:
        from datetime import datetime
        try: secs = int(now() - datetime.fromisoformat(parent["started_at"]).timestamp())
        except Exception: pass
    summary = {"parent": pid, "winner": role, "winner_model": w["model"], "reason": reason,
               "candidates": [{"role": k["bo2_role"], "job": k["id"], "model": k["model"], "seat": k["seat_id"],
                               "status": k["status"], "verify_status": k["verify_status"], "verify_p": k["verify_p"],
                               "tokens": k["tokens"] or 0, "secs": k["secs"], "fix_rounds": k["fix_rounds"] or 0,
                               "result": k["result_path"]} for k in kids], "finished_at": iso()}
    (JOBS / pid).mkdir(parents=True, exist_ok=True)
    (JOBS / pid / "best_of_two.json").write_text(json.dumps(summary, indent=2))
    c.execute("UPDATE jobs SET status=?, winner=?, winner_reason=?, result_path=?, verify_p=?, verify_status=?, "
              "finished_at=?, secs=?, err_sig=?, lease_until=NULL WHERE id=?",
              (status, role, reason, w["result_path"], w["verify_p"], w["verify_status"], iso(), secs,
               None if status == "done" else w["err_sig"], pid))
    c.commit()
    log({"event": "best_of_two", **{k: v for k, v in summary.items() if k != "candidates"},
         "candidates": [{x: y for x, y in cd.items() if x != "result"} for cd in summary["candidates"]]})
    print(f"best-of-two {pid}: winner {role} ({w['model']}) - {reason}")
    return summary
