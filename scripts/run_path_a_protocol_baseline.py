#!/usr/bin/env python3
"""Score pre-registered kernels on the locked train/holdout split."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_baseline import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_QT,
    STAGE_GOAL_ID,
    build_path_a_protocol_baseline,
    format_protocol_baseline_table,
    write_path_a_protocol_baseline,
)
from app.factor_v3_path_a_protocol_signal import (  # noqa: E402
    DEFAULT_BOUNCE_QT_PATH as SIGNAL_BOUNCE_QT,
    DEFAULT_ENTRY_QT_PATH as SIGNAL_ENTRY_QT,
    DEFAULT_HOLD_QT_PATH as SIGNAL_HOLD_QT,
    DEFAULT_QT_PATH as SIGNAL_QT,
    DEFAULT_SHAPE_QT_PATH as SIGNAL_SHAPE_QT,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--qualified-trades-path", type=Path, default=DEFAULT_QT)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument(
        "--family",
        choices=(
            "baseline",
            "factor",
            "alt",
            "signal",
            "signal_hold",
            "signal_entry",
            "signal_bounce",
            "signal_shape",
        ),
        default="baseline",
    )
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    qt = args.qualified_trades_path
    if args.family == "signal" and qt == DEFAULT_QT:
        qt = SIGNAL_QT
    if args.family == "signal_hold" and qt == DEFAULT_QT:
        qt = SIGNAL_HOLD_QT
    if args.family == "signal_entry" and qt == DEFAULT_QT:
        qt = SIGNAL_ENTRY_QT
    if args.family == "signal_bounce" and qt == DEFAULT_QT:
        qt = SIGNAL_BOUNCE_QT
    if args.family == "signal_shape" and qt == DEFAULT_QT:
        qt = SIGNAL_SHAPE_QT
    report = build_path_a_protocol_baseline(
        repo_root=repo,
        qualified_trades_path=qt if qt.is_absolute() else (repo / qt),
        require_local_research=not args.allow_any_role,
        family=args.family,
    )
    family_roots = {
        "baseline": DEFAULT_OUTPUT_ROOT,
        "factor": Path("data/research_runs/path_a_protocol_factor"),
        "alt": Path("data/research_runs/path_a_protocol_alt"),
        "signal": Path("data/research_runs/path_a_protocol_signal"),
        "signal_hold": Path("data/research_runs/path_a_protocol_signal_hold"),
        "signal_entry": Path("data/research_runs/path_a_protocol_signal_entry"),
        "signal_bounce": Path("data/research_runs/path_a_protocol_signal_bounce"),
        "signal_shape": Path("data/research_runs/path_a_protocol_signal_shape"),
    }
    chosen_root = args.output_root
    if chosen_root == DEFAULT_OUTPUT_ROOT:
        chosen_root = family_roots[args.family]
    output_root = (
        chosen_root if chosen_root.is_absolute() else (repo / chosen_root)
    ).resolve()
    pointer = write_path_a_protocol_baseline(report, output_root=output_root)
    print(format_protocol_baseline_table(report), end="")
    print("wrote", pointer.get("table_path"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
