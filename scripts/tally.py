#!/usr/bin/env python3
"""Usage: tally.py <success|failure|router_error> '<json: goal,kind,action,reason,note>'"""
import json, sys, datetime, pathlib
L = pathlib.Path(__file__).resolve().parent.parent / "logs"
outcome = sys.argv[1]; rec = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
t = json.loads((L/"eval_tally.json").read_text())
t["decisions"] += 1
if outcome == "success": t["successes"] += 1
else:
    t["failures"] += 1
    if outcome == "router_error": t["router_errors"] = t.get("router_errors", 0) + 1
a = rec.get("action")
if a: t.setdefault("route_counts", {})[a] = t["route_counts"].get(a, 0) + 1
t["updated_at_sgt"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
(L/"eval_tally.json").write_text(json.dumps(t, ensure_ascii=False, indent=2))
rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), **rec, "outcome": outcome}
with open(L/"eval.jsonl", "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(json.dumps({k: t[k] for k in ("decisions","successes","failures","router_errors")}))
