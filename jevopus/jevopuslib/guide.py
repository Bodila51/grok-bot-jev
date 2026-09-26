"""`jevopus.py guide`: a short, complete English guide for the bot to translate and relay to the owner."""
import json
from .core import RECIPES, config, db, playbook, refresh_seats


PURPOSE = {  # one-line purpose per recipe (fallback: the recipe's description)
    "code-review": "read-only review of a code folder, findings by severity",
    "competitor-analysis": "compare named competitors: positioning, offer, public pricing, gaps; with sources",
    "data-cleanup": "clean a CSV (duplicates, formats, types) + a short report",
    "landing-page": "one-file responsive landing page (HTML, no build step)",
    "lead-finder": "find matching companies/agencies from public sources + pitch drafts (never sent)",
    "media-kit": "one-page media kit for a creator from your facts; missing data clearly marked",
    "motion-video": "short motion-design MP4 video rendered offline",
    "presentation": "slide deck (self-contained HTML, printable to PDF) + speaker notes",
    "research-brief": "short brief with live web search, comparison table and cited sources",
    "x-post-drafts": "5 X/Twitter post drafts in your voice (never posted)"}


def cmd_guide(a):
    from . import models as mdl
    c = db(); cfg = config(); refresh_seats(c)
    avail = [m for m in cfg["models"] if mdl.availability(c, cfg, m)["available"]]
    off = [m for m in cfg["models"] if m not in avail]
    seats = [s["id"] for s in c.execute("SELECT id FROM seats WHERE enabled=1")]
    recipes = []
    for p in sorted(RECIPES.glob("*.json")):
        try: r = json.loads(p.read_text()); recipes.append((r["name"], r.get("description", "")))
        except Exception: pass
    mode = playbook().get("mode")
    L = ["JEVOPUS GUIDE - how to work with me",
         f"Now: seats connected: {', '.join(seats) or 'none yet'}; models available: {', '.join(avail) or 'none'}"
         + (f"; not connected: {', '.join(off)}" if off else "") + ".",
         "",
         "1. Tasks in plain words. Say what you want, any limits, and how we know it is done. I turn it into a job, pick",
         "   the worker (Codex or Claude Code) and model, run it, check the result against 'done when' (one fix round",
         "   if needed) and report back. Small questions I answer directly without spending a worker.",
         "2. Naming a model. Say e.g. 'use Sonnet' or 'GPT-6 Sol'; common names and small typos are understood. If it is",
         "   not connected or is cooling down, the job waits and I tell you why, how to connect it, and 1-2 alternatives:",
         "   you choose connect / use the alternative / cancel. Nothing is swapped silently.",
         "3. Best of two. Say 'best of two' for important work: two models do the same job (in parallel when two seats",
         "   are connected), both are checked, and you get the better one with the reason. It costs about twice the usage.",
         f"4. Recipes (ready-made jobs; just give the details):"]
    L += [f"   - {n}: {PURPOSE.get(n) or d[:90]}" for n, d in recipes]
    L += ["5. Limits. 'limits' shows per worker how much of the plan's allowance is used and when it resets (numbers",
          "   reported by the provider), plus what I counted myself. Big jobs wait instead of starting on a worker above 90%.",
          "6. Reports and transparency. After each job you get: model and why, tokens, time, check result, files. For",
          "   every job I keep the exact prompt sent and the full worker log; ask to see them any time.",
          "7. Teach me. Tell me 'good' or 'wrong' about a result; it is recorded and improves which model I pick next time.",
          "8. Other bots can send jobs with a short block (they cannot approve risky actions for you):",
          "     JEVOPUS JOB",
          "     from: <bot name>",
          "     goal: <one line>",
          "     done-when: <checkable finish condition>",
          "     model: <optional>   best-of-two: no   confirmed: no",
          "9. Your yes is always needed for anything irreversible or external: sending, posting, publishing, paying,",
          "   deleting outside the job folder, account changes. Workers only write drafts and files; they have no passwords.",
          "10. Updates: once a week I can check for a Jevopus update and install it only after you say yes.",
          f"11. Decision helper (Jev) mode: {mode}. In shadow its advice is logged and checked against results; when it is",
          "    right at least 90% of the time over 20+ decisions I will suggest 'active'. Only you switch it.",
          "Ask 'what models are available', 'limits', 'report of my last job', or 'show the prompt' any time."]
    print("\n".join(L))
