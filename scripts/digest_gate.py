#!/usr/bin/env python3
"""Triage one overnight-digest candidate with TypeSafe Jev before briefing."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from typesafe_sdk import Choice, Noul, Score  # noqa: E402

from src.jev_client import system_one  # noqa: E402
from src.config import load_config  # noqa: E402


QUESTIONS = {
    "domain": Choice(
        instructions="Which digest domain does this item mainly belong to for Chaos42 DAMA?",
        criteria={
            "ai": "Models, local LLM, coding agents, cheap API routes, Grok Bot/pi/Cursor",
            "scifi": "SF film/TV, Chinese SF, awards/magazines",
            "nogizaka": "Nogizaka46 news, blogs, media, 工事中",
            "infra_open": "Self-host, VPS, Cloudflare, GitHub tools, indie/dev tools scan",
            "other": "None of the above interest anchors",
        },
    ),
    "interest_fit": Noul(
        instructions="Is this clearly relevant to the user's standing interests (AI/local models/agents, sci-fi, Nogizaka46, DevOps/self-host open source)?"
    ),
    "hard_news": Noul(
        instructions="Does the text report a concrete, time-bound, checkable event (release, date, dollar figure, official announcement) rather than pure opinion or vibe?"
    ),
    "credibility": Score(
        instructions="How strong is the sourcing implied by the candidate text?",
        criteria=[
            "Rumor / anonymous / no primary link",
            "Secondary report or aggregator; needs verification",
            "Primary or official source (release notes, studio, Deadline/Variety-class, official site)",
        ],
    ),
    "action": Choice(
        instructions="What should the overnight digest do with this item?",
        criteria={
            "keep_brief": "Include in main A/B/C sections with source link",
            "keep_scan": "Include only in open-source/infra scan section",
            "drop": "Discard; low fit, duplicate, or not actionable overnight news",
        },
    ),
}


def gate(candidate: dict) -> dict:
    cfg = load_config()
    model = cfg.get("model") or "jev-latest"
    state = {
        "title": candidate.get("title") or "",
        "summary": candidate.get("summary") or candidate.get("text") or "",
        "source_url": candidate.get("url") or "",
        "source_name": candidate.get("source") or "",
        "window_note": candidate.get("window") or "overnight digest past ~24h SGT",
    }
    result = system_one(state, QUESTIONS, model=model)
    domain = result.choices["domain"]
    action = result.choices["action"]
    fit = result.nouls["interest_fit"]
    hard = result.nouls["hard_news"]
    cred = result.scores["credibility"]
    out = {
        "domain": domain.choice,
        "domain_confidence": float(domain.confidence),
        "interest_fit": float(fit.noul),
        "hard_news": float(hard.noul),
        "credibility_score": float(cred.score),
        "credibility_confidence": float(cred.confidence),
        "action": action.choice,
        "action_confidence": float(action.confidence),
        "model": getattr(result, "model", model),
        "usage": getattr(result, "usage", None),
    }
    # Soft policy: drop if low fit + drop action, or other domain with drop
    if out["action"] == "drop" and out["action_confidence"] >= 0.55:
        out["policy"] = "drop"
    elif out["domain"] == "other" and out["interest_fit"] < 0.45:
        out["policy"] = "drop"
    elif out["action"] == "keep_scan":
        out["policy"] = "keep_scan"
    else:
        out["policy"] = "keep_brief"
    return out


def main() -> None:
    raw = sys.stdin.read().strip() if not sys.argv[1:] else sys.argv[1]
    if not raw:
        print("usage: digest_gate.py '<json>' or pipe JSON", file=sys.stderr)
        sys.exit(2)
    candidate = json.loads(raw)
    print(json.dumps(gate(candidate), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
