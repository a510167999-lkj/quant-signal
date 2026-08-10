#!/usr/bin/env python3
"""Path A: frozen-candidate fixed-spec ~2y train-window replay."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_frozen_train_window_replay import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_frozen_train_window_replay,
    write_path_a_frozen_train_window_replay,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A frozen train replay ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument("--qualified-trades-path", type=Path, default=None)
    parser.add_argument("--frozen-candidate-path", type=Path, default=None)
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    report = build_path_a_frozen_train_window_replay(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
        qualified_trades_path=args.qualified_trades_path,
        frozen_candidate_path=args.frozen_candidate_path,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_frozen_train_window_replay(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    m = report.get("replay_metrics") or {}
    rs = report.get("rolling_12m_summary") or {}
    v = report.get("verdict") or {}
    print("\n=== summary ===")
    print(
        "span",
        (report.get("input_qualified_trades") or {}).get("first_signal_date"),
        "..",
        (report.get("input_qualified_trades") or {}).get("last_signal_date"),
        "years",
        (report.get("input_qualified_trades") or {}).get("approx_span_years"),
    )
    print(
        "selected",
        m.get("selected_trade_count"),
        "signal_days",
        m.get("signal_days"),
        "win%",
        m.get("trade_win_rate_pct"),
    )
    print(
        "full_path return/MDD",
        m.get("portfolio_compounded_return_pct"),
        m.get("portfolio_max_drawdown_pct"),
        "mdd_pass15",
        v.get("full_path_max_drawdown_pass_15"),
    )
    print(
        "latest_1y return/MDD",
        m.get("rolling_1y_latest_return_pct"),
        m.get("rolling_1y_latest_max_drawdown_pct"),
        "pass50_15",
        v.get("latest_rolling_12m_pass_50_15"),
    )
    print(
        "rolling n",
        rs.get("window_count"),
        "both_pass_rate",
        rs.get("both_pass_rate"),
        "worst_mdd",
        rs.get("worst_mdd_pct"),
        "min_ret",
        rs.get("min_return_pct"),
    )
    print("guarantee", v.get("meets_user_requirement_as_guarantee"))
    print("md", pointer.get("rolling_windows_md"))
    print("csv", pointer.get("rolling_windows_csv"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
