from __future__ import annotations

import os


def ensure_typesafe_api_key() -> str:
    """Require the public package's only supported credential source."""
    try:
        return os.environ["TYPESAFE_API_KEY"]
    except KeyError as exc:
        raise SystemExit(
            "TYPESAFE_API_KEY is missing; export it in the process environment"
        ) from exc
