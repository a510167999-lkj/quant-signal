#!/usr/bin/env python3
"""Path A E2: sandwich search between E1 Pareto anchors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_e2_sandwich_matrix import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_e2_sandwich_matrix,
    write_path_a_e2_sandwich_matrix,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A E2 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--max-variants", type=int, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    report = build_path_a_e2_sandwich_matrix(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
        max_variants=args.max_variants,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_e2_sandwich_matrix(report, output_root=output_root)

    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\n--- top by combined shortfall ---")
    for row in (report.get("top_variants") or [])[:12]:
        print(
            f"{row.get('label')}: ret={row.get('rolling_1y_latest_return_pct')} "
            f"mdd={row.get('rolling_1y_latest_max_drawdown_pct')} "
            f"shortfall={row.get('combined_shortfall_pct')} "
            f"both={row.get('pass_both_50_15')}"
        )
    print("\n--- nearest pass-return (lowest |mdd|) ---")
    for row in (report.get("nearest_pass_return_by_drawdown") or [])[:5]:
        print(
            f"{row.get('label')}: ret={row.get('rolling_1y_latest_return_pct')} "
            f"mdd={row.get('rolling_1y_latest_max_drawdown_pct')}"
        )
    print("\n--- nearest pass-drawdown (highest ret) ---")
    for row in (report.get("nearest_pass_drawdown_by_return") or [])[:5]:
        print(
            f"{row.get('label')}: ret={row.get('rolling_1y_latest_return_pct')} "
            f"mdd={row.get('rolling_1y_latest_max_drawdown_pct')}"
        )
    print("pass_both_count=", report.get("pass_both_count"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
