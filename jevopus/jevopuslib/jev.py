"""All Jev (TypeSafe System One) calls. One parallel call per decision point; keyword fallbacks on error.
Code owns policy; Jev only judges. Never put secrets in state."""
import re

WORKER_NOTE = ("Worker seats are separate headless coding agents (Codex/Claude Code) that run in their own job "
             "directory, can edit files and run commands, and cost a subscription session each. The dispatcher "
             "can instead answer directly or do a few tool calls itself.")
IRREV_RE = re.compile(r"\b(send|e-?mail|post|publish|tweet|pay|purchase|buy|transfer|delete|remove|rm -rf|drop|"
                      r"deploy|push|share|invite|log ?in|password|account|unsubscribe|message)\b", re.I)


def _client(pb):
    from typesafe_sdk import TypeSafeClient
    return TypeSafeClient(model=pb.get("model") or "jev-latest")


def route_questions(job, models, recent, fail_info, evidence=None):
    """Build state + questions for the routing call. models: {name: fit}; recent: [(id, goal)];
    evidence: {model: one-line past-outcome summary} (see evidence.py)."""
    from typesafe_sdk import Choice, Noul, Score
    state = {"job": {"goal": job["goal"][:1500], "constraints": job["constraints"] or "",
                     "done_when": job["done_when"] or "", "kind_hint": job["kind"] or "unknown"},
             "workers": WORKER_NOTE}
    q = {
        "needs_farm": Noul(instructions=
            "Does `job` need a dedicated worker agent session (creating/editing files, running code, multi-step "
            "coding or research), rather than being handled inline by the dispatcher with a direct answer or a "
            "few quick tool calls?"),
        "tier": Choice(instructions="If `job` goes to a worker, which seat tier does it need?", criteria={
            "volume": "Routine, well-specified work: small scripts, simple edits, boilerplate, running tests, "
                      "short bounded tasks a standard coding model finishes reliably",
            "strong": "Hard or long work: large multi-file refactors, architecture/design, subtle debugging, "
                      "long research-and-build tasks, work where a weaker model is likely to fail"}),
        "complexity": Score(instructions="How complex is `job` for an autonomous coding agent?", criteria=[
            "Trivial: a direct answer or one command", "Small: one short file or a few commands",
            "Medium: several files or steps, some judgment", "Large: many files/steps, hours of focused work",
            "Very large: multi-day project or deep open-ended research"]),
        "irreversible": Noul(instructions=
            "Does `job` ask the worker to send, email, post or publish something to other people or the internet, "
            "pay or purchase, delete data outside its own job directory, or change account settings or logins? "
            "Writing drafts or files locally without sending them does not count."),
    }
    if len(models) > 1:
        state["models"] = models
        instr = "Which worker model in `models` fits `job` best? Each option describes the kind of work it suits."
        if evidence:
            state["model_history"] = {m: evidence.get(m, "no past jobs") for m in models}
            instr += (" `model_history` lists real outcomes of earlier jobs on each model (verified pass / fail / "
                      "needs review, plus user feedback), similar jobs first, then all jobs. When a model has at least "
                      "3 past outcomes, weigh those real results more than its description; with fewer samples treat "
                      "them as weak hints. A model with no history is not worse, just unknown.")
        q["model"] = Choice(instructions=instr, criteria=dict(models))
    if recent:
        state["recent_done_jobs"] = {jid: g[:200] for jid, g in recent}
        crit = {jid: f"Earlier finished job with the same deliverable: {g[:200]}" for jid, g in recent}
        crit["none"] = "None of the earlier jobs produces what `job` asks for; it needs new work"
        q["reuse"] = Choice(instructions="Does one job in `recent_done_jobs` already deliver exactly what `job` "
                                         "asks for, so its result can be reused instead of running again?",
                            criteria=crit)
    if fail_info:
        state["retry"] = fail_info
        q["stop_retry"] = Noul(instructions="Given `retry.prior_error` repeated `retry.same_error_count` times for "
                                            "this same goal, should we STOP retrying the same approach?")
    return state, q


def ask_route(pb, job, models, recent, fail_info, evidence=None):
    state, q = route_questions(job, models, recent, fail_info, evidence)
    with _client(pb) as c:
        r = c.system_one(state=state, questions=q)
    out = {"needs_farm_p": round(float(r.nouls["needs_farm"].noul), 3),
           "tier": r.choices["tier"].choice, "tier_conf": round(float(r.choices["tier"].confidence), 3),
           "complexity": round(float(r.scores["complexity"].score), 3),
           "complexity_conf": round(float(r.scores["complexity"].confidence), 3),
           "irreversible_p": round(float(r.nouls["irreversible"].noul), 3)}
    if "model" in q:
        out["model"], out["model_conf"] = r.choices["model"].choice, round(float(r.choices["model"].confidence), 3)
    if "reuse" in q:
        out["reuse"], out["reuse_conf"] = r.choices["reuse"].choice, round(float(r.choices["reuse"].confidence), 3)
    if "stop_retry" in q:
        out["stop_retry_p"] = round(float(r.nouls["stop_retry"].noul), 3)
    return out


def fallback_route(job, models, recent, fail_info):
    g = f"{job['goal']} {job['constraints'] or ''}".lower(); n = len(g.split())
    inline = job["kind"] in ("chat", "lookup") or (n < 12 and g.rstrip().endswith("?"))
    strong = n > 80 or any(w in g for w in ("refactor", "architecture", "migrate", "design", "entire", "whole repo"))
    out = {"needs_farm_p": 0.1 if inline else 0.9, "tier": "strong" if strong else "volume", "tier_conf": 0.6,
           "complexity": 3.0 if strong else 1.0, "complexity_conf": 0.5,
           "irreversible_p": 0.9 if IRREV_RE.search(job["goal"]) else 0.1}  # fail safe: keywords -> confirm
    if len(models) > 1: out.update(model=None, model_conf=0.0)
    if recent:
        exact = [jid for jid, gg in recent if " ".join(gg.lower().split()) == " ".join(job["goal"].lower().split())]
        out.update(reuse=exact[0] if exact else "none", reuse_conf=1.0 if exact else 0.0)
    if fail_info: out["stop_retry_p"] = 1.0  # without Jev, be conservative: stop at the retry limit
    return out


def ask_verify(pb, state):
    from typesafe_sdk import Noul
    q = {"done": Noul(instructions="Do `result` and the files listed in `files` (with their existence and sizes "
                                   "checked by code) satisfy `done_when` for `goal`? `worker_log_tail` is the end of "
                                   "the worker's own output. Answer yes only if the deliverable is actually there.")}
    with _client(pb) as c:
        r = c.system_one(state=state, questions=q)
    return round(float(r.nouls["done"].noul), 3)


def fallback_verify(state):
    ok = state["result"].get("status") == "done" and all(f.get("exists") for f in state["files"]) and state["files"]
    return 0.8 if ok else 0.2


def ping(pb):
    from typesafe_sdk import Noul
    with _client(pb) as c:
        r = c.system_one(state={"text": "2 + 2 = 4"}, questions={"ok": Noul(instructions="Is `text` arithmetically correct?")})
    return round(float(r.nouls["ok"].noul), 3)
