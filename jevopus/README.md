# Jevopus v2.5 — worker seats with a Jev decision layer

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
               [--best-of-two] [--search]                                  # two models, keep the better; live web search
jevopus.py route <id>     # ONE Jev call: inline-vs-worker, tier, complexity, irreversible guard, model choice,
                          #   reuse-cache choice, stop-retry (when the same error repeated)
jevopus.py tick           # only leases: queued worker jobs -> free enabled non-cooldown seat offering the job's model
jevopus.py run <id>       # run leased job, parse tokens/session, Jev-verify vs done_when, <=1 fix round (resume)
jevopus.py confirm <id> | cancel <id> | status
jevopus.py report <id>    # what the bot shows the user after each job
jevopus.py usage [--since 7d] | weekly | feedback <id> ok|wrong [note]
jevopus.py recipes | doctor | setup-seat codex-sub|codex-api|claude-strong [--disable] | init
jevopus.py limits         # per seat: provider-reported usage % + reset time (real CLI data), Jevopus-counted jobs/tokens
                          #   in 5h / 7d, cooldown state
jevopus.py jev-mode [shadow|active] [--by NAME] [--note T] [--force]   # show / change the Jev mode (owner decision)
```
Typical flow: `id=$(jevopus.py submit ...) && jevopus.py route $id && jevopus.py tick && jevopus.py run $id && jevopus.py report $id`
(the same flow works for best-of-two: `run <id>` on the parent runs both candidates and picks the winner).

## Best of two
Ask for it with `submit --best-of-two` (or `best-of-two: yes` in a JEVOPUS JOB), opt a recipe in with `"best_of_two": true`,
or set `config.json` → `best_of_two.recipes` (list of recipe names) / `best_of_two.all_jobs`.
- `route` does the normal checks first (cache reuse, stop-retry, irreversible guard, model choice). Candidate **a** is the
  normal pick (your `--model` pin wins); candidate **b** is picked by Jev Choice (with the same evidence history) among the
  other models, **preferring a model on a different seat/provider** (e.g. Codex vs Claude Code) so both run in parallel.
  With one enabled seat, b is a different model on the same seat and the two run one after the other (one job per seat
  stays). If only one model is available, the job runs as a normal single job (noted). `--seat S` keeps both on S.
- The parent job gets `route=best_of_two`; the candidates are real jobs `<id>-a` / `<id>-b` with their own dirs
  `jobs/<id>/a/` and `jobs/<id>/b/` (prompt, worker logs, result.json, files), leased by `tick` like any job (cooldowns,
  limits gating and the guard apply; `confirm <id>` / `cancel <id>` on the parent apply to both).
- Each candidate is verified against done-when (with its own fix round). Winner: a verified pass beats a fail; if both
  pass, a **blind Jev comparison** of the two results (no model names) decides when its confidence ≥
  `min_choice_confidence`, otherwise the verifier score, then fewer tokens. If both fail, the better attempt is kept and
  the parent is `needs_review`. The reason is stored (`jobs.winner_reason`, `jobs/<id>/best_of_two.json`, decision
  `bo2_winner`) and `report <id>` shows both models, seats, statuses, verification, tokens, time and the winner.
- Evidence: each candidate counts once as a result for its own model; the parent has no seat and 0 tokens, so it is never
  double counted. `feedback <id> ok|wrong` on the parent is also applied to the winning candidate.

## Limits
`jevopus.py limits` shows, per seat, only what is really known:
- **provider** (real): Codex CLI writes `rate_limits` (used %, window length, reset time, plan) into its session logs
  (`$CODEX_HOME/sessions/**/rollout-*.jsonl`); Jevopus reads them after each run and on `limits`/`tick`/`doctor`.
  Claude Code reports `rate_limit_event` (utilization, resetsAt, 5h/7d windows) in `--output-format stream-json`, which
  the Claude seat now uses; values appear after the first Claude run. Each value shows its source and when it was seen;
  a window that has reset since is marked STALE (current value unknown). Nothing is estimated or invented.
- **counted** (Jevopus's own records): jobs and tokens per seat in the last 5h and 7d (exact for Jevopus jobs; your
  interactive use of the same login is not visible), plus cooldown state.
- **Gating**: before leasing a **large** job (Jev complexity ≥ `limits.large_complexity_min`, default 3 on the 0-4 scale,
  or any best-of-two candidate) a seat whose fresh provider-reported usage is ≥ `limits.skip_above_percent` (default 90)
  is skipped; if no seat fits, the job stays queued with a `HELD (limits)` note instead of starting. `doctor` and `status`
  print a one-line limits summary.

## Jev mode (shadow / active)
`jevopus.py jev-mode` shows the mode (the `mode:` line of the Jev router `config.yaml`), the accuracy progress and the
change history; `jevopus.py jev-mode active|shadow [--by NAME]` changes it and records who/when (db `meta.jev_mode_log`
and `logs/routing.jsonl`). What the mode means: Jevopus itself always applies its Jev decisions with the thresholds and
keyword fallbacks above (this was already the case in shadow). `shadow` = the dispatcher bot treats Jev's routing
advice (inline vs worker, reuse, tier) as advice and may override it with its own judgment; `active` = the bot honors
it. `weekly` and `doctor` show accuracy toward the bar (≥90% on ≥20 decisions with an outcome) and suggest switching
when it is met; switching to active below the bar needs `--force`. New installs start in shadow; only the owner switches.

## Decisions (Jev) and policy
- Thresholds from the Jev repo `config.yaml` (`policy.act_min` 0.80 / `surface_min` 0.50; `thresholds.reuse_min`,
  `min_choice_confidence`, `stop_retry_min`; `limits.max_retries_same_error`). Noul confidence = max(p, 1-p).
- **Model selection** (how a model is chosen, in order):
  1. **Pins win**: `--model M` is used as-is; `--seat S` limits candidates to that seat's models.
  2. **Candidates** = models offered by ENABLED seats that appear in `config.json` → `models`. One candidate → used.
  3. **Jev Choice** sees, per candidate, the **fit text** (editable defaults, not benchmarks) and a compact
     **history** block built from `jevopus.db`: verified pass / fail / needs-review counts and your `feedback`
     ok/wrong counts, first on *similar* jobs (same recipe; without a recipe: same kind + ≥2 shared goal keywords),
     then on all jobs, plus up to 2 short redacted goal snippets. Jev is told to weigh real outcomes over
     descriptions once a model has ≥3 outcomes and to treat fewer as weak hints; models with no history are
     "unknown", not worse. No prompts, logs or results of other jobs are sent.
  4. **Thresholds**: Jev's pick is applied only if confidence ≥ `min_choice_confidence`; otherwise `default_model`.
  The evidence is stored with the job (`model_evidence`, and on the `model` decision) and shown by
  `jevopus.py report <id>` as `model evidence`. `feedback <id> ok|wrong` directly improves future picks.
- **Models** (defaults, merged into existing `config.json` on upgrade without touching your edits): Codex
  `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`; Claude Code `opus`, `sonnet` (claude-sonnet-5), `haiku`
  (claude-haiku-4-5). Claude models are passed to `claude --model` as CLI aliases (override per model with
  `"cli_model"`); the `claude-strong` seat offers all three.
- **Reuse/cache**: Jev Choice over the last 20 done+verified jobs (+ "none"); conf ≥ `reuse_min` → job becomes
  `cached` and points at the earlier result. `--force` skips this.
- **Stop-retry**: failures are tracked per goal hash + error signature; after `max_retries_same_error` identical
  failures Jev's stop_retry noul ≥ `stop_retry_min` → job `blocked` (refused, logged). Change the goal/approach.
- **Irreversible guard**: Jev noul on goal+constraints (send/publish/pay/delete outside job dir/account changes);
  p ≥ 0.5 → the job needs `--confirmed` or `jevopus.py confirm <id>` before tick will lease it. Irreversible actions
  always need a human regardless of Jev; workers get no credentials.
- **Web search**: recipes with `"search": true` (research-brief, lead-finder, competitor-analysis) or `submit --search`
  run Codex with `-c web_search="live"` (Codex 0.157: same as `codex exec --search`, also valid on `exec resume`; the
  allowed values are disabled|cached|indexed|live) and Claude Code with `--allowedTools WebSearch WebFetch`.
- **Verification**: after a run, Jev noul "result.json + listed files (existence/size checked by code) satisfy
  done_when?" (state: goal, done_when, result, files, last 4k of worker log). p < 0.5 → resume the same session
  with a fix prompt (max `verify.max_fix_rounds`, default 1), else status `needs_review`.
- Claude Code seat runs `claude -p ... --output-format stream-json --verbose --permission-mode acceptEdits` (usage,
  session id and rate-limit events are parsed from the stream).
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
voice; 5 drafts, never posted) · **lead-finder** (criteria, offer, n, region, sender; public-source lead table
leads.md/leads.csv + pitches.md drafts, never sends, public contact channels only) · **media-kit** (creator, facts,
style, contact; one-page media_kit.html + .md from given facts only, missing data as visible [ADD: ...] placeholders)
· **competitor-analysis** (subject, competitors, focus; cited comparison table, public pricing or "not public",
strengths/weaknesses, gaps) · **presentation** (topic, outline, slides, audience, style; self-contained slides.html
with keyboard navigation and print-to-PDF, plus slides.md with speaker notes). Web recipes cite sources and mark
unknowns. Example: `jevopus.py submit --recipe research-brief --var topic="..." --var n=3`.

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
best-of-two: no            search: no      (optional; yes -> --best-of-two / --search)
```
The legacy header `FARM JOB` is accepted the same way. The Jevopus bot routes it, runs it and replies with
`jevopus.py report <id>`. `confirmed: yes` is honored only when the human owner confirmed; a bot cannot confirm
irreversible actions for them.

## Environment
`JEVOPUS_HOME`, `JEVOPUS_JEV_DIR`, `JEVOPUS_JEV_REPO`, `JEVOPUS_RUN_TIMEOUT`, `JEVOPUS_OPENAI_API_KEY` — each falls
back to the old `FARM_*` name. Workers get `JEVOPUS_RENDER_PY` (render venv python). An old `farm.db` in the home is
renamed to `jevopus.db` on first use. On upgrade, new `config.json` settings (e.g. `limits`, `best_of_two`) and new job columns are
added automatically; values you edited are never overwritten.

## Files
`jevopus.py` (CLI) · `jevopuslib/core.py` (paths, config, db + idempotent migrations) · `jevopuslib/jev.py` (all
Jev calls + fallbacks) · `jevopuslib/runner.py` (submit/route/tick/run/verify) · `jevopuslib/bestof2.py` (best of two)
· `jevopuslib/limits.py` (limits) · `jevopuslib/reports.py` (+ jev-mode) ·
`jevopuslib/evidence.py` (model history for Jev) · `jevopuslib/setup.py` (doctor, setup-seat) · `config.json` (created on first run; models table etc.) · `recipes/` ·
`tools/resume_fix.sh` (manual follow-up on a codex session) · `tools/render-venv` (Pillow, numpy, pycairo; optional).
