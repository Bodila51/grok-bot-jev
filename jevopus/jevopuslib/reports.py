"""status / report / usage / weekly / feedback / recipes."""
import json, re, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .core import JOBS, LOG, RECIPES, config, db, jev_dir, job_dir, log, playbook, refresh_seats


def cmd_status(a):
    c = db(); refresh_seats(c)
    print("SEATS")
    for s in c.execute("SELECT * FROM seats"):
        cd = f" until {time.strftime('%H:%M', time.localtime(s['cooldown_until']))}" if s["cooldown_until"] else ""
        print(f"  {s['id']:14} {s['cli']:6} en={s['enabled']} {s['status']:8}{cd} {s['auth']:12} models={s['models']}  {s['note'] or ''}")
    print("JOBS")
    for j in c.execute("SELECT * FROM jobs ORDER BY created_at"):
        print(f"  {j['id']} {j['status']:12} {j['route'] or '-'}/{j['model'] or j['tier'] or '-'} seat={j['seat_id'] or '-'} "
              f":: {j['goal'][:60]!r}")
    from . import limits
    print("LIMITS  " + limits.short_line(c, config()) + "   (details: jevopus.py limits)")


def _since(spec):
    m = re.fullmatch(r"(\d+)([dh])", spec or "7d")
    if not m: sys.exit("--since like 7d or 24h")
    d = timedelta(days=int(m[1])) if m[2] == "d" else timedelta(hours=int(m[1]))
    return (datetime.now(timezone.utc) - d).isoformat(timespec="seconds")


def cmd_report(a):
    c = db(); j = c.execute("SELECT * FROM jobs WHERE id=?", (a.job_id,)).fetchone()
    if not j: sys.exit(f"no job {a.job_id}")
    jdir = job_dir(j)
    print(f"JOB {j['id']}  status={j['status']}  route={j['route']}  from_agent={j['from_agent'] or '-'}  recipe={j['recipe'] or '-'}")
    print(f"goal: {j['goal']}")
    if j["cached_from"]: print(f"cache: reused result of {j['cached_from']} (not rerun)")
    if j["parent_id"]: print(f"best-of-two: candidate {j['bo2_role']} of {j['parent_id']} (see jevopus.py report {j['parent_id']})")
    if j["route"] == "best_of_two" or j["best_of_two"]:
        kids = c.execute("SELECT * FROM jobs WHERE parent_id=? ORDER BY bo2_role", (j["id"],)).fetchall()
        if kids:
            print("BEST OF TWO" + (f": winner {j['winner']} - {j['winner_reason']}" if j["winner"] else " (no winner yet)"))
            for k in kids:
                print(f"  {k['bo2_role']}: {k['id']}  model={k['model']}  seat={k['seat_id'] or '-'}  status={k['status']}  "
                      f"verify={k['verify_status'] or '-'}" + (f" p={k['verify_p']}" if k['verify_p'] is not None else "")
                      + f"  tokens={k['tokens'] or 0:,}  time={k['secs'] if k['secs'] is not None else '-'}s  "
                      f"fix rounds={k['fix_rounds'] or 0}" + ("  <- WINNER" if k["bo2_role"] == j["winner"] else ""))
            print(f"  total tokens (both): {sum(k['tokens'] or 0 for k in kids):,}   details: {JOBS / j['id'] / 'best_of_two.json'}"
                  f"   per-candidate logs/prompts: jevopus.py report {j['id']}-a | -b")
        elif j["best_of_two"]: print("best-of-two requested (candidates are created by route)")
    print(f"seat: {j['seat_id'] or '-'}   model: {j['model'] or '-'} ({j['model_source'] or '-'})   effort: {j['effort'] or 'default'}")
    print(f"tokens: {j['tokens'] or 0:,}   duration: {j['secs'] if j['secs'] is not None else '-'}s   "
          f"fix rounds: {j['fix_rounds'] or 0}   guard: {'needs confirm' if j['requires_confirm'] else 'ok'}"
          f"{' (confirmed)' if j['confirmed'] else ''}")
    print(f"verification: {j['verify_status'] or '-'}" + (f" (p={j['verify_p']})" if j['verify_p'] is not None else ""))
    print("jev decisions:")
    for d in c.execute("SELECT * FROM decisions WHERE job_id=? ORDER BY id", (j["id"],)):
        print(f"  {d['kind']:11} {d['answer']:<22} conf={d['conf'] if d['conf'] is not None else '-':<6} {d['band'] or '':9} "
              f"{'applied' if d['applied'] else 'advice'}{'' if d['jev_used'] else ' (fallback)'}")
    print(f"note: {j['route_note'] or '-'}")
    try: ev = json.loads(j["model_evidence"]) if j["model_evidence"] else None
    except Exception: ev = None
    if ev:
        print(f"model evidence ({ev.get('basis')}):")
        for m, line in (ev.get("lines") or {}).items(): print(f"  {m:12} {line}")
    else:
        print("model evidence: - (" + ("model pinned" if j["model_source"] == "pinned" else "routed before evidence existed / no candidate models") + ")")
    if j["feedback"]: print(f"feedback: {j['feedback']} {j['feedback_note'] or ''}")
    rp = Path(j["result_path"]) if j["result_path"] else jdir / "result.json"
    try:
        r = json.loads(rp.read_text())
        print(f"result: {r.get('status')} - {r.get('summary')}")
        if r.get("open_issues"): print(f"open issues: {r['open_issues']}")
        base = rp.parent
        print("files: " + ", ".join(str(base / f) for f in r.get("files") or []))
    except Exception: print("result: (none yet)")
    logs = sorted(str(p) for p in [*jdir.glob("worker*.log"), *jdir.glob("[ab]/worker*.log")])
    print(f"logs: {', '.join(logs) or '-'}\nresult.json: {rp}")
    if j["prompt"]: print("---- exact prompt sent ----\n" + j["prompt"])


def cmd_usage(a):
    c = db(); since = _since(a.since)
    print(f"USAGE since {a.since} ({since} UTC)")
    print(f"  {'seat':14} {'jobs':>4} {'done':>4} {'failed':>6} {'review':>6} {'tokens':>10} {'cooldowns':>9}")
    for s in c.execute("SELECT id FROM seats"):
        r = c.execute("SELECT COUNT(*) n, SUM(status='done') d, SUM(status='failed') f, SUM(status='needs_review') nr, "
                      "SUM(COALESCE(tokens,0)) t FROM jobs WHERE seat_id=? AND created_at>=?", (s["id"], since)).fetchone()
        cd = c.execute("SELECT COUNT(*) FROM seat_events WHERE seat_id=? AND event='cooldown' AND ts>=?", (s["id"], since)).fetchone()[0]
        print(f"  {s['id']:14} {r['n']:>4} {r['d'] or 0:>4} {r['f'] or 0:>6} {r['nr'] or 0:>6} {r['t'] or 0:>10,} {cd:>9}")
    other = c.execute("SELECT route, COUNT(*) FROM jobs WHERE seat_id IS NULL AND created_at>=? GROUP BY route", (since,)).fetchall()
    if other: print("  not run on a seat: " + ", ".join(f"{r[0] or 'unrouted'}={r[1]}" for r in other))


def job_outcome(j):
    if j["feedback"]: return j["feedback"]  # user override wins
    return {"pass": "ok", "fail": "wrong"}.get(j["verify_status"])


def cmd_weekly(a):
    c = db(); since = _since("7d"); pb = playbook()
    jobs = c.execute("SELECT * FROM jobs WHERE created_at>=?", (since,)).fetchall()
    print(f"WEEKLY JEVOPUS REPORT (last 7 days, {len(jobs)} jobs)")
    for label, key in (("route", "route"), ("tier", "tier"), ("model", "model"), ("status", "status")):
        cnt = Counter(j[key] or "-" for j in jobs)
        print(f"  by {label:6}: " + ", ".join(f"{k}={v}" for k, v in cnt.most_common()))
    print(f"  tokens: {sum(j['tokens'] or 0 for j in jobs):,}   cache hits: {sum(j['status']=='cached' for j in jobs)}   "
          f"blocked by stop_retry: {sum(j['route']=='stopped' for j in jobs)}   guarded: {sum(bool(j['requires_confirm']) for j in jobs)}")
    acc7 = jev_accuracy(c, since)
    print("  Jev decisions vs outcomes (outcome = verification pass/fail, or your feedback override):")
    for k, (ok, bad, unk) in sorted(acc7["per"].items()):
        n = ok + bad
        print(f"    {k:11} judged={n:>3} ok={ok:>3} wrong={bad:>3} no-outcome={unk:>3}" + (f"  acc={ok/n:.0%}" if n else ""))
    print(f"  estimated Jev accuracy (7d): {acc7['acc']:.0%} on {acc7['n']} decisions with an outcome (estimate; outcomes are per job)")
    print("  " + mode_progress_line(c, pb))


def cmd_feedback(a):
    c = db()
    if not c.execute("SELECT 1 FROM jobs WHERE id=?", (a.job_id,)).fetchone(): sys.exit(f"no job {a.job_id}")
    c.execute("UPDATE jobs SET feedback=?, feedback_note=? WHERE id=?", (a.verdict, " ".join(a.note), a.job_id))
    w = c.execute("SELECT winner FROM jobs WHERE id=?", (a.job_id,)).fetchone()["winner"]
    if w:  # best-of-two: the delivered result is the winner's, so its model gets the feedback (evidence)
        c.execute("UPDATE jobs SET feedback=?, feedback_note=? WHERE id=?", (a.verdict, " ".join(a.note), f"{a.job_id}-{w}"))
    c.execute("UPDATE decisions SET outcome=? WHERE job_id=?", (a.verdict, a.job_id)); c.commit()
    log({"event": "feedback", "job_id": a.job_id, "verdict": a.verdict}); print(f"{a.job_id}: feedback {a.verdict} recorded")


def cmd_recipes(a):
    for p in sorted(RECIPES.glob("*.json")):
        r = json.loads(p.read_text())
        vs = " ".join(f"{k}={'<required>' if (v.get('default') if isinstance(v, dict) else v) in (None, '') else repr(v.get('default') if isinstance(v, dict) else v)}"
                      for k, v in r.get("vars", {}).items())
        print(f"  {r['name']:20} {r.get('description','')}\n  {'':20} vars: {vs}  tools: {','.join(r.get('tools', [])) or '-'}")


ACC_MIN, ACC_N = 0.9, 20


def jev_accuracy(c, since=None):
    """Jev decisions (jev_used=1) vs job outcomes. -> {per: {kind: [ok, wrong, unknown]}, ok, n, acc, ready}."""
    jobs = {j["id"]: j for j in c.execute("SELECT * FROM jobs" + (" WHERE created_at>=?" if since else ""), (since,) if since else ())}
    per = defaultdict(lambda: [0, 0, 0])
    q = "SELECT * FROM decisions WHERE jev_used=1" + (" AND ts>=?" if since else "")
    for d in c.execute(q, (since,) if since else ()):
        j = jobs.get(d["job_id"]); o = job_outcome(j) if j else None
        per[d["kind"]][0 if o == "ok" else 1 if o == "wrong" else 2] += 1
    ok = sum(v[0] for v in per.values()); n = ok + sum(v[1] for v in per.values())
    acc = ok / n if n else 0.0
    return {"per": dict(per), "ok": ok, "n": n, "acc": acc, "ready": n >= ACC_N and acc >= ACC_MIN}


def mode_progress_line(c, pb=None):
    pb = pb or playbook(); a = jev_accuracy(c); mode = pb.get("mode")
    s = f"Jev mode: {mode}; accuracy (all time) {a['acc']:.0%} on {a['n']}/{ACC_N} decisions with an outcome (need >={ACC_MIN:.0%} on >={ACC_N})"
    if mode != "active":
        s += " -> READY: owner may switch with `jevopus.py jev-mode active`" if a["ready"] else " -> stay in shadow"
    return s


def _mode_log(c):
    r = c.execute("SELECT v FROM meta WHERE k='jev_mode_log'").fetchone()
    try: return json.loads(r[0]) if r else []
    except Exception: return []


def set_mode_in_yaml(path, mode):
    """Rewrite only the top-level `mode:` line of the Jev config.yaml (comments and other keys untouched)."""
    txt = path.read_text() if path.exists() else ""
    if re.search(r"(?m)^mode:.*$", txt): txt = re.sub(r"(?m)^mode:.*$", f"mode: {mode}", txt, count=1)
    else: txt = txt + ("" if txt.endswith("\n") or not txt else "\n") + f"mode: {mode}\n"
    path.write_text(txt)


def cmd_jev_mode(a):
    import getpass, os
    c = db(); pb = playbook(); cur = pb.get("mode"); d = jev_dir(); acc = jev_accuracy(c)
    if not a.mode:
        print(f"Jev mode: {cur}   (config: {d / 'config.yaml' if d else 'no Jev repo found'})")
        print("  shadow = Jev's routing advice is logged and applied by Jevopus with thresholds and fallbacks, but the")
        print("           dispatcher bot may override it with its own judgment; active = the bot honors Jev's route advice.")
        print("  " + mode_progress_line(c, pb))
        hist = _mode_log(c)
        if hist:
            print("  changes:"); [print(f"    {h['ts']}  {h['from']} -> {h['to']}  by {h['by']}" + (f"  ({h['note']})" if h.get("note") else "")) for h in hist[-10:]]
        else: print("  changes: none recorded (installed in shadow)")
        return
    if not d: sys.exit("no Jev router repo found (see jevopus.py doctor)")
    if a.mode == cur: print(f"Jev mode already {cur}; nothing changed"); return
    if a.mode == "active" and not acc["ready"] and not a.force:
        sys.exit(f"not switching: accuracy {acc['acc']:.0%} on {acc['n']} decisions (need >={ACC_MIN:.0%} on >={ACC_N}). "
                 "The owner can override with --force.")
    by = a.by or os.environ.get("JEVOPUS_ACTOR") or getpass.getuser()
    set_mode_in_yaml(d / "config.yaml", a.mode)
    hist = _mode_log(c) + [{"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "from": cur, "to": a.mode,
                            "by": by, "note": (a.note or "") + (" (forced below threshold)" if a.force and not acc["ready"] else ""),
                            "accuracy": round(acc["acc"], 3), "decisions": acc["n"]}]
    c.execute("INSERT OR REPLACE INTO meta VALUES('jev_mode_log', ?)", (json.dumps(hist),)); c.commit()
    log({"event": "jev_mode", "from": cur, "to": a.mode, "by": by, "accuracy": round(acc["acc"], 3), "decisions": acc["n"]})
    print(f"Jev mode: {cur} -> {a.mode} (by {by}); written to {d / 'config.yaml'}")
