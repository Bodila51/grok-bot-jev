---
name: jev-usage-router
description: >-
  use this on every substantive turn and before digest item keep/drop, browser,
  multi-source research, retries, multi-skill loads, or subagents: call
  jev-router (and digest_gate for overnight candidates); honor active-mode
  actions; Jev judges, Grok works
---
# Jev Usage Router for Grok Bot

TypeSafe Jev is the **first** cheap decision layer. Pattern (see also https://x.com/0xlangeai/status/2101555600618602625): **Jev 管判，Grok 管干**.

## Local install (this host)

- Repo: `$GROK_BOT_JEV_ROOT` (default on box: `/home/box/src/Bodila51/grok-bot-jev`)
- Config: `config.yaml` — **`mode: active`**, `enabled: true`, `model: jev-latest`
- Wrapper: `jev-router` (sources `~/.config/typesafe/env` then `$GROK_BOT_JEV_ROOT/.venv/bin/python -m src.cli`)
- Digest triage: `cd "$GROK_BOT_JEV_ROOT" && set -a; source ~/.config/typesafe/env; set +a; .venv/bin/python scripts/digest_gate.py '<json>'`
- Key only in that env file (0600) or `TYPESAFE_API_KEY`; never in logs, commits, chat, or this skill
- Logs: `logs/runs.jsonl`, `logs/eval_tally.json`, `logs/eval.jsonl`

Kill switch: `enabled: false` in config, or user says `bypass jev` / `no jev`.

## When to call Jev first (always, unless bypass)

Call **before** expensive work and for any **Classification / Choice / Score / Noul**-shaped judgment:

1. Overnight digest candidate keep|drop|domain|credibility (`digest_gate.py`)
2. About to open browser / multi-source research / retry same failure / load several specialized skills / launch subagent → `jev-router`
3. Route a user ask: chat_only vs lookup vs research vs coding vs ask_human
4. Model-tier / need_llm gates; duplicate vs new; interest fit of Trends/Buzzing/PH/Reddit items
5. Official System One patterns: intent routing, select-don’t-generate, verify-and-escalate, speculative fan-out (batch many questions in one call)

Do **not** use Jev for: writing prose, coding patches, multi-step debugging that needs generation, irreversible sends (Jev may return `ask_human` — still require normal confirmation).

## Per-turn bulletin (short)

When you called the router this turn, include **Jev 通报** (1–3 lines):

1. `action` + one-line `reason`
2. What you skipped or capped because of it
3. Optional tally from `logs/eval_tally.json`

## Active-mode honor table (`jev-router`)

- `reuse_cache` — use fresh artifact; no repeat heavy work
- `stop_retry` — stop same approach; propose alternate or ask user
- `run_deterministic` — bounded lookup only
- `chat_only` — answer without tools
- `ask_human` — pause before send/publish/pay/delete/permission change
- `allow_subagent` — only if actually needed and available
- `research_capped` — ≤ `details.max_browser_sources` then synthesize
- `proceed_full` — normal work + ordinary safety

On router failure / missing key: fall back to normal Grok path; do not invent a decision.

## Overnight digest gate (`digest_gate.py`)

For each candidate item (title + short summary + url + source), run digest_gate. Honor:

- `policy: drop` → omit from brief (may note count of dropped)
- `policy: keep_scan` → open-source/infra scan section only
- `policy: keep_brief` → main A/B/C as `domain` says
- Prefer high `hard_news` + high `credibility_score`; if `action_confidence` is middling, still include but mark 置信度中 and verify primary link
- Batch independent candidates with separate calls (or extend script later); never put API key in state

## Official skill alignment

Also follow TypeSafe `typesafe-ai` skill mental model when designing questions: one judgment per question; batch independent questions; put meaning in instructions/criteria; use confidence for gating not as accuracy proof. Live docs: https://docs.typesafe.ai/llms.txt

## Limits

No pre-wake hook. Active only works if this policy is followed. Confidence ≠ statistical accuracy; Chinese sarcasm and mixed language need ongoing tally feedback (`对`/`错` on bulletins).
