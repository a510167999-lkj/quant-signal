#!/usr/bin/env python3
"""Path A F5: OOS market-level diagnosis (read-only)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_f5_oos_diagnosis import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_f5_oos_diagnosis,
    write_path_a_f5_oos_diagnosis,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A F5 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    report = build_path_a_f5_oos_diagnosis(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_f5_oos_diagnosis(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print(f"\nOOS signal days: {report.get('oos_signal_date_count')}")
    print(f"OOS trades: {report.get('oos_qualified_trade_count')}")
    print(f"level dist (by day): {report.get('market_level_distribution_by_day')}")
    pre = report.get("frozen_rule_prefilter") or {}
    print(
        f"frozen prefilter: pass_days={pre.get('pass_days')} "
        f"pass_trades={pre.get('pass_trades')} "
        f"blocked_trades={pre.get('blocked_trades')}"
    )
    cf = report.get("counterfactual_include_cautious") or {}
    print(
        f"counterfactual (+cautious): pass_days={cf.get('pass_days')} "
        f"pass_trades={cf.get('pass_trades')}"
    )
    sens = report.get("favorable_threshold_sensitivity") or {}
    print(
        f"favorable near-miss (差1条): {sens.get('near_miss_count')} days "
        f"{sens.get('near_miss_dates')}"
    )
    print(f"\nconclusion: {report.get('diagnosis_conclusion')}")
    print("diagnosis_md=", pointer.get("diagnosis_md"))
    print("guarantee=", report.get("meets_user_requirement_as_guarantee"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
