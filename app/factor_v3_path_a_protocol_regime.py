"""Weekly bounce-vs-cash switch driven by market_level, not window returns."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_p3_monthly_slow_update as p3
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_monthly_switch import (
    week_decision_points,
    _load_selected_arm,
    _score_book,
)
from app.factor_v3_path_a_protocol_monthly_switch_specs import (
    BOUNCE_ARM_ID,
    BOUNCE_VARIANT,
    CASH_ARM_ID,
)
from app.storage import write_json

STAGE_GOAL_ID = "path-a-protocol-regime-switch/v1"
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_regime_switch")
SELECTION_CRITERION = "bounce_if_last_market_favorable_else_cash"


def market_level_by_date(trades: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for trade in trades:
        day = str(trade.get("signal_date") or "")[:10]
        level = str(trade.get("market_level") or "")
        if day and level and day not in out:
            out[day] = level
    return out


def last_level(series: dict[str, str], cutoff: str) -> str | None:
    prior = [day for day in series if day <= cutoff]
    if not prior:
        return None
    return series[max(prior)]


def simulate_regime_switch(
    *,
    bounce_trades: list[dict[str, Any]],
    calendar_dates: list[str],
    market_series: dict[str, str],
    cadence: str = "week",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if cadence == "week":
        points = week_decision_points(calendar_dates)
    else:
        points = p3._month_decision_points(calendar_dates)
    ledger: list[dict[str, Any]] = []
    current = CASH_ARM_ID
    for idx, day in enumerate(points):
        if idx == 0:
            ledger.append(
                {
                    "decision_date": day,
                    "selected_arm": current,
                    "reason": "initial_seed_cash",
                    "switched": False,
                    "market_level": None,
                }
            )
            continue
        prior = [item for item in calendar_dates if item < day]
        cutoff = prior[-1] if prior else day
        level = last_level(market_series, cutoff)
        best = BOUNCE_ARM_ID if level == "favorable" else CASH_ARM_ID
        switched = best != current
        current = best
        ledger.append(
            {
                "decision_date": day,
                "selected_arm": current,
                "reason": f"level_{level or 'unknown'}",
                "switched": switched,
                "market_level": level,
            }
        )
    periods = []
    for index, entry in enumerate(ledger):
        start = str(entry["decision_date"])
        end = (
            str(ledger[index + 1]["decision_date"])
            if index + 1 < len(ledger)
            else "9999-99-99"
        )
        periods.append(
            {
                "start": start,
                "end": end,
                "arm_id": entry["selected_arm"],
                "decision_date": start,
            }
        )
    return ledger, periods


def build_path_a_protocol_regime_switch(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise ValueError(f"requires local_research (got {role!r})")
    root = (repo_root or Path.cwd()).resolve()
    from app.factor_v3_path_a_protocol_signal import DEFAULT_BOUNCE_QT_PATH

    bounce_selected, bounce_meta, _dates = _load_selected_arm(
        root / DEFAULT_BOUNCE_QT_PATH,
        variant=BOUNCE_VARIANT,
        expected_family="path-a-protocol-signal-bounce/v1",
    )
    from app import factor_v3_path_a_frozen_train_window_replay as train_replay
    from app.factor_v3_path_a_protocol_monthly_switch import _locked_dates

    raw = train_replay._load_json(root / DEFAULT_BOUNCE_QT_PATH) or {}
    locked_raw = _locked_dates(list(raw.get("qualified_trades") or []))
    series = market_level_by_date(locked_raw)
    calendar = sorted(
        {
            str(trade.get("signal_date") or "")[:10]
            for trade in bounce_selected
            if trade.get("signal_date")
        }
    )
    ledger, periods = simulate_regime_switch(
        bounce_trades=bounce_selected,
        calendar_dates=calendar,
        market_series=series,
        cadence="week",
    )
    arm_trades = {BOUNCE_ARM_ID: bounce_selected, CASH_ARM_ID: []}
    spliced = p3._period_trades(periods, arm_trades)
    train = proto.filter_trades_for_partition(spliced, "train")
    holdout = proto.filter_trades_for_partition(spliced, "holdout")
    usage: dict[str, int] = {}
    for row in ledger:
        arm = str(row["selected_arm"])
        usage[arm] = usage.get(arm, 0) + 1
    return {
        "stage_goal_id": STAGE_GOAL_ID,
        "selection_criterion": SELECTION_CRITERION,
        "cadence": "week",
        "bounce_name_count": bounce_meta.get("name_count"),
        "decision_point_count": len(ledger),
        "switch_count": sum(1 for row in ledger if row.get("switched")),
        "arm_usage": usage,
        "ledger": ledger,
        "train": _score_book(train),
        "holdout": _score_book(holdout),
        "both_partition_26_15": False,
        "effective_strategy": False,
        "promotable": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
    }


def write_regime_switch(report: dict[str, Any], *, output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    slim = deepcopy(report)
    write_json(str(output_root / "LATEST.json"), slim)
    train = report.get("train") or {}
    holdout = report.get("holdout") or {}
    table = "\n".join(
        [
            f"stage={report.get('stage_goal_id')}",
            f"rule={report.get('selection_criterion')}",
            f"decisions={report.get('decision_point_count')} "
            f"switches={report.get('switch_count')} usage={report.get('arm_usage')}",
            f"train_1y={train.get('rolling_1y_latest_return_pct')} "
            f"train_mdd={train.get('portfolio_max_drawdown_pct')} "
            f"train_dual={train.get('dual_pass_26_15')} n={train.get('selected_trade_count')}",
            f"holdout_1y={holdout.get('rolling_1y_latest_return_pct')} "
            f"holdout_mdd={holdout.get('portfolio_max_drawdown_pct')} "
            f"holdout_dual={holdout.get('dual_pass_26_15')} n={holdout.get('selected_trade_count')}",
            f"effective_strategy={report.get('effective_strategy')}",
            "",
        ]
    )
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {"ok": True, "table_path": str(output_root / "TABLE.txt")}
