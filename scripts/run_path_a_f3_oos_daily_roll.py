#!/usr/bin/env python3
"""Path A F3: post-close daily OOS roll (auto oos-end-date + F2 pipeline)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_f3_oos_daily_roll import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SCHEDULE_HINT_LOCAL,
    DEFAULT_TZ,
    STAGE_GOAL_ID,
    build_path_a_f3_oos_daily_roll,
    resolve_oos_end_date,
    write_path_a_f3_oos_daily_roll,
)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Path A F3 ({STAGE_GOAL_ID}). "
            f"Suggested schedule: weekdays {DEFAULT_SCHEDULE_HINT_LOCAL} {DEFAULT_TZ}."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument(
        "--as-of",
        default=None,
        help="Inclusive OOS end date YYYY-MM-DD (default: today Asia/Shanghai)",
    )
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--no-extend", action="store_true")
    parser.add_argument("--no-generate-qualified", action="store_true")
    parser.add_argument(
        "--skip-if-no-new-session",
        action="store_true",
        help="If store already covers as-of date, skip F2 work",
    )
    parser.add_argument("--max-universe-symbols", type=int, default=300)
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    _load_dotenv(repo / ".env")
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    oos_end = resolve_oos_end_date(as_of=args.as_of, tz_name=args.timezone)
    print(f"f3_oos_end_date={oos_end} tz={args.timezone}", flush=True)

    report = build_path_a_f3_oos_daily_roll(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
        as_of=oos_end,
        tz_name=str(args.timezone),
        extend=not args.no_extend,
        generate_qualified=not args.no_generate_qualified,
        max_universe_symbols=int(args.max_universe_symbols),
        skip_if_no_new_session=bool(args.skip_if_no_new_session),
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_f3_oos_daily_roll(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print(
        "roll_advanced=",
        report.get("roll_advanced"),
        "sessions_delta=",
        report.get("sessions_delta"),
        "sessions_after=",
        report.get("sessions_after"),
        "session_max=",
        (report.get("store_after") or {}).get("max_trade_date"),
    )
    zr = report.get("zero_refit_replay") or {}
    print(
        "zero_refit selected=",
        zr.get("selected_trade_count"),
        "reason=",
        zr.get("reason"),
        "effective_strategy=",
        report.get("effective_strategy_found"),
    )
    print("\nnext_actions:")
    for a in report.get("next_actions") or []:
        print("-", a)
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
