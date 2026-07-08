import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.a_share_universe import AShareUniverseProvider, select_deep_scan_candidates
from app.analysis import build_analysis
from app.announcement_context import AnnouncementContextProvider
from app.compat import model_to_dict
from app.config import Settings
from app.fund_flow import FundFlowContextProvider
from app.industry_history import IndustryHistoryProvider
from app.industry_strength import IndustryStrengthProvider
from app.indicators import add_indicators
from app.margin_eligibility import MarginEligibilityProvider
from app.market_regime import evaluate_market_regime
from app.mootdx_l1 import MootdxL1QuoteProvider
from app.news_context import NewsContextProvider
from app.schemas import AnalyzeRequest
from app.signal_tags import (
    build_announcement_tags,
    build_candidate_context_tags,
    build_market_breadth_tags,
    build_price_action_tags,
    build_proxy_market_tags,
    build_signal_tags,
)
from app.storage import append_jsonl, read_json, read_jsonl, unique_by_key, write_json
from app.trading_calendar import (
    is_a_share_trading_time,
    is_trade_day,
    next_calendar_gap,
    next_trade_date,
    now_cn,
)


LOCK_STALE_SECONDS = 3 * 60 * 60
RUN_SLOT_AUTO = "auto"
RUN_SLOT_CONTEXTS = {
    "pre_open": {
        "label": "09:00 盘前候选",
        "time": "09:00",
        "phase": "open",
        "scan_profile": "fast",
        "use_l1_context": False,
        "intent": "based_on_previous_close_and_cached_context",
    },
    "open_confirm": {
        "label": "09:32 开盘确认",
        "time": "09:32",
        "phase": "open",
        "scan_profile": "fast",
        "use_l1_context": True,
        "intent": "confirm_after_call_auction_and_open_print",
    },
    "pre_close": {
        "label": "14:55 尾盘策略",
        "time": "14:55",
        "phase": "close",
        "scan_profile": "fast",
        "use_l1_context": True,
        "intent": "pre_close_risk_and_opportunity_check",
    },
    "post_close": {
        "label": "15:02 收盘复盘",
        "time": "15:02",
        "phase": "close",
        "scan_profile": "full",
        "use_l1_context": True,
        "intent": "post_close_full_review_and_next_day_candidates",
    },
}
HOT_INDUSTRY_WINDOWS = (
    {"key": "1d", "label": "当日", "days": 1},
    {"key": "3d", "label": "3日", "days": 3},
    {"key": "5d", "label": "5日", "days": 5},
    {"key": "10d", "label": "10日", "days": 10},
)


def _recommendation_key(item: Dict[str, Any]) -> str:
    return "%s:%s" % (item.get("market", "a"), item.get("symbol", ""))


def _resolve_run_slot(run_slot: str, moment: datetime) -> Dict[str, Any]:
    raw = str(run_slot or RUN_SLOT_AUTO).strip() or RUN_SLOT_AUTO
    if raw in RUN_SLOT_CONTEXTS:
        return {"slot": raw, **RUN_SLOT_CONTEXTS[raw]}
    minutes = moment.hour * 60 + moment.minute
    if minutes < 9 * 60 + 32:
        slot = "pre_open"
    elif minutes < 14 * 60 + 55:
        slot = "open_confirm"
    elif minutes < 15 * 60 + 2:
        slot = "pre_close"
    else:
        slot = "post_close"
    return {"slot": slot, **RUN_SLOT_CONTEXTS[slot]}


def _slot_max_deep(settings: Settings, run_slot: Dict[str, Any], max_deep: int = None) -> int:
    if max_deep:
        return max_deep
    if run_slot.get("scan_profile") == "full":
        return settings.scan_max_deep
    return min(settings.scan_max_deep, settings.intraday_scan_max_deep)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _avg(values: List[float]) -> Optional[float]:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _return_pct_from_candles(candles: List[Dict[str, Any]], periods: int) -> Optional[float]:
    if len(candles) <= periods:
        return None
    latest = _num(candles[-1].get("close"))
    base = _num(candles[-periods - 1].get("close"))
    if latest <= 0 or base <= 0:
        return None
    return (latest / base - 1) * 100


def _compound_change_pct(records: List[Dict[str, Any]], days: int) -> Optional[float]:
    if days <= 0:
        return None
    recent = [row for row in records[-days:] if row]
    if len(recent) < days:
        return None
    compounded = 1.0
    valid_changes = 0
    for row in recent:
        change = _num(row.get("change_pct"), None)
        if change is None:
            continue
        compounded *= 1 + change / 100
        valid_changes += 1
    if valid_changes == days:
        return (compounded - 1) * 100

    if len(records) <= days:
        return None
    latest = _num(records[-1].get("close"))
    base = _num(records[-days - 1].get("close"))
    if latest <= 0 or base <= 0:
        return None
    return (latest / base - 1) * 100


def _candidate_counts_by_industry(candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for candidate in candidates:
        industry_name = str(candidate.get("industry") or "")
        if industry_name:
            counts[industry_name] = counts.get(industry_name, 0) + 1
    return counts


def _daily_hot_industries(
    industry_payload: Dict[str, Any],
    candidate_counts: Dict[str, int],
    limit: int,
) -> List[Dict[str, Any]]:
    hot_industries = []
    for rank, industry in enumerate((industry_payload.get("industries") or [])[:limit], start=1):
        name = str(industry.get("name") or "")
        if not name:
            continue
        change_pct = _num(industry.get("change_pct"), None)
        hot_industries.append(
            {
                "rank": rank,
                "window": "1d",
                "label": "当日",
                "days": 1,
                "name": name,
                "change_pct": change_pct,
                "return_pct": change_pct,
                "turnover": industry.get("turnover"),
                "breadth": industry.get("breadth"),
                "industry_score": industry.get("industry_score"),
                "candidate_count": candidate_counts.get(name, 0),
            }
        )
    return hot_industries


def _hot_industry_windows(
    industry_payload: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    history_provider: IndustryHistoryProvider,
    as_of_date: str,
    limit: int,
) -> Dict[str, Any]:
    candidate_counts = _candidate_counts_by_industry(candidates)
    by_window = {"1d": _daily_hot_industries(industry_payload, candidate_counts, limit)}
    errors: List[Dict[str, Any]] = []
    industries = list(industry_payload.get("industries") or [])
    if not industries:
        return {"by_window": by_window, "errors": errors}

    board_codes: Dict[str, str] = {}
    try:
        board_codes = {
            str(board.get("name") or ""): str(board.get("code") or "")
            for board in history_provider.boards(use_cache_on_error=True)
        }
    except Exception as exc:
        errors.append({"window": "history", "stage": "boards", "message": str(exc)})

    end_date = _date_text(as_of_date)
    start_date = (datetime.fromisoformat(end_date) - timedelta(days=28)).date().isoformat()
    histories: Dict[str, List[Dict[str, Any]]] = {}
    history_industries = industries[: max(limit * 8, 24)]

    def load_history(industry: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]], Optional[str]]:
        name = str(industry.get("name") or "")
        if not name:
            return "", [], None
        try:
            return name, history_provider.history(
                name,
                start_date=start_date,
                end_date=end_date,
                board_code=board_codes.get(name),
                use_cache_on_error=True,
            ), None
        except Exception as exc:
            return name, [], str(exc)

    workers = min(6, max(len(history_industries), 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(load_history, industry) for industry in history_industries]
        for future in as_completed(futures):
            name, records, error = future.result()
            if not name:
                continue
            if records:
                histories[name] = records
            elif error and len(errors) < 10:
                errors.append({"industry": name, "stage": "history", "message": error})

    for window in HOT_INDUSTRY_WINDOWS[1:]:
        rows = []
        days = int(window["days"])
        for industry in history_industries:
            name = str(industry.get("name") or "")
            records = histories.get(name) or []
            if not records:
                continue
            records = sorted(records, key=lambda row: str(row.get("date") or ""))
            return_pct = _compound_change_pct(records, days)
            if return_pct is None:
                continue
            latest = records[-1]
            rows.append(
                {
                    "rank": 0,
                    "window": window["key"],
                    "label": window["label"],
                    "days": days,
                    "name": name,
                    "change_pct": round(return_pct, 2),
                    "return_pct": round(return_pct, 2),
                    "latest_change_pct": round(_num(latest.get("change_pct")), 2),
                    "turnover": round(_num(latest.get("turnover")), 2),
                    "breadth": industry.get("breadth"),
                    "industry_score": round(return_pct, 4),
                    "candidate_count": candidate_counts.get(name, 0),
                    "as_of": latest.get("date"),
                    "history_rows": len(records),
                }
            )
        rows.sort(
            key=lambda row: (
                _num(row.get("return_pct")),
                _num(row.get("turnover")),
                _num(row.get("candidate_count")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(rows[:limit], start=1):
            row["rank"] = rank
        by_window[str(window["key"])] = rows[:limit]
    return {"by_window": by_window, "errors": errors}


def _date_text(value: Any) -> str:
    return str(value or "")[:10]


def _limit_threshold_pct(symbol: Any) -> float:
    code = str(symbol or "")
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0


def _candle_change_pct(candles: List[Dict[str, Any]], index: int) -> float:
    row = candles[index]
    if index <= 0:
        return 0.0
    previous_close = _num(candles[index - 1].get("close"))
    close = _num(row.get("close"))
    if previous_close <= 0 or close <= 0:
        return 0.0
    return (close / previous_close - 1) * 100


def _price_action_context(result: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    candles = result.get("candles") or []
    if len(candles) < 2:
        return {"tags": []}
    latest = candles[-1]
    previous = candles[-2]
    open_price = _num(latest.get("open"))
    high = _num(latest.get("high"))
    low = _num(latest.get("low"))
    close = _num(latest.get("close"))
    previous_close = _num(previous.get("close"))
    limit_threshold = _limit_threshold_pct(candidate.get("symbol") or result.get("symbol"))
    signal_change_pct = _num(candidate.get("change_pct"))
    if not signal_change_pct and previous_close > 0 and close > 0:
        signal_change_pct = (close / previous_close - 1) * 100

    gap_pct = (open_price / previous_close - 1) * 100 if open_price > 0 and previous_close > 0 else None
    intraday_return_pct = (close / open_price - 1) * 100 if open_price > 0 and close > 0 else None
    range_pct = (high / low - 1) * 100 if high > 0 and low > 0 else None
    close_position_pct = ((close - low) / (high - low) * 100) if high > low and close > 0 else None
    upper_shadow_pct = ((high / close - 1) * 100) if high > 0 and close > 0 else None
    lower_shadow_pct = ((close / low - 1) * 100) if close > 0 and low > 0 else None

    recent_limit_up_count = 0
    recent_near_limit_up_count = 0
    recent_large_up_count = 0
    start = max(1, len(candles) - 20)
    for index in range(start, len(candles)):
        change = _candle_change_pct(candles, index)
        if change >= limit_threshold - 0.5:
            recent_limit_up_count += 1
        if change >= limit_threshold - 1.5:
            recent_near_limit_up_count += 1
        if change >= 6:
            recent_large_up_count += 1

    context = {
        "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
        "intraday_return_pct": round(intraday_return_pct, 2) if intraday_return_pct is not None else None,
        "range_pct": round(range_pct, 2) if range_pct is not None else None,
        "close_position_pct": round(close_position_pct, 2) if close_position_pct is not None else None,
        "upper_shadow_pct": round(upper_shadow_pct, 2) if upper_shadow_pct is not None else None,
        "lower_shadow_pct": round(lower_shadow_pct, 2) if lower_shadow_pct is not None else None,
        "signal_change_pct": round(signal_change_pct, 2),
        "limit_threshold_pct": limit_threshold,
        "recent_limit_up_count_20d": recent_limit_up_count,
        "recent_near_limit_up_count_20d": recent_near_limit_up_count,
        "recent_large_up_count_20d": recent_large_up_count,
    }
    context["tags"] = build_price_action_tags(context)
    return context


def _proxy_return_context(data_provider) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    returns_20d = []
    returns_60d = []
    errors = []
    proxies = [
        {"symbol": "510300", "market": "etf", "name": "沪深300ETF"},
        {"symbol": "159915", "market": "etf", "name": "创业板ETF"},
    ]
    for proxy in proxies:
        try:
            frame, _source = data_provider.history(proxy["symbol"], proxy["market"], lookback_days=180, adjust="qfq")
            frame = add_indicators(frame)
            latest = frame.iloc[-1]
            returns_20d.append(_num(latest.get("return_20d")) * 100)
            returns_60d.append(_num(latest.get("return_60d")) * 100)
        except Exception as exc:
            errors.append({"stage": "relative_strength_proxy", "symbol": proxy["symbol"], "message": str(exc)})
    context = {
        "proxy_return_20d_avg_pct": round(_avg(returns_20d), 2) if returns_20d else None,
        "proxy_return_20d_max_pct": round(max(returns_20d), 2) if returns_20d else None,
        "proxy_return_60d_avg_pct": round(_avg(returns_60d), 2) if returns_60d else None,
        "proxy_return_60d_max_pct": round(max(returns_60d), 2) if returns_60d else None,
    }
    return context, errors


def _relative_strength_context(result: Dict[str, Any], proxy_returns: Dict[str, Any]) -> Dict[str, Any]:
    indicators = result.get("indicators") or {}
    candles = result.get("candles") or []
    stock_20d = indicators.get("return_20d_pct")
    if stock_20d is None:
        stock_20d = _return_pct_from_candles(candles, 20)
    stock_60d = _return_pct_from_candles(candles, 60)
    proxy_20d = proxy_returns.get("proxy_return_20d_avg_pct")
    proxy_60d = proxy_returns.get("proxy_return_60d_avg_pct")
    proxy_20d_max = proxy_returns.get("proxy_return_20d_max_pct")
    proxy_60d_max = proxy_returns.get("proxy_return_60d_max_pct")

    rel_20d = _num(stock_20d) - _num(proxy_20d) if stock_20d is not None and proxy_20d is not None else None
    rel_60d = _num(stock_60d) - _num(proxy_60d) if stock_60d is not None and proxy_60d is not None else None
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
        if proxy_20d_max is not None and _num(stock_20d) >= _num(proxy_20d_max) + 5:
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
        if proxy_60d_max is not None and _num(stock_60d) >= _num(proxy_60d_max) + 10:
            tags.append("rs60_market_leader")

    return {
        "stock_return_20d_pct": round(_num(stock_20d), 2) if stock_20d is not None else None,
        "stock_return_60d_pct": round(_num(stock_60d), 2) if stock_60d is not None else None,
        "relative_strength_20d_pct": round(rel_20d, 2) if rel_20d is not None else None,
        "relative_strength_60d_pct": round(rel_60d, 2) if rel_60d is not None else None,
        **proxy_returns,
        "tags": sorted(set(tags)),
    }


def _market_breadth_context(analysis_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for row in analysis_rows:
        result = row.get("result") or {}
        candidate = row.get("candidate") or {}
        indicators = result.get("indicators") or {}
        close = _num(result.get("last_close"))
        ma20 = _num(indicators.get("ma20"))
        ma60 = _num(indicators.get("ma60"))
        amount = _num(candidate.get("amount"))
        if close <= 0 or ma20 <= 0 or ma60 <= 0:
            continue
        rows.append(
            {
                "above_ma20": close >= ma20,
                "above_ma60": close >= ma60,
                "return_20d_positive": _num(indicators.get("return_20d_pct")) > 0,
                "return_20d_pct": _num(indicators.get("return_20d_pct")),
                "advancing": _num(candidate.get("change_pct")) > 0,
                "liquid_300m": amount >= 300_000_000,
            }
        )

    sample_count = len(rows)
    if not sample_count:
        return {"sample_count": 0, "tags": []}
    returns = sorted(row["return_20d_pct"] for row in rows)
    midpoint = sample_count // 2
    median_return = (
        (returns[midpoint - 1] + returns[midpoint]) / 2 if sample_count % 2 == 0 else returns[midpoint]
    )
    context = {
        "sample_count": sample_count,
        "scope": "current_deep_scan_candidates",
        "above_ma20_pct": round(sum(1 for row in rows if row["above_ma20"]) / sample_count * 100, 2),
        "above_ma60_pct": round(sum(1 for row in rows if row["above_ma60"]) / sample_count * 100, 2),
        "return_20d_positive_pct": round(
            sum(1 for row in rows if row["return_20d_positive"]) / sample_count * 100,
            2,
        ),
        "advancing_pct": round(sum(1 for row in rows if row["advancing"]) / sample_count * 100, 2),
        "liquid_300m_pct": round(sum(1 for row in rows if row["liquid_300m"]) / sample_count * 100, 2),
        "median_return_20d_pct": round(median_return, 2),
    }
    context["tags"] = build_market_breadth_tags(context)
    return context


def _score_result(result: Dict[str, Any], candidate: Dict[str, Any], market_context: Dict[str, Any]) -> float:
    action_bonus = {"BUY": 25, "WATCH": 12, "HOLD": 0, "REDUCE": -20, "SELL": -35}.get(
        result.get("action"), 0
    )
    quality = result.get("strategy_quality") or {}
    industry = result.get("industry") or {}
    news = result.get("news_context") or {}
    announcement = result.get("announcement_context") or {}
    fund_flow = result.get("fund_flow_context") or {}
    return round(
        float(result.get("score") or 0) * 12
        + float(result.get("confidence") or 0)
        + action_bonus
        + float(candidate.get("prefilter_score") or 0)
        + _num(quality.get("score_bonus"))
        + _num(industry.get("score_bonus"))
        + _num(news.get("score_adjustment"))
        + _num(announcement.get("score_adjustment"))
        + _num(fund_flow.get("score_adjustment"))
        + _num(market_context.get("score_adjustment")),
        4,
    )


def _strategy_quality(backtest: Dict[str, Any], settings: Settings) -> Dict[str, Any]:
    outcomes = backtest.get("signal_outcomes") or {}
    trade_count = int(outcomes.get("signal_count") or backtest.get("trade_count") or 0)
    win_rate = _num(outcomes.get("win_rate_pct"), _num(backtest.get("win_rate_pct")))
    max_drawdown = abs(_num(backtest.get("max_drawdown_pct")))
    strategy_return = _num(outcomes.get("avg_return_pct"), _num(backtest.get("strategy_return_pct")))
    avg_adverse = abs(_num(outcomes.get("avg_adverse_pct")))
    buy_hold_return = _num(backtest.get("buy_hold_return_pct"))
    edge = strategy_return
    reasons = []
    passed = True

    if trade_count < settings.min_backtest_trades:
        passed = False
        reasons.append("历史触发次数不足")
    if win_rate < settings.min_backtest_win_rate:
        passed = False
        reasons.append("历史胜率偏低")
    if strategy_return < settings.min_backtest_avg_return:
        passed = False
        reasons.append("历史平均收益不足")
    if max_drawdown > settings.max_backtest_drawdown:
        passed = False
        reasons.append("历史最大回撤偏大")
    if avg_adverse > settings.max_backtest_avg_adverse:
        passed = False
        reasons.append("历史平均不利波动偏大")

    score_bonus = edge * 1.2 + (win_rate - 50) * 0.25 - max(0, avg_adverse - 4) * 1.2 - max(0, max_drawdown - 18) * 0.5
    score_bonus = max(min(score_bonus, 18), -25)
    return {
        "passed": passed,
        "trade_count": trade_count,
        "win_rate_pct": round(win_rate, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "strategy_return_pct": round(strategy_return, 2),
        "avg_adverse_pct": round(avg_adverse, 2),
        "buy_hold_return_pct": round(buy_hold_return, 2),
        "edge_pct": round(edge, 2),
        "hold_days": outcomes.get("hold_days"),
        "skipped_unexecutable_count": outcomes.get("skipped_unexecutable_count", 0),
        "score_bonus": round(score_bonus, 2),
        "reasons": reasons,
    }


def _industry_context(candidate: Dict[str, Any], industry_map: Dict[str, Any]) -> Dict[str, Any]:
    payload = industry_map.get(candidate.get("symbol"), {}) if industry_map else {}
    if not payload:
        return {"industry": None, "industry_rank": None, "score_bonus": 0}
    rank = int(payload.get("industry_rank") or 99)
    score_bonus = max(12 - rank, 2) + _num(payload.get("industry_score")) * 0.15
    return {
        "industry": payload.get("industry"),
        "industry_rank": rank,
        "industry_change_pct": payload.get("industry_change_pct"),
        "score_bonus": round(min(score_bonus, 14), 2),
    }


def _strict_signal_status(
    result: Dict[str, Any],
    candidate: Dict[str, Any],
    quality: Dict[str, Any],
    market_context: Dict[str, Any],
    settings: Settings,
    extra_signal_tags: List[str] = None,
) -> Dict[str, Any]:
    tags = sorted(
        set(
            build_signal_tags(result)
            + build_candidate_context_tags(candidate, quality)
            + list(extra_signal_tags or [])
        )
    )
    tag_set = set(tags)
    reasons = []
    passed = True

    min_score = _num(settings.recommendation_min_signal_score)
    if _num(result.get("score")) < min_score:
        passed = False
        reasons.append("signal_score_below_min")

    allowed_market_levels = set(settings.recommendation_allowed_market_levels or [])
    if allowed_market_levels and market_context.get("level") not in allowed_market_levels:
        passed = False
        reasons.append("market_level_not_allowed")

    required_tags = set(settings.recommendation_required_signal_tags or [])
    if required_tags and settings.recommendation_require_all_signal_tags:
        missing = sorted(required_tags - tag_set)
        if missing:
            passed = False
            reasons.append("missing_signal_tags:%s" % ",".join(missing))
    elif required_tags and not (required_tags & tag_set):
        passed = False
        reasons.append("missing_any_signal_tag")

    excluded_tags = set(settings.recommendation_excluded_signal_tags or [])
    blocked = sorted(excluded_tags & tag_set)
    if blocked:
        passed = False
        reasons.append("excluded_signal_tags:%s" % ",".join(blocked))

    return {
        "passed": passed,
        "tags": tags,
        "min_signal_score": min_score,
        "required_tags": sorted(required_tags),
        "require_all_tags": bool(settings.recommendation_require_all_signal_tags),
        "excluded_tags": sorted(excluded_tags),
        "allowed_market_levels": sorted(allowed_market_levels),
        "reasons": reasons,
    }


def _l1_context_tags(l1_quote: Dict[str, Any]) -> List[str]:
    return list((l1_quote or {}).get("tags") or [])


def _compact_analysis(
    result: Dict[str, Any],
    candidate: Dict[str, Any],
    settings: Settings,
    industry_map: Dict[str, Any],
    market_context: Dict[str, Any],
    news_context: Dict[str, Any] = None,
    announcement_context: Dict[str, Any] = None,
    fund_flow_context: Dict[str, Any] = None,
    relative_strength_context: Dict[str, Any] = None,
    market_breadth_context: Dict[str, Any] = None,
    price_action_context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    quality = _strategy_quality(result.get("backtest") or {}, settings)
    industry = _industry_context(candidate, industry_map)
    relative_strength_context = relative_strength_context or {}
    market_breadth_context = market_breadth_context or {}
    price_action_context = price_action_context or {}
    announcement_context = announcement_context or {}
    strict_signal = _strict_signal_status(
        result,
        candidate,
        quality,
        market_context,
        settings,
        extra_signal_tags=(
            list(relative_strength_context.get("tags") or [])
            + list(price_action_context.get("tags") or [])
            + list(market_breadth_context.get("tags") or [])
            + _l1_context_tags(candidate.get("l1_quote") or {})
            + build_announcement_tags(announcement_context)
        ),
    )
    news_context = news_context or {}
    fund_flow_context = fund_flow_context or {}
    risks = list(result["risks"])
    if announcement_context.get("level") in {"high_risk", "watch_risk"}:
        risks.insert(0, "公告披露存在风险信号，需暂停或人工复核。")
    if news_context.get("level") in {"high_risk", "watch_risk"}:
        risks.insert(0, "外部消息存在风险信号，需人工复核。")
    if fund_flow_context.get("level") in {"high_outflow", "outflow_risk"}:
        risks.insert(0, "近期主力资金流出，需降低仓位或等待确认。")
    compact = {
        "symbol": result["symbol"],
        "market": result["market"],
        "name": result.get("name") or candidate.get("name"),
        "as_of": result["as_of"],
        "action": result["action"],
        "action_label": result["action_label"],
        "score": result["score"],
        "confidence": result["confidence"],
        "last_close": result["last_close"],
        "levels": result["levels"],
        "entry_zone": result["entry_zone"],
        "trade_plans": result.get("trade_plans", {}),
        "reasons": result["reasons"],
        "risks": risks[:5],
        "indicators": result["indicators"],
        "signal_tags": strict_signal["tags"],
        "strict_signal": strict_signal,
        "strategy_quality": quality,
        "industry": industry,
        "relative_strength": {
            key: value for key, value in relative_strength_context.items() if key != "tags"
        },
        "market_breadth": {
            key: value for key, value in market_breadth_context.items() if key != "tags"
        },
        "price_action": {
            key: value for key, value in price_action_context.items() if key != "tags"
        },
        "l1_quote": candidate.get("l1_quote") or {},
        "margin_eligibility": {
            key: value
            for key, value in (candidate.get("margin_eligibility") or {}).items()
            if key in {
                "exchange",
                "financing_underlying",
                "financing_eligible",
                "short_underlying",
                "short_eligible",
                "collateral_eligible",
                "price_limit",
                "as_of",
                "sources",
            }
        },
        "news_context": {
            "level": news_context.get("level", "neutral"),
            "score": news_context.get("score", 0),
            "score_adjustment": news_context.get("score_adjustment", 0),
            "allow_recommendation": news_context.get("allow_recommendation", True),
            "article_count": news_context.get("article_count", 0),
            "negative_count": news_context.get("negative_count", 0),
            "positive_count": news_context.get("positive_count", 0),
            "headlines": news_context.get("headlines", []),
            "errors": news_context.get("errors", []),
        },
        "announcement_context": {
            "level": announcement_context.get("level", "neutral"),
            "score": announcement_context.get("score", 0),
            "score_adjustment": announcement_context.get("score_adjustment", 0),
            "allow_recommendation": announcement_context.get("allow_recommendation", True),
            "announcement_count": announcement_context.get("announcement_count", 0),
            "negative_count": announcement_context.get("negative_count", 0),
            "positive_count": announcement_context.get("positive_count", 0),
            "event_counts": announcement_context.get("event_counts", {}),
            "announcements": announcement_context.get("announcements", []),
            "errors": announcement_context.get("errors", []),
        },
        "fund_flow_context": {
            "level": fund_flow_context.get("level", "neutral"),
            "score_adjustment": fund_flow_context.get("score_adjustment", 0),
            "allow_recommendation": fund_flow_context.get("allow_recommendation", True),
            "main_net_amount_3d": fund_flow_context.get("main_net_amount_3d", 0),
            "main_net_ratio_3d": fund_flow_context.get("main_net_ratio_3d", 0),
            "positive_days_3d": fund_flow_context.get("positive_days_3d", 0),
            "latest_main_net_ratio": fund_flow_context.get("latest_main_net_ratio", 0),
            "errors": fund_flow_context.get("errors", []),
        },
        "market_context": {
            "level": market_context.get("level"),
            "label": market_context.get("label"),
            "score_adjustment": market_context.get("score_adjustment"),
        },
        "exit_plan": {
            "profit_lock_activation_pct": settings.monitor_profit_lock_activation_pct,
            "profit_lock_fraction": 1.0,
            "profit_lock_execution": "next_open_after_prior_completed_high",
        },
        "prefilter": {
            "latest": candidate.get("latest"),
            "amount": candidate.get("amount"),
            "change_pct": candidate.get("change_pct"),
            "prefilter_score": candidate.get("prefilter_score"),
        },
    }
    compact["rank_score"] = _score_result(compact, candidate, market_context)
    return compact


class RecommendationService:
    def __init__(self, settings: Settings, data_provider, disclaimer: str) -> None:
        self.settings = settings
        self.data_provider = data_provider
        self.disclaimer = disclaimer
        self.universe = AShareUniverseProvider(settings.universe_cache_path)
        self.industry = IndustryStrengthProvider(settings.industry_cache_path, settings.industry_top_n)
        self.industry_history = IndustryHistoryProvider(settings.industry_history_cache_dir)
        self.fund_flow = FundFlowContextProvider(
            settings.fund_flow_cache_path,
            settings.enable_fund_flow_context,
        )
        self.news = NewsContextProvider(
            settings.news_cache_path,
            settings.news_lookback_days,
            settings.enable_news_context,
        )
        self.announcements = AnnouncementContextProvider(
            settings.announcement_cache_path,
            settings.announcement_lookback_days,
            settings.enable_announcement_context,
        )
        self.margin_eligibility = MarginEligibilityProvider(
            settings.margin_eligibility_cache_path,
            settings.enable_margin_eligibility_context,
        )
        self.l1_quotes = MootdxL1QuoteProvider(
            servers=settings.mootdx_servers,
            timeout_seconds=settings.mootdx_timeout_seconds,
            enabled=settings.enable_mootdx_l1_context,
        )

    def latest(self) -> Dict[str, Any]:
        return read_json(
            self.settings.latest_recommendations_path,
            {
                "generated_at": None,
                "trade_date": None,
                "items": [],
                "errors": [],
                "summary": {},
                "disclaimer": self.disclaimer,
            },
        )

    def _lock_metadata(self) -> Dict[str, Any]:
        return read_json(self.settings.recommendation_lock_path, {})

    def _lock_is_stale(self, metadata: Dict[str, Any]) -> bool:
        started_at = metadata.get("started_at")
        try:
            started = datetime.fromisoformat(started_at)
        except Exception:
            return True
        try:
            return (now_cn() - started).total_seconds() > LOCK_STALE_SECONDS
        except TypeError:
            return True

    def _acquire_recommendation_lock(self) -> Optional[str]:
        lock_path = Path(self.settings.recommendation_lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        token = str(uuid.uuid4())
        payload = {
            "token": token,
            "started_at": now_cn().isoformat(),
            "pid": os.getpid(),
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        for attempt in range(2):
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                metadata = self._lock_metadata()
                if attempt == 0 and self._lock_is_stale(metadata):
                    try:
                        lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                return None
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.write(b"\n")
            return token
        return None

    def _release_recommendation_lock(self, token: Optional[str]) -> None:
        if not token:
            return
        lock_path = Path(self.settings.recommendation_lock_path)
        metadata = self._lock_metadata()
        if metadata.get("token") != token:
            return
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

    def _already_running_payload(self, max_deep: int = None, run_slot: str = RUN_SLOT_AUTO) -> Dict[str, Any]:
        slot_context = _resolve_run_slot(run_slot, now_cn())
        current = self.latest()
        summary = dict(current.get("summary") or {})
        summary.update(
            {
                "running": True,
                "reason": "already_running",
                "max_deep": max_deep or summary.get("max_deep") or self.settings.scan_max_deep,
                "run_slot": slot_context,
                "lock": self._lock_metadata(),
            }
        )
        current["run_slot"] = slot_context["slot"]
        current["run_slot_label"] = slot_context["label"]
        current["summary"] = summary
        current["disclaimer"] = self.disclaimer
        return current

    def begin_recommendation_run(
        self,
        max_deep: int = None,
        run_slot: str = RUN_SLOT_AUTO,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        token = self._acquire_recommendation_lock()
        if not token:
            return self._already_running_payload(max_deep=max_deep, run_slot=run_slot), None
        return self.mark_recommendations_started(max_deep=max_deep, run_slot=run_slot), token

    def mark_recommendations_started(
        self,
        max_deep: int = None,
        run_slot: str = RUN_SLOT_AUTO,
    ) -> Dict[str, Any]:
        slot_context = _resolve_run_slot(run_slot, now_cn())
        current = self.latest()
        payload = {
            "generated_at": now_cn().isoformat(),
            "trade_date": now_cn().date().isoformat(),
            "run_slot": slot_context["slot"],
            "run_slot_label": slot_context["label"],
            "items": current.get("items", []),
            "errors": [],
            "summary": {
                "running": True,
                "max_deep": _slot_max_deep(self.settings, slot_context, max_deep),
                "run_slot": slot_context,
                "previous_generated_at": current.get("generated_at"),
            },
            "disclaimer": self.disclaimer,
        }
        write_json(self.settings.latest_recommendations_path, payload)
        return payload

    def recent_alerts(self, limit: int = 100) -> List[Dict[str, Any]]:
        alerts = read_jsonl(self.settings.alerts_path, limit=max(limit, 500))
        alerts.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return alerts[:limit]

    def _recent_recommended_symbols(self, moment: datetime, cooldown_days: int) -> set[str]:
        if cooldown_days <= 0:
            return set()
        cutoff = moment - timedelta(days=cooldown_days)
        history = read_jsonl(self.settings.recommendation_history_path, limit=1000)
        latest = self.latest()
        if latest.get("items"):
            history.append(latest)

        symbols = set()
        for run in history:
            generated_at = run.get("generated_at")
            try:
                generated = datetime.fromisoformat(generated_at)
            except Exception:
                continue
            if generated < cutoff:
                continue
            for item in run.get("items", []):
                if item.get("market") == "a" and item.get("symbol"):
                    symbols.add(str(item["symbol"]))
        return symbols

    def generate_daily_recommendations(
        self,
        force: bool = False,
        max_deep: int = None,
        result_limit: int = None,
        lock_token: Optional[str] = None,
        run_slot: str = RUN_SLOT_AUTO,
    ) -> Dict[str, Any]:
        token = lock_token or self._acquire_recommendation_lock()
        if not token:
            return self._already_running_payload(max_deep=max_deep, run_slot=run_slot)
        try:
            return self._generate_daily_recommendations_unlocked(force, max_deep, result_limit, run_slot)
        except Exception as exc:
            moment = now_cn()
            slot_context = _resolve_run_slot(run_slot, moment)
            payload = {
                "generated_at": moment.isoformat(),
                "trade_date": moment.date().isoformat(),
                "run_slot": slot_context["slot"],
                "run_slot_label": slot_context["label"],
                "items": [],
                "errors": [{"message": str(exc)}],
                "summary": {"running": False, "failed": True, "run_slot": slot_context},
                "disclaimer": self.disclaimer,
            }
            write_json(self.settings.latest_recommendations_path, payload)
            return payload
        finally:
            self._release_recommendation_lock(token)

    def _generate_daily_recommendations_unlocked(
        self,
        force: bool = False,
        max_deep: int = None,
        result_limit: int = None,
        run_slot: str = RUN_SLOT_AUTO,
    ) -> Dict[str, Any]:
        started = now_cn()
        slot_context = _resolve_run_slot(run_slot, started)
        if not force and not is_trade_day(started.date()):
            payload = {
                "generated_at": started.isoformat(),
                "trade_date": started.date().isoformat(),
                "run_slot": slot_context["slot"],
                "run_slot_label": slot_context["label"],
                "items": [],
                "errors": [],
                "summary": {"skipped": True, "reason": "not_trade_day", "run_slot": slot_context},
                "disclaimer": self.disclaimer,
            }
            write_json(self.settings.latest_recommendations_path, payload)
            return payload

        max_deep = _slot_max_deep(self.settings, slot_context, max_deep)
        result_limit = result_limit or self.settings.scan_result_limit
        market_context = evaluate_market_regime(self.data_provider, self.disclaimer)
        industry_payload = self.industry.build_map(use_cache_on_error=True)
        industry_map = industry_payload.get("symbol_map", {})
        snapshot = self.universe.snapshot(use_cache_on_error=True)
        margin_payload = {"summary": {"enabled": False}, "symbol_map": {}, "errors": []}
        try:
            margin_payload = self.margin_eligibility.build_map(use_cache_on_error=True)
        except Exception as exc:
            margin_payload = {
                "summary": {"enabled": self.settings.enable_margin_eligibility_context, "error": str(exc)},
                "symbol_map": {},
                "errors": [{"source": "margin_eligibility", "message": str(exc)}],
            }
        margin_map = margin_payload.get("symbol_map") or {}
        candidates = select_deep_scan_candidates(
            snapshot=snapshot,
            max_deep=max_deep,
            min_amount=self.settings.scan_min_amount,
            min_price=self.settings.scan_min_price,
            max_price=self.settings.scan_max_price,
            industry_map=industry_map,
            per_industry_top_n=self.settings.scan_per_industry_top_n,
            industry_top_n=self.settings.scan_industry_top_n,
        )
        for candidate in candidates:
            margin_context = margin_map.get(str(candidate.get("symbol") or ""))
            if margin_context:
                candidate["margin_eligibility"] = margin_context
        if slot_context.get("use_l1_context", True):
            l1_payload = self.l1_quotes.quotes([str(candidate.get("symbol") or "") for candidate in candidates])
        else:
            l1_payload = {
                "enabled": bool(getattr(self.l1_quotes, "enabled", False)),
                "available": False,
                "quotes": {},
                "errors": [],
                "skipped": True,
                "reason": "run_slot_without_l1_context",
            }
        l1_map = l1_payload.get("quotes") or {}
        for candidate in candidates:
            l1_quote = l1_map.get(str(candidate.get("symbol") or ""))
            if l1_quote:
                candidate["l1_quote"] = l1_quote
                candidate["latest"] = l1_quote.get("price") or candidate.get("latest")
                candidate["amount"] = l1_quote.get("amount") or candidate.get("amount")
                if l1_quote.get("change_pct") is not None:
                    candidate["change_pct"] = l1_quote.get("change_pct")
        cooldown_symbols = self._recent_recommended_symbols(
            started,
            self.settings.recommendation_symbol_cooldown_days,
        )

        analysis_rows: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        cooldown_skipped_count = 0
        for candidate in candidates:
            try:
                if str(candidate.get("symbol")) in cooldown_symbols:
                    cooldown_skipped_count += 1
                    continue
                result = build_analysis(
                    AnalyzeRequest(
                        symbol=candidate["symbol"],
                        market="a",
                        name=candidate.get("name"),
                        lookback_days=360,
                        adjust="qfq",
                    ),
                    self.data_provider,
                    self.disclaimer,
                )
                result = model_to_dict(result)
                analysis_rows.append({"candidate": candidate, "result": result})
            except Exception as exc:
                errors.append(
                    {
                        "symbol": candidate.get("symbol"),
                        "name": candidate.get("name"),
                        "message": str(exc),
                    }
                )

        proxy_returns, proxy_errors = _proxy_return_context(self.data_provider)
        errors.extend(proxy_errors)
        market_breadth = _market_breadth_context(analysis_rows)

        items: List[Dict[str, Any]] = []
        for row in analysis_rows:
            candidate = row["candidate"]
            result = row["result"]
            try:
                relative_strength = _relative_strength_context(result, proxy_returns)
                price_action = _price_action_context(result, candidate)
                compact = _compact_analysis(
                    result,
                    candidate,
                    self.settings,
                    industry_map,
                    market_context,
                    relative_strength_context=relative_strength,
                    market_breadth_context=market_breadth,
                    price_action_context=price_action,
                )
                allowed_actions = {"BUY", "WATCH"} if market_context.get("allow_watch", True) else {"BUY"}
                min_score = _num(market_context.get("min_signal_score"), 2)
                if (
                    compact["action"] in allowed_actions
                    and compact["score"] >= min_score
                    and compact["strict_signal"]["passed"]
                    and compact["strategy_quality"]["passed"]
                ):
                    news_context = self.news.evaluate(candidate["symbol"], use_cache_on_error=True)
                    announcement_context = self.announcements.evaluate(candidate["symbol"], use_cache_on_error=True)
                    fund_flow_context = self.fund_flow.evaluate(candidate["symbol"], use_cache_on_error=True)
                    compact = _compact_analysis(
                        result,
                        candidate,
                        self.settings,
                        industry_map,
                        market_context,
                        news_context,
                        announcement_context,
                        fund_flow_context,
                        relative_strength_context=relative_strength,
                        market_breadth_context=market_breadth,
                        price_action_context=price_action,
                    )
                    if (
                        compact["news_context"].get("allow_recommendation", True)
                        and compact["announcement_context"].get("allow_recommendation", True)
                        and compact["fund_flow_context"].get("allow_recommendation", True)
                    ):
                        items.append(compact)
            except Exception as exc:
                errors.append(
                    {
                        "symbol": candidate.get("symbol"),
                        "name": candidate.get("name"),
                        "message": str(exc),
                    }
                )

        items.sort(key=lambda item: item.get("rank_score", 0), reverse=True)
        selected = items[:result_limit]
        hot_industry_payload = _hot_industry_windows(
            industry_payload=industry_payload,
            candidates=candidates,
            history_provider=self.industry_history,
            as_of_date=started.date().isoformat(),
            limit=self.settings.scan_industry_top_n,
        )
        hot_industries_by_window = hot_industry_payload["by_window"]
        hot_industries = hot_industries_by_window.get("1d", [])
        payload = {
            "generated_at": now_cn().isoformat(),
            "trade_date": started.date().isoformat(),
            "run_slot": slot_context["slot"],
            "run_slot_label": slot_context["label"],
            "items": selected,
            "errors": errors[:50],
            "summary": {
                "running": False,
                "run_slot": slot_context,
                "snapshot_count": len(snapshot),
                "candidate_count": len(candidates),
                "candidate_industry_count": len({item.get("industry") for item in candidates if item.get("industry")}),
                "industry_top_n": self.settings.scan_industry_top_n,
                "per_industry_top_n": self.settings.scan_per_industry_top_n,
                "hot_industries": hot_industries,
                "hot_industries_by_window": hot_industries_by_window,
                "hot_industry_errors": hot_industry_payload.get("errors", [])[:10],
                "qualified_count": len(items),
                "returned_count": len(selected),
                "max_deep": max_deep,
                "result_limit": result_limit,
                "market_context": market_context,
                "market_breadth": {
                    key: value for key, value in market_breadth.items() if key != "tags"
                },
                "market_breadth_tags": market_breadth.get("tags", []),
                "relative_strength_proxy": proxy_returns,
                "margin_eligibility": margin_payload.get("summary") or {},
                "margin_eligibility_errors": (margin_payload.get("errors") or [])[:5],
                "l1_quote": {
                    "enabled": bool(l1_payload.get("enabled")),
                    "available": bool(l1_payload.get("available")),
                    "quote_count": len(l1_map),
                    "elapsed_seconds": l1_payload.get("elapsed_seconds"),
                    "skipped": bool(l1_payload.get("skipped")),
                    "reason": l1_payload.get("reason"),
                    "errors": (l1_payload.get("errors") or [])[:5],
                },
                "strict_signal_filter": {
                    "min_signal_score": self.settings.recommendation_min_signal_score,
                    "required_signal_tags": self.settings.recommendation_required_signal_tags or [],
                    "require_all_signal_tags": self.settings.recommendation_require_all_signal_tags,
                    "excluded_signal_tags": self.settings.recommendation_excluded_signal_tags or [],
                    "allowed_market_levels": self.settings.recommendation_allowed_market_levels or [],
                    "symbol_cooldown_days": self.settings.recommendation_symbol_cooldown_days,
                    "profit_lock_activation_pct": self.settings.monitor_profit_lock_activation_pct,
                },
                "cooldown_skipped_count": cooldown_skipped_count,
                "industry_count": len(industry_payload.get("industries", [])),
            },
            "disclaimer": self.disclaimer,
        }
        write_json(self.settings.latest_recommendations_path, payload)
        append_jsonl(self.settings.recommendation_history_path, payload)
        return payload

    def monitor_recommendations(self, force: bool = False) -> Dict[str, Any]:
        moment = now_cn()
        if not force and not is_a_share_trading_time(moment):
            return {
                "checked_at": moment.isoformat(),
                "skipped": True,
                "reason": "outside_a_share_trading_time",
                "alerts": [],
            }

        monitored = self._recent_recommended_items(moment)
        errors = []
        l1_payload = self.l1_quotes.quotes([str(item.get("symbol") or "") for item in monitored])
        l1_map = l1_payload.get("quotes") or {}
        snapshot_map = {
            "a:%s" % symbol: {
                "symbol": symbol,
                "market": "a",
                "latest": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "amount": quote.get("amount"),
                "l1_quote": quote,
            }
            for symbol, quote in l1_map.items()
        }
        missing_symbols = {
            str(item.get("symbol") or "")
            for item in monitored
            if str(item.get("symbol") or "") and str(item.get("symbol") or "") not in l1_map
        }
        if missing_symbols:
            try:
                for item in self.universe.snapshot(use_cache_on_error=True):
                    key = _recommendation_key(item)
                    if key in snapshot_map:
                        continue
                    if str(item.get("symbol") or "") in missing_symbols:
                        snapshot_map[key] = item
            except Exception as exc:
                errors.append({"source": "a_share_universe", "message": str(exc)})

        existing_ids = {item.get("id") for item in read_jsonl(self.settings.alerts_path, limit=3000)}

        alerts = []
        for item in monitored:
            try:
                alert = self._evaluate_alert(item, snapshot_map)
            except Exception as exc:
                errors.append({"symbol": item.get("symbol"), "message": str(exc)})
                continue
            if not alert:
                continue
            if alert["id"] in existing_ids:
                continue
            append_jsonl(self.settings.alerts_path, alert)
            self._send_webhook(alert)
            alerts.append(alert)
            existing_ids.add(alert["id"])

        return {
            "checked_at": moment.isoformat(),
            "skipped": False,
            "monitored_count": len(monitored),
            "l1_quote": {
                "enabled": bool(l1_payload.get("enabled")),
                "available": bool(l1_payload.get("available")),
                "quote_count": len(l1_map),
                "elapsed_seconds": l1_payload.get("elapsed_seconds"),
                "errors": (l1_payload.get("errors") or [])[:5],
            },
            "alerts": alerts,
            "errors": errors[:50],
        }

    def monitor_planned_exits(self, force: bool = False) -> Dict[str, Any]:
        """盘后日级：对近期推荐扫描计划退出（profit-lock / 长假前退出），发执行性 alert。

        与盘中 ``monitor_recommendations`` 并列——计划性退出走日级（用已完成日线
        + 交易日历），止损 / 支撑 / 盘中跌幅走盘中实时。

        - profit-lock exit：PLAN.md 达标功臣，T+1 开盘成交口径（对齐回测
          ``_apply_partial_profit_lock``），alert id 含 today 每日复核。
        - calendar gap exit：长假前最后一交易日收盘退出（对齐回测
          ``_truncate_trade_before_calendar_gap``），alert id 含 exit_date 跨日去重。
        - 前日高点 trailing 留后续刀。
        """
        moment = now_cn()
        monitored = self._recent_recommended_items(moment)
        errors: List[Dict[str, Any]] = []
        existing_ids = {item.get("id") for item in read_jsonl(self.settings.alerts_path, limit=3000)}
        today_date = moment.date()
        today_iso = today_date.isoformat()
        next_trade = next_trade_date(today_date)
        profit_lock_exit_date = (
            next_trade.isoformat() if next_trade else (today_date + timedelta(days=1)).isoformat()
        )
        gap_days = self.settings.monitor_pre_exit_calendar_gap_days
        calendar_gap = next_calendar_gap(today_date, gap_days) if gap_days > 0 else None

        planned_exits: List[Dict[str, Any]] = []
        for item in monitored:
            symbol = item["symbol"]

            try:
                context = self._profit_lock_alert_context(
                    item, item.get("last_close"), include_today=True
                )
            except Exception as exc:
                errors.append({"symbol": symbol, "message": str(exc)})
                context = {}
            if context:
                alert_id = "%s:%s:planned_profit_lock_exit" % (today_iso, symbol)
                if alert_id not in existing_ids:
                    activation_pct = context["activation_pct"]
                    alert = {
                        "id": alert_id,
                        "created_at": moment.isoformat(),
                        "event_type": "planned_profit_lock_exit",
                        "severity": "warning",
                        "symbol": symbol,
                        "market": "a",
                        "name": item.get("name"),
                        "title": "计划退出：利润保护（次日开盘）",
                        "message": (
                            "已完成日线最高价 %.3f 较参考价 %.3f 达到 %.2f%% 利润保护阈值，"
                            "按策略应于 %s 以开盘价退出。"
                            % (
                                context["prior_high"],
                                context["reference_price"],
                                activation_pct,
                                profit_lock_exit_date,
                            )
                        ),
                        "exit_date": profit_lock_exit_date,
                        "exit_price_type": "next_open",
                        "exit_price": None,
                        "reference_price": context["reference_price"],
                        "prior_high": context["prior_high"],
                        "trigger_price": context["trigger_price"],
                        "activation_pct": activation_pct,
                        "recommended_at": item.get("recommended_at"),
                        "recommendation_action": item.get("action"),
                        "recommendation_score": item.get("score"),
                    }
                    append_jsonl(self.settings.alerts_path, alert)
                    self._send_webhook(alert)
                    planned_exits.append(alert)
                    existing_ids.add(alert_id)

            if calendar_gap:
                last_before, first_after = calendar_gap
                if last_before >= today_date:
                    gap_actual = (first_after - last_before).days
                    cg_exit_date = last_before.isoformat()
                    cg_alert_id = "calendar_gap_exit:%s:%s" % (cg_exit_date, symbol)
                    if cg_alert_id not in existing_ids:
                        alert = {
                            "id": cg_alert_id,
                            "created_at": moment.isoformat(),
                            "event_type": "planned_calendar_gap_exit",
                            "severity": "warning",
                            "symbol": symbol,
                            "market": "a",
                            "name": item.get("name"),
                            "title": "计划退出：长假前收盘",
                            "message": (
                                "未来存在 %d 日历日的休市跳空，按策略应于 %s"
                                "（假期前最后一交易日）收盘退出。" % (gap_actual, cg_exit_date)
                            ),
                            "exit_date": cg_exit_date,
                            "exit_price_type": "close",
                            "exit_price": None,
                            "calendar_gap_days": gap_actual,
                            "recommended_at": item.get("recommended_at"),
                            "recommendation_action": item.get("action"),
                            "recommendation_score": item.get("score"),
                        }
                        append_jsonl(self.settings.alerts_path, alert)
                        self._send_webhook(alert)
                        planned_exits.append(alert)
                        existing_ids.add(cg_alert_id)

        return {
            "checked_at": moment.isoformat(),
            "skipped": False,
            "monitored_count": len(monitored),
            "next_trade_date": profit_lock_exit_date,
            "planned_exits": planned_exits,
            "errors": errors[:50],
        }

    def _recent_recommended_items(self, moment: datetime) -> List[Dict[str, Any]]:
        cutoff = moment - timedelta(days=self.settings.monitor_recent_days)
        history = read_jsonl(self.settings.recommendation_history_path, limit=500)
        latest = self.latest()
        if latest.get("items"):
            history.append(latest)

        items: List[Dict[str, Any]] = []
        for run in history:
            generated_at = run.get("generated_at")
            try:
                generated = datetime.fromisoformat(generated_at)
            except Exception:
                generated = moment
            if generated < cutoff:
                continue
            for item in run.get("items", []):
                if item.get("market") == "a":
                    merged = dict(item)
                    merged["recommended_at"] = generated_at
                    items.append(merged)
        return unique_by_key(reversed(items), "symbol")

    def _evaluate_alert(
        self,
        recommendation: Dict[str, Any],
        snapshot_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        key = _recommendation_key(recommendation)
        snapshot = snapshot_map.get(key, {})
        latest_price = snapshot.get("latest") or recommendation.get("last_close")
        change_pct = float(snapshot.get("change_pct") or 0)
        levels = recommendation.get("levels") or {}
        stop_loss = levels.get("stop_loss")
        support = levels.get("support")
        take_profit = levels.get("take_profit")

        event_type = None
        severity = "info"
        title = ""
        reason = ""
        if stop_loss and latest_price and float(latest_price) <= float(stop_loss):
            event_type = "stop_loss"
            severity = "critical"
            title = "跌破止损"
            reason = "现价 %.3f 已低于推荐时止损 %.3f。" % (float(latest_price), float(stop_loss))
        elif support and latest_price and float(latest_price) < float(support):
            event_type = "support_break"
            severity = "warning"
            title = "跌破支撑"
            reason = "现价 %.3f 已低于推荐时支撑 %.3f。" % (float(latest_price), float(support))
        elif take_profit and latest_price and float(latest_price) >= float(take_profit):
            event_type = "take_profit"
            severity = "info"
            title = "达到止盈"
            reason = "现价 %.3f 已达到推荐时止盈 %.3f，可按计划止盈或上移止损。" % (
                float(latest_price),
                float(take_profit),
            )
        elif change_pct <= -abs(self.settings.monitor_intraday_drop_pct):
            event_type = "intraday_drop"
            severity = "warning"
            title = "盘中跌幅扩大"
            reason = "盘中跌幅 %.2f%%，超过监控阈值 %.2f%%。" % (
                change_pct,
                self.settings.monitor_intraday_drop_pct,
            )
        else:
            analysis = build_analysis(
                AnalyzeRequest(
                    symbol=recommendation["symbol"],
                    market="a",
                    name=recommendation.get("name"),
                    lookback_days=240,
                    adjust="qfq",
                ),
                self.data_provider,
                self.disclaimer,
            )
            analysis = model_to_dict(analysis)
            if analysis["action"] in {"SELL", "REDUCE"}:
                event_type = "signal_weak"
                severity = "warning" if analysis["action"] == "REDUCE" else "critical"
                title = analysis["action_label"]
                reason = "策略信号已转为 %s，评分 %.2f。" % (analysis["action"], analysis["score"])

        if not event_type:
            return {}

        today = now_cn().date().isoformat()
        symbol = recommendation["symbol"]
        alert_id = "%s:%s:%s" % (today, symbol, event_type)
        return {
            "id": alert_id,
            "created_at": now_cn().isoformat(),
            "event_type": event_type,
            "severity": severity,
            "symbol": symbol,
            "market": "a",
            "name": recommendation.get("name"),
            "title": title,
            "message": reason,
            "latest_price": latest_price,
            "change_pct": change_pct,
            "recommended_at": recommendation.get("recommended_at"),
            "recommendation_action": recommendation.get("action"),
            "recommendation_score": recommendation.get("score"),
        }

    def _profit_lock_alert_context(
        self,
        recommendation: Dict[str, Any],
        latest_price: Any,
        include_today: bool = False,
    ) -> Dict[str, Any]:
        activation_pct = float(self.settings.monitor_profit_lock_activation_pct or 0.0)
        if activation_pct <= 0 or not latest_price:
            return {}
        reference_price = _num(recommendation.get("last_close"))
        if reference_price <= 0:
            return {}
        recommended_at = recommendation.get("recommended_at")
        try:
            recommended_date = datetime.fromisoformat(recommended_at).date().isoformat()
        except Exception:
            recommended_date = _date_text(recommendation.get("as_of"))
        if not recommended_date:
            return {}

        today = now_cn().date().isoformat()
        try:
            frame, _source = self.data_provider.history(
                recommendation["symbol"],
                "a",
                lookback_days=120,
                adjust="qfq",
            )
        except Exception:
            return {}
        if frame is None or frame.empty or "date" not in frame.columns or "high" not in frame.columns:
            return {}

        completed = frame.copy()
        completed["date"] = completed["date"].astype(str).str[:10]
        if include_today:
            completed = completed[(completed["date"] > recommended_date) & (completed["date"] <= today)]
        else:
            completed = completed[(completed["date"] > recommended_date) & (completed["date"] < today)]
        if completed.empty:
            return {}
        prior_high = float(completed["high"].max())
        trigger_price = reference_price * (1 + activation_pct / 100)
        if prior_high < trigger_price:
            return {}
        return {
            "reference_price": reference_price,
            "prior_high": prior_high,
            "trigger_price": trigger_price,
            "activation_pct": activation_pct,
        }

    def _send_webhook(self, alert: Dict[str, Any]) -> None:
        if not self.settings.alert_webhook_url:
            return
        payload = json.dumps(alert, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.settings.alert_webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10).read()
        except (urllib.error.URLError, TimeoutError):
            return
