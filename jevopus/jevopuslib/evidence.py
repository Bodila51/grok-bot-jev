"""Model evidence: compact per-model outcome history from past jobs, shown to Jev before it picks a model.
Only counts and short, redacted goal snippets leave the db (never prompts, logs or results)."""
import re

STOP = set("""a an and are as at be build by can do does for from get has have how i in into is it its make
me my need of on or our please should that the their then this to use using we what when with write you your
file files page script new one all any add create simple single""".split())
SECRETISH = re.compile(r"(sk|pk|rk|ghp|gho|xox[a-z]?|AKIA|eyJ)[-_A-Za-z0-9.]{8,}|[A-Za-z0-9_\-]{32,}")
MIN_SAMPLES = 3      # below this, history is a weak hint
MAX_EXAMPLES = 2     # goal snippets per model
SNIPPET = 60


def keywords(text):
    return {w for w in re.findall(r"[a-z][a-z0-9+#.-]{2,}", (text or "").lower()) if w not in STOP}


def snippet(goal):
    g = SECRETISH.sub("***", " ".join((goal or "").split()))
    return g[:SNIPPET] + ("…" if len(g) > SNIPPET else "")


def outcome(j):
    """-> pass | fail | review | None (not finished). Rate-limited failures are not a model's fault."""
    if j["status"] == "done" and (j["verify_status"] in (None, "pass")): return "pass"
    if j["status"] == "needs_review": return "review"
    if j["status"] == "failed" and j["verify_status"] != "skipped": return "fail"
    return None


def _tally(rows):
    t = {"pass": 0, "fail": 0, "review": 0, "fb_ok": 0, "fb_wrong": 0}
    for j in rows:
        o = outcome(j)
        if o: t[o] += 1
        if j["feedback"] == "ok": t["fb_ok"] += 1
        elif j["feedback"] == "wrong": t["fb_wrong"] += 1
    t["n"] = t["pass"] + t["fail"] + t["review"]
    return t


def _fmt(t):
    s = f"{t['pass']} pass/{t['fail']} fail/{t['review']} review"
    if t["fb_ok"] or t["fb_wrong"]: s += f", user ok {t['fb_ok']}/wrong {t['fb_wrong']}"
    return s


def is_similar(job, past, kw):
    if job["recipe"]: return past["recipe"] == job["recipe"]
    return past["kind"] == job["kind"] and len(kw & keywords(past["goal"])) >= 2


def build(c, job, models):
    """models: iterable of candidate model names. -> {"lines": {model: str}, "summary": str, "counts": {...}}"""
    kw = keywords(f"{job['goal']} {job['constraints'] or ''}")
    basis = f"same recipe '{job['recipe']}'" if job["recipe"] else f"same kind '{job['kind']}' + >=2 shared goal keywords"
    lines, counts = {}, {}
    for m in models:
        rows = c.execute("SELECT id, goal, kind, recipe, status, verify_status, feedback FROM jobs WHERE model=? AND id!=? "
                         "AND seat_id IS NOT NULL ORDER BY created_at DESC LIMIT 200", (m, job["id"])).fetchall()
        sim = [r for r in rows if is_similar(job, r, kw)]
        ts, ta = _tally(sim), _tally(rows)
        counts[m] = {"similar": ts, "all": ta}
        if not ta["n"] and not ta["fb_ok"] and not ta["fb_wrong"]:
            lines[m] = "no past jobs"; continue
        ex = [f"'{snippet(r['goal'])}' {outcome(r) or r['status']}" + (f" (user: {r['feedback']})" if r["feedback"] else "")
              for r in sim if outcome(r) or r["feedback"]][:MAX_EXAMPLES]
        lines[m] = (f"similar: {_fmt(ts)}" + (f" e.g. {'; '.join(ex)}" if ex else "") + f" | all jobs: {_fmt(ta)}"
                    + ("" if ta["n"] >= MIN_SAMPLES else " (few samples)"))[:320]
    summary = f"basis={basis}; " + "; ".join(
        f"{m}: sim {v['similar']['pass']}p/{v['similar']['fail']}f/{v['similar']['review']}r, "
        f"all {v['all']['pass']}p/{v['all']['fail']}f/{v['all']['review']}r"
        + (f", fb {v['all']['fb_ok']}ok/{v['all']['fb_wrong']}wrong" if v['all']['fb_ok'] or v['all']['fb_wrong'] else "")
        for m, v in counts.items())
    return {"basis": basis, "lines": lines, "summary": summary[:600], "counts": counts}
