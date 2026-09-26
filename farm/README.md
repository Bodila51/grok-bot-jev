# Farm v2 — worker farm with a Jev decision layer

Farm runs headless coding agents ("seats": Codex via a ChatGPT subscription or an API key; Claude Code optional)
on this computer, one job per seat, and brings back compact results. **Jev** (TypeSafe System One) makes the
judgment calls; plain code owns leases, runs, policy and logs. Secrets are never printed or logged, and workers
never receive credentials (all `*_API_KEY`/`*TOKEN*`/`*SECRET*` env vars are stripped from worker processes).

## Install (fresh computer)
`bash farm_install.sh [--no-render]` (single self-contained script; `FARM_HOME` default `/workspace/farm`).
It installs Node 22 + `@openai/codex` if needed, clones the public Jev router
[grok-bot-jev](https://github.com/Bodila51/grok-bot-jev) (reuses an existing grok-bot-jev or muse-jev-playbook
checkout if present), writes Farm, creates `farm.db` with **all seats disabled**, and runs `farm.py doctor`.
Then: `python3 $FARM_HOME/farm.py setup-seat codex-sub` (device login; seat is enabled only after a live test).
From a clone of grok-bot-jev: `bash farm/install.sh`.

## Commands
```
farm.py submit --goal G [--constraints C] [--done-when D] [--kind K]    # prints job_id
               [--model M] [--effort low|medium|high] [--seat S]        # pins always win over Jev
               [--recipe NAME --var k=v ...] [--from-agent NAME] [--confirmed] [--force]
farm.py route <id>     # ONE Jev call: inline-vs-farm, tier, complexity, irreversible guard, model choice,
                       #   reuse-cache choice, stop-retry (when the same error repeated)
farm.py tick           # only leases: queued farm jobs -> free enabled non-cooldown seat offering the job's model
farm.py run <id>       # run leased job, parse tokens/session, Jev-verify vs done_when, <=1 fix round (resume)
farm.py confirm <id> | cancel <id> | status
farm.py report <id>    # what the bot shows the user after each job
farm.py usage [--since 7d] | weekly | feedback <id> ok|wrong [note]
farm.py recipes | doctor | setup-seat codex-sub|codex-api|claude-strong [--disable] | init
```
Typical flow: `id=$(farm.py submit ...) && farm.py route $id && farm.py tick && farm.py run $id && farm.py report $id`.

## Decisions (Jev) and policy
- Thresholds from the Jev repo `config.yaml` (`policy.act_min` 0.80 / `surface_min` 0.50; `thresholds.reuse_min`,
  `min_choice_confidence`, `stop_retry_min`; `limits.max_retries_same_error`). Noul confidence = max(p, 1-p).
- **Model selection**: if no `--model`, Jev Choice among models on ENABLED seats, using the task-fit text in
  `config.json` → `models` (editable defaults, not benchmarks). Below `min_choice_confidence` → `default_model`.
- **Reuse/cache**: Jev Choice over the last 20 done+verified jobs (+ "none"); conf ≥ `reuse_min` → job becomes
  `cached` and points at the earlier result. `--force` skips this.
- **Stop-retry**: failures are tracked per goal hash + error signature; after `max_retries_same_error` identical
  failures Jev's stop_retry noul ≥ `stop_retry_min` → job `blocked` (refused, logged). Change the goal/approach.
- **Irreversible guard**: Jev noul on goal+constraints (send/publish/pay/delete outside job dir/account changes);
  p ≥ 0.5 → the job needs `--confirmed` or `farm.py confirm <id>` before tick will lease it. Irreversible actions
  always need a human regardless of Jev; workers get no credentials.
- **Verification**: after a run, Jev noul "result.json + listed files (existence/size checked by code) satisfy
  done_when?" (state: goal, done_when, result, files, last 4k of worker log). p < 0.5 → resume the same session
  with a fix prompt (max `verify.max_fix_rounds`, default 1), else status `needs_review`.
- Kill switch: `enabled: false` in the Jev config or "bypass jev"/"no jev" in the goal → keyword fallback rules
  (`jev_used:false`). Jev errors also fall back; the guard fallback is keyword-based and errs toward confirming.
- Farm applies its decisions itself; `farm.py weekly` estimates Jev accuracy (outcome = verification pass/fail or
  your `feedback` override) and says when the Jev config may move from `mode: shadow` to `active` (≥90% on ≥20).

## Seats and rules
- `codex-sub` (ChatGPT subscription, `CODEX_HOME=seats/codex-sub/codex`, runs with API-key env removed),
  `codex-1` (= `setup-seat codex-api`, key passed to `codex login --with-api-key` via stdin from
  `FARM_OPENAI_API_KEY`/`OPENAI_API_KEY`), `claude-strong` (optional, off by default).
- Seats stay disabled until `setup-seat` passes a live test. One active job per seat; parallel only across seats.
- On a rate/usage limit the seat goes to `cooldown` (1h, `cooldown_sec`) and the job is marked failed —
  it is never moved to another login. Pinned `--seat` jobs only ever lease to that seat.
- Don't spend a seat on work a few direct tool calls can do: `route=inline` jobs are never leased.
- Worker contract: `jobs/<id>/result.json` = `{status, summary, files, open_issues}`; raw output in `worker*.log`.
- Logs: `logs/routing.jsonl` (route/verify/run/fix events, no secrets); decisions in `farm.db` (`decisions`).

## Recipes (`recipes/*.json`)
research-brief (topic, n) · landing-page (topic, style) · motion-video (title, lines, tagline, seconds — adapts
`recipes/assets/motion_reference.py`, the offline Pillow+ffmpeg renderer from the Farm presentation job) ·
code-review (repo, focus; read-only) · data-cleanup (input CSV → cleaned.csv + report) · x-post-drafts (topic,
voice; 5 drafts, never posted). Example: `farm.py submit --recipe research-brief --var topic="..." --var n=3`.

## Agent intake (other bots → Farm)
Other agents send a plain message; the receiving bot maps it 1:1 onto `farm.py submit --from-agent <name>`:
```
FARM JOB
from: <agent name>
goal: <one line>
constraints: <hard limits, optional>
done-when: <checkable finish condition>
recipe: <name, optional>   vars: k=v; k=v   (optional)
model: <optional pin>      confirmed: no
```
The Farm bot routes it, runs it and replies with `farm.py report <id>`. `confirmed: yes` is honored only when the
human owner confirmed; a bot cannot confirm irreversible actions for them.

## Files
`farm.py` (CLI) · `farmlib/core.py` (paths, config, db + idempotent migrations) · `farmlib/jev.py` (all Jev
calls + fallbacks) · `farmlib/runner.py` (submit/route/tick/run/verify) · `farmlib/reports.py` · `farmlib/setup.py`
(doctor, setup-seat) · `config.json` (created on first run; models table etc.) · `recipes/` · `tools/resume_fix.sh`
(manual follow-up on a codex session) · `tools/render-venv` (Pillow, numpy, pycairo; optional).
