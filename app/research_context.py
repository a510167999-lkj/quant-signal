"""研究回测的信号日上下文计算。

从 research_backtest.py 抽出，把横截面特征转成信号日上下文 dict 的纯计算逻辑
（大盘强度 / 代理收益 / 相对强度 / K 线形态 / 市场宽度 / 行业轮动 / 历史质量），
与回测编排解耦，供 research_backtest 调用。
"""
from collections import defaultdict
from typing import Any, Dict, List

import pandas as pd

from app.config import Settings
from app.industry_history import IndustryHistoryProvider
from app.research_common import _date_value, _num
from app.signal_tags import build_market_breadth_tags, build_price_action_tags, build_proxy_market_tags
from app.signals import evaluate_signal


def _round2(value: Any) -> Any:
    """None 安全的两位小数四舍五入；None 透传。"""
    return round(value, 2) if value is not None else None


def _pct(rows: List[Dict[str, Any]], key: str) -> float:
    """rows 中 key 为真的占比（%），两位小数。调用方保证 rows 非空。"""
    return round(sum(1 for row in rows if row[key]) / len(rows) * 100, 2)


def _historical_market_context(proxy_frames: List[pd.DataFrame], signal_date: str) -> Dict[str, Any]:
    scores = []
    weak_count = 0
    strong_count = 0
    for frame in proxy_frames:
        upto = frame[frame["date"] <= signal_date]
        if len(upto) < 90:
            continue
        signal = evaluate_signal(upto)
        score = _num(signal.get("score"))
        scores.append(score)
        if signal.get("action") in {"SELL", "REDUCE"}:
            weak_count += 1
        if signal.get("action") == "BUY" or score >= 2:
            strong_count += 1

    if not scores:
        return {
            "level": "unknown",
            "min_signal_score": 2.0,
            "allow_watch": True,
            "score_adjustment": 0,
        }

    avg_score = sum(scores) / len(scores)
    if weak_count >= 2 or avg_score <= -1.5:
        return {
            "level": "defensive",
            "min_signal_score": 4.0,
            "allow_watch": False,
            "score_adjustment": -18,
        }
    if weak_count == 1 or avg_score < 0.5:
        return {
            "level": "cautious",
            "min_signal_score": 3.0,
            "allow_watch": True,
            "score_adjustment": -8,
        }
    if strong_count >= 2 and avg_score >= 2:
        return {
            "level": "favorable",
            "min_signal_score": 2.0,
            "allow_watch": True,
            "score_adjustment": 8,
        }
    return {
        "level": "neutral",
        "min_signal_score": 2.5,
        "allow_watch": True,
        "score_adjustment": 0,
    }


def _historical_proxy_returns(proxy_frames: List[pd.DataFrame], signal_date: str) -> Dict[str, Any]:
    returns_20d = []
    returns_60d = []
    for frame in proxy_frames:
        upto = frame[frame["date"] <= signal_date]
        if len(upto) < 21:
            continue
        latest = upto.iloc[-1]
        returns_20d.append(_num(latest.get("return_20d")) * 100)
        if len(upto) >= 61:
            returns_60d.append(_num(latest.get("return_60d")) * 100)

    return {
        "proxy_return_20d_avg_pct": round(sum(returns_20d) / len(returns_20d), 2) if returns_20d else None,
        "proxy_return_20d_max_pct": round(max(returns_20d), 2) if returns_20d else None,
        "proxy_return_60d_avg_pct": round(sum(returns_60d) / len(returns_60d), 2) if returns_60d else None,
        "proxy_return_60d_max_pct": round(max(returns_60d), 2) if returns_60d else None,
    }


def _relative_strength_context(latest_row: Any, proxy_returns: Dict[str, Any]) -> Dict[str, Any]:
    stock_20d = _num(latest_row.get("return_20d")) * 100
    stock_60d = _num(latest_row.get("return_60d")) * 100
    proxy_20d = proxy_returns.get("proxy_return_20d_avg_pct")
    proxy_60d = proxy_returns.get("proxy_return_60d_avg_pct")
    proxy_20d_max = proxy_returns.get("proxy_return_20d_max_pct")
    proxy_60d_max = proxy_returns.get("proxy_return_60d_max_pct")
    rel_20d = stock_20d - _num(proxy_20d) if proxy_20d is not None else None
    rel_60d = stock_60d - _num(proxy_60d) if proxy_60d is not None else None

    tags = list(build_proxy_market_tags(proxy_returns))
    if rel_20d is not None:
        if rel_20d >= 0:
            tags.append("rs20_nonnegative")
        if rel_20d >= 5:
            tags.append("rs20_strong")
        if rel_20d >= 10:
            tags.append("rs20_gte_10")
        elif rel_20d >= 5:
            tags.append("rs20_5_to_10")
        elif rel_20d >= 0:
            tags.append("rs20_0_to_5")
        else:
            tags.append("rs20_lt_0")
        if proxy_20d_max is not None and stock_20d >= _num(proxy_20d_max) + 5:
            tags.append("rs20_market_leader")
    if rel_60d is not None:
        if rel_60d >= 0:
            tags.append("rs60_nonnegative")
        if rel_60d >= 10:
            tags.append("rs60_strong")
        if rel_60d >= 20:
            tags.append("rs60_gte_20")
        elif rel_60d >= 10:
            tags.append("rs60_10_to_20")
        elif rel_60d >= 0:
            tags.append("rs60_0_to_10")
        else:
            tags.append("rs60_lt_0")
        if proxy_60d_max is not None and stock_60d >= _num(proxy_60d_max) + 10:
            tags.append("rs60_market_leader")

    return {
        "stock_return_20d_pct": round(stock_20d, 2),
        "stock_return_60d_pct": round(stock_60d, 2),
        "relative_strength_20d_pct": round(rel_20d, 2) if rel_20d is not None else None,
        "relative_strength_60d_pct": round(rel_60d, 2) if rel_60d is not None else None,
        **proxy_returns,
        "tags": tags,
    }


def _limit_threshold_pct(symbol: Any) -> float:
    code = str(symbol or "")
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0


def _row_change_pct(frame: pd.DataFrame, index: int) -> float:
    row = frame.iloc[index]
    change_pct = _num(row.get("change_pct"))
    if change_pct:
        return change_pct
    if index <= 0:
        return 0.0
    previous_close = _num(frame.iloc[index - 1].get("close"))
    close = _num(row.get("close"))
    if previous_close <= 0 or close <= 0:
        return 0.0
    return (close / previous_close - 1) * 100


def _price_action_context(frame: pd.DataFrame, index: int, symbol: Any) -> Dict[str, Any]:
    row = frame.iloc[index]
    previous = frame.iloc[index - 1] if index > 0 else row
    open_price = _num(row.get("open"))
    high = _num(row.get("high"))
    low = _num(row.get("low"))
    close = _num(row.get("close"))
    previous_close = _num(previous.get("close"))
    limit_threshold = _limit_threshold_pct(symbol)
    signal_change_pct = _row_change_pct(frame, index)

    gap_pct = (open_price / previous_close - 1) * 100 if open_price > 0 and previous_close > 0 else None
    intraday_return_pct = (close / open_price - 1) * 100 if open_price > 0 and close > 0 else None
    range_pct = (high / low - 1) * 100 if high > 0 and low > 0 else None
    close_position_pct = ((close - low) / (high - low) * 100) if high > low and close > 0 else None
    upper_shadow_pct = ((high / close - 1) * 100) if high > 0 and close > 0 else None
    lower_shadow_pct = ((close / low - 1) * 100) if close > 0 and low > 0 else None

    recent_limit_up_count = 0
    recent_near_limit_up_count = 0
    recent_large_up_count = 0
    start = max(1, index - 19)
    for row_index in range(start, index + 1):
        change = _row_change_pct(frame, row_index)
        if change >= limit_threshold - 0.5:
            recent_limit_up_count += 1
        if change >= limit_threshold - 1.5:
            recent_near_limit_up_count += 1
        if change >= 6:
            recent_large_up_count += 1

    context = {
        "gap_pct": _round2(gap_pct),
        "intraday_return_pct": _round2(intraday_return_pct),
        "range_pct": _round2(range_pct),
        "close_position_pct": _round2(close_position_pct),
        "upper_shadow_pct": _round2(upper_shadow_pct),
        "lower_shadow_pct": _round2(lower_shadow_pct),
        "signal_change_pct": round(signal_change_pct, 2),
        "limit_threshold_pct": limit_threshold,
        "recent_limit_up_count_20d": recent_limit_up_count,
        "recent_near_limit_up_count_20d": recent_near_limit_up_count,
        "recent_large_up_count_20d": recent_large_up_count,
    }
    context["tags"] = build_price_action_tags(context)
    return context


def _historical_market_breadth(
    symbol_frames: Dict[str, Dict[str, Any]],
    start_date: str,
    hold_days: int,
) -> Dict[str, Dict[str, Any]]:
    rows_by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for payload in symbol_frames.values():
        frame = payload["frame"]
        for index in range(60, len(frame) - hold_days - 1):
            row = frame.iloc[index]
            signal_date = str(row.get("date"))
            if signal_date < start_date:
                continue
            previous = frame.iloc[index - 1] if index > 0 else row
            close = _num(row.get("close"))
            if close <= 0:
                continue
            previous_close = _num(previous.get("close"))
            change_pct = _num(row.get("change_pct"))
            if not change_pct and previous_close > 0:
                change_pct = (close / previous_close - 1) * 100
            amount = _num(row.get("amount"))
            if amount <= 0:
                amount = close * _num(row.get("volume"))
            return_20d_pct = _num(row.get("return_20d")) * 100
            rows_by_date[signal_date].append(
                {
                    "above_ma20": close >= _num(row.get("ma20")),
                    "above_ma60": close >= _num(row.get("ma60")),
                    "return_20d_positive": return_20d_pct > 0,
                    "return_20d_pct": return_20d_pct,
                    "advancing": change_pct > 0,
                    "liquid_300m": amount >= 300_000_000,
                }
            )

    result: Dict[str, Dict[str, Any]] = {}
    for signal_date, rows in rows_by_date.items():
        sample_count = len(rows)
        if not sample_count:
            continue
        returns = [row["return_20d_pct"] for row in rows]
        context = {
            "sample_count": sample_count,
            "above_ma20_pct": _pct(rows, "above_ma20"),
            "above_ma60_pct": _pct(rows, "above_ma60"),
            "return_20d_positive_pct": _pct(rows, "return_20d_positive"),
            "advancing_pct": _pct(rows, "advancing"),
            "liquid_300m_pct": _pct(rows, "liquid_300m"),
            "median_return_20d_pct": round(float(pd.Series(returns).median()), 2),
        }
        context["tags"] = build_market_breadth_tags(context)
        result[signal_date] = context
    return result


def _historical_industry_rotation_contexts(
    settings: Settings,
    start_date: str,
    end_date: str,
    max_boards: int,
) -> Dict[str, Dict[str, Any]]:
    start = _date_value(start_date) - pd.Timedelta(days=160)
    provider = IndustryHistoryProvider(settings.industry_history_cache_dir)
    contexts = provider.rotation_contexts(
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end_date,
        max_boards=max_boards,
        use_cache_on_error=True,
    )
    return {date: context for date, context in contexts.items() if date >= start_date}


def _quality_from_prior(prior: List[Dict[str, Any]], settings: Settings) -> Dict[str, Any]:
    if len(prior) < settings.min_backtest_trades:
        return {}
    wins = [item for item in prior if item["return_pct"] > 0]
    win_rate = len(wins) / len(prior) * 100 if prior else 0
    avg_return = sum(item["return_pct"] for item in prior) / len(prior) if prior else 0
    avg_adverse = abs(sum(item["max_adverse_pct"] for item in prior) / len(prior)) if prior else 0
    if win_rate < settings.min_backtest_win_rate:
        return {}
    if avg_return < settings.min_backtest_avg_return:
        return {}
    if avg_adverse > settings.max_backtest_avg_adverse:
        return {}

    score_bonus = avg_return * 1.2 + (win_rate - 50) * 0.25 - max(0, avg_adverse - 4) * 1.2
    return {
        "trade_count": len(prior),
        "win_rate_pct": win_rate,
        "avg_return_pct": avg_return,
        "avg_adverse_pct": avg_adverse,
        "score_bonus": max(min(score_bonus, 18), -25),
    }
