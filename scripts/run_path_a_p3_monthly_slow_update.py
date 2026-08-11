#!/usr/bin/env python3
"""Path A P3: monthly slow-update adaptive layer (WF-style simulation)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_p3_monthly_slow_update import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_p3_monthly_slow_update,
    write_path_a_p3_monthly_slow_update,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A P3 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    report = build_path_a_p3_monthly_slow_update(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_p3_monthly_slow_update(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print(f"\ndecision_points: {report.get('decision_point_count')}")
    print(f"switches: {report.get('switch_count')}")
    print(f"arm_usage: {report.get('arm_usage_in_decisions')}")
    p3m = (report.get("p3_spliced") or {}).get("metrics") or {}
    bm = (report.get("baseline_always_e4_primary") or {}).get("metrics") or {}
    p0m = (report.get("baseline_always_p0_loss_streak_3") or {}).get("metrics") or {}
    print(
        f"\nalways e4_primary:  ret={bm.get('portfolio_compounded_return_pct')} "
        f"mdd={bm.get('portfolio_max_drawdown_pct')} both={bm.get('rolling_both_pass_rate')}"
    )
    print(
        f"always p0_ls3:      ret={p0m.get('portfolio_compounded_return_pct')} "
        f"mdd={p0m.get('portfolio_max_drawdown_pct')} both={p0m.get('rolling_both_pass_rate')}"
    )
    print(
        f"P3 monthly splice:  ret={p3m.get('portfolio_compounded_return_pct')} "
        f"mdd={p3m.get('portfolio_max_drawdown_pct')} both={p3m.get('rolling_both_pass_rate')}"
    )
    print("\nledger (switches only):")
    for e in report.get("ledger") or []:
        if e.get("switched"):
            print(
                f"  {e['decision_date']} -> {e['selected_arm']} "
                f"(cutoff={e.get('data_cutoff')}, reason={e.get('reason')})"
            )
    print("ledger_md=", pointer.get("monthly_ledger_md"))
    print("guarantee=", report.get("meets_user_requirement_as_guarantee"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
