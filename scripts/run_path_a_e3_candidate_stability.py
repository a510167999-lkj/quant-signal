#!/usr/bin/env python3
"""Path A E3: freeze candidates and audit rolling stability."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_e3_candidate_stability import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_e3_candidate_stability,
    write_path_a_e3_candidate_stability,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A E3 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    report = build_path_a_e3_candidate_stability(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_e3_candidate_stability(report, output_root=output_root)

    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\n--- candidates ---")
    for c in report.get("candidates") or []:
        latest = c.get("latest") or {}
        rolling = c.get("rolling") or {}
        print(
            f"{c.get('candidate_id')}: latest_dual={latest.get('dual_pass_50_15')} "
            f"ret={latest.get('rolling_1y_latest_return_pct')} "
            f"mdd={latest.get('rolling_1y_latest_max_drawdown_pct')} "
            f"trades={latest.get('selected_trade_count')} "
            f"both_rate={rolling.get('both_pass_rate')} "
            f"worst_mdd={rolling.get('worst_mdd_pct')} "
            f"stability_ready={c.get('stability_ready_for_oos_planning')}"
        )
    print(
        "any_stability_ready_for_oos_planning=",
        report.get("any_stability_ready_for_oos_planning"),
    )
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
