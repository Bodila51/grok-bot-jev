#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.router import route_task

CASES = [
    {"goal": "What is 2+2?", "kind": "chat"},
    {
        "goal": "Summarize AI agent news for tomorrow briefing",
        "kind": "research",
        "cached_artifact": True,
        "cached_note": "Fresh briefing written 20 minutes ago covering same topic",
    },
    {
        "goal": "Open the site and click through checkout to compare prices",
        "kind": "browser",
    },
    {
        "goal": "Retry the same failing scrape",
        "kind": "browser",
        "prior_error": "timeout on selector",
        "same_error_count": 2,
    },
    {"goal": "Send the email to the customer now", "kind": "account"},
]


def main() -> int:
    print("Grok Bot Jev router dry-run\n")
    for i, case in enumerate(CASES, 1):
        out = route_task(case)
        print(f"=== case {i}: {case['goal'][:60]} ===")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print()
    print(f"Logged to {ROOT / 'logs' / 'runs.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
