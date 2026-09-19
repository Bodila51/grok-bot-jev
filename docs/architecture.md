# Architecture

## Architecture A: skill gates around Grok Bot

This package implements an external decision layer rather than changing Grok Bot. A Grok Bot skill prepares a small task state, calls `src.cli` (or the equivalent Python function), and then applies the returned action before expensive work.

```text
user request
    |
    v
Grok Bot wakes and loads the skill
    |
    +--> kill switch / bypass marker? ---- yes ---> normal Grok Bot path
    |
    v
TypeSafe Jev system_one(state, questions)
    |
    v
router policy: cache | stop | deterministic | chat | capped research |
                subagent | human approval | full work
    |
    v
Grok Bot executes the action (active) or treats it as advice (shadow)
```

The Python router normalizes state, defines the Jev choice/noul/score questions, applies thresholds and limits, and appends a JSONL decision record. The API key is read only from `TYPESAFE_API_KEY` in the process environment.

## What works

- Cache reuse, bounded research, retry stopping, direct chat/lookup suggestions, subagent suggestions, and an approval gate for account-changing intents.
- A configuration kill switch and explicit `shadow`/`active` modes.
- Auditable decision records without storing the credential.

## What does not

- This is not a Grok Bot middleware or pre-wake interceptor. The bot must wake, load the skill, and make the router call.
- Shadow mode cannot force behavior. Active mode relies on the installed skill honoring `route.action`.
- Jev does not perform the browser/research/coding work and does not replace Grok Bot's confirmation or safety policy.
- The included A/B results are local proxy measurements, not proof of lower Grok Bot token usage or a universal speedup.

## Failure behavior

If the router is disabled, bypassed, or unavailable, the safe integration fallback is to return to the normal Grok Bot path rather than inventing a decision. Account and irreversible actions still require human confirmation.
