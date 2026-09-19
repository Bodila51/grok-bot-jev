# Contributing

Thanks for helping improve this reference integration.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Set `TYPESAFE_API_KEY` in your environment for live Jev calls. For offline checks, set `enabled: false` in `config.yaml`.

## Checks before a PR

```bash
.venv/bin/python -m compileall src scripts
# offline dry-run
.venv/bin/python -c "import yaml; from pathlib import Path; p=Path('config.yaml'); d=yaml.safe_load(p.read_text()); d['enabled']=False; p.write_text(yaml.safe_dump(d))"
.venv/bin/python scripts/dry_run.py
```

Live dry-runs against TypeSafe are optional and use your own key. Do not commit keys, `.env`, `config.yaml`, or logs.

## Pull requests

- Keep changes focused.
- Prefer small diffs with a clear why.
- Update README or docs when behavior changes.
- Do not claim universal Grok Bot token savings; keep A/B notes as local proxy measurements.

## Scope

This project is a skill + script decision layer. It is not a Grok Bot middleware or pre-wake interceptor. Please keep PRs aligned with that boundary.
