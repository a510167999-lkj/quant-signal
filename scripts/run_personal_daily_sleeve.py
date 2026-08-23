#!/usr/bin/env python3
"""Daily always-on personal sleeve. Not bounce. No auto orders."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.personal_capital_contract import DEFAULT_CAPITAL_CNY  # noqa: E402
from app.personal_daily_sleeve import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_and_store,
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
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL_CNY)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--max-files", type=int, default=0)
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    _load_dotenv(repo / ".env")
    os.chdir(repo)
    report = build_and_store(
        cache_dir=repo / "data/research_cache/jiaoch_stk_mins_3y_v2_holdout",
        output_root=(
            args.output_root if args.output_root.is_absolute() else repo / args.output_root
        ),
        capital_cny=args.capital,
        as_of=args.as_of,
        max_files=args.max_files,
    )
    print(f"candidate={report.get('candidate_id')} picks={len(report.get('picks') or [])}")
    for row in report.get("picks") or []:
        print(
            f"  {row.get('action')} {row.get('symbol')} "
            f"ret20={row.get('ret20')} lots={row.get('lots')}"
        )
    print("不是连跌反弹；非正式有效；不自动下单")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
