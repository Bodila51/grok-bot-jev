from __future__ import annotations

from typing import Any

from src.secrets import ensure_typesafe_api_key


def system_one(state: dict[str, Any], questions: dict[str, Any], model: str) -> Any:
    # Imported here so the kill-switch / bypass path in src.router works without the SDK installed.
    from typesafe_sdk import TypeSafeClient

    ensure_typesafe_api_key()
    with TypeSafeClient(model=model) as client:
        return client.system_one(state=state, questions=questions)
