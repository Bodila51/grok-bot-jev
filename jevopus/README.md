# Jevopus v2 — worker seats with a Jev decision layer

Jevopus (formerly "Farm") runs headless coding agents ("seats") on this computer, one job per seat, and brings
back compact results. Two first-class worker options: **Codex** (ChatGPT subscription login, or an OpenAI API key)
and **Claude Code** (Claude login). **Jev** (TypeSafe System One) makes the judgment calls; plain code owns leases,
runs, policy and logs. Secrets are never printed or logged, and workers never receive credentials (all
`*_API_KEY`/`*TOKEN*`/`*SECRET*` env vars are stripped from worker processes).

## Install (fresh computer)
One command, from GitHub:
```
git clone https://github.com/Bodila51/grok-bot-jev && bash grok-bot-jev/jevopus/install.sh [--no-render]
```
or the single self-contained script: `bash jevopus_install.sh [--no-render]`. `JEVOPUS_HOME` defaults to
`/workspace/jevopus` (the legacy `FARM_HOME` is still honored). The installer installs Node 22 and the Codex CLI if
needed, uses the Jev router [grok-bot-jev](https://github.com/Bodila51/grok-bot-jev) (reuses an existing
grok-bot-jev or muse-jev-playbook checkout if present), writes Jevopus, creates `jevopus.db` with **all seats
disabled**, and runs `jevopus.py doctor`. Then set up the seat(s) you want — each is enabled only after a live test:
- Codex (ChatGPT subscription): `python3 $JEVOPUS_HOME/jevopus.py setup-seat codex-sub` (device login)
- Claude Code: `npm install -g @anthropic-ai/claude-code`, log in once with
  `CLAUDE_CONFIG_DIR=$JEVOPUS_HOME/seats/claude-strong/claude claude`, then `python3 $JEVOPUS_HOME/jevopus.py setup-seat claude-strong`
- Codex (OpenAI API key): `python3 $JEVOPUS_HOME/jevopus.py setup-seat codex-api`

## Commands
```
jevopus.py submit --goal G [--constraints C] [--done-when D] [--kind K]    # prints job_id
               [--model M] [--effort low|medium|high] [--seat S]           # pins always win over Jev
               [--recipe NAME --var k=v ...] [--from-agent NAME] [--confirmed] [--force]
jevopus.py route <id>     # ONE Jev call: inline-vs-worker, tier, complexity, irreversible guard, model choice,
                          #   reuse-cache choice, stop-retry (when the same error repeated)
jevopus.py tick           # only leases: queued worker jobs -> free enabled non-cooldown seat offering the job's model
jevopus.py run <id>       # run leased job, parse tokens/session, Jev-verify vs done_when, <=1 fix round (resume)
jevopus.py confirm <id> | cancel <id> | status
jevopus.py report <id>    # what the bot shows the user after each job
jevopus.py usage [--since 7d] | weekly | feedback <id> ok|wrong [note]
jevopus.py recipes | doctor | setup-seat codex-sub|codex-api|claude-strong [--disable] | init
```
Typical flow: `id=$(jevopus.py submit ...) && jevopus.py route $id && jevopus.py tick && jevopus.py run $id && jevopus.py report $id`.

## Decisions (Jev) and policy
- Thresholds from the Jev repo `config.yaml` (`policy.act_min` 0.80 / `surface_min` 0.50; `thresholds.reuse_min`,
  `min_choice_confidence`, `stop_retry_min`; `limits.max_retries_same_error`). Noul confidence = max(p, 1-p).
- **Model selection**: if no `--model`, Jev Choice among models on ENABLED seats (Codex and/or Claude Code), using
  the task-fit text in `config.json` → `models` (editable defaults, not benchmarks). Below `min_choice_confidence`
  → `default_model`.
- **Reuse/cache**: Jev Choice over the last 20 done+verified jobs (+ "none"); conf ≥ `reuse_min` → job becomes
  `cached` and points at the earlier result. `--force` skips this.
- **Stop-retry**: failures are tracked per goal hash + error signature; after `max_retries_same_error` identical
  failures Jev's stop_retry noul ≥ `stop_retry_min` → job `blocked` (refused, logged). Change the goal/approach.
- **Irreversible guard**: Jev noul on goal+constraints (send/publish/pay/delete outside job dir/account changes);
  p ≥ 0.5 → the job needs `--confirmed` or `jevopus.py confirm <id>` before tick will lease it. Irreversible actions
  always need a human regardless of Jev; workers get no credentials.
- **Verification**: after a run, Jev noul "result.json + listed files (existence/size checked by code) satisfy
  done_when?" (state: goal, done_when, result, files, last 4k of worker log). p < 0.5 → resume the same session
  with a fix prompt (max `verify.max_fix_rounds`, default 1), else status `needs_review`.
- Kill switch: `enabled: false` in the Jev config or "bypass jev"/"no jev" in the goal → keyword fallback rules
  (`jev_used:false`). Jev errors also fall back; the guard fallback is keyword-based and errs toward confirming.
- Jevopus applies its decisions itself; `jevopus.py weekly` estimates Jev accuracy (outcome = verification pass/fail
  or your `feedback` override) and says when the Jev config may move from `mode: shadow` to `active` (≥90% on ≥20).

## Seats and rules
- `codex-sub` — Codex on a ChatGPT subscription (`CODEX_HOME=seats/codex-sub/codex`, runs with API-key env removed).
- `claude-strong` — Claude Code on a Claude login (`CLAUDE_CONFIG_DIR=seats/claude-strong/claude`).
- `codex-1` — Codex on an OpenAI API key (= `setup-seat codex-api`; key passed to `codex login --with-api-key` via
  stdin from `JEVOPUS_OPENAI_API_KEY`, else `FARM_OPENAI_API_KEY`, else `OPENAI_API_KEY`).
- All seats start disabled and are enabled only after `setup-seat` passes a live test. One active job per seat;
  parallel only across seats.
- On a rate/usage limit the seat goes to `cooldown` (1h, `cooldown_sec`) and the job is marked failed —
  it is never moved to another login. Pinned `--seat` jobs only ever lease to that seat.
- Don't spend a seat on work a few direct tool calls can do: `route=inline` jobs are never leased.
- Worker contract: `jobs/<id>/result.json` = `{status, summary, files, open_issues}`; raw output in `worker*.log`.
- Logs: `logs/routing.jsonl` (route/verify/run/fix events, no secrets); decisions in `jevopus.db` (`decisions`).
- Internal identifiers kept from v1 for history continuity: route value `farm` (= "sent to a worker seat") and the
  Jev decision `needs_farm`.

## Recipes (`recipes/*.json`)
research-brief (topic, n) · landing-page (topic, style) · motion-video (title, lines, tagline, seconds — adapts
`recipes/assets/motion_reference.py`, the offline Pillow+ffmpeg renderer from the Jevopus presentation job) ·
code-review (repo, focus; read-only) · data-cleanup (input CSV → cleaned.csv + report) · x-post-drafts (topic,
voice; 5 drafts, never posted). Example: `jevopus.py submit --recipe research-brief --var topic="..." --var n=3`.

## Agent intake (other bots → Jevopus)
Other agents send a plain message; the receiving bot maps it 1:1 onto `jevopus.py submit --from-agent <name>`:
```
JEVOPUS JOB
from: <agent name>
goal: <one line>
constraints: <hard limits, optional>
done-when: <checkable finish condition>
recipe: <name, optional>   vars: k=v; k=v   (optional)
model: <optional pin>      confirmed: no
```
The legacy header `FARM JOB` is accepted the same way. The Jevopus bot routes it, runs it and replies with
`jevopus.py report <id>`. `confirmed: yes` is honored only when the human owner confirmed; a bot cannot confirm
irreversible actions for them.

## Environment
`JEVOPUS_HOME`, `JEVOPUS_JEV_DIR`, `JEVOPUS_JEV_REPO`, `JEVOPUS_RUN_TIMEOUT`, `JEVOPUS_OPENAI_API_KEY` — each falls
back to the old `FARM_*` name. Workers get `JEVOPUS_RENDER_PY` (render venv python). An old `farm.db` in the home is
renamed to `jevopus.db` on first use.

## Files
`jevopus.py` (CLI) · `jevopuslib/core.py` (paths, config, db + idempotent migrations) · `jevopuslib/jev.py` (all
Jev calls + fallbacks) · `jevopuslib/runner.py` (submit/route/tick/run/verify) · `jevopuslib/reports.py` ·
`jevopuslib/setup.py` (doctor, setup-seat) · `config.json` (created on first run; models table etc.) · `recipes/` ·
`tools/resume_fix.sh` (manual follow-up on a codex session) · `tools/render-venv` (Pillow, numpy, pycairo; optional).
