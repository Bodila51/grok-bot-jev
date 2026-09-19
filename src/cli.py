from __future__ import annotations

import json
import sys

from src.router import route_task


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: python -m src.cli '<json state>'", file=sys.stderr)
        return 2
    state = json.loads(argv[0])
    out = route_task(state)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
