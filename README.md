# Grok Bot Jev Router

Connect [TypeSafe Jev](https://docs.typesafe.ai) to Grok Bot as a cheap decision layer. Jev classifies the request before expensive research, browser, retry, or subagent work, so Grok Bot can reuse a fresh artifact, stop a failing retry, cap research, or ask for approval.

This package is a small open-source reference implementation. It does not change Grok Bot's foundation model and does not route Cursor models.

## What

- A Python router around the TypeSafe SDK's `system_one` call.
- Explicit actions such as `reuse_cache`, `stop_retry`, `run_deterministic`, `chat_only`, `research_capped`, `allow_subagent`, and `ask_human`.
- `shadow` mode for measurement and `active` mode for a skill that honors the returned action.
- A kill switch through `enabled: false` or a `bypass jev` marker.

## Why

A small classification call can prevent needless tool work. The router is intentionally conservative: it never sends, publishes, pays, deletes, or changes permissions without a human approval path, and it logs decisions without logging the API key.

## Install

```bash
git clone <your-fork-url> grok-bot-jev
cd grok-bot-jev
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
Set `TYPESAFE_API_KEY` in the process environment using your shell or secret manager.
```

Keep `config.yaml`, `.env`, and local logs uncommitted. `src/secrets.py` reads only `TYPESAFE_API_KEY` from the environment; it never reads a secrets file.

## Run dry-run

The script sends the sample states through Jev and prints JSON decisions:

```bash
.venv/bin/python scripts/dry_run.py
# or one state:
.venv/bin/python -m src.cli '{"goal":"summarize the latest release notes","kind":"research"}'
```

For a no-network smoke check, disable the router first:

```bash
cp config.example.yaml config.yaml
# edit config.yaml: enabled: false
.venv/bin/python scripts/dry_run.py
```

## Use with Grok Bot skill

Copy [`skill/jev-usage-router.SKILL.md`](skill/jev-usage-router.SKILL.md) into a Grok Bot skill. The skill describes the request state, when to call this router, and how to honor each action. Run the router in the environment where this repository is installed; pass only task metadata and short context, never credentials.

Start in `mode: shadow`: decisions are logged and advisory. Move to `mode: active` only after reviewing logs; the Grok Bot skill must then honor `route.action`. The router is not a hidden interceptor and cannot force a bot that ignores the skill to stop.

## A/B results highlights

The included local A/B summary is in [`examples/ab_results.md`](examples/ab_results.md), with sanitized source data in [`examples/chatgpt_pack.json`](examples/chatgpt_pack.json). Highlights from the recorded run:

- Across five tasks, flight browser opens went from 1 to 0 when a cached artifact was reused; same-approach retries went from 3 to 0; flight-prep skills loaded went from 6 to 3; model-research pages fetched went from 10 to 4.
- The Grok Bot weekly usage meter moved 37% → 38% in the first phase and 39% after the second phase. Exact per-task Grok tokens were unavailable, so this is not a token-savings claim.
- In the separate 24-candidate timing comparison, the uncapped arm took 53.803 seconds and fetched 14 pages; the Jev-ranked top-five arm took 4.125 seconds and fetched 5 pages, a reported 13.0× wall-time ratio. It used two Jev calls and an estimated $0.000405 Jev cost; fewer hits were by design because of the top-five cap.

These are one local run's proxy measurements, not a benchmark or guarantee.

## Limits

- Jev is an additional network call and can be wrong, unavailable, or slower than the skipped work.
- Shadow mode does not enforce anything; active mode depends on the pasted skill honoring the decision.
- There are no pre-wake hooks here. Grok Bot must wake and invoke the router; this package cannot reduce the cost of the wake-up itself.
- The router does not execute browser actions, research, payments, messages, or account changes. Those remain Grok Bot responsibilities with their own confirmation rules.
- The included A/B data reports proxy metrics; it does not expose exact Grok Bot tokens or Grok Bot dollar cost.

## Links

- [TypeSafe documentation](https://docs.typesafe.ai)
- [Grok Bot documentation](https://cursor.com/docs/grok-bot)
- [Architecture notes](docs/architecture.md)

## License

MIT. See [LICENSE](LICENSE).
