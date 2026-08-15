#!/usr/bin/env python3
"""Daily roll for frozen reclaim independent OOS. Not formal final-OOS."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_reclaim_oos_roll import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SCHEDULE_HINT_LOCAL,
    DEFAULT_TZ,
    STAGE_GOAL_ID,
    build_path_a_protocol_reclaim_oos_roll,
    format_reclaim_oos_roll_table,
    write_path_a_protocol_reclaim_oos_roll,
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
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"{STAGE_GOAL_ID}. Suggested schedule: weekdays "
            f"{DEFAULT_SCHEDULE_HINT_LOCAL} {DEFAULT_TZ}."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--no-extend", action="store_true")
    parser.add_argument("--no-rebuild-qt", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    _load_dotenv(repo / ".env")
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    report = build_path_a_protocol_reclaim_oos_roll(
        repo_root=repo,
        as_of=args.as_of,
        tz_name=str(args.timezone),
        extend=not args.no_extend,
        rebuild_qt=not args.no_rebuild_qt,
        require_local_research=not args.allow_any_role,
        max_symbols=args.max_symbols,
    )
    output_root = (
        args.output_root if args.output_root.is_absolute() else (repo / args.output_root)
    ).resolve()
    pointer = write_path_a_protocol_reclaim_oos_roll(report, output_root=output_root)
    print(format_reclaim_oos_roll_table(report), end="")
    print("wrote", pointer.get("report_path"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
