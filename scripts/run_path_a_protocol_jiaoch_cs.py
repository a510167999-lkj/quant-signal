#!/usr/bin/env python3
"""Path-A Jiaoch cross-section helpers: moneyflow, monthly bak_basic, turnover index."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_daily_basic_value import (  # noqa: E402
    DEFAULT_CALENDAR_SPEC,
    locked_trade_dates,
)
from app.factor_v3_path_a_protocol_jiaoch_cs import (  # noqa: E402
    collect_cs,
    index_turnover_from_733,
    month_starts,
)

FLOW_FIELDS = ("ts_code", "trade_date", "net_mf_vol", "net_mf_amount")
INDUSTRY_FIELDS = ("trade_date", "ts_code", "name", "industry", "list_date")
FLOW_ROOT = Path("data/research_cache/jiaoch_moneyflow_path_a")
INDUSTRY_ROOT = Path("data/research_cache/jiaoch_bak_basic_month_path_a")
TURNOVER_ROOT = Path("data/research_cache/jiaoch_turnover_path_a")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("flow", "industry", "turnover-index"), required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--max-days", type=int, default=0)
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    dates = locked_trade_dates(repo / DEFAULT_CALENDAR_SPEC)
    if args.kind == "turnover-index":
        report = index_turnover_from_733(
            repo_root=repo, output_root=repo / TURNOVER_ROOT
        )
    elif args.kind == "flow":
        if args.max_days > 0:
            dates = dates[: args.max_days]
        report = collect_cs(
            api_name="moneyflow",
            fields=FLOW_FIELDS,
            source_version="jiaoch-moneyflow/v1",
            output_root=repo / FLOW_ROOT,
            repo_root=repo,
            dates=dates,
        )
    else:
        dates = month_starts(dates)
        if args.max_days > 0:
            dates = dates[: args.max_days]
        report = collect_cs(
            api_name="bak_basic",
            fields=INDUSTRY_FIELDS,
            source_version="jiaoch-bak-basic-month/v1",
            output_root=repo / INDUSTRY_ROOT,
            repo_root=repo,
            dates=dates,
        )
    print(
        f"kind={args.kind} planned={report.get('planned', report.get('written'))} "
        f"fetched={report.get('fetched', report.get('written'))} "
        f"skipped={report.get('skipped')} failed={len(report.get('failed') or [])} "
        f"complete={report.get('complete')}"
    )
    if report.get("failed"):
        print("failed", report["failed"][:6])
    return 0 if report.get("complete", True) and not report.get("failed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
