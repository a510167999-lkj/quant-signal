#!/usr/bin/env python3
"""Zero-refit diagnostic alt split at 2025-01-01. Not independent OOS."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_reclaim_alt_split import (  # noqa: E402
    DEFAULT_EVAL_END,
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_protocol_reclaim_alt_split,
    format_reclaim_alt_split_table,
    write_path_a_protocol_reclaim_alt_split,
)
from app.factor_v3_path_a_protocol_reclaim_oos import DEFAULT_QT_PATH as OOS_QT
from app.factor_v3_path_a_protocol_signal import DEFAULT_HOLD_QT_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--hold-qt-path", type=Path, default=DEFAULT_HOLD_QT_PATH)
    parser.add_argument("--oos-qt-path", type=Path, default=OOS_QT)
    parser.add_argument("--eval-end", default=DEFAULT_EVAL_END)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    hold_qt = (
        args.hold_qt_path
        if args.hold_qt_path.is_absolute()
        else (repo / args.hold_qt_path)
    )
    oos_qt = (
        args.oos_qt_path if args.oos_qt_path.is_absolute() else (repo / args.oos_qt_path)
    )
    report = build_path_a_protocol_reclaim_alt_split(
        repo_root=repo,
        hold_qt_path=hold_qt,
        oos_qt_path=oos_qt,
        eval_end=str(args.eval_end),
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root if args.output_root.is_absolute() else (repo / args.output_root)
    ).resolve()
    pointer = write_path_a_protocol_reclaim_alt_split(report, output_root=output_root)
    print(format_reclaim_alt_split_table(report), end="")
    print("wrote", pointer.get("report_path"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
