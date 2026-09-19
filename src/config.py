from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    data.setdefault("enabled", True)
    data.setdefault("mode", "shadow")
    data.setdefault("model", "jev-latest")
    data.setdefault("thresholds", {})
    data.setdefault("limits", {})
    data.setdefault("logging", {"path": "logs/runs.jsonl"})
    return data


def resolve_log_path(cfg: dict[str, Any]) -> Path:
    rel = cfg.get("logging", {}).get("path", "logs/runs.jsonl")
    path = Path(rel)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
