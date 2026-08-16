#!/usr/bin/env python3
"""Pull Jiaoch PE / PE_TTM / PB / market cap for the Path-A locked window."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_daily_basic_value import (  # noqa: E402
    DEFAULT_CALENDAR_SPEC,
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    collect_path_a_daily_basic_value,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--calendar-spec", type=Path, default=DEFAULT_CALENDAR_SPEC)
    parser.add_argument("--max-days", type=int, default=0)
    parser.add_argument("--sleep-s", type=float, default=0.15)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    calendar = args.calendar_spec
    if not calendar.is_absolute():
        calendar = repo / calendar
    output = args.output_root
    if not output.is_absolute():
        output = repo / output
    report = collect_path_a_daily_basic_value(
        repo_root=repo,
        output_root=output,
        calendar_spec=calendar,
        max_days=args.max_days,
        sleep_s=args.sleep_s,
        require_local_research=not args.allow_any_role,
    )
    print(
        f"planned={report.get('planned')} fetched={report.get('fetched')} "
        f"skipped={report.get('skipped')} failed={len(report.get('failed') or [])} "
        f"complete={report.get('complete')}"
    )
    if report.get("failed"):
        print("failed", report["failed"][:8])
    print("wrote", report.get("output_root"))
    return 0 if report.get("complete") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
