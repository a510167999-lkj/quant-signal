#!/usr/bin/env python3
"""Build the pre-registered MA20-reclaim QT from Jiaoch v2 caches."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_signal import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_QT_PATH,
    STAGE_GOAL_ID,
    build_path_a_protocol_signal_qt,
    write_path_a_protocol_signal_qt,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--qualified-trades-output", type=Path, default=DEFAULT_QT_PATH)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    cache = args.cache_dir
    if not cache.is_absolute():
        cache = repo / cache
    payload = build_path_a_protocol_signal_qt(
        repo_root=repo,
        cache_dir=cache,
        max_symbols=args.max_symbols,
        require_local_research=not args.allow_any_role,
    )
    qt_path = args.qualified_trades_output
    if not qt_path.is_absolute():
        qt_path = repo / qt_path
    wrote = write_path_a_protocol_signal_qt(payload, qt_path=qt_path)
    summary = (payload.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    print(summary)
    print("wrote", wrote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
