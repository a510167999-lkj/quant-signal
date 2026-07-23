"""权益曲线与资本模型数学。

从 research_backtest.py 抽出的纯计算块：basket / slot-exit / slot-daily 三种资本
模型下的权益曲线与回撤。逐字搬移，未改任何逻辑。
"""
from collections import defaultdict
from typing import Any, Dict, List

from app.research_common import _num


def _max_drawdown_pct(equity_curve: List[float]) -> float:
    peak = equity_curve[0] if equity_curve else 1.0
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, value / peak - 1)
    return round(max_drawdown * 100, 2)


def _max_drawdown_pct_from_points(
    equity_points: List[Dict[str, Any]],
    start_equity: float = 1.0,
) -> float:
    return round(
        _max_drawdown_pct_from_points_raw(
            equity_points,
            start_equity=start_equity,
        ),
        2,
    )


def _max_drawdown_pct_from_points_raw(
    equity_points: List[Dict[str, Any]],
    start_equity: float = 1.0,
) -> float:
    peak = start_equity or 1.0
    max_drawdown = 0.0
    for point in equity_points:
        close_equity = _num(point.get("equity"), peak)
        drawdown_equity = _num(point.get("drawdown_equity"), close_equity)
        if peak:
            max_drawdown = min(max_drawdown, drawdown_equity / peak - 1)
        peak = max(peak, close_equity)
    return max_drawdown * 100


def _equity_points_from_basket_returns(
    basket_returns: List[Dict[str, Any]],
    hold_days: int,
    exposure_multiplier: float = 1.0,
    annual_financing_rate_pct: float = 0.0,
    roundtrip_cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> List[Dict[str, Any]]:
    compound = 1.0
    points = []
    exposure = max(float(exposure_multiplier), 0)
    daily_financing_cost = max(exposure - 1, 0) * max(float(annual_financing_rate_pct), 0) / 100 / 252
    daily_trade_cost = (
        exposure
        * max(float(roundtrip_cost_bps) + float(slippage_bps) * 2, 0)
        / 10_000
        / max(hold_days, 1)
    )
    for item in basket_returns:
        period_return = (item["return_pct"] / 100) / max(hold_days, 1) * exposure
        period_return -= daily_financing_cost + daily_trade_cost
        compound *= max(0, 1 + period_return)
        points.append(
            {
                "signal_date": item["signal_date"],
                "equity": compound,
                "return_pct": item["return_pct"],
                "net_period_return_pct": round(period_return * 100, 4),
                "count": item.get("count", 0),
            }
        )
    return points


def _equity_points_from_slot_exit_returns(
    selected: List[Dict[str, Any]],
    max_active_positions: int,
    exposure_multiplier: float = 1.0,
    annual_financing_rate_pct: float = 0.0,
    roundtrip_cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> List[Dict[str, Any]]:
    if not selected:
        return []
    slot_count = max(int(max_active_positions or 0), 1)
    exposure = max(float(exposure_multiplier), 0)
    slot_exposure = exposure / slot_count
    borrowed_slot_exposure = (
        slot_exposure * max(exposure - 1, 0) / exposure
        if exposure > 0
        else 0
    )
    cost_rate = max(float(roundtrip_cost_bps) + float(slippage_bps) * 2, 0) / 10_000
    financing_daily_rate = max(float(annual_financing_rate_pct), 0) / 100 / 252

    events_by_exit_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in selected:
        exit_date = str(trade.get("exit_date") or trade.get("signal_date"))
        holding_days = max(1, int(_num(trade.get("holding_days"), 1)))
        gross_contribution = slot_exposure * _num(trade.get("return_pct")) / 100
        trade_cost = slot_exposure * cost_rate
        financing_cost = borrowed_slot_exposure * financing_daily_rate * holding_days
        events_by_exit_date[exit_date].append(
            {
                "symbol": trade.get("symbol"),
                "signal_date": trade.get("signal_date"),
                "exit_date": exit_date,
                "gross_contribution": gross_contribution,
                "net_contribution": gross_contribution - trade_cost - financing_cost,
                "holding_days": holding_days,
            }
        )

    compound = 1.0
    points = []
    for exit_date in sorted(events_by_exit_date):
        events = events_by_exit_date[exit_date]
        net_period_return = sum(item["net_contribution"] for item in events)
        compound *= max(0, 1 + net_period_return)
        points.append(
            {
                "signal_date": exit_date,
                "event_date": exit_date,
                "equity": compound,
                "return_pct": round(net_period_return * 100, 4),
                "net_period_return_pct": round(net_period_return * 100, 4),
                "count": len(events),
                "capital_model": "slot_exit",
                "slot_count": slot_count,
                "slot_exposure": round(slot_exposure, 6),
            }
        )
    return points


def _equity_points_from_slot_daily_returns(
    selected: List[Dict[str, Any]],
    max_active_positions: int,
    exposure_multiplier: float = 1.0,
    annual_financing_rate_pct: float = 0.0,
    roundtrip_cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> List[Dict[str, Any]]:
    if not selected:
        return []

    slot_count = max(int(max_active_positions or 0), 1)
    exposure = max(float(exposure_multiplier), 0)
    slot_exposure = exposure / slot_count
    borrowed_slot_exposure = (
        slot_exposure * max(exposure - 1, 0) / exposure
        if exposure > 0
        else 0
    )
    cost_rate = max(float(roundtrip_cost_bps) + float(slippage_bps) * 2, 0) / 10_000
    half_trade_cost_rate = cost_rate / 2
    financing_daily_rate = max(float(annual_financing_rate_pct), 0) / 100 / 252

    entries_by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_dates = set()
    for trade in selected:
        path = trade.get("mark_to_market_path") or []
        entry_date = str(trade.get("entry_date") or trade.get("signal_date"))
        if not path:
            path = [
                {
                    "date": entry_date,
                    "close_return_pct": _num(trade.get("return_pct")),
                    "low_return_pct": _num(trade.get("max_adverse_pct")),
                }
            ]
        normalized_path = []
        for mark in path:
            date_text = str(mark.get("date") or "")[:10]
            if not date_text:
                continue
            normalized_path.append(
                {
                    "date": date_text,
                    "close_return_pct": _num(mark.get("close_return_pct")),
                    "low_return_pct": _num(mark.get("low_return_pct"), _num(mark.get("close_return_pct"))),
                }
            )
            all_dates.add(date_text)
        if not normalized_path:
            continue
        enriched = dict(trade)
        enriched["mark_to_market_path"] = normalized_path
        enriched["_mark_by_date"] = {mark["date"]: mark for mark in normalized_path}
        enriched["_entry_date"] = normalized_path[0]["date"]
        enriched["_exit_date"] = normalized_path[-1]["date"]
        entries_by_date[enriched["_entry_date"]].append(enriched)

    if not all_dates:
        return []

    realized_equity = 1.0
    active: List[Dict[str, Any]] = []
    points = []

    for current_date in sorted(all_dates):
        base_equity = realized_equity + sum(_num(item.get("_last_contribution")) for item in active)
        for trade in entries_by_date.get(current_date, []):
            notional = max(base_equity, 0) * slot_exposure
            trade["_notional"] = notional
            trade["_entry_cost"] = notional * half_trade_cost_rate
            trade["_exit_cost"] = notional * half_trade_cost_rate
            trade["_daily_financing_cost"] = max(base_equity, 0) * borrowed_slot_exposure * financing_daily_rate
            trade["_days_held"] = 0
            trade["_last_contribution"] = -trade["_entry_cost"]
            active.append(trade)

        close_contribution = 0.0
        low_contribution = 0.0
        exiting = []
        for trade in active:
            mark = trade["_mark_by_date"].get(current_date)
            if mark:
                trade["_last_mark"] = mark
                trade["_days_held"] = int(trade.get("_days_held") or 0) + 1
            else:
                mark = trade.get("_last_mark") or {"close_return_pct": 0.0, "low_return_pct": 0.0}
            days_held = max(1, int(trade.get("_days_held") or 1))
            exit_cost = trade["_exit_cost"] if current_date >= trade["_exit_date"] else 0.0
            accrued_cost = trade["_entry_cost"] + exit_cost + trade["_daily_financing_cost"] * days_held
            close_value = trade["_notional"] * _num(mark.get("close_return_pct")) / 100 - accrued_cost
            low_value = trade["_notional"] * _num(mark.get("low_return_pct")) / 100 - accrued_cost
            trade["_last_contribution"] = close_value
            close_contribution += close_value
            low_contribution += min(low_value, close_value)
            if current_date >= trade["_exit_date"]:
                exiting.append(trade)

        close_equity = realized_equity + close_contribution
        low_equity = realized_equity + low_contribution
        points.append(
            {
                "signal_date": current_date,
                "event_date": current_date,
                "equity": max(0, close_equity),
                "drawdown_equity": max(0, low_equity),
                "return_pct": round((close_equity - realized_equity) * 100, 4),
                "net_period_return_pct": round((close_equity - realized_equity) * 100, 4),
                "count": len(active),
                "capital_model": "slot_daily",
                "slot_count": slot_count,
                "slot_exposure": round(slot_exposure, 6),
            }
        )

        for trade in exiting:
            realized_equity += _num(trade.get("_last_contribution"))
            active.remove(trade)

    return points
