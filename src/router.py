from __future__ import annotations

import re
from typing import Any

from src.config import load_config, resolve_log_path
from src.jev_client import system_one
from src.logger import log_run


# Whole-word markers, checked only in fields the user authors (never in notes/raw,
# which may carry pasted page content), or an explicit boolean set by the skill.
BYPASS_RE = re.compile(r"\b(?:bypass jev|no jev)\b", re.IGNORECASE)


def _bypassed(state: dict[str, Any]) -> bool:
    if state.get("bypass_jev") is True:
        return True
    text = " ".join(str(state.get(k) or "") for k in ("goal", "user_message"))
    return bool(BYPASS_RE.search(text))


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def route_task(state: dict[str, Any]) -> dict[str, Any]:
    """Run Jev gates and return a routing decision for Grok Bot."""
    cfg = load_config()
    log_path = resolve_log_path(cfg)

    if not cfg.get("enabled", True) or _bypassed(state):
        out = {
            "action": "proceed_full",
            "reason": "lab disabled or bypass jev",
            "mode": cfg.get("mode"),
            "jev_used": False,
            "details": {},
        }
        log_run(log_path, {"event": "route", "state_goal": state.get("goal"), **out})
        return out

    # Keep the kill-switch path dependency-free so it works during incidents.
    from typesafe_sdk import Choice, Noul, Score

    thr = cfg.get("thresholds") or {}
    limits = cfg.get("limits") or {}
    model = cfg.get("model") or "jev-latest"

    # Normalize state for Jev
    jstate = {
        "goal": state.get("goal") or state.get("raw") or "",
        "kind_hint": state.get("kind") or state.get("kind_hint") or "unknown",
        "has_cached_artifact": bool(state.get("cached_artifact")),
        "cached_note": state.get("cached_note") or "",
        "prior_error": state.get("prior_error") or "",
        "same_error_count": _int(state.get("same_error_count")),
        "sources_found": _int(state.get("sources_found")),
        "constraints": state.get("constraints") or "",
    }

    questions = {
        "intent": Choice(
            instructions="What kind of work does this request mainly need?",
            criteria={
                "chat": "Short answer or conversation; no tools needed",
                "lookup": "Known file/API/status check; mostly deterministic",
                "research": "Need web/search and synthesis",
                "browser": "Need interactive browser clicks",
                "coding": "Edit code / tests / repo work",
                "write": "Draft long prose/email/doc",
                "account": "Send, publish, pay, delete, change permissions",
            },
        ),
        "reuse_cache": Noul(
            instructions="Is there already a fresh enough cached result that should be reused instead of doing new heavy work?"
        ),
        "needs_subagent": Noul(
            instructions="Does this clearly need an extra specialized bot (research/browser/coding) beyond one Grok Bot turn?"
        ),
        "stop_retry": Noul(
            instructions="Given prior_error and same_error_count, should we STOP retrying the same approach?"
        ),
        "complexity": Score(
            instructions="How much Grok Bot effort is justified?",
            criteria=[
                "Trivial — one step or cached",
                "Normal — short tool use",
                "Heavy — multi-step research/browser/coding",
            ],
        ),
    }

    try:
        result = system_one(jstate, questions, model=model)
        intent = result.choices["intent"]
        reuse = result.nouls["reuse_cache"]
        sub = result.nouls["needs_subagent"]
        stop = result.nouls["stop_retry"]
        complexity = result.scores["complexity"]
    except Exception as exc:  # network, auth, rate limit, unexpected shape
        # Fall back to the normal Grok Bot path instead of crashing the caller.
        out = {
            "action": "proceed_full",
            "reason": f"jev unavailable: {type(exc).__name__}",
            "mode": cfg.get("mode"),
            "jev_used": False,
            "details": {},
        }
        log_run(
            log_path,
            {
                "event": "route_error",
                "goal": jstate["goal"][:300],
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                **{k: out[k] for k in ("action", "reason", "mode")},
            },
        )
        return out

    reuse_n = float(reuse.noul)
    sub_n = float(sub.noul)
    stop_n = float(stop.noul)
    # Score 0..2 → normalize
    comp = float(complexity.score) / 2.0

    action = "proceed_full"
    reason = "default full Grok Bot work"

    if intent.choice == "account":
        action = "ask_human"
        reason = "account/irreversible class — require approval"
    elif jstate["has_cached_artifact"] and reuse_n >= float(thr.get("reuse_min", 0.65)):
        action = "reuse_cache"
        reason = f"reuse_cache noul={reuse_n:.2f}"
    elif jstate["same_error_count"] >= int(limits.get("max_retries_same_error", 1)) and stop_n >= 0.55:
        action = "stop_retry"
        reason = f"stop_retry noul={stop_n:.2f} same_error_count={jstate['same_error_count']}"
    elif intent.choice == "lookup" and float(intent.confidence) >= float(thr.get("min_choice_confidence", 0.55)):
        action = "run_deterministic"
        reason = "intent=lookup"
    elif intent.choice == "chat" and float(intent.confidence) >= float(thr.get("min_choice_confidence", 0.55)):
        action = "chat_only"
        reason = "intent=chat"
    elif sub_n >= float(thr.get("subagent_min", 0.75)):
        action = "allow_subagent"
        reason = f"needs_subagent noul={sub_n:.2f}"
    elif intent.choice in {"research", "browser"}:
        action = "research_capped"
        reason = f"cap sources at {limits.get('max_browser_sources', 5)}"

    details = {
        "intent": intent.choice,
        "intent_confidence": round(float(intent.confidence), 4),
        "intent_probs": {k: round(float(v), 4) for k, v in (intent.probabilities or {}).items()},
        "reuse_cache": round(reuse_n, 4),
        "needs_subagent": round(sub_n, 4),
        "stop_retry": round(stop_n, 4),
        "complexity_0_1": round(comp, 4),
        "max_browser_sources": int(limits.get("max_browser_sources", 5)),
    }

    out = {
        "action": action,
        "reason": reason,
        "mode": cfg.get("mode"),
        "jev_used": True,
        "details": details,
        "policy": {
            "honor_in_active_mode": True,
            "shadow_mode_is_advisory": cfg.get("mode") == "shadow",
        },
    }
    log_run(
        log_path,
        {
            "event": "route",
            "goal": jstate["goal"][:300],
            "action": action,
            "reason": reason,
            "mode": cfg.get("mode"),
            "details": details,
        },
    )
    return out
