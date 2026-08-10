#!/usr/bin/env python3
"""Path A E4: auto stability-improvement matrix (no TCB)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_e4_stability_improve import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_e4_stability_improve,
    write_path_a_e4_stability_improve,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A E4 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--max-variants", type=int, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    report = build_path_a_e4_stability_improve(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
        max_variants=args.max_variants,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_e4_stability_improve(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\n--- top by score_rank ---")
    for row in (report.get("top_variants") or [])[:12]:
        latest = row.get("latest") or {}
        rolling = row.get("rolling") or {}
        print(
            f"{row.get('label')}: ret={latest.get('rolling_1y_latest_return_pct')} "
            f"mdd={latest.get('rolling_1y_latest_max_drawdown_pct')} "
            f"both_rate={rolling.get('both_pass_rate')} "
            f"worst_mdd={rolling.get('worst_mdd_pct')} "
            f"trades={latest.get('selected_trade_count')} "
            f"ready={row.get('stability_ready_for_oos_planning')}"
        )
    print("stability_ready_count=", report.get("stability_ready_count"))
    print("oos_authorized=", report.get("oos_authorized"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
