#!/usr/bin/env python3
"""Path A E1: drawdown-focused variant matrix (no TCB)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_e1_drawdown_matrix import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_e1_drawdown_matrix,
    write_path_a_e1_drawdown_matrix,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A E1 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--max-variants", type=int, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    report = build_path_a_e1_drawdown_matrix(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
        max_variants=args.max_variants,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_e1_drawdown_matrix(report, output_root=output_root)

    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\n--- top variants ---")
    for row in report.get("top_variants") or []:
        print(
            f"{row.get('label')}: ret={row.get('rolling_1y_latest_return_pct')} "
            f"mdd={row.get('rolling_1y_latest_max_drawdown_pct')} "
            f"both={row.get('pass_both_50_15')} "
            f"pass15={row.get('pass_drawdown_15')} pass50={row.get('pass_return_50')}"
        )
    print("pass_both_count=", report.get("pass_both_count"))
    print("pass_drawdown_count=", report.get("pass_drawdown_count"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
