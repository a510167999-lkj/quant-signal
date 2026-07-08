"""研究回测的组合管理与单笔交易实现。

从 research_backtest.py 抽出，给定候选 + 未来 K 线模拟组合交易：滚动窗口权益
统计、按信号日选股 + 持仓去重/冷却/仓位数控制、单笔交易实现（止损/止盈/移动
止损 + mark-to-market 路径）。与回测编排解耦，供 research_backtest 调用。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd

from app.research_common import _date_value
from app.research_equity import _max_drawdown_pct_from_points


def _window_portfolio_stats(
    equity_points: List[Dict[str, Any]],
    days: int = 365,
) -> Dict[str, Any]:
    if not equity_points:
        return {
            "window_days": days,
            "latest": None,
            "best": None,
            "worst": None,
            "window_count": 0,
        }

    windows = []
    for end_index, end in enumerate(equity_points):
        end_date = _date_value(end["signal_date"])
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
        windows.append(
            {
                "start_date": segment[0]["signal_date"],
                "end_date": end["signal_date"],
                "signal_days": len(segment),
                "trade_count": sum(int(point.get("count") or 0) for point in segment),
                "return_pct": round((end["equity"] / start_equity - 1) * 100, 2),
                "max_drawdown_pct": _max_drawdown_pct_from_points(segment, start_equity=start_equity),
            }
        )

    if not windows:
        return {
            "window_days": days,
            "latest": None,
            "best": None,
            "worst": None,
            "window_count": 0,
        }

    return {
        "window_days": days,
        "latest": windows[-1],
        "best": max(windows, key=lambda item: item["return_pct"]),
        "worst": min(windows, key=lambda item: item["return_pct"]),
        "window_count": len(windows),
    }


def _select_with_portfolio_controls(
    by_signal_date: Dict[str, List[Dict[str, Any]]],
    top_n: int,
    symbol_cooldown_days: int = 0,
    max_active_positions: int = 0,
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    active_positions: List[Dict[str, Any]] = []
    last_exit_by_symbol: Dict[str, datetime] = {}

    for signal_date in sorted(by_signal_date):
        trades = sorted(by_signal_date[signal_date], key=lambda item: item["rank_score"], reverse=True)
        signal_day = _date_value(signal_date)
        active_positions = [
            item for item in active_positions if _date_value(item["exit_date"]) >= signal_day
        ]
        day_count = 0
        active_symbols = {item["symbol"] for item in active_positions}

        for trade in trades:
            symbol = trade.get("symbol")
            if symbol in active_symbols:
                continue
            previous_exit = last_exit_by_symbol.get(symbol)
            if previous_exit and symbol_cooldown_days > 0:
                if (signal_day - previous_exit).days < symbol_cooldown_days:
                    continue
            if max_active_positions > 0 and len(active_positions) >= max_active_positions:
                break

            selected.append(trade)
            active_positions.append(trade)
            active_symbols.add(symbol)
            last_exit_by_symbol[symbol] = _date_value(trade["exit_date"])
            day_count += 1
            if day_count >= top_n:
                break

    return selected


def _realized_trade_from_future(
    frame: pd.DataFrame,
    entry_index: int,
    exit_index: int,
    stop_loss_pct: float = None,
    take_profit_pct: float = None,
    trailing_stop_pct: float = None,
) -> Dict[str, Any]:
    entry = float(frame.iloc[entry_index].get("open", frame.iloc[entry_index]["close"]))
    future = frame.iloc[entry_index : exit_index + 1]
    exit_price = float(frame.iloc[exit_index]["close"])
    exit_date = str(frame.iloc[exit_index]["date"])
    exit_reason = "time_exit"
    exit_offset = len(future) - 1
    hard_stop_price = entry * (1 - abs(stop_loss_pct) / 100) if stop_loss_pct else None
    take_profit_price = entry * (1 + abs(take_profit_pct) / 100) if take_profit_pct else None
    high_watermark = entry

    for offset, (_future_index, future_row) in enumerate(future.iterrows()):
        low = float(future_row.get("low", entry))
        high = float(future_row.get("high", entry))
        trailing_price = (
            high_watermark * (1 - abs(trailing_stop_pct) / 100)
            if trailing_stop_pct and high_watermark > 0
            else None
        )
        triggered_stops = []
        if hard_stop_price and low <= hard_stop_price:
            triggered_stops.append(("stop_loss", hard_stop_price))
        if trailing_price and low <= trailing_price:
            triggered_stops.append(("trailing_stop", trailing_price))
        if triggered_stops:
            exit_reason, exit_price = min(triggered_stops, key=lambda item: item[1])
            exit_date = str(future_row["date"])
            exit_offset = offset
            break
        if take_profit_price and high >= take_profit_price:
            exit_price = take_profit_price
            exit_date = str(future_row["date"])
            exit_reason = "take_profit"
            exit_offset = offset
            break
        high_watermark = max(high_watermark, high)

    realized_future = future.iloc[: exit_offset + 1]
    mark_to_market_path = []
    for offset, (_future_index, future_row) in enumerate(realized_future.iterrows()):
        open_mark = float(future_row.get("open", entry))
        close_mark = float(future_row.get("close", entry))
        high_mark = float(future_row.get("high", close_mark))
        low_mark = float(future_row.get("low", close_mark))
        if offset == exit_offset:
            close_mark = exit_price
            high_mark = max(high_mark, exit_price)
            low_mark = min(low_mark, exit_price)
        mark_to_market_path.append(
            {
                "date": str(future_row["date"]),
                "open_return_pct": round((open_mark / entry - 1) * 100, 4) if entry else 0,
                "high_return_pct": round((high_mark / entry - 1) * 100, 4) if entry else 0,
                "close_return_pct": round((close_mark / entry - 1) * 100, 4) if entry else 0,
                "low_return_pct": round((low_mark / entry - 1) * 100, 4) if entry else 0,
            }
        )
    return {
        "entry_date": str(frame.iloc[entry_index]["date"]),
        "exit_date": exit_date,
        "exit_reason": exit_reason,
        "holding_days": max(1, exit_offset),
        "mark_to_market_path": mark_to_market_path,
        "return_pct": round((exit_price / entry - 1) * 100, 4) if entry else 0,
        "max_adverse_pct": round((float(realized_future["low"].min()) / entry - 1) * 100, 4)
        if entry and len(realized_future)
        else 0,
        "max_favorable_pct": round((float(realized_future["high"].max()) / entry - 1) * 100, 4)
        if entry and len(realized_future)
        else 0,
    }
