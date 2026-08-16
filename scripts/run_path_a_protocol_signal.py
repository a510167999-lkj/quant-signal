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
    DEFAULT_BOUNCE_QT_PATH,
    DEFAULT_CACHE_DIR,
    DEFAULT_ENTRY_QT_PATH,
    DEFAULT_HOLD_QT_PATH,
    DEFAULT_HORIZON_QT_PATH,
    DEFAULT_QT_PATH,
    DEFAULT_CLASSIC_QT_PATH,
    DEFAULT_FLOW_QT_PATH,
    DEFAULT_GAP_QT_PATH,
    DEFAULT_INDUSTRY_QT_PATH,
    DEFAULT_SHAPE_QT_PATH,
    DEFAULT_TURN_QT_PATH,
    STAGE_GOAL_ID,
    build_path_a_protocol_signal_qt,
    write_path_a_protocol_signal_qt,
)
from app.factor_v3_path_a_protocol_signal_specs import (  # noqa: E402
    SIGNAL_BOUNCE_FAMILY,
    SIGNAL_ENTRY_FAMILY,
    SIGNAL_FAMILY,
    SIGNAL_HOLD_FAMILY,
    SIGNAL_HORIZON_FAMILY,
    SIGNAL_CLASSIC_FAMILY,
    SIGNAL_FLOW_FAMILY,
    SIGNAL_GAP_FAMILY,
    SIGNAL_INDUSTRY_FAMILY,
    SIGNAL_SHAPE_FAMILY,
    SIGNAL_TURN_FAMILY,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--qualified-trades-output", type=Path, default=None)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument(
        "--family",
        choices=(
            "signal",
            "hold",
            "entry",
            "bounce",
            "shape",
            "classic",
            "gap",
            "turn",
            "flow",
            "industry",
            "horizon",
        ),
        default="signal",
    )
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    cache = args.cache_dir
    if not cache.is_absolute():
        cache = repo / cache
    if args.family == "hold":
        hold_horizons = (5, 10)
        signal_family = SIGNAL_HOLD_FAMILY
        default_qt = DEFAULT_HOLD_QT_PATH
        book = "reclaim"
    elif args.family == "entry":
        hold_horizons = (5,)
        signal_family = SIGNAL_ENTRY_FAMILY
        default_qt = DEFAULT_ENTRY_QT_PATH
        book = "entry"
    elif args.family == "bounce":
        hold_horizons = (5,)
        signal_family = SIGNAL_BOUNCE_FAMILY
        default_qt = DEFAULT_BOUNCE_QT_PATH
        book = "bounce"
    elif args.family == "shape":
        hold_horizons = (5,)
        signal_family = SIGNAL_SHAPE_FAMILY
        default_qt = DEFAULT_SHAPE_QT_PATH
        book = "shape"
    elif args.family == "classic":
        hold_horizons = (5,)
        signal_family = SIGNAL_CLASSIC_FAMILY
        default_qt = DEFAULT_CLASSIC_QT_PATH
        book = "classic"
    elif args.family == "gap":
        hold_horizons = (5,)
        signal_family = SIGNAL_GAP_FAMILY
        default_qt = DEFAULT_GAP_QT_PATH
        book = "gap"
    elif args.family == "turn":
        hold_horizons = (5,)
        signal_family = SIGNAL_TURN_FAMILY
        default_qt = DEFAULT_TURN_QT_PATH
        book = "turn"
    elif args.family == "flow":
        hold_horizons = (5,)
        signal_family = SIGNAL_FLOW_FAMILY
        default_qt = DEFAULT_FLOW_QT_PATH
        book = "flow"
    elif args.family == "industry":
        hold_horizons = (5,)
        signal_family = SIGNAL_INDUSTRY_FAMILY
        default_qt = DEFAULT_INDUSTRY_QT_PATH
        book = "industry"
    elif args.family == "horizon":
        hold_horizons = (1, 3, 5, 7, 10)
        signal_family = SIGNAL_HORIZON_FAMILY
        default_qt = DEFAULT_HORIZON_QT_PATH
        book = "bounce"
    else:
        hold_horizons = (5,)
        signal_family = SIGNAL_FAMILY
        default_qt = DEFAULT_QT_PATH
        book = "reclaim"
    payload = build_path_a_protocol_signal_qt(
        repo_root=repo,
        cache_dir=cache,
        max_symbols=args.max_symbols,
        require_local_research=not args.allow_any_role,
        hold_horizons=hold_horizons,
        signal_family=signal_family,
        book=book,
    )
    qt_path = args.qualified_trades_output or default_qt
    if not qt_path.is_absolute():
        qt_path = repo / qt_path
    wrote = write_path_a_protocol_signal_qt(payload, qt_path=qt_path)
    summary = (payload.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    print(summary)
    print("wrote", wrote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
