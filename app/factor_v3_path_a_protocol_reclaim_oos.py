"""Independent post-holdout replay of frozen sig_pull_negext_h5.

The locked holdout ended 2026-07-03 and was already opened while picking the
rule. This module scores the same frozen spec on signal dates after that day.

It is Path-A shadow paper, not formal final-OOS. A six-week window cannot
judge the 26/15 rolling-12m contract. effective_strategy stays false.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_protocol_baseline as baseline
from app import factor_v3_path_a_protocol_reclaim_freeze as freeze
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal import (
    DEFAULT_CACHE_DIR,
    HOLD_DAYS,
    STOP_LOSS_PCT,
    build_path_a_protocol_signal_qt,
    write_path_a_protocol_signal_qt,
)
from app.factor_v3_path_a_protocol_signal_specs import (
    FROZEN_RECLAIM_CANDIDATE_ID,
    FROZEN_RECLAIM_VARIANT,
)
from app.jiaoch_live_market import JIAOCH_DAILY_CACHE_SOURCE_VERSION
from app.storage import write_json

STAGE_GOAL_ID = "path-a-protocol-reclaim-oos/v1"
REPORT_SCHEMA = "path-a-protocol-reclaim-oos-report/v1"
SIGNAL_FAMILY = "path-a-protocol-signal-reclaim-oos/v1"
INDEPENDENT_OOS_START = "2026-07-04"
DEFAULT_OOS_END = "2026-08-13"
MIN_CALENDAR_DAYS_FOR_12M = 365
TWELVE_MONTH_DUE_DATE = (
    date.fromisoformat(INDEPENDENT_OOS_START) + timedelta(days=MIN_CALENDAR_DAYS_FOR_12M)
).isoformat()
DEFAULT_QT_PATH = Path(
    "data/research_cache/qualified_hold5_stop5_oos_jiaoch_reclaim.json"
)
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_reclaim_oos")
DEFAULT_CACHE_DIR_PATH = DEFAULT_CACHE_DIR


class PathAReclaimOosError(ValueError):
    """Raised when the independent reclaim OOS cannot be scored."""


def twelve_month_window_evaluable(start: str, end: str) -> bool:
    first = date.fromisoformat(str(start)[:10])
    last = date.fromisoformat(str(end)[:10])
    return (last - first).days >= MIN_CALENDAR_DAYS_FOR_12M


def days_until_twelve_month_evaluable(as_of: str) -> int:
    due = date.fromisoformat(TWELVE_MONTH_DUE_DATE)
    today = date.fromisoformat(str(as_of)[:10])
    return max(0, (due - today).days)


def filter_independent_oos_trades(
    trades: list[dict[str, Any]],
    *,
    start: str = INDEPENDENT_OOS_START,
    end: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last = str(end or "")[:10]
    for trade in trades:
        day = str(trade.get("signal_date") or "")[:10]
        if not day or day < start:
            continue
        if last and day > last:
            continue
        if proto.partition_for_signal_date(day) is not None:
            continue
        out.append(trade)
    return out


def market_level_snapshot(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_trade: dict[str, int] = {}
    by_day: dict[str, str] = {}
    for trade in trades:
        level = str(trade.get("market_level") or "unknown")
        by_trade[level] = by_trade.get(level, 0) + 1
        day = str(trade.get("signal_date") or "")[:10]
        if day and day not in by_day:
            by_day[day] = level
    day_counts: dict[str, int] = {}
    for level in by_day.values():
        day_counts[level] = day_counts.get(level, 0) + 1
    allowed = ("favorable", "neutral")
    return {
        "by_trade": by_trade,
        "by_day": day_counts,
        "signal_days": len(by_day),
        "frozen_rule_pass_trades": sum(
            n for lvl, n in by_trade.items() if lvl in allowed
        ),
        "frozen_rule_allowed_levels": list(allowed),
    }


def score_independent_oos(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return baseline._score_partition(trades, FROZEN_RECLAIM_VARIANT)


def build_reclaim_oos_report(
    *,
    trades: list[dict[str, Any]],
    oos_start: str = INDEPENDENT_OOS_START,
    oos_end: str = DEFAULT_OOS_END,
    qualified_trades_path: str | None = None,
    name_count: int | None = None,
    raw_trade_count: int | None = None,
) -> dict[str, Any]:
    filtered = filter_independent_oos_trades(
        trades, start=oos_start, end=oos_end
    )
    scored = score_independent_oos(filtered)
    spec = freeze.frozen_reclaim_spec()
    card = freeze.build_frozen_reclaim_card()
    evaluable = twelve_month_window_evaluable(oos_start, oos_end)
    latest = scored.get("latest_1y_return_pct")
    mdd = scored.get("full_path_mdd_pct")
    dual = False
    if evaluable and latest is not None and mdd is not None:
        dual = goal.meets_primary_performance_targets(
            rolling_12m_net_return_pct=float(latest),
            max_drawdown_pct=float(mdd),
        )
    signal_dates = [
        str(row.get("signal_date") or "")[:10]
        for row in filtered
        if str(row.get("signal_date") or "")
    ]
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "candidate_id": FROZEN_RECLAIM_CANDIDATE_ID,
        "candidate_spec_sha256": card["candidate_spec_sha256"],
        "spec": spec,
        "oos_window": {
            "start": oos_start,
            "end": oos_end,
            "first_signal_date": min(signal_dates) if signal_dates else None,
            "last_signal_date": max(signal_dates) if signal_dates else None,
        },
        "holdout_end": proto.HOLDOUT_END,
        "independent_oos": True,
        "formal_final_oos": False,
        "formal_final_oos_still_sealed": True,
        "twelve_month_evaluable": evaluable,
        "twelve_month_due_date": TWELVE_MONTH_DUE_DATE,
        "days_until_twelve_month_evaluable": days_until_twelve_month_evaluable(
            oos_end
        ),
        "independent_oos_dual_pass_26_15": dual,
        "effective_strategy": False,
        "promotable": False,
        "development_only": True,
        "user_accepted_hypothesis": True,
        "zero_refit": True,
        "parameter_search": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "search_universe_policy": proto.SEARCH_UNIVERSE_POLICY,
        "data_source_policy": goal.DATA_SOURCE_POLICY,
        "source_version": JIAOCH_DAILY_CACHE_SOURCE_VERSION,
        "signal_family": SIGNAL_FAMILY,
        "qualified_trades_path": qualified_trades_path,
        "name_count": name_count,
        "raw_trade_count": raw_trade_count if raw_trade_count is not None else len(filtered),
        "oos_trade_count": len(filtered),
        "market_levels": market_level_snapshot(filtered),
        "score": scored,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
    }


def format_reclaim_oos_table(report: dict[str, Any]) -> str:
    score = report.get("score") or {}
    window = report.get("oos_window") or {}
    levels = (report.get("market_levels") or {}).get("by_trade") or {}
    return "\n".join(
        [
            f"stage={report.get('stage_goal_id')}",
            f"candidate={report.get('candidate_id')}",
            f"spec_sha256={report.get('candidate_spec_sha256')}",
            f"oos={window.get('start')}..{window.get('end')}",
            f"independent_oos={report.get('independent_oos')}",
            f"formal_final_oos={report.get('formal_final_oos')}",
            f"twelve_month_evaluable={report.get('twelve_month_evaluable')}",
            f"twelve_month_due_date={report.get('twelve_month_due_date')}",
            f"days_until_12m={report.get('days_until_twelve_month_evaluable')}",
            f"independent_oos_dual_pass_26_15="
            f"{report.get('independent_oos_dual_pass_26_15')}",
            f"effective_strategy={report.get('effective_strategy')}",
            f"raw={report.get('raw_trade_count')} "
            f"selected={score.get('selected_trade_count')} "
            f"path={score.get('full_path_return_pct')} "
            f"mdd={score.get('full_path_mdd_pct')}",
            f"latest_1y={score.get('latest_1y_return_pct')} "
            f"latest_mdd={score.get('latest_1y_mdd_pct')}",
            f"market_levels={levels}",
            "",
        ]
    )


def write_reclaim_oos_report(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "LATEST.json"), report)
    table = format_reclaim_oos_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "effective_strategy": False,
        "table_path": str(output_root / "TABLE.txt"),
        "report_path": str(output_root / "LATEST.json"),
    }


def build_path_a_protocol_reclaim_oos(
    *,
    repo_root: Path | None = None,
    cache_dir: Path | None = None,
    qualified_trades_path: Path | None = None,
    oos_end: str = DEFAULT_OOS_END,
    max_symbols: int = 0,
    rebuild_qt: bool = False,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathAReclaimOosError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    if oos_end < INDEPENDENT_OOS_START:
        raise PathAReclaimOosError(
            f"oos_end {oos_end} is before independent start {INDEPENDENT_OOS_START}"
        )
    root = (repo_root or Path.cwd()).resolve()
    qt_path = (
        Path(qualified_trades_path).resolve()
        if qualified_trades_path is not None
        else (root / DEFAULT_QT_PATH)
    )
    payload: dict[str, Any] | None = None
    if qt_path.is_file() and not rebuild_qt:
        from app.factor_v3_path_a_frozen_train_window_replay import _load_json

        payload = _load_json(qt_path)
        meta = (payload or {}).get("summary", {}).get("path_a_3y_clean_replay") or {}
        if meta.get("signal_family") != SIGNAL_FAMILY:
            payload = None
        elif meta.get("source_version") != JIAOCH_DAILY_CACHE_SOURCE_VERSION:
            payload = None
        elif (meta.get("window") or {}).get("start") != INDEPENDENT_OOS_START:
            payload = None
    if payload is None:
        payload = build_path_a_protocol_signal_qt(
            repo_root=root,
            cache_dir=cache_dir or (root / DEFAULT_CACHE_DIR_PATH),
            max_symbols=max_symbols,
            require_local_research=require_local_research,
            hold_horizons=(HOLD_DAYS,),
            signal_family=SIGNAL_FAMILY,
            book="reclaim",
            start_date=INDEPENDENT_OOS_START,
            end_date=oos_end,
            slice_name="independent_oos",
        )
        write_path_a_protocol_signal_qt(payload, qt_path=qt_path)
    meta = (payload.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("slice") == "traded":
        raise PathAReclaimOosError("refuses the contaminated 191-name slice")
    if meta.get("source_version") != JIAOCH_DAILY_CACHE_SOURCE_VERSION:
        raise PathAReclaimOosError("QT is not Jiaoch stk_mins v2")
    trades = list(payload.get("qualified_trades") or [])
    return build_reclaim_oos_report(
        trades=trades,
        oos_start=INDEPENDENT_OOS_START,
        oos_end=oos_end,
        qualified_trades_path=str(qt_path),
        name_count=meta.get("name_count"),
        raw_trade_count=meta.get("raw_trade_count", len(trades)),
    )


__all__ = [
    "DEFAULT_OOS_END",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_QT_PATH",
    "INDEPENDENT_OOS_START",
    "TWELVE_MONTH_DUE_DATE",
    "SIGNAL_FAMILY",
    "STAGE_GOAL_ID",
    "PathAReclaimOosError",
    "STOP_LOSS_PCT",
    "build_path_a_protocol_reclaim_oos",
    "build_reclaim_oos_report",
    "filter_independent_oos_trades",
    "format_reclaim_oos_table",
    "days_until_twelve_month_evaluable",
    "twelve_month_window_evaluable",
    "write_reclaim_oos_report",
]
