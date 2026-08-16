#!/usr/bin/env python3
"""Weekly bounce-if-favorable else cash. Not window-return switching."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_regime import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_protocol_regime_switch,
    write_regime_switch,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    report = build_path_a_protocol_regime_switch(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
    )
    train = report.get("train") or {}
    holdout = report.get("holdout") or {}
    report["both_partition_26_15"] = bool(
        train.get("dual_pass_26_15") and holdout.get("dual_pass_26_15")
    )
    out = args.output_root
    if not out.is_absolute():
        out = repo / out
    pointer = write_regime_switch(report, output_root=out)
    print(
        f"stage={report.get('stage_goal_id')} "
        f"decisions={report.get('decision_point_count')} "
        f"switches={report.get('switch_count')} usage={report.get('arm_usage')}"
    )
    print(
        f"train_1y={train.get('rolling_1y_latest_return_pct')} "
        f"train_mdd={train.get('portfolio_max_drawdown_pct')} "
        f"holdout_1y={holdout.get('rolling_1y_latest_return_pct')} "
        f"holdout_mdd={holdout.get('portfolio_max_drawdown_pct')} "
        f"both={report.get('both_partition_26_15')}"
    )
    print("wrote", pointer.get("table_path"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
