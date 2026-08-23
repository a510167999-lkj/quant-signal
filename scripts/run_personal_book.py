#!/usr/bin/env python3
"""Build the personal small-capital ticket from bounce daily. No broker orders."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.personal_book import DEFAULT_OUTPUT_ROOT, build_and_store  # noqa: E402
from app.personal_capital_contract import DEFAULT_CAPITAL_CNY, STAGE_GOAL_ID  # noqa: E402


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
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    _load_dotenv(repo / ".env")
    os.chdir(repo)
    ticket = build_and_store(
        output_root=(
            args.output_root if args.output_root.is_absolute() else repo / args.output_root
        ),
        capital_cny=args.capital,
        cache_dir=repo / "data/research_cache/jiaoch_stk_mins_3y_v2_holdout",
        bounce_path=repo / "data/research_runs/path_a_protocol_bounce_daily/LATEST.json",
    )
    print(
        f"action={ticket.get('action')} symbol={ticket.get('symbol') or '-'} "
        f"lots={ticket.get('lots')} halt={ticket.get('halt')}"
    )
    print(ticket.get("headline") or "")
    print("非正式有效；不自动下单；个人线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
