#!/usr/bin/env python3
"""Monthly cash / bounce / reclaim switch. Not formal OOS."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_monthly_switch import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_protocol_monthly_switch,
    format_monthly_switch_table,
    write_path_a_protocol_monthly_switch,
)
from app.factor_v3_path_a_protocol_signal import (  # noqa: E402
    DEFAULT_BOUNCE_QT_PATH,
    DEFAULT_HOLD_QT_PATH,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--bounce-qt-path", type=Path, default=DEFAULT_BOUNCE_QT_PATH)
    parser.add_argument("--reclaim-qt-path", type=Path, default=DEFAULT_HOLD_QT_PATH)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    bounce = (
        args.bounce_qt_path
        if args.bounce_qt_path.is_absolute()
        else (repo / args.bounce_qt_path)
    )
    reclaim = (
        args.reclaim_qt_path
        if args.reclaim_qt_path.is_absolute()
        else (repo / args.reclaim_qt_path)
    )
    report = build_path_a_protocol_monthly_switch(
        repo_root=repo,
        bounce_qt_path=bounce,
        reclaim_qt_path=reclaim,
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root if args.output_root.is_absolute() else (repo / args.output_root)
    ).resolve()
    pointer = write_path_a_protocol_monthly_switch(report, output_root=output_root)
    print(format_monthly_switch_table(report), end="")
    print("wrote", pointer.get("report_path"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
