"""Seat limits: provider-reported usage (real data the worker CLIs record) + Jevopus's own counted usage.

Sources (never invented):
  codex  - Codex CLI writes `token_count` events with `rate_limits` {primary/secondary: used_percent, window_minutes,
           resets_at} into its session logs ($CODEX_HOME/sessions/**/rollout-*.jsonl). Read-only scan.
  claude - Claude Code emits `rate_limit_event` lines (rate_limit_info: status, utilization, resetsAt, rateLimitType,
           unifiedWindows) in `--output-format stream-json` output; Jevopus parses them from worker logs.
Counted = Jevopus's own jobs/tokens per seat (exact for Jevopus jobs, blind to your interactive use)."""
import glob, json, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WIN_LABEL = {300: "5h", 10080: "7d", 1440: "1d", 43200: "30d"}
CLAUDE_WIN = {"five_hour": ("5h", 300), "seven_day": ("7d", 10080), "seven_day_opus": ("7d-opus", 10080),
              "seven_day_sonnet": ("7d-sonnet", 10080), "seven_day_overage_included": ("7d-overage", 10080),
              "overage": ("overage", None)}


def _ts(s):
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception: return None


def _codex_windows(rl, seen):
    out = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict) or w.get("used_percent") is None: continue
        m = w.get("window_minutes")
        out.append({"window": WIN_LABEL.get(m, f"{m}m" if m else key), "window_minutes": m,
                    "used_percent": float(w["used_percent"]), "resets_at": w.get("resets_at"), "seen_at": seen,
                    "plan": rl.get("plan_type"), "note": rl.get("rate_limit_reached_type") or ""})
    return out


def scan_codex(home_dir, max_files=25):
    """Newest rate_limits snapshot from the Codex session logs of one seat -> list of windows (maybe empty)."""
    files = sorted(glob.glob(os.path.join(home_dir, "sessions", "**", "rollout-*.jsonl"), recursive=True),
                   key=lambda p: os.path.getmtime(p), reverse=True)[:max_files]
    for f in files:
        best = None
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    if '"rate_limits"' not in line: continue
                    try: d = json.loads(line)
                    except Exception: continue
                    rl = (d.get("payload") or {}).get("rate_limits")
                    if isinstance(rl, dict): best = (rl, _ts(d.get("timestamp") or "") or os.path.getmtime(f))
        except OSError: continue
        if best:
            w = _codex_windows(*best)
            if w: return w
    return []


def parse_claude(log_text, seen=None):
    """rate_limit_event lines in Claude Code stream-json output -> list of windows (last value per window wins)."""
    seen = seen or time.time(); got = {}
    for line in (log_text or "").splitlines():
        if '"rate_limit_event"' not in line: continue
        try: info = json.loads(line).get("rate_limit_info") or {}
        except Exception: continue
        def pct(u): return None if u is None else float(u) * (100 if float(u) <= 1.0 else 1)
        for k, w in (info.get("unifiedWindows") or {}).items():
            lab, mins = CLAUDE_WIN.get(k, (k, None))
            got[lab] = {"window": lab, "window_minutes": mins, "used_percent": pct(w.get("utilization")),
                        "resets_at": w.get("resetsAt"), "seen_at": seen, "plan": None, "note": info.get("status") or ""}
        if info.get("rateLimitType") and info.get("utilization") is not None:
            lab, mins = CLAUDE_WIN.get(info["rateLimitType"], (info["rateLimitType"], None))
            got[lab] = {"window": lab, "window_minutes": mins, "used_percent": pct(info["utilization"]),
                        "resets_at": info.get("resetsAt"), "seen_at": seen, "plan": None, "note": info.get("status") or ""}
    return [w for w in got.values() if w["used_percent"] is not None]


def record(c, seat_id, source, windows):
    for w in windows:
        c.execute("INSERT OR REPLACE INTO seat_limits(seat_id,source,window,window_minutes,used_percent,resets_at,seen_at,plan,note) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (seat_id, source, w["window"], w["window_minutes"], w["used_percent"],
                                                w["resets_at"], w["seen_at"], w.get("plan"), w.get("note")))
    c.commit()


def refresh(c):
    """Re-read provider data that is available offline (Codex session logs). Claude data arrives with each run."""
    for s in c.execute("SELECT * FROM seats WHERE cli='codex'").fetchall():
        w = scan_codex(s["home_dir"])
        if w: record(c, s["id"], "codex session log (token_count.rate_limits)", w)


def after_run(c, seat, log_text):
    """Called after every worker run: store the newest provider-reported limits for that seat."""
    try:
        if seat["cli"] == "codex": w, src = scan_codex(seat["home_dir"]), "codex session log (token_count.rate_limits)"
        else: w, src = parse_claude(log_text), "claude stream-json (rate_limit_event)"
        if w: record(c, seat["id"], src, w)
    except Exception: pass


def provider(c, seat_id, t=None):
    """-> list of rows; each has 'fresh' (window not yet reset) and 'stale' flags."""
    t = t or time.time(); out = []
    for r in c.execute("SELECT * FROM seat_limits WHERE seat_id=? ORDER BY window_minutes", (seat_id,)):
        d = dict(r); d["stale"] = bool(d["resets_at"] and d["resets_at"] < t); out.append(d)
    return out


def max_used(c, seat_id, t=None):
    """Highest provider-reported used_percent among windows that have not reset since seen (None = no data)."""
    vals = [(r["used_percent"], r) for r in provider(c, seat_id, t) if not r["stale"] and r["used_percent"] is not None]
    return max(vals, key=lambda x: x[0]) if vals else None


def counted(c, seat_id, hours):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    r = c.execute("SELECT COUNT(*) n, SUM(COALESCE(tokens,0)) t FROM jobs WHERE seat_id=? AND "
                  "COALESCE(started_at, created_at)>=?", (seat_id, since)).fetchone()
    return r["n"] or 0, r["t"] or 0


def is_large(job, cfg):
    return bool(job["best_of_two"] or job["parent_id"]) or \
        (job["complexity"] is not None and float(job["complexity"]) >= float(cfg["limits"]["large_complexity_min"]))


def gate(c, cfg, seats, job):
    """Filter seats for a job. Large jobs skip seats whose provider-reported usage >= threshold.
    -> (allowed seats, [(seat_id, reason)] skipped)."""
    if not is_large(job, cfg): return list(seats), []
    th = float(cfg["limits"]["skip_above_percent"]); ok, skipped = [], []
    for s in seats:
        m = max_used(c, s["id"])
        if m and m[0] >= th:
            skipped.append((s["id"], f"{s['id']} provider-reported {m[0]:.0f}% used ({m[1]['window']} window, "
                                     f"resets {fmt_t(m[1]['resets_at'])}) >= {th:.0f}%"))
        else: ok.append(s)
    return ok, skipped


def fmt_t(epoch):
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(epoch)) if epoch else "?"


def ago(epoch):
    if not epoch: return "?"
    s = int(time.time() - epoch)
    return f"{s // 60}m ago" if s < 3600 else f"{s // 3600}h ago" if s < 172800 else f"{s // 86400}d ago"


def short_line(c, cfg):
    """One line for doctor/status."""
    parts = []
    for s in c.execute("SELECT * FROM seats WHERE enabled=1"):
        m = max_used(c, s["id"]); n5, t5 = counted(c, s["id"], 5)
        cd = " COOLDOWN" if s["status"] == "cooldown" else ""
        parts.append(f"{s['id']}: " + (f"{m[0]:.0f}% of {m[1]['window']} (provider)" if m else "provider n/a")
                     + f", {n5} job(s)/{t5:,} tok in 5h (counted){cd}")
    return "; ".join(parts) or "no enabled seat"


def cmd_limits(a):
    from .core import config, db, refresh_seats
    c = db(); cfg = config(); refresh_seats(c); refresh(c); th = cfg["limits"]["skip_above_percent"]
    print(f"LIMITS  (now {time.strftime('%Y-%m-%d %H:%M %Z')}; large jobs skip a seat at >= {th}% provider-reported usage)")
    print("  provider = real numbers reported by the worker CLI; counted = Jevopus's own jobs/tokens (exact for Jevopus jobs,")
    print("  does not see your interactive use of the same login); no percentages are estimated or invented.")
    for s in c.execute("SELECT * FROM seats ORDER BY enabled DESC, id"):
        cd = (f"COOLDOWN until {fmt_t(s['cooldown_until'])}" if s["status"] == "cooldown" and s["cooldown_until"] else s["status"])
        print(f"\n{s['id']:14} {s['cli']:6} enabled={s['enabled']}  state={cd}")
        rows = provider(c, s["id"])
        if not rows:
            how = ("appears after the first Codex run on this seat (read from its session logs)" if s["cli"] == "codex"
                   else "appears after the first Claude Code run on this seat (rate_limit_event in stream-json)")
            print(f"  provider: no data yet - {how}")
        for r in rows:
            flag = " [STALE: window has reset since; current usage unknown, likely lower]" if r["stale"] else ""
            print(f"  provider: {r['window']:>3} window {r['used_percent']:.0f}% used, resets {fmt_t(r['resets_at'])}"
                  f"{' (plan ' + r['plan'] + ')' if r['plan'] else ''}{' ' + r['note'] if r['note'] else ''}{flag}")
            print(f"            source: {r['source']}, seen {fmt_t(r['seen_at'])} ({ago(r['seen_at'])})")
        for label, h in (("5h", 5), ("7d", 168)):
            n, t = counted(c, s["id"], h)
            print(f"  counted:  last {label}: {n} job(s), {t:,} tokens")
