#!/usr/bin/env python3
"""Path A: score development strategy matrix baseline without TCB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_strategy_matrix_baseline import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_strategy_matrix_baseline,
    write_path_a_strategy_matrix_baseline,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Path A strategy matrix baseline ({STAGE_GOAL_ID})"
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    report = build_path_a_strategy_matrix_baseline(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_strategy_matrix_baseline(report, output_root=output_root)

    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\n--- arm scores (development_only) ---")
    for row in report["arm_scores"]:
        print(
            f"{row['arm']}: ret={row['rolling_1y_latest_return_pct']:.2f}% "
            f"mdd={row['rolling_1y_latest_max_drawdown_pct']:.2f}% "
            f"pass50={row['pass_return_50']} pass15={row['pass_drawdown_15']} "
            f"both={row['pass_both_50_15']}"
        )
    print("any_arm_passes_50_15=", report["any_arm_passes_50_15"])
    print("effective_strategy_found=", report["effective_strategy_found"])
    print("best_arm_by_heuristic=", report["best_arm_by_heuristic"])
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
