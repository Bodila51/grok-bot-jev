"""status / report / usage / weekly / feedback / recipes."""
import json, re, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .core import JOBS, LOG, RECIPES, db, jev_dir, log, playbook, refresh_seats


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


def _since(spec):
    m = re.fullmatch(r"(\d+)([dh])", spec or "7d")
    if not m: sys.exit("--since like 7d or 24h")
    d = timedelta(days=int(m[1])) if m[2] == "d" else timedelta(hours=int(m[1]))
    return (datetime.now(timezone.utc) - d).isoformat(timespec="seconds")


def cmd_report(a):
    c = db(); j = c.execute("SELECT * FROM jobs WHERE id=?", (a.job_id,)).fetchone()
    if not j: sys.exit(f"no job {a.job_id}")
    jdir = JOBS / j["id"]
    print(f"JOB {j['id']}  status={j['status']}  route={j['route']}  from_agent={j['from_agent'] or '-'}  recipe={j['recipe'] or '-'}")
    print(f"goal: {j['goal']}")
    if j["cached_from"]: print(f"cache: reused result of {j['cached_from']} (not rerun)")
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
    logs = sorted(str(p) for p in jdir.glob("worker*.log"))
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
    outc = {j["id"]: job_outcome(j) for j in jobs}
    per = defaultdict(lambda: [0, 0, 0])  # ok, wrong, unknown
    for d in c.execute("SELECT * FROM decisions WHERE ts>=? AND jev_used=1", (since,)):
        o = outc.get(d["job_id"]); per[d["kind"]][0 if o == "ok" else 1 if o == "wrong" else 2] += 1
    print("  Jev decisions vs outcomes (outcome = verification pass/fail, or your feedback override):")
    tot_ok = tot = 0
    for k, (ok, bad, unk) in sorted(per.items()):
        n = ok + bad; tot_ok += ok; tot += n
        print(f"    {k:11} judged={n:>3} ok={ok:>3} wrong={bad:>3} no-outcome={unk:>3}" + (f"  acc={ok/n:.0%}" if n else ""))
    acc = tot_ok / tot if tot else 0
    ready = tot >= 20 and acc >= 0.9
    print(f"  estimated Jev accuracy: {acc:.0%} on {tot} decisions with an outcome (estimate; outcomes are per job)")
    print(f"  playbook mode: {pb.get('mode')} -> " + ("READY to switch to active (>=90% on >=20)" if ready else
          f"stay in shadow (need >=90% on >=20 decisions; have {acc:.0%} on {tot})"))
    if ready and pb.get("mode") != "active":
        print(f"    to switch: set `mode: active` in {jev_dir()}/config.yaml")


def cmd_feedback(a):
    c = db()
    if not c.execute("SELECT 1 FROM jobs WHERE id=?", (a.job_id,)).fetchone(): sys.exit(f"no job {a.job_id}")
    c.execute("UPDATE jobs SET feedback=?, feedback_note=? WHERE id=?", (a.verdict, " ".join(a.note), a.job_id))
    c.execute("UPDATE decisions SET outcome=? WHERE job_id=?", (a.verdict, a.job_id)); c.commit()
    log({"event": "feedback", "job_id": a.job_id, "verdict": a.verdict}); print(f"{a.job_id}: feedback {a.verdict} recorded")


def cmd_recipes(a):
    for p in sorted(RECIPES.glob("*.json")):
        r = json.loads(p.read_text())
        vs = " ".join(f"{k}={'<required>' if (v.get('default') if isinstance(v, dict) else v) in (None, '') else repr(v.get('default') if isinstance(v, dict) else v)}"
                      for k, v in r.get("vars", {}).items())
        print(f"  {r['name']:15} {r.get('description','')}\n  {'':15} vars: {vs}  tools: {','.join(r.get('tools', [])) or '-'}")
