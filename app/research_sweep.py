from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

import pandas as pd

from app import research_goal_contract as goal
from app.research_common import _date_value
from app.research_equity import (
    _equity_points_from_basket_returns,
    _equity_points_from_slot_daily_returns,
    _equity_points_from_slot_exit_returns,
    _max_drawdown_pct_from_points_raw,
)
from app.research_portfolio import _select_with_portfolio_controls
from app.signal_tags import (
    build_announcement_tags,
    build_candidate_context_tags,
    build_entry_executability_tags,
    build_proxy_market_tags,
)
from app.storage import read_json


DEFAULT_SWEEP_TAGS = [
    "score_gte_5",
    "balanced_rsi",
    "moderate_20d_momentum",
    "volume_confirmed",
    "near_60d_high",
    "breakout_20d",
    "controlled_volatility",
    "high_volatility",
    "rs20_nonnegative",
    "rs20_strong",
    "rs60_nonnegative",
    "rs60_strong",
    "rs20_market_leader",
    "rs60_market_leader",
    "candidate_rank_lte_20",
    "candidate_rank_lte_40",
    "amount_gte_1b",
    "amount_gte_300m",
    "amount_gte_100m",
    "amount_rank_pct_top_10",
    "amount_rank_pct_top_20",
    "amount_rank_pct_top_40",
    "candidate_change_negative",
    "candidate_change_0_to_3",
    "candidate_change_3_to_6",
    "candidate_change_gte_6",
    "candidate_rank_pct_top_25",
    "candidate_rank_pct_top_50",
    "prefilter_score_gte_12",
    "prefilter_score_gte_13",
    "prior_win_gte_60",
    "prior_win_gte_70",
    "prior_return_gte_1",
    "prior_return_gte_3",
    "prior_adverse_lte_3",
    "prior_adverse_lte_4",
    "breadth_ma20_gte_50",
    "breadth_ma20_gte_60",
    "breadth_ma20_gte_70",
    "breadth_ma20_lt_50",
    "breadth_ma60_gte_50",
    "breadth_ma60_gte_60",
    "breadth_ma60_lt_50",
    "breadth_ret20_pos_gte_50",
    "breadth_ret20_pos_gte_60",
    "breadth_ret20_pos_gte_70",
    "breadth_ret20_pos_lt_50",
    "breadth_advancing_gte_50",
    "breadth_advancing_gte_60",
    "breadth_advancing_lt_50",
    "breadth_median_ret20_gte_0",
    "breadth_median_ret20_gte_5",
    "breadth_median_ret20_gte_10",
    "breadth_median_ret20_lt_0",
    "breadth_liquid_300m_gte_50",
    "breadth_liquid_300m_lt_50",
    "proxy20_avg_gte_0",
    "proxy20_avg_gte_5",
    "proxy20_avg_gte_10",
    "proxy20_avg_gte_15",
    "proxy20_avg_lt_0",
    "proxy20_avg_lte_neg5",
    "proxy60_avg_gte_0",
    "proxy60_avg_gte_5",
    "proxy60_avg_gte_10",
    "proxy60_avg_gte_15",
    "proxy60_avg_lt_0",
    "proxy60_avg_lte_neg5",
    "proxy20_max_gte_5",
    "proxy20_max_gte_10",
    "proxy20_max_gte_15",
    "proxy60_max_gte_5",
    "proxy60_max_gte_10",
    "proxy60_max_gte_15",
    "proxy_market_bullish",
    "proxy_market_hot",
    "proxy_market_fragile",
    "price_gap_down",
    "price_gap_up_0_to_2",
    "price_gap_up_2_to_5",
    "price_gap_up_gte_5",
    "price_intraday_gain",
    "price_intraday_gain_gte_3",
    "price_intraday_loss",
    "price_close_near_high",
    "price_close_weak",
    "price_range_lt_4",
    "price_range_4_to_8",
    "price_range_gte_8",
    "price_upper_shadow_gte_3",
    "price_lower_shadow_gte_3",
    "price_signal_limit_up",
    "price_signal_near_limit_up",
    "recent_limit_up_20d",
    "recent_limit_up_20d_gte_2",
    "recent_near_limit_up_20d",
    "recent_near_limit_up_20d_gte_2",
    "recent_large_up_20d",
    "recent_large_up_20d_gte_3",
    "margin_financing_underlying",
    "margin_financing_eligible",
    "margin_short_underlying",
    "margin_short_eligible",
    "margin_collateral_eligible",
    "margin_exchange_sse",
    "margin_exchange_szse",
    "margin_price_limit_10",
    "margin_price_limit_20",
    "industry_ma20_gte_50",
    "industry_ma20_gte_60",
    "industry_ma20_gte_70",
    "industry_ma20_lt_50",
    "industry_ret20_pos_gte_50",
    "industry_ret20_pos_gte_60",
    "industry_ret20_pos_gte_70",
    "industry_ret20_pos_lt_50",
    "industry_advancing_gte_50",
    "industry_advancing_gte_60",
    "industry_advancing_gte_70",
    "industry_advancing_lt_50",
    "industry_median_ret20_gte_0",
    "industry_median_ret20_gte_5",
    "industry_median_ret20_gte_10",
    "industry_median_ret20_lt_0",
    "industry_top_ret20_gte_10",
    "industry_top_ret20_gte_20",
    "industry_top_ret20_gte_30",
    "industry_dispersion_gte_15",
    "industry_dispersion_gte_25",
    "industry_rotation_broad",
    "industry_rotation_narrow_hot",
    "lhb_on_list",
    "lhb_net_buy_positive",
    "lhb_net_buy_negative",
    "lhb_net_buy_ratio_gte_3",
    "lhb_net_buy_ratio_gte_5",
    "lhb_net_buy_ratio_lte_neg3",
    "lhb_turnover_ratio_gte_20",
    "lhb_turnover_ratio_gte_40",
    "lhb_turnover_rate_gte_10",
    "lhb_turnover_rate_gte_20",
    "lhb_institution_buy",
    "lhb_institution_sell",
    "lhb_reason_up",
    "lhb_reason_down",
    "lhb_reason_turnover",
    "lhb_reason_amplitude",
    "lhb_reason_multi_day",
    "announcement_level_neutral",
    "announcement_level_positive",
    "announcement_level_watch_risk",
    "announcement_level_high_risk",
    "announcement_allowed",
    "announcement_blocked",
    "announcement_count_gte_1",
    "announcement_count_gte_5",
    "announcement_count_gte_20",
    "announcement_negative",
    "announcement_negative_gte_2",
    "announcement_positive",
    "announcement_positive_gte_2",
    "announcement_score_lte_neg14",
    "announcement_score_neg",
    "announcement_score_pos",
    "announcement_event_regulatory_penalty",
    "announcement_event_exchange_inquiry",
    "announcement_event_delisting_or_st",
    "announcement_event_litigation_freeze",
    "announcement_event_pledge_or_reduction",
    "announcement_event_earnings_positive",
    "announcement_event_earnings_negative",
    "announcement_event_contract_or_order",
    "announcement_event_equity_incentive",
    "announcement_event_dividend_distribution",
    "announcement_event_financing_or_guarantee",
    "announcement_event_routine_governance",
    "announcement_event_buyback_or_increase",
    "announcement_event_other",
    "holding_calendar_gap_lte_4",
    "holding_calendar_gap_lte_6",
    "holding_calendar_gap_gte_5",
    "holding_calendar_gap_gte_7",
    "holding_calendar_gap_gte_10",
    "entry_executable",
    "entry_not_executable",
    "entry_gap_lt_neg1",
    "entry_gap_gte_neg1",
    "entry_gap_gte_0",
    "entry_gap_neg1_to_2",
    "entry_gap_gt_2",
    "entry_range_lt_6",
    "entry_range_gte_6",
]

DEFAULT_MARKET_LEVELS = [
    None,
    {"favorable", "neutral", "cautious"},
    {"favorable", "neutral"},
    {"favorable"},
]


def _latest_window_portfolio_stats(
    equity_points: List[Dict[str, Any]],
    days: int = 365,
) -> Dict[str, Any]:
    if not equity_points:
        return {}

    end = equity_points[-1]
    end_date = _date_value(end["signal_date"])
    start_cutoff = end_date - timedelta(days=days)
    start_index = 0
    for index, point in enumerate(equity_points):
        if _date_value(point["signal_date"]) >= start_cutoff:
            start_index = index
            break
    start_equity = equity_points[start_index - 1]["equity"] if start_index > 0 else 1.0
    segment = equity_points[start_index:]
    if not segment or not start_equity:
        return {}
    return_pct_raw = (end["equity"] / start_equity - 1) * 100
    max_drawdown_pct_raw = _max_drawdown_pct_from_points_raw(
        segment,
        start_equity=start_equity,
    )
    return {
        "start_date": segment[0]["signal_date"],
        "end_date": end["signal_date"],
        "signal_days": len(segment),
        "trade_count": sum(int(point.get("count") or 0) for point in segment),
        "return_pct": round(return_pct_raw, 2),
        "return_pct_raw": return_pct_raw,
        "max_drawdown_pct": round(max_drawdown_pct_raw, 2),
        "max_drawdown_pct_raw": max_drawdown_pct_raw,
    }


def _rolling_12m_window_stats(
    equity_points: List[Dict[str, Any]],
    days: int = 365,
) -> List[Dict[str, Any]]:
    """Return every completed rolling window, not only the latest one."""
    if not equity_points:
        return []
    windows = []
    history_start = _date_value(equity_points[0]["signal_date"])
    for end_index, end in enumerate(equity_points):
        end_date = _date_value(end["signal_date"])
        if (end_date - history_start).days < days:
            continue
        start_cutoff = end_date - timedelta(days=days)
        start_index = 0
        for candidate_index in range(end_index, -1, -1):
            if _date_value(equity_points[candidate_index]["signal_date"]) < start_cutoff:
                start_index = candidate_index + 1
                break
        start_equity = equity_points[start_index - 1]["equity"] if start_index > 0 else 1.0
        segment = equity_points[start_index : end_index + 1]
        if not segment or not start_equity:
            continue
        return_pct_raw = (end["equity"] / start_equity - 1) * 100
        max_drawdown_pct_raw = _max_drawdown_pct_from_points_raw(
            segment,
            start_equity=start_equity,
        )
        windows.append(
            {
                "start_date": max(start_cutoff, history_start).strftime(
                    "%Y-%m-%d"
                ),
                "end_date": end["signal_date"],
                "signal_days": len(segment),
                "trade_count": sum(int(point.get("count") or 0) for point in segment),
                "return_pct": round(return_pct_raw, 2),
                "return_pct_raw": return_pct_raw,
                "max_drawdown_pct": round(max_drawdown_pct_raw, 2),
                "max_drawdown_pct_raw": max_drawdown_pct_raw,
            }
        )
    return windows


def _equity_points_on_evaluation_sessions(
    equity_points: List[Dict[str, Any]],
    *,
    evaluation_start_date: str | None,
    evaluation_end_date: str | None,
    evaluation_session_dates: Sequence[str],
) -> List[Dict[str, Any]]:
    if isinstance(evaluation_session_dates, (str, bytes)):
        raise ValueError("evaluation session dates must be a sequence")
    sessions = [
        _date_value(value).strftime("%Y-%m-%d")
        for value in evaluation_session_dates
    ]
    if not sessions:
        raise ValueError("evaluation session dates cannot be empty")
    if len(sessions) != len(set(sessions)) or sessions != sorted(sessions):
        raise ValueError(
            "evaluation session dates must be unique and strictly increasing"
        )
    if not evaluation_start_date or not evaluation_end_date:
        raise ValueError(
            "evaluation session grid requires start and end dates"
        )
    evaluation_start = _date_value(evaluation_start_date).strftime("%Y-%m-%d")
    evaluation_end = _date_value(evaluation_end_date).strftime("%Y-%m-%d")
    if sessions[0] != evaluation_start or sessions[-1] != evaluation_end:
        raise ValueError(
            "evaluation session grid must exactly match start and end dates"
        )

    session_set = set(sessions)
    points_by_date: Dict[str, Dict[str, Any]] = {}
    for point in equity_points:
        point_date = _date_value(point["signal_date"]).strftime("%Y-%m-%d")
        if point_date not in session_set:
            raise ValueError(
                "equity event is outside the evaluation session grid"
            )
        if point_date in points_by_date:
            raise ValueError(
                "evaluation session grid received duplicate equity events"
            )
        points_by_date[point_date] = point

    filled: List[Dict[str, Any]] = []
    last_equity = 1.0
    for session_date in sessions:
        event = points_by_date.get(session_date)
        if event is not None:
            point = dict(event)
            point["signal_date"] = session_date
            point["event_date"] = session_date
            point["evaluation_session"] = True
            point["cash_session"] = False
            last_equity = float(point["equity"])
        else:
            point = {
                "signal_date": session_date,
                "event_date": session_date,
                "equity": last_equity,
                "drawdown_equity": last_equity,
                "return_pct": 0.0,
                "net_period_return_pct": 0.0,
                "count": 0,
                "evaluation_session": True,
                "cash_session": True,
            }
        filled.append(point)
    return filled


def _validate_slot_daily_paths_on_evaluation_sessions(
    selected: Sequence[Dict[str, Any]],
    evaluation_session_dates: Sequence[str],
) -> None:
    sessions = [
        _date_value(value).strftime("%Y-%m-%d")
        for value in evaluation_session_dates
    ]
    session_positions = {
        session_date: index
        for index, session_date in enumerate(sessions)
    }
    for trade in selected:
        entry_date = _date_value(
            trade.get("entry_date") or trade.get("signal_date")
        ).strftime("%Y-%m-%d")
        exit_date = _date_value(
            trade.get("exit_date") or trade.get("signal_date")
        ).strftime("%Y-%m-%d")
        entry_position = session_positions.get(entry_date)
        exit_position = session_positions.get(exit_date)
        if (
            entry_position is None
            or exit_position is None
            or entry_position > exit_position
        ):
            raise ValueError(
                "slot-daily active interval is outside the evaluation session grid"
            )
        path = trade.get("mark_to_market_path")
        if not isinstance(path, list):
            raise ValueError(
                "slot-daily active interval requires a complete mark path"
            )
        observed_dates = [
            _date_value(mark.get("date")).strftime("%Y-%m-%d")
            for mark in path
        ]
        expected_dates = sessions[entry_position : exit_position + 1]
        if observed_dates != expected_dates:
            raise ValueError(
                "slot-daily active interval has a missing evaluation session mark"
            )


def _trade_metrics(
    selected: List[Dict[str, Any]],
    hold_days: int,
    max_active_positions: int = 0,
    exposure_multiplier: float = 1.0,
    annual_financing_rate_pct: float = 0.0,
    roundtrip_cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
    capital_model: str = "signal-day",
    evaluation_start_date: str | None = None,
    evaluation_end_date: str | None = None,
    evaluation_session_dates: Sequence[str] | None = None,
) -> Dict[str, Any]:
    selected_by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in selected:
        selected_by_signal_date[item["signal_date"]].append(item)

    basket_returns = []
    for signal_date, top_trades in selected_by_signal_date.items():
        if top_trades:
            basket_returns.append(
                {
                    "signal_date": signal_date,
                    "return_pct": sum(item["return_pct"] for item in top_trades) / len(top_trades),
                    "count": len(top_trades),
                }
            )
    basket_returns.sort(key=lambda item: item["signal_date"])

    quality_cost_pct = max(
        float(roundtrip_cost_bps) + float(slippage_bps) * 2,
        0.0,
    ) / 100.0
    returns = [
        float(item["return_pct"]) - quality_cost_pct
        for item in selected
    ]
    adverse = [item for item in selected if item.get("max_adverse_pct") is not None]
    wins = [value for value in returns if value > 0]
    winning_returns = [value for value in returns if value > 0]
    losing_returns = [value for value in returns if value < 0]
    if (
        capital_model == "slot-daily"
        and evaluation_session_dates is not None
    ):
        _validate_slot_daily_paths_on_evaluation_sessions(
            selected,
            evaluation_session_dates,
        )
    if capital_model == "slot-daily":
        equity_points = _equity_points_from_slot_daily_returns(
            selected,
            max_active_positions=max_active_positions,
            exposure_multiplier=exposure_multiplier,
            annual_financing_rate_pct=annual_financing_rate_pct,
            roundtrip_cost_bps=roundtrip_cost_bps,
            slippage_bps=slippage_bps,
        )
    elif capital_model == "slot-exit":
        equity_points = _equity_points_from_slot_exit_returns(
            selected,
            max_active_positions=max_active_positions,
            exposure_multiplier=exposure_multiplier,
            annual_financing_rate_pct=annual_financing_rate_pct,
            roundtrip_cost_bps=roundtrip_cost_bps,
            slippage_bps=slippage_bps,
        )
    else:
        capital_model = "signal-day"
        equity_points = _equity_points_from_basket_returns(
            basket_returns,
            hold_days,
            exposure_multiplier=exposure_multiplier,
            annual_financing_rate_pct=annual_financing_rate_pct,
            roundtrip_cost_bps=roundtrip_cost_bps,
            slippage_bps=slippage_bps,
        )
    if evaluation_session_dates is None and evaluation_end_date is not None:
        raise ValueError(
            "evaluation session dates are required with evaluation end date"
        )
    evaluation_grid_included = evaluation_session_dates is not None
    rolling_equity_points = (
        _equity_points_on_evaluation_sessions(
            equity_points,
            evaluation_start_date=evaluation_start_date,
            evaluation_end_date=evaluation_end_date,
            evaluation_session_dates=evaluation_session_dates,
        )
        if evaluation_session_dates is not None
        else list(equity_points)
    )
    evaluation_start = (
        _date_value(evaluation_start_date)
        if evaluation_start_date
        else None
    )
    evaluation_end = (
        _date_value(evaluation_end_date)
        if evaluation_end_date
        else None
    )
    cash_anchor_included = bool(
        rolling_equity_points
        and (
            rolling_equity_points[0].get("cash_session") is True
            if evaluation_grid_included
            else (
                evaluation_start is not None
                and evaluation_start
                < _date_value(rolling_equity_points[0]["signal_date"])
            )
        )
    )
    if cash_anchor_included and not evaluation_grid_included:
        rolling_equity_points.insert(
            0,
            {
                "signal_date": evaluation_start.strftime("%Y-%m-%d"),
                "event_date": evaluation_start.strftime("%Y-%m-%d"),
                "equity": 1.0,
                "drawdown_equity": 1.0,
                "return_pct": 0.0,
                "net_period_return_pct": 0.0,
                "count": 0,
                "capital_model": capital_model,
                "cash_anchor": True,
            },
        )
    metric_equity_points = (
        rolling_equity_points if evaluation_grid_included else equity_points
    )
    latest_1y = _latest_window_portfolio_stats(
        rolling_equity_points, days=365
    )
    rolling_1y_windows = _rolling_12m_window_stats(
        rolling_equity_points, days=365
    )

    def window_quality(window: Dict[str, Any]) -> Dict[str, Any]:
        start_date = _date_value(window["start_date"])
        end_date = _date_value(window["end_date"])
        window_trades = [
            item
            for item in selected
            if start_date
            <= _date_value(item.get("exit_date") or item.get("signal_date"))
            <= end_date
        ]
        returns = [
            float(item.get("return_pct") or 0.0) - quality_cost_pct
            for item in window_trades
        ]
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value < 0]
        average_win = sum(wins) / len(wins) if wins else None
        average_loss = abs(sum(losses) / len(losses)) if losses else None
        payoff_ratio = average_win / average_loss if average_win is not None and average_loss else None
        gross_loss = abs(sum(losses))
        profit_factor = sum(wins) / gross_loss if gross_loss else None
        win_rate = (
            len(wins) / len(window_trades) * 100
            if window_trades
            else None
        )
        drawdown_raw = window.get("max_drawdown_pct_raw")
        window_return_raw = window.get("return_pct_raw")
        calmar_raw = (
            float(window_return_raw) / abs(float(drawdown_raw))
            if window_return_raw is not None
            and drawdown_raw not in {None, 0}
            else None
        )
        drawdown = window.get("max_drawdown_pct")
        window_return = window.get("return_pct")
        calmar = (
            float(window_return) / abs(float(drawdown))
            if window_return is not None and drawdown not in {None, 0}
            else None
        )
        return {
            "selected_trade_count": len(window_trades),
            "win_count": len(wins),
            "nonwin_count": len(window_trades) - len(wins),
            "win_rate_pct": round(win_rate, 2)
            if win_rate is not None
            else None,
            "win_rate_pct_raw": win_rate,
            "payoff_ratio": round(payoff_ratio, 2) if payoff_ratio is not None else None,
            "payoff_ratio_raw": payoff_ratio,
            "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
            "profit_factor_raw": profit_factor,
            "calmar": round(calmar, 2) if calmar is not None else None,
            "calmar_raw": calmar_raw,
        }

    rolling_1y_windows = [
        {**window, **window_quality(window)} for window in rolling_1y_windows
    ]
    rolling_full_window = bool(
        rolling_equity_points
        and (
            _date_value(rolling_equity_points[-1]["signal_date"])
            - _date_value(rolling_equity_points[0]["signal_date"])
        ).days
        >= 365
    )
    rolling_position_days = latest_1y.get("trade_count")
    rolling_trade_count = None
    if latest_1y.get("start_date") and latest_1y.get("end_date"):
        rolling_start = _date_value(latest_1y["start_date"])
        rolling_end = _date_value(latest_1y["end_date"])
        rolling_trade_count = sum(
            1
            for item in selected
            if _date_value(item.get("entry_date") or item.get("signal_date")) <= rolling_end
            and _date_value(item.get("exit_date") or item.get("signal_date")) >= rolling_start
        )
    average_win = (
        sum(winning_returns) / len(winning_returns) if winning_returns else None
    )
    average_loss = (
        abs(sum(losing_returns) / len(losing_returns)) if losing_returns else None
    )
    payoff_ratio = (
        average_win / average_loss if average_win is not None and average_loss else None
    )
    gross_profit = sum(winning_returns)
    gross_loss = abs(sum(losing_returns))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    max_drawdown_raw = (
        _max_drawdown_pct_from_points_raw(metric_equity_points)
        if metric_equity_points
        else None
    )
    max_drawdown = (
        round(max_drawdown_raw, 2)
        if max_drawdown_raw is not None
        else None
    )
    latest_return = latest_1y.get("return_pct")
    latest_return_raw = latest_1y.get("return_pct_raw")
    calmar_raw = (
        float(latest_return_raw) / abs(float(max_drawdown_raw))
        if latest_return_raw is not None
        and max_drawdown_raw not in {None, 0}
        else None
    )
    calmar = (
        float(latest_return) / abs(float(max_drawdown))
        if latest_return is not None and max_drawdown not in {None, 0}
        else None
    )
    latest_rolling = rolling_1y_windows[-1] if rolling_1y_windows else {}
    latest_rolling_drawdown = latest_rolling.get("max_drawdown_pct")
    latest_rolling_drawdown_raw = latest_rolling.get(
        "max_drawdown_pct_raw"
    )
    latest_rolling_return_raw = latest_rolling.get("return_pct_raw")
    calmar_latest_12m_raw = (
        float(latest_rolling_return_raw)
        / abs(float(latest_rolling_drawdown_raw))
        if latest_rolling_return_raw is not None
        and latest_rolling_drawdown_raw not in {None, 0}
        else None
    )
    calmar_latest_12m = (
        float(latest_rolling.get("return_pct"))
        / abs(float(latest_rolling_drawdown))
        if latest_rolling.get("return_pct") is not None
        and latest_rolling_drawdown not in {None, 0}
        else None
    )
    trade_win_rate_raw = (
        len(wins) / len(selected) * 100
        if selected
        else None
    )
    compounded_return_raw = (
        (metric_equity_points[-1]["equity"] - 1) * 100
        if metric_equity_points
        else None
    )

    return {
        "selected_trade_count": len(selected),
        "signal_days": len(basket_returns),
        "exposure_multiplier": round(float(exposure_multiplier), 2),
        "annual_financing_rate_pct": round(float(annual_financing_rate_pct), 2),
        "roundtrip_cost_bps": round(float(roundtrip_cost_bps), 2),
        "slippage_bps": round(float(slippage_bps), 2),
        "trade_return_basis": "net_after_roundtrip_cost_and_slippage",
        "quality_trade_cost_pct": round(quality_cost_pct, 4),
        "capital_model": capital_model,
        "gate_metric_basis": "unrounded_float64",
        "trade_win_count": len(wins),
        "trade_nonwin_count": len(selected) - len(wins),
        "trade_win_rate_pct": round(trade_win_rate_raw, 2)
        if trade_win_rate_raw is not None
        else None,
        "trade_win_rate_pct_raw": trade_win_rate_raw,
        "trade_payoff_ratio": round(payoff_ratio, 2) if payoff_ratio is not None else None,
        "trade_payoff_ratio_raw": payoff_ratio,
        "trade_profit_factor": round(profit_factor, 2)
        if profit_factor is not None
        else None,
        "trade_profit_factor_raw": profit_factor,
        "trade_avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        "trade_median_return_pct": round(float(pd.Series(returns).median()), 2)
        if returns
        else None,
        "trade_avg_max_adverse_pct": round(
            sum(item["max_adverse_pct"] for item in adverse) / len(adverse),
            2,
        )
        if adverse
        else None,
        "portfolio_compounded_return_pct": round(compounded_return_raw, 2)
        if compounded_return_raw is not None
        else None,
        "portfolio_compounded_return_pct_raw": compounded_return_raw,
        "portfolio_max_drawdown_pct": max_drawdown,
        "portfolio_max_drawdown_pct_raw": max_drawdown_raw,
        "portfolio_calmar_latest_1y": round(calmar, 2) if calmar is not None else None,
        "portfolio_calmar_latest_1y_raw": calmar_raw,
        "portfolio_calmar_latest_1y_method": "latest_365d_return_over_full_history_drawdown_legacy",
        "calmar_latest_12m": round(calmar_latest_12m, 2)
        if calmar_latest_12m is not None
        else None,
        "calmar_latest_12m_raw": calmar_latest_12m_raw,
        "calmar_latest_12m_method": "latest_365d_return_over_latest_365d_drawdown",
        "rolling_1y_latest_return_pct": latest_return,
        "rolling_1y_latest_return_pct_raw": latest_return_raw,
        "rolling_1y_latest_max_drawdown_pct": latest_rolling_drawdown,
        "rolling_1y_latest_max_drawdown_pct_raw": (
            latest_rolling_drawdown_raw
        ),
        "rolling_1y_windows": rolling_1y_windows,
        "rolling_1y_latest_full_window": rolling_full_window,
        "rolling_1y_latest_trade_count": rolling_trade_count,
        "rolling_1y_latest_active_position_days": rolling_position_days,
        "rolling_1y_evaluation_start_date": (
            evaluation_start.strftime("%Y-%m-%d")
            if evaluation_start is not None
            else None
        ),
        "rolling_1y_evaluation_end_date": (
            evaluation_end.strftime("%Y-%m-%d")
            if evaluation_end is not None
            else None
        ),
        "rolling_1y_evaluation_session_count": (
            len(rolling_equity_points)
            if evaluation_grid_included
            else None
        ),
        "rolling_1y_evaluation_session_grid_included": (
            evaluation_grid_included
        ),
        "rolling_1y_cash_anchor_included": cash_anchor_included,
    }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(result):
        return default
    return result


def _trade_sweep_tags(trade: Dict[str, Any]) -> Set[str]:
    tags = {str(tag) for tag in (trade.get("signal_tags") or [])}
    tags.update(build_candidate_context_tags(trade))
    tags.update(build_entry_executability_tags(trade.get("entry_executability") or {}))
    tags.update(build_proxy_market_tags(trade.get("relative_strength") or {}))
    tags.update(build_announcement_tags(trade.get("announcement_context") or {}))
    tags.update(_holding_calendar_gap_tags(trade))
    return tags


def _holding_calendar_gap_tags(trade: Dict[str, Any]) -> Set[str]:
    path = trade.get("mark_to_market_path") or []
    dates = []
    for mark in path:
        date_text = str(mark.get("date") or "")[:10]
        if not date_text:
            continue
        try:
            dates.append(_date_value(date_text))
        except ValueError:
            continue
    dates = sorted(set(dates))
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    max_gap = max(gaps) if gaps else 0
    tags = set()
    if max_gap <= 4:
        tags.add("holding_calendar_gap_lte_4")
    if max_gap <= 6:
        tags.add("holding_calendar_gap_lte_6")
    if max_gap >= 5:
        tags.add("holding_calendar_gap_gte_5")
    if max_gap >= 7:
        tags.add("holding_calendar_gap_gte_7")
    if max_gap >= 10:
        tags.add("holding_calendar_gap_gte_10")
    return tags


def _truncate_trade_before_calendar_gap(trade: Dict[str, Any], min_gap_days: int) -> Dict[str, Any]:
    if not min_gap_days or min_gap_days <= 0:
        return trade
    path = trade.get("mark_to_market_path") or []
    normalized_path = []
    for mark in path:
        date_text = str(mark.get("date") or "")[:10]
        if not date_text:
            continue
        normalized_mark = dict(mark)
        normalized_mark["date"] = date_text
        normalized_path.append(normalized_mark)
    normalized_path.sort(key=lambda mark: mark["date"])

    if len(normalized_path) < 2:
        return trade

    for index, (left, right) in enumerate(zip(normalized_path, normalized_path[1:])):
        gap_days = (_date_value(right["date"]) - _date_value(left["date"])).days
        if gap_days >= min_gap_days:
            return _replace_trade_mark_path(
                trade,
                normalized_path[: index + 1],
                exit_reason="pre_calendar_gap_exit",
            )
    return trade


def _apply_prior_high_trailing_stop(
    trade: Dict[str, Any],
    trailing_stop_pct: float,
    activation_pct: float = 0.0,
) -> Dict[str, Any]:
    if not trailing_stop_pct or trailing_stop_pct <= 0:
        return trade
    path = trade.get("mark_to_market_path") or []
    normalized_path = []
    for mark in path:
        date_text = str(mark.get("date") or "")[:10]
        if not date_text:
            continue
        normalized_mark = dict(mark)
        normalized_mark["date"] = date_text
        normalized_path.append(normalized_mark)
    normalized_path.sort(key=lambda mark: mark["date"])

    if len(normalized_path) < 2:
        return trade

    high_watermark_pct = 0.0
    kept_path = []
    activation_pct = max(float(activation_pct or 0.0), 0.0)
    trailing_stop_pct = abs(float(trailing_stop_pct))
    for index, mark in enumerate(normalized_path):
        close_return = _num(mark.get("close_return_pct"))
        open_return = _num(mark.get("open_return_pct"), close_return)
        high_return = _num(mark.get("high_return_pct"), close_return)
        low_return = _num(mark.get("low_return_pct"), close_return)

        if index > 0 and high_watermark_pct >= activation_pct:
            stop_return = ((1 + high_watermark_pct / 100) * (1 - trailing_stop_pct / 100) - 1) * 100
            if open_return <= stop_return or low_return <= stop_return:
                exit_return = open_return if open_return <= stop_return else stop_return
                exit_mark = dict(mark)
                exit_mark["close_return_pct"] = round(exit_return, 4)
                exit_mark["low_return_pct"] = round(exit_return, 4)
                exit_mark["high_return_pct"] = round(max(open_return, exit_return), 4)
                kept_path.append(exit_mark)
                return _replace_trade_mark_path(
                    trade,
                    kept_path,
                    exit_reason="prior_high_trailing_stop",
                )

        kept_path.append(mark)
        high_watermark_pct = max(high_watermark_pct, high_return)

    return trade


def _apply_partial_profit_lock(
    trade: Dict[str, Any],
    activation_pct: float,
    fraction: float,
) -> Dict[str, Any]:
    if not activation_pct or activation_pct <= 0 or not fraction or fraction <= 0:
        return trade
    path = trade.get("mark_to_market_path") or []
    normalized_path = []
    for mark in path:
        date_text = str(mark.get("date") or "")[:10]
        if not date_text:
            continue
        normalized_mark = dict(mark)
        normalized_mark["date"] = date_text
        normalized_path.append(normalized_mark)
    normalized_path.sort(key=lambda mark: mark["date"])

    if len(normalized_path) < 2:
        return trade

    activation_pct = max(float(activation_pct or 0.0), 0.0)
    fraction = min(max(float(fraction or 0.0), 0.0), 1.0)
    if fraction <= 0:
        return trade

    high_watermark_pct = 0.0
    locked_return = None
    adjusted_path = []
    for index, mark in enumerate(normalized_path):
        close_return = _num(mark.get("close_return_pct"))
        open_return = _num(mark.get("open_return_pct"), close_return)
        high_return = _num(mark.get("high_return_pct"), close_return)

        if locked_return is None and index > 0 and high_watermark_pct >= activation_pct:
            locked_return = open_return

        if locked_return is not None:
            adjusted_mark = dict(mark)
            for key in [
                "open_return_pct",
                "high_return_pct",
                "low_return_pct",
                "close_return_pct",
            ]:
                raw_return = _num(mark.get(key), close_return)
                effective_return = locked_return * fraction + raw_return * (1 - fraction)
                adjusted_mark[key] = round(effective_return, 4)
            adjusted_path.append(adjusted_mark)
            if fraction >= 1.0:
                return _replace_trade_mark_path(
                    trade,
                    adjusted_path,
                    exit_reason="profit_lock_exit",
                )
        else:
            adjusted_path.append(mark)
            high_watermark_pct = max(high_watermark_pct, high_return)

    if locked_return is None:
        return trade
    return _replace_trade_mark_path(
        trade,
        adjusted_path,
        exit_reason="partial_profit_lock",
    )


def _replace_trade_mark_path(
    trade: Dict[str, Any],
    path: List[Dict[str, Any]],
    exit_reason: str,
) -> Dict[str, Any]:
    if not path:
        return trade
    adjusted = deepcopy(trade)
    adjusted["mark_to_market_path"] = deepcopy(path)
    last_mark = path[-1]
    close_return = _num(last_mark.get("close_return_pct"))
    adverse_returns = [
        _num(mark.get("low_return_pct"), _num(mark.get("close_return_pct"))) for mark in path
    ]
    adjusted["exit_date"] = last_mark["date"]
    adjusted["return_pct"] = close_return
    adjusted["holding_days"] = len(path)
    adjusted["max_adverse_pct"] = min(adverse_returns) if adverse_returns else close_return
    adjusted["exit_reason"] = exit_reason
    return adjusted


def _history_returns_for_symbol(
    symbol: str,
    cache_dir: str,
    history_lookback_days: int,
    returns_cache: Dict[str, List[tuple]],
) -> List[tuple]:
    cache_key = "%s:%s:%s" % (cache_dir, history_lookback_days, symbol)
    if cache_key in returns_cache:
        return returns_cache[cache_key]

    cache_path = Path(cache_dir) / ("a_%s_%s_qfq.json" % (symbol, history_lookback_days))
    payload = read_json(str(cache_path), {})
    records = payload.get("records") if isinstance(payload, dict) else []
    returns = []
    previous_close = None
    for record in records or []:
        date_text = str(record.get("date") or "")[:10]
        close = _num(record.get("close"), None)
        if not date_text or close is None or close <= 0:
            continue
        if previous_close and previous_close > 0:
            returns.append((date_text, close / previous_close - 1))
        previous_close = close
    returns_cache[cache_key] = returns
    return returns


def _correlation_as_of(
    left_symbol: str,
    right_symbol: str,
    as_of: str,
    cache_dir: str,
    lookback_days: int,
    min_periods: int,
    history_lookback_days: int,
    returns_cache: Dict[str, List[tuple]],
    correlation_cache: Dict[tuple, Any] = None,
) -> float:
    if not left_symbol or not right_symbol or left_symbol == right_symbol:
        return None
    cache_key = None
    if correlation_cache is not None:
        left_key, right_key = sorted([left_symbol, right_symbol])
        cache_key = (
            cache_dir,
            lookback_days,
            min_periods,
            history_lookback_days,
            as_of,
            left_key,
            right_key,
        )
        if cache_key in correlation_cache:
            return correlation_cache[cache_key]
    left_returns = [
        item
        for item in _history_returns_for_symbol(
            left_symbol, cache_dir, history_lookback_days, returns_cache
        )
        if item[0] <= as_of
    ]
    right_returns = [
        item
        for item in _history_returns_for_symbol(
            right_symbol, cache_dir, history_lookback_days, returns_cache
        )
        if item[0] <= as_of
    ]
    if not left_returns or not right_returns:
        if cache_key is not None:
            correlation_cache[cache_key] = None
        return None
    left_by_date = dict(left_returns[-lookback_days * 2 :])
    right_by_date = dict(right_returns[-lookback_days * 2 :])
    dates = sorted(set(left_by_date) & set(right_by_date))[-lookback_days:]
    if len(dates) < min_periods:
        if cache_key is not None:
            correlation_cache[cache_key] = None
        return None
    left_values = [left_by_date[date] for date in dates]
    right_values = [right_by_date[date] for date in dates]
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    left_var = sum((value - left_mean) ** 2 for value in left_values)
    right_var = sum((value - right_mean) ** 2 for value in right_values)
    if left_var <= 0 or right_var <= 0:
        if cache_key is not None:
            correlation_cache[cache_key] = None
        return None
    covariance = sum(
        (left - left_mean) * (right - right_mean) for left, right in zip(left_values, right_values)
    )
    result = covariance / ((left_var * right_var) ** 0.5)
    if cache_key is not None:
        correlation_cache[cache_key] = result
    return result


def _select_with_correlation_budget(
    by_signal_date: Dict[str, List[Dict[str, Any]]],
    top_n: int,
    symbol_cooldown_days: int,
    max_active_positions: int,
    correlation_threshold: float,
    correlation_lookback_days: int,
    correlation_cache_dir: str,
    correlation_min_periods: int,
    correlation_history_lookback_days: int,
    returns_cache: Dict[str, List[tuple]] = None,
    correlation_cache: Dict[tuple, Any] = None,
) -> tuple:
    selected: List[Dict[str, Any]] = []
    active_positions: List[Dict[str, Any]] = []
    last_exit_by_symbol: Dict[str, Any] = {}
    returns_cache = returns_cache if returns_cache is not None else {}
    correlation_cache = correlation_cache if correlation_cache is not None else {}
    skipped_for_correlation = 0

    for signal_date in sorted(by_signal_date):
        trades = sorted(
            by_signal_date[signal_date], key=lambda item: item["rank_score"], reverse=True
        )
        signal_day = _date_value(signal_date)
        active_positions = [
            item for item in active_positions if _date_value(item["exit_date"]) >= signal_day
        ]
        day_count = 0
        active_symbols = {item["symbol"] for item in active_positions}

        for trade in trades:
            symbol = str(trade.get("symbol") or "")
            if symbol in active_symbols:
                continue
            previous_exit = last_exit_by_symbol.get(symbol)
            if previous_exit and symbol_cooldown_days > 0:
                if (signal_day - previous_exit).days < symbol_cooldown_days:
                    continue
            if max_active_positions > 0 and len(active_positions) >= max_active_positions:
                break

            correlation_blocked = False
            for active in active_positions:
                correlation = _correlation_as_of(
                    symbol,
                    str(active.get("symbol") or ""),
                    signal_date,
                    correlation_cache_dir,
                    correlation_lookback_days,
                    correlation_min_periods,
                    correlation_history_lookback_days,
                    returns_cache,
                    correlation_cache,
                )
                if correlation is not None and correlation >= correlation_threshold:
                    correlation_blocked = True
                    break
            if correlation_blocked:
                skipped_for_correlation += 1
                continue

            selected.append(trade)
            active_positions.append(trade)
            active_symbols.add(symbol)
            last_exit_by_symbol[symbol] = _date_value(trade["exit_date"])
            day_count += 1
            if day_count >= top_n:
                break

    return selected, skipped_for_correlation


def _market_label(levels: Set[str] = None) -> str:
    if not levels:
        return "all_market_levels"
    return "market_" + "_".join(sorted(levels))


def _filter_label(
    tags: Iterable[str],
    market_levels: Set[str] = None,
    excluded_tags: Iterable[str] = None,
) -> str:
    tag_part = "+".join(sorted(tags)) if tags else "no_tag_filter"
    excluded_part = ""
    if excluded_tags:
        excluded_part = "|exclude_" + "+".join(sorted(excluded_tags))
    return "%s|%s%s" % (tag_part, _market_label(market_levels), excluded_part)


def build_default_sweep_specs(
    available_tags: Set[str],
    max_filter_size: int = 3,
) -> List[Dict[str, Any]]:
    tags = [tag for tag in DEFAULT_SWEEP_TAGS if tag in available_tags]
    specs = []
    seen = set()
    for size in range(0, max(0, max_filter_size) + 1):
        for tag_combo in combinations(tags, size):
            required_tags = set(tag_combo)
            for market_levels in DEFAULT_MARKET_LEVELS:
                key = (tuple(sorted(required_tags)), tuple(sorted(market_levels or [])))
                if key in seen:
                    continue
                seen.add(key)
                specs.append(
                    {
                        "label": _filter_label(required_tags, market_levels),
                        "required_signal_tags": sorted(required_tags),
                        "market_levels": sorted(market_levels) if market_levels else [],
                    }
                )
    return specs


def sweep_qualified_trades(
    qualified_trades: List[Dict[str, Any]],
    hold_days: int,
    top_n: int = 10,
    symbol_cooldown_days: int = 0,
    max_active_positions: int = 0,
    min_trades: int = 20,
    max_filter_size: int = 3,
    target_win_rate_pct: float = 52.0,
    target_drawdown_pct: float = goal.TARGET_MAX_DRAWDOWN_PCT,
    target_one_year_return_pct: float = goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
    target_profit_factor: float = 1.3,
    target_calmar: float = 1.5,
    exposure_multipliers: List[float] = None,
    annual_financing_rate_pct: float = 0.0,
    roundtrip_cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
    capital_model: str = "signal-day",
    required_signal_tags: List[str] = None,
    excluded_signal_tags: List[str] = None,
    market_levels: List[str] = None,
    force_exposure_multipliers: bool = False,
    pre_exit_calendar_gap_days: int = 0,
    prior_high_trailing_stop_pct: float = None,
    prior_high_trailing_activation_pct: float = 0.0,
    partial_profit_activation_pct: float = None,
    partial_profit_fraction: float = 0.0,
    correlation_threshold: float = None,
    correlation_lookback_days: int = 60,
    correlation_cache_dir: str = "data/research_cache",
    correlation_min_periods: int = 20,
    correlation_history_lookback_days: int = 620,
    fixed_spec: bool = False,
) -> Dict[str, Any]:
    if capital_model not in {"signal-day", "slot-exit", "slot-daily"}:
        capital_model = "signal-day"
    exposure_multipliers = exposure_multipliers or [1.0]
    exposure_multipliers = sorted(
        {round(max(float(item), 0.0), 2) for item in exposure_multipliers if item}
    )
    if 1.0 not in exposure_multipliers and not force_exposure_multipliers:
        exposure_multipliers.insert(0, 1.0)
    if not exposure_multipliers:
        exposure_multipliers = [1.0]
    pre_exit_calendar_gap_days = max(int(pre_exit_calendar_gap_days or 0), 0)
    prior_high_trailing_stop_pct = (
        abs(float(prior_high_trailing_stop_pct))
        if prior_high_trailing_stop_pct is not None and float(prior_high_trailing_stop_pct) > 0
        else None
    )
    prior_high_trailing_activation_pct = max(float(prior_high_trailing_activation_pct or 0.0), 0.0)
    partial_profit_activation_pct = (
        abs(float(partial_profit_activation_pct))
        if partial_profit_activation_pct is not None and float(partial_profit_activation_pct) > 0
        else None
    )
    partial_profit_fraction = min(max(float(partial_profit_fraction or 0.0), 0.0), 1.0)
    correlation_enabled = correlation_threshold is not None and float(correlation_threshold) > 0
    correlation_lookback_days = max(int(correlation_lookback_days or 0), 1)
    correlation_min_periods = max(int(correlation_min_periods or 0), 2)
    correlation_history_lookback_days = max(
        int(correlation_history_lookback_days or 0), correlation_lookback_days
    )
    if pre_exit_calendar_gap_days:
        qualified_trades = [
            _truncate_trade_before_calendar_gap(trade, pre_exit_calendar_gap_days)
            for trade in qualified_trades
        ]
    if prior_high_trailing_stop_pct:
        qualified_trades = [
            _apply_prior_high_trailing_stop(
                trade,
                prior_high_trailing_stop_pct,
                prior_high_trailing_activation_pct,
            )
            for trade in qualified_trades
        ]
    if partial_profit_activation_pct and partial_profit_fraction:
        qualified_trades = [
            _apply_partial_profit_lock(
                trade,
                partial_profit_activation_pct,
                partial_profit_fraction,
            )
            for trade in qualified_trades
        ]
    trade_tag_pairs = [(trade, _trade_sweep_tags(trade)) for trade in qualified_trades]
    available_tags = {str(tag) for _trade, tag_set in trade_tag_pairs for tag in tag_set}
    all_indices = set(range(len(trade_tag_pairs)))
    tag_index: Dict[str, Set[int]] = defaultdict(set)
    market_index: Dict[str, Set[int]] = defaultdict(set)
    for index, (trade, tag_set) in enumerate(trade_tag_pairs):
        for tag in tag_set:
            tag_index[str(tag)].add(index)
        market_index[str(trade.get("market_level") or "unknown")].add(index)
    fixed_excluded_tags = {
        str(tag).strip() for tag in (excluded_signal_tags or []) if str(tag).strip()
    }
    if (
        fixed_spec
        or required_signal_tags is not None
        or market_levels is not None
        or fixed_excluded_tags
    ):
        fixed_tags = {str(tag).strip() for tag in (required_signal_tags or []) if str(tag).strip()}
        fixed_market_levels = {
            str(level).strip() for level in (market_levels or []) if str(level).strip()
        }
        specs = [
            {
                "label": _filter_label(fixed_tags, fixed_market_levels, fixed_excluded_tags),
                "required_signal_tags": sorted(fixed_tags),
                "excluded_signal_tags": sorted(fixed_excluded_tags),
                "market_levels": sorted(fixed_market_levels),
            }
        ]
    else:
        specs = build_default_sweep_specs(available_tags, max_filter_size=max_filter_size)
    rows = []
    correlation_returns_cache: Dict[str, List[tuple]] = {}
    correlation_value_cache: Dict[tuple, Any] = {}

    for spec in specs:
        required_tags = set(spec.get("required_signal_tags") or [])
        excluded_tags = set(spec.get("excluded_signal_tags") or [])
        market_levels = set(spec.get("market_levels") or [])
        candidate_indices = all_indices
        if required_tags:
            required_sets = [tag_index.get(tag, set()) for tag in required_tags]
            if not required_sets or any(not item for item in required_sets):
                continue
            candidate_indices = set(min(required_sets, key=len))
            for index_set in required_sets:
                candidate_indices &= index_set
        if market_levels:
            allowed_market_indices = set()
            for level in market_levels:
                allowed_market_indices |= market_index.get(level, set())
            candidate_indices = candidate_indices & allowed_market_indices
        if excluded_tags:
            excluded_indices = set()
            for tag in excluded_tags:
                excluded_indices |= tag_index.get(tag, set())
            candidate_indices = candidate_indices - excluded_indices
        if len(candidate_indices) < min_trades:
            continue
        filtered = [trade_tag_pairs[index][0] for index in sorted(candidate_indices)]

        by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for trade in filtered:
            by_signal_date[trade["signal_date"]].append(trade)
        if correlation_enabled:
            selected, correlation_skip_count = _select_with_correlation_budget(
                by_signal_date,
                top_n,
                symbol_cooldown_days=symbol_cooldown_days,
                max_active_positions=max_active_positions,
                correlation_threshold=float(correlation_threshold),
                correlation_lookback_days=correlation_lookback_days,
                correlation_cache_dir=correlation_cache_dir,
                correlation_min_periods=correlation_min_periods,
                correlation_history_lookback_days=correlation_history_lookback_days,
                returns_cache=correlation_returns_cache,
                correlation_cache=correlation_value_cache,
            )
        else:
            selected = _select_with_portfolio_controls(
                by_signal_date,
                top_n,
                symbol_cooldown_days=symbol_cooldown_days,
                max_active_positions=max_active_positions,
            )
            correlation_skip_count = 0
        if len(selected) < min_trades:
            continue

        base_metrics = None
        base_win_drawdown_pass = False
        for exposure_multiplier in exposure_multipliers:
            if (
                exposure_multiplier != 1.0
                and not base_win_drawdown_pass
                and not force_exposure_multipliers
            ):
                continue
            metrics = _trade_metrics(
                selected,
                hold_days,
                max_active_positions=max_active_positions,
                exposure_multiplier=exposure_multiplier,
                annual_financing_rate_pct=annual_financing_rate_pct,
                roundtrip_cost_bps=roundtrip_cost_bps,
                slippage_bps=slippage_bps,
                capital_model=capital_model,
            )
            max_drawdown = abs(metrics.get("portfolio_max_drawdown_pct") or 0)
            win_rate = metrics.get("trade_win_rate_pct") or 0
            rolling_return = metrics.get("rolling_1y_latest_return_pct")
            win_drawdown_pass = bool(
                win_rate >= target_win_rate_pct and max_drawdown <= target_drawdown_pct
            )
            if exposure_multiplier == 1.0:
                base_metrics = metrics
                base_win_drawdown_pass = win_drawdown_pass
            one_year_return_pass = bool(
                rolling_return is not None and rolling_return >= target_one_year_return_pct
            )
            profit_factor_pass = bool(
                (
                    metrics.get("trade_profit_factor") is not None
                    and float(metrics["trade_profit_factor"]) >= target_profit_factor
                )
                or int(metrics.get("trade_nonwin_count") or 0) == 0
            )
            calmar_value = metrics.get("calmar_latest_12m")
            if calmar_value is None:
                calmar_value = metrics.get("portfolio_calmar_latest_1y")
            calmar_pass = bool(
                (
                    calmar_value is not None
                    and float(calmar_value) >= target_calmar
                )
                or (
                    rolling_return is not None
                    and float(rolling_return) > 0
                    and float(metrics.get("portfolio_max_drawdown_pct") or 0) == 0
                )
            )
            quality_pass = profit_factor_pass and calmar_pass
            rows.append(
                {
                    **spec,
                    **metrics,
                    "pre_exit_calendar_gap_days": pre_exit_calendar_gap_days or None,
                    "prior_high_trailing_stop_pct": prior_high_trailing_stop_pct,
                    "prior_high_trailing_activation_pct": prior_high_trailing_activation_pct
                    if prior_high_trailing_stop_pct
                    else None,
                    "partial_profit_activation_pct": partial_profit_activation_pct,
                    "partial_profit_fraction": partial_profit_fraction
                    if partial_profit_activation_pct
                    else None,
                    "excluded_signal_tags": sorted(fixed_excluded_tags)
                    if fixed_excluded_tags
                    else None,
                    "correlation_threshold": round(float(correlation_threshold), 4)
                    if correlation_enabled
                    else None,
                    "correlation_lookback_days": correlation_lookback_days
                    if correlation_enabled
                    else None,
                    "correlation_skip_count": correlation_skip_count
                    if correlation_enabled
                    else None,
                    "target_win_drawdown_pass": win_drawdown_pass,
                    "target_one_year_return_pass": one_year_return_pass,
                    "target_quality_pass": quality_pass,
                    "target_all_pass": bool(
                        win_drawdown_pass and one_year_return_pass and quality_pass
                    ),
                    "target_gap_1y_return_pct": round(
                        target_one_year_return_pct - rolling_return, 2
                    )
                    if rolling_return is not None
                    else None,
                }
            )
        if (
            base_metrics is None
            and 1.0 not in exposure_multipliers
            and not force_exposure_multipliers
        ):
            metrics = _trade_metrics(
                selected,
                hold_days,
                max_active_positions=max_active_positions,
                exposure_multiplier=1.0,
                annual_financing_rate_pct=annual_financing_rate_pct,
                roundtrip_cost_bps=roundtrip_cost_bps,
                slippage_bps=slippage_bps,
                capital_model=capital_model,
            )
            max_drawdown = abs(metrics.get("portfolio_max_drawdown_pct") or 0)
            win_rate = metrics.get("trade_win_rate_pct") or 0
            rolling_return = metrics.get("rolling_1y_latest_return_pct")
            win_drawdown_pass = bool(
                win_rate >= target_win_rate_pct and max_drawdown <= target_drawdown_pct
            )
            one_year_return_pass = bool(
                rolling_return is not None and rolling_return >= target_one_year_return_pct
            )
            profit_factor_pass = bool(
                (
                    metrics.get("trade_profit_factor") is not None
                    and float(metrics["trade_profit_factor"]) >= target_profit_factor
                )
                or int(metrics.get("trade_nonwin_count") or 0) == 0
            )
            calmar_value = metrics.get("calmar_latest_12m")
            if calmar_value is None:
                calmar_value = metrics.get("portfolio_calmar_latest_1y")
            calmar_pass = bool(
                (
                    calmar_value is not None
                    and float(calmar_value) >= target_calmar
                )
                or (
                    rolling_return is not None
                    and float(rolling_return) > 0
                    and float(metrics.get("portfolio_max_drawdown_pct") or 0) == 0
                )
            )
            quality_pass = profit_factor_pass and calmar_pass
            rows.append(
                {
                    **spec,
                    **metrics,
                    "pre_exit_calendar_gap_days": pre_exit_calendar_gap_days or None,
                    "prior_high_trailing_stop_pct": prior_high_trailing_stop_pct,
                    "prior_high_trailing_activation_pct": prior_high_trailing_activation_pct
                    if prior_high_trailing_stop_pct
                    else None,
                    "partial_profit_activation_pct": partial_profit_activation_pct,
                    "partial_profit_fraction": partial_profit_fraction
                    if partial_profit_activation_pct
                    else None,
                    "excluded_signal_tags": sorted(fixed_excluded_tags)
                    if fixed_excluded_tags
                    else None,
                    "correlation_threshold": round(float(correlation_threshold), 4)
                    if correlation_enabled
                    else None,
                    "correlation_lookback_days": correlation_lookback_days
                    if correlation_enabled
                    else None,
                    "correlation_skip_count": correlation_skip_count
                    if correlation_enabled
                    else None,
                    "target_win_drawdown_pass": win_drawdown_pass,
                    "target_one_year_return_pass": one_year_return_pass,
                    "target_quality_pass": quality_pass,
                    "target_all_pass": bool(
                        win_drawdown_pass and one_year_return_pass and quality_pass
                    ),
                    "target_gap_1y_return_pct": round(
                        target_one_year_return_pct - rolling_return, 2
                    )
                    if rolling_return is not None
                    else None,
                }
            )

    for row in rows:
        windows = row.get("rolling_1y_windows") or []
        row["target_rolling_12m_stability_pass"] = bool(
            windows
            and all(
                window.get("return_pct") is not None
                and float(window["return_pct"]) >= target_one_year_return_pct
                and window.get("max_drawdown_pct") is not None
                and abs(float(window["max_drawdown_pct"])) <= target_drawdown_pct
                and window.get("payoff_ratio") is not None
                and float(window["payoff_ratio"]) >= target_profit_factor
                and window.get("profit_factor") is not None
                and float(window["profit_factor"]) >= target_profit_factor
                and window.get("calmar") is not None
                and float(window["calmar"]) >= target_calmar
                for window in windows
            )
        )

    rows.sort(
        key=lambda item: (
            item["target_all_pass"],
            item["target_win_drawdown_pass"],
            item.get("rolling_1y_latest_return_pct") or -999,
            item.get("trade_win_rate_pct") or 0,
            item.get("selected_trade_count") or 0,
        ),
        reverse=True,
    )

    def brief(row: Dict[str, Any] = None) -> Dict[str, Any]:
        if not row:
            return {}
        keys = [
            "label",
            "required_signal_tags",
            "excluded_signal_tags",
            "market_levels",
            "exposure_multiplier",
            "annual_financing_rate_pct",
            "roundtrip_cost_bps",
            "slippage_bps",
            "capital_model",
            "pre_exit_calendar_gap_days",
            "prior_high_trailing_stop_pct",
            "prior_high_trailing_activation_pct",
            "partial_profit_activation_pct",
            "partial_profit_fraction",
            "correlation_threshold",
            "correlation_lookback_days",
            "correlation_skip_count",
            "selected_trade_count",
            "trade_win_count",
            "trade_nonwin_count",
            "trade_win_rate_pct",
            "trade_payoff_ratio",
            "trade_profit_factor",
            "portfolio_max_drawdown_pct",
            "portfolio_calmar_latest_1y",
            "calmar_latest_12m",
            "rolling_1y_latest_return_pct",
            "rolling_1y_latest_max_drawdown_pct",
            "rolling_1y_windows",
            "target_win_drawdown_pass",
            "target_one_year_return_pass",
            "target_quality_pass",
            "target_rolling_12m_stability_pass",
            "target_all_pass",
            "target_gap_1y_return_pct",
        ]
        return {key: row.get(key) for key in keys}

    win_drawdown_rows = [row for row in rows if row.get("target_win_drawdown_pass")]
    all_pass_rows = [row for row in rows if row.get("target_all_pass")]
    rolling_stability_rows = [
        row for row in rows if row.get("target_rolling_12m_stability_pass")
    ]
    best_by_win_rate = max(
        rows,
        key=lambda item: (
            item.get("trade_win_rate_pct") or 0,
            item.get("selected_trade_count") or 0,
            item.get("rolling_1y_latest_return_pct") or -999,
        ),
        default=None,
    )
    best_by_rolling_return = max(
        rows,
        key=lambda item: (
            item.get("rolling_1y_latest_return_pct") or -999,
            item.get("trade_win_rate_pct") or 0,
            item.get("selected_trade_count") or 0,
        ),
        default=None,
    )
    return {
        "qualified_trade_count": len(qualified_trades),
        "available_tag_count": len(available_tags),
        "available_tags": sorted(available_tags),
        "spec_count": len(specs),
        "returned_count": len(rows),
        "target_win_drawdown_pass_count": len(win_drawdown_rows),
        "target_all_pass_count": len(all_pass_rows),
        "target_rolling_12m_stability_pass_count": len(rolling_stability_rows),
        "target_win_rate_pct": target_win_rate_pct,
        "target_drawdown_pct": target_drawdown_pct,
        "target_one_year_return_pct": target_one_year_return_pct,
        "target_profit_factor": target_profit_factor,
        "target_calmar": target_calmar,
        "min_trades": min_trades,
        "exposure_multipliers": exposure_multipliers,
        "annual_financing_rate_pct": round(float(annual_financing_rate_pct), 2),
        "roundtrip_cost_bps": round(float(roundtrip_cost_bps), 2),
        "slippage_bps": round(float(slippage_bps), 2),
        "capital_model": capital_model,
        "pre_exit_calendar_gap_days": pre_exit_calendar_gap_days or None,
        "prior_high_trailing_stop_pct": prior_high_trailing_stop_pct,
        "prior_high_trailing_activation_pct": prior_high_trailing_activation_pct
        if prior_high_trailing_stop_pct
        else None,
        "partial_profit_activation_pct": partial_profit_activation_pct,
        "partial_profit_fraction": partial_profit_fraction
        if partial_profit_activation_pct
        else None,
        "excluded_signal_tags": sorted(fixed_excluded_tags) if fixed_excluded_tags else None,
        "correlation_threshold": round(float(correlation_threshold), 4)
        if correlation_enabled
        else None,
        "correlation_lookback_days": correlation_lookback_days if correlation_enabled else None,
        "correlation_min_periods": correlation_min_periods if correlation_enabled else None,
        "diagnostics": {
            "best_target_all": brief(all_pass_rows[0] if all_pass_rows else None),
            "best_target_win_drawdown": brief(win_drawdown_rows[0] if win_drawdown_rows else None),
            "best_by_win_rate": brief(best_by_win_rate),
            "best_by_rolling_return": brief(best_by_rolling_return),
        },
        "top": rows[:50],
    }
