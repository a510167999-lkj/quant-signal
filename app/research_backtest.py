from collections import defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from app.announcement_context import build_announcement_context, fetch_cninfo_announcements
from app.a_share_universe import AShareUniverseProvider, select_deep_scan_candidates
from app.config import Settings
from app.dragon_tiger import DragonTigerProvider, OFFICIAL_SOURCE_CAVEAT as DRAGON_TIGER_CAVEAT
from app.execution import assess_entry_executability
from app.indicators import add_indicators
from app.margin_eligibility import MarginEligibilityProvider
from app.market_data import AkshareDataProvider
from app.research_common import _date_value, _date_yyyymmdd, _num
from app.research_context import (
    _historical_market_breadth,
    _historical_market_context,
    _historical_proxy_returns,
    _historical_industry_rotation_contexts,
    _price_action_context,
    _quality_from_prior,
    _relative_strength_context,
)
from app.research_equity import (
    _equity_points_from_basket_returns,
    _max_drawdown_pct,
    _max_drawdown_pct_from_points,
)
from app.signal_tags import (
    build_candidate_context_tags,
    build_signal_tags,
    score_bucket,
)
from app.signals import evaluate_signal
from app.storage import read_json, write_json


MARKET_PROXY_SYMBOLS = [
    {"symbol": "510300", "market": "etf", "name": "沪深300ETF"},
    {"symbol": "159915", "market": "etf", "name": "创业板ETF"},
]


def _load_snapshot(settings: Settings, use_live_snapshot: bool) -> List[Dict[str, Any]]:
    if use_live_snapshot:
        return AShareUniverseProvider(settings.universe_cache_path).snapshot(use_cache_on_error=True)
    payload = read_json(settings.universe_cache_path, {"items": []})
    return payload.get("items", []) if isinstance(payload, dict) else []


def _benchmark_return(provider: AkshareDataProvider, symbol: str, start_date: str, lookback_days: int) -> float:
    frame, _source = provider.history(symbol, "etf", lookback_days=lookback_days, adjust="qfq")
    frame = frame[frame["date"] >= start_date]
    if len(frame) < 2:
        return 0.0
    return (float(frame.iloc[-1]["close"]) / float(frame.iloc[0]["open"]) - 1) * 100


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


def _history_with_file_cache(
    provider: AkshareDataProvider,
    market: str,
    symbol: str,
    lookback_days: int,
    adjust: str,
    cache_dir: str,
):
    cache_path = Path(cache_dir) / ("%s_%s_%s_%s.json" % (market, symbol, lookback_days, adjust or "none"))
    cached = read_json(str(cache_path), {})
    if isinstance(cached, dict) and cached.get("records"):
        return pd.DataFrame(cached["records"]), cached.get("source", "research-cache")

    frame, source = provider.history(symbol, market, lookback_days=lookback_days, adjust=adjust)
    write_json(
        str(cache_path),
        {
            "source": source,
            "records": frame.to_dict(orient="records"),
        },
    )
    return frame, source


def _announcements_with_file_cache(
    symbol: str,
    start_date: str,
    end_date: str,
    cache_dir: str,
) -> List[Dict[str, Any]]:
    cache_path = Path(cache_dir) / ("announcements_%s_%s_%s.json" % (symbol, start_date, end_date))
    cached = read_json(str(cache_path), {})
    if isinstance(cached, dict) and isinstance(cached.get("items"), list):
        return cached["items"]

    items = fetch_cninfo_announcements(symbol, start_date, end_date)
    write_json(
        str(cache_path),
        {
            "source": "CNINFO stock_zh_a_disclosure_report_cninfo",
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "items": items,
        },
    )
    return items


def _compact_announcement_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "level": payload.get("level", "neutral"),
        "score": payload.get("score", 0),
        "score_adjustment": payload.get("score_adjustment", 0),
        "allow_recommendation": payload.get("allow_recommendation", True),
        "announcement_count": payload.get("announcement_count", 0),
        "negative_count": payload.get("negative_count", 0),
        "positive_count": payload.get("positive_count", 0),
        "event_counts": payload.get("event_counts", {}),
        "announcements": payload.get("announcements", [])[:3],
        "errors": payload.get("errors", []),
    }


def _add_counts(target: Dict[str, int], counts: Dict[str, Any]) -> None:
    for key, value in (counts or {}).items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            amount = 1
        target[key] = target.get(key, 0) + max(amount, 1)


def _trade_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "avg_adverse_pct": None,
        }
    wins = [item for item in trades if item.get("return_pct", 0) > 0]
    adverse = [item for item in trades if item.get("max_adverse_pct") is not None]
    return {
        "trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2),
        "avg_return_pct": round(sum(item.get("return_pct", 0) for item in trades) / len(trades), 2),
        "avg_adverse_pct": round(sum(item.get("max_adverse_pct", 0) for item in adverse) / len(adverse), 2)
        if adverse
        else None,
    }


def _announcement_group_stats(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_level: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in selected:
        context = item.get("announcement_context") or {}
        level = context.get("level") or "disabled"
        by_level[level].append(item)
        event_counts = context.get("event_counts") or {}
        if event_counts:
            for category in event_counts:
                by_event[category].append(item)
        else:
            by_event["no_announcement_event"].append(item)

    return {
        "by_level": {
            key: _trade_stats(value)
            for key, value in sorted(by_level.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        },
        "by_event": {
            key: _trade_stats(value)
            for key, value in sorted(by_event.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        },
    }


def _score_bucket(value: Any) -> str:
    return score_bucket(value)


def _rate_bucket(value: Any, prefix: str) -> str:
    rate = _num(value)
    if rate >= 70:
        return "%s_gte_70" % prefix
    if rate >= 60:
        return "%s_60_to_70" % prefix
    if rate >= 50:
        return "%s_50_to_60" % prefix
    return "%s_lt_50" % prefix


def _return_bucket(value: Any, prefix: str) -> str:
    result = _num(value)
    if result >= 3:
        return "%s_gte_3" % prefix
    if result >= 1:
        return "%s_1_to_3" % prefix
    if result >= 0:
        return "%s_0_to_1" % prefix
    return "%s_lt_0" % prefix


def _rank_bucket(position: Any) -> str:
    rank = int(_num(position))
    if rank <= 80:
        return "rank_1_80"
    if rank <= 150:
        return "rank_81_150"
    if rank <= 300:
        return "rank_151_300"
    if rank <= 600:
        return "rank_301_600"
    return "rank_gt_600"


def _amount_bucket(value: Any) -> str:
    amount = _num(value)
    if amount >= 1_000_000_000:
        return "amount_gte_1b"
    if amount >= 300_000_000:
        return "amount_300m_to_1b"
    if amount >= 100_000_000:
        return "amount_100m_to_300m"
    if amount >= 30_000_000:
        return "amount_30m_to_100m"
    return "amount_lt_30m"


def _relative_strength_bucket(value: Any, prefix: str, high: float, mid: float) -> str:
    if value is None:
        return "%s_unknown" % prefix
    result = _num(value)
    if result >= high:
        return "%s_gte_%s" % (prefix, int(high))
    if result >= mid:
        return "%s_%s_to_%s" % (prefix, int(mid), int(high))
    if result >= 0:
        return "%s_0_to_%s" % (prefix, int(mid))
    return "%s_lt_0" % prefix


def _signal_tags(signal: Dict[str, Any]) -> List[str]:
    return build_signal_tags(signal)


def _research_group_stats(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_market_level: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_action: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_signal_tag: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_score_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_prior_win_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_prior_return_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_candidate_rank_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_amount_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_rs20_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_rs60_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for item in selected:
        by_market_level[str(item.get("market_level") or "unknown")].append(item)
        by_action[str(item.get("action") or "unknown")].append(item)
        by_score_bucket[_score_bucket(item.get("score"))].append(item)
        by_prior_win_bucket[_rate_bucket(item.get("prior_win_rate_pct"), "prior_win")].append(item)
        by_prior_return_bucket[_return_bucket(item.get("prior_avg_return_pct"), "prior_return")].append(item)
        by_candidate_rank_bucket[_rank_bucket(item.get("candidate_rank"))].append(item)
        by_amount_bucket[_amount_bucket(item.get("candidate_amount"))].append(item)
        relative = item.get("relative_strength") or {}
        by_rs20_bucket[
            _relative_strength_bucket(relative.get("relative_strength_20d_pct"), "rs20", 10, 5)
        ].append(item)
        by_rs60_bucket[
            _relative_strength_bucket(relative.get("relative_strength_60d_pct"), "rs60", 20, 10)
        ].append(item)
        tags = item.get("signal_tags") or []
        if tags:
            for tag in tags:
                by_signal_tag[str(tag)].append(item)
        else:
            by_signal_tag["untagged"].append(item)

    def build(payload: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        return {
            key: _trade_stats(value)
            for key, value in sorted(payload.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        }

    return {
        "by_market_level": build(by_market_level),
        "by_action": build(by_action),
        "by_signal_tag": build(by_signal_tag),
        "by_score_bucket": build(by_score_bucket),
        "by_prior_win_bucket": build(by_prior_win_bucket),
        "by_prior_return_bucket": build(by_prior_return_bucket),
        "by_candidate_rank_bucket": build(by_candidate_rank_bucket),
        "by_amount_bucket": build(by_amount_bucket),
        "by_rs20_bucket": build(by_rs20_bucket),
        "by_rs60_bucket": build(by_rs60_bucket),
    }


def _split_events(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _historical_snapshot_item(base: Dict[str, Any], frame: pd.DataFrame, index: int) -> Dict[str, Any]:
    row = frame.iloc[index]
    previous = frame.iloc[index - 1] if index > 0 else row
    latest = _num(row.get("close"))
    amount = _num(row.get("amount"))
    if amount <= 0:
        amount = latest * _num(row.get("volume"))
    previous_close = _num(previous.get("close"))
    change_pct = _num(row.get("change_pct"))
    if not change_pct and previous_close > 0:
        change_pct = (latest / previous_close - 1) * 100
    return {
        "symbol": base.get("symbol"),
        "market": base.get("market", "a"),
        "name": base.get("name"),
        "latest": latest,
        "amount": amount,
        "change_pct": change_pct,
        "volume": _num(row.get("volume")),
        "date": str(row.get("date")),
    }


def _load_research_universe_items(
    settings: Settings,
    use_live_snapshot: bool,
    max_universe_symbols: int = 300,
) -> List[Dict[str, Any]]:
    snapshot = _load_snapshot(settings, use_live_snapshot)
    items = []
    seen = set()
    for item in snapshot:
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        if len(symbol) != 6 or not symbol.isdigit() or not name or symbol in seen:
            continue
        seen.add(symbol)
        items.append(dict(item))
    items.sort(key=lambda row: _num(row.get("amount")), reverse=True)
    if max_universe_symbols and max_universe_symbols > 0:
        return items[:max_universe_symbols]
    return items


def _build_historical_candidate_maps(
    symbol_frames: Dict[str, Dict[str, Any]],
    start_date: str,
    hold_days: int,
    max_deep: int,
    settings: Settings,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    snapshot_by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for payload in symbol_frames.values():
        frame = payload["frame"]
        base = payload["base"]
        for index in range(1, len(frame) - hold_days - 1):
            signal_date = str(frame.iloc[index]["date"])
            if signal_date < start_date:
                continue
            snapshot_by_date[signal_date].append(_historical_snapshot_item(base, frame, index))

    candidate_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for signal_date, snapshot in snapshot_by_date.items():
        candidates = select_deep_scan_candidates(
            snapshot=snapshot,
            max_deep=max_deep,
            min_amount=settings.scan_min_amount,
            min_price=settings.scan_min_price,
            max_price=settings.scan_max_price,
        )
        candidate_maps[signal_date] = {
            str(candidate.get("symbol")): {**candidate, "candidate_rank": position}
            for position, candidate in enumerate(candidates, 1)
        }
    return candidate_maps


def _research_payload_from_trades(
    selected: List[Dict[str, Any]],
    all_trades: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    settings: Settings,
    provider: AkshareDataProvider,
    start_date: str,
    max_deep: int,
    top_n: int,
    hold_days: int,
    lookback_days: int,
    buy_only: bool,
    min_score: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_stop_pct: float,
    symbol_cooldown_days: int,
    max_active_positions: int,
    required_events: set,
    require_all_announcement_events: bool,
    excluded_events: set,
    required_market_levels: set,
    required_signal_tags: set,
    require_all_signal_tags: bool,
    excluded_signal_tags: set,
    min_prior_win_rate: float,
    min_prior_avg_return: float,
    max_prior_avg_adverse: float,
    use_announcement_context: bool,
    announcement_lookback_days: int,
    announcement_blocked_count: int,
    announcement_scored_count: int,
    announcement_positive_count: int,
    announcement_watch_risk_count: int,
    announcement_fetch_errors: int,
    announcement_blocked_by_event: Dict[str, int],
    candidate_count: int,
    fetched_symbols: int,
    include_qualified_trades: bool,
    extra_summary: Dict[str, Any] = None,
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

    wins = [item for item in selected if item["return_pct"] > 0]
    adverse_items = [item for item in selected if item.get("max_adverse_pct") is not None]
    equity_points = _equity_points_from_basket_returns(basket_returns, hold_days)
    compound = equity_points[-1]["equity"] if equity_points else 1.0
    equity_curve = [1.0] + [point["equity"] for point in equity_points]
    rolling_1y = _window_portfolio_stats(equity_points, days=365)

    returns = [item["return_pct"] for item in selected]
    median_return = float(pd.Series(returns).median()) if returns else 0.0
    summary = {
        "start_date": start_date,
        "end_date": max([item["exit_date"] for item in selected], default=None),
        "candidate_count": candidate_count,
        "fetched_symbols": fetched_symbols,
        "error_count": len(errors),
        "raw_qualified_trade_count": len(all_trades),
        "selected_trade_count": len(selected),
        "signal_days": len(basket_returns),
        "top_n": top_n,
        "hold_days": hold_days,
        "buy_only": buy_only,
        "min_score": min_score,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "symbol_cooldown_days": symbol_cooldown_days,
        "max_active_positions": max_active_positions,
        "announcement_context_enabled": use_announcement_context,
        "announcement_lookback_days": announcement_lookback_days if use_announcement_context else None,
        "required_announcement_events": sorted(required_events),
        "require_all_announcement_events": bool(require_all_announcement_events),
        "excluded_announcement_events": sorted(excluded_events),
        "required_market_levels": sorted(required_market_levels),
        "required_signal_tags": sorted(required_signal_tags),
        "require_all_signal_tags": bool(require_all_signal_tags),
        "excluded_signal_tags": sorted(excluded_signal_tags),
        "min_prior_win_rate": min_prior_win_rate,
        "min_prior_avg_return": min_prior_avg_return,
        "max_prior_avg_adverse": max_prior_avg_adverse,
        "announcement_blocked_count": announcement_blocked_count,
        "announcement_scored_count": announcement_scored_count,
        "announcement_positive_count": announcement_positive_count,
        "announcement_watch_risk_count": announcement_watch_risk_count,
        "announcement_fetch_errors": announcement_fetch_errors,
        "announcement_blocked_by_event": dict(sorted(announcement_blocked_by_event.items())),
        "trade_win_rate_pct": round(len(wins) / len(selected) * 100, 2) if selected else None,
        "trade_avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        "trade_median_return_pct": round(median_return, 2) if returns else None,
        "trade_avg_max_adverse_pct": round(
            sum(item["max_adverse_pct"] for item in adverse_items) / len(adverse_items), 2
        )
        if adverse_items
        else None,
        "basket_win_rate_pct": round(
            len([item for item in basket_returns if item["return_pct"] > 0]) / len(basket_returns) * 100,
            2,
        )
        if basket_returns
        else None,
        "basket_avg_return_pct": round(
            sum(item["return_pct"] for item in basket_returns) / len(basket_returns),
            2,
        )
        if basket_returns
        else None,
        "portfolio_compounded_return_pct": round((compound - 1) * 100, 2) if basket_returns else None,
        "portfolio_max_drawdown_pct": _max_drawdown_pct(equity_curve) if basket_returns else None,
        "rolling_1y": rolling_1y,
        "hs300etf_buy_hold_pct": round(_benchmark_return(provider, "510300", start_date, lookback_days), 2),
        "cybetf_buy_hold_pct": round(_benchmark_return(provider, "159915", start_date, lookback_days), 2),
    }
    if extra_summary:
        summary.update(extra_summary)
    return {
        "summary": summary,
        "announcement_group_stats": _announcement_group_stats(selected) if use_announcement_context else {},
        "research_group_stats": _research_group_stats(selected),
        "qualified_trades": all_trades if include_qualified_trades else [],
        "best": sorted(selected, key=lambda item: item["return_pct"], reverse=True)[:5],
        "worst": sorted(selected, key=lambda item: item["return_pct"])[:5],
        "errors": errors[:20],
    }


def run_candidate_research_backtest(
    settings: Settings,
    provider: AkshareDataProvider,
    start_date: str,
    max_deep: int,
    top_n: int,
    hold_days: int,
    lookback_days: int,
    use_live_snapshot: bool = False,
    cache_dir: str = "data/research_cache",
    progress_every: int = 0,
    buy_only: bool = False,
    min_score: float = None,
    stop_loss_pct: float = None,
    take_profit_pct: float = None,
    trailing_stop_pct: float = None,
    symbol_cooldown_days: int = 0,
    max_active_positions: int = 0,
    use_announcement_context: bool = False,
    announcement_lookback_days: int = None,
    require_announcement_events: Any = None,
    require_all_announcement_events: bool = False,
    exclude_announcement_events: Any = None,
    require_market_levels: Any = None,
    require_signal_tags: Any = None,
    require_all_signal_tags: bool = False,
    exclude_signal_tags: Any = None,
    min_prior_win_rate: float = None,
    min_prior_avg_return: float = None,
    max_prior_avg_adverse: float = None,
    use_margin_eligibility_context: bool = False,
    include_qualified_trades: bool = False,
) -> Dict[str, Any]:
    snapshot = _load_snapshot(settings, use_live_snapshot)
    candidates = select_deep_scan_candidates(
        snapshot=snapshot,
        max_deep=max_deep,
        min_amount=settings.scan_min_amount,
        min_price=settings.scan_min_price,
        max_price=settings.scan_max_price,
    )

    proxy_frames = []
    for proxy in MARKET_PROXY_SYMBOLS:
        frame, _source = _history_with_file_cache(
            provider,
            proxy["market"],
            proxy["symbol"],
            lookback_days,
            "qfq",
            cache_dir,
        )
        proxy_frames.append(add_indicators(frame))

    all_trades: List[Dict[str, Any]] = []
    by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    errors = []
    fetched_symbols = 0
    market_cache: Dict[str, Dict[str, Any]] = {}
    proxy_return_cache: Dict[str, Dict[str, Any]] = {}
    announcement_blocked_count = 0
    announcement_scored_count = 0
    announcement_positive_count = 0
    announcement_watch_risk_count = 0
    announcement_fetch_errors = 0
    announcement_blocked_by_event: Dict[str, int] = {}
    announcement_lookback_days = announcement_lookback_days or settings.announcement_lookback_days
    required_events = set(_split_events(require_announcement_events))
    excluded_events = set(_split_events(exclude_announcement_events))
    required_market_levels = set(_split_events(require_market_levels))
    required_signal_tags = set(_split_events(require_signal_tags))
    excluded_signal_tags = set(_split_events(exclude_signal_tags))
    margin_symbol_map: Dict[str, Dict[str, Any]] = {}
    margin_fetch_errors = 0
    if use_margin_eligibility_context:
        try:
            margin_payload = MarginEligibilityProvider(
                settings.margin_eligibility_cache_path,
                settings.enable_margin_eligibility_context,
            ).build_map(use_cache_on_error=True)
            margin_symbol_map = margin_payload.get("symbol_map") or {}
        except Exception as exc:
            margin_fetch_errors += 1
            errors.append({"stage": "margin_eligibility_context", "message": str(exc)})

    for position, candidate in enumerate(candidates, 1):
        symbol = candidate.get("symbol")
        try:
            frame, _source = _history_with_file_cache(provider, "a", symbol, lookback_days, "qfq", cache_dir)
            frame = add_indicators(frame)
            fetched_symbols += 1
            announcement_items: List[Dict[str, Any]] = []
            if use_announcement_context:
                announcement_start = (
                    _date_value(start_date) - pd.Timedelta(days=announcement_lookback_days)
                ).strftime("%Y%m%d")
                announcement_end = _date_yyyymmdd(frame["date"].max())
                try:
                    announcement_items = _announcements_with_file_cache(
                        str(symbol),
                        announcement_start,
                        announcement_end,
                        cache_dir,
                    )
                except Exception as exc:
                    announcement_fetch_errors += 1
                    errors.append(
                        {
                            "symbol": symbol,
                            "name": candidate.get("name"),
                            "message": "announcement_fetch_failed: %s" % exc,
                        }
                    )
            prior_outcomes = []
            for index in range(90, len(frame) - hold_days - 1):
                signal_date = str(frame.iloc[index]["date"])
                signal = evaluate_signal(frame.iloc[: index + 1])
                if signal["action"] not in {"BUY", "WATCH"} or signal["score"] < 2:
                    continue

                entry_index = index + 1
                exit_index = entry_index + hold_days
                executable = assess_entry_executability(
                    frame.iloc[index],
                    frame.iloc[entry_index],
                    max_gap_up_pct=settings.max_entry_gap_up_pct,
                    max_gap_down_pct=settings.max_entry_gap_down_pct,
                    locked_limit_gap_pct=settings.locked_limit_gap_pct,
                    max_intraday_range_pct=settings.max_entry_intraday_range_pct,
                )
                if not executable["executable"]:
                    continue

                realized = {
                    **_realized_trade_from_future(
                        frame,
                        entry_index,
                        exit_index,
                        stop_loss_pct=stop_loss_pct,
                        take_profit_pct=take_profit_pct,
                        trailing_stop_pct=trailing_stop_pct,
                    ),
                    "signal_date": signal_date,
                }
                prior_outcomes.append(realized)
                if signal_date < start_date:
                    continue

                matured_prior = [item for item in prior_outcomes if item["exit_date"] < signal_date]
                quality = _quality_from_prior(matured_prior, settings)
                if not quality:
                    continue
                if min_prior_win_rate is not None and quality["win_rate_pct"] < float(min_prior_win_rate):
                    continue
                if min_prior_avg_return is not None and quality["avg_return_pct"] < float(min_prior_avg_return):
                    continue
                if max_prior_avg_adverse is not None and quality["avg_adverse_pct"] > float(max_prior_avg_adverse):
                    continue
                if signal_date not in market_cache:
                    market_cache[signal_date] = _historical_market_context(proxy_frames, signal_date)
                market_context = market_cache[signal_date]
                if signal_date not in proxy_return_cache:
                    proxy_return_cache[signal_date] = _historical_proxy_returns(proxy_frames, signal_date)
                relative_strength = _relative_strength_context(frame.iloc[index], proxy_return_cache[signal_date])
                if required_market_levels and market_context["level"] not in required_market_levels:
                    continue
                allowed_actions = {"BUY"} if buy_only else {"BUY", "WATCH"}
                if not market_context["allow_watch"]:
                    allowed_actions = {"BUY"}
                score_threshold = max(market_context["min_signal_score"], min_score or 0)
                if signal["action"] not in allowed_actions or signal["score"] < score_threshold:
                    continue
                margin_eligibility = margin_symbol_map.get(str(symbol), {}) if use_margin_eligibility_context else {}
                candidate_for_tags = (
                    {**candidate, "margin_eligibility": margin_eligibility}
                    if margin_eligibility
                    else candidate
                )
                signal_tags = sorted(
                    set(
                        _signal_tags(signal)
                        + list(relative_strength.get("tags") or [])
                        + build_candidate_context_tags(candidate_for_tags, quality)
                    )
                )
                signal_tag_set = set(signal_tags)
                if required_signal_tags and require_all_signal_tags and not required_signal_tags.issubset(signal_tag_set):
                    continue
                if required_signal_tags and not require_all_signal_tags and not (signal_tag_set & required_signal_tags):
                    continue
                if excluded_signal_tags and signal_tag_set & excluded_signal_tags:
                    continue

                action_bonus = {"BUY": 25, "WATCH": 12}.get(signal["action"], 0)
                announcement_context = {}
                if use_announcement_context:
                    announcement_context = build_announcement_context(
                        announcement_items,
                        as_of_date=signal_date,
                        lookback_days=announcement_lookback_days,
                        updated_at=signal_date,
                    )
                    if announcement_context.get("level") != "neutral":
                        announcement_scored_count += 1
                    if announcement_context.get("level") == "positive":
                        announcement_positive_count += 1
                    if announcement_context.get("level") == "watch_risk":
                        announcement_watch_risk_count += 1
                    if not announcement_context.get("allow_recommendation", True):
                        announcement_blocked_count += 1
                        _add_counts(announcement_blocked_by_event, announcement_context.get("event_counts") or {})
                        continue
                    event_set = set((announcement_context.get("event_counts") or {}).keys())
                    if required_events and require_all_announcement_events and not required_events.issubset(event_set):
                        continue
                    if required_events and not require_all_announcement_events and not (event_set & required_events):
                        continue
                    if excluded_events and event_set & excluded_events:
                        continue
                rank_score = (
                    float(signal["score"]) * 12
                    + float(signal["confidence"])
                    + action_bonus
                    + float(candidate.get("prefilter_score") or 0)
                    + quality["score_bonus"]
                    + _num(announcement_context.get("score_adjustment"))
                    + market_context["score_adjustment"]
                )
                trade = {
                    **realized,
                    "symbol": symbol,
                    "name": candidate.get("name"),
                    "candidate_rank": position,
                    "candidate_prefilter_score": candidate.get("prefilter_score"),
                    "candidate_amount": candidate.get("amount"),
                    "candidate_latest": candidate.get("latest"),
                    "candidate_change_pct": candidate.get("change_pct"),
                    "action": signal["action"],
                    "score": float(signal["score"]),
                    "rank_score": round(rank_score, 4),
                    "market_level": market_context["level"],
                    "signal_tags": signal_tags,
                    "relative_strength": {
                        key: value for key, value in relative_strength.items() if key != "tags"
                    },
                    "prior_count": quality["trade_count"],
                    "prior_win_rate_pct": round(quality["win_rate_pct"], 2),
                    "prior_avg_return_pct": round(quality["avg_return_pct"], 2),
                    "prior_avg_adverse_pct": round(quality["avg_adverse_pct"], 2),
                    "entry_executability": executable,
                    "margin_eligibility": margin_eligibility,
                    "announcement_context": _compact_announcement_context(announcement_context)
                    if use_announcement_context
                    else {},
                }
                all_trades.append(trade)
                by_signal_date[signal_date].append(trade)
        except Exception as exc:
            errors.append({"symbol": symbol, "name": candidate.get("name"), "message": str(exc)})
        if progress_every and position % progress_every == 0:
            print(
                json.dumps(
                    {
                        "processed": position,
                        "candidate_count": len(candidates),
                        "raw_qualified_trade_count": len(all_trades),
                        "announcement_blocked_count": announcement_blocked_count,
                        "errors": len(errors),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    selected = _select_with_portfolio_controls(
        by_signal_date,
        top_n,
        symbol_cooldown_days=symbol_cooldown_days,
        max_active_positions=max_active_positions,
    )
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

    wins = [item for item in selected if item["return_pct"] > 0]
    adverse_items = [item for item in selected if item.get("max_adverse_pct") is not None]
    equity_points = _equity_points_from_basket_returns(basket_returns, hold_days)
    compound = equity_points[-1]["equity"] if equity_points else 1.0
    equity_curve = [1.0] + [point["equity"] for point in equity_points]
    rolling_1y = _window_portfolio_stats(equity_points, days=365)

    returns = [item["return_pct"] for item in selected]
    median_return = float(pd.Series(returns).median()) if returns else 0.0
    summary = {
        "start_date": start_date,
        "end_date": max([item["exit_date"] for item in selected], default=None),
        "candidate_count": len(candidates),
        "fetched_symbols": fetched_symbols,
        "error_count": len(errors),
        "raw_qualified_trade_count": len(all_trades),
        "selected_trade_count": len(selected),
        "signal_days": len(basket_returns),
        "top_n": top_n,
        "hold_days": hold_days,
        "buy_only": buy_only,
        "min_score": min_score,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "symbol_cooldown_days": symbol_cooldown_days,
        "max_active_positions": max_active_positions,
        "announcement_context_enabled": use_announcement_context,
        "announcement_lookback_days": announcement_lookback_days if use_announcement_context else None,
        "required_announcement_events": sorted(required_events),
        "require_all_announcement_events": bool(require_all_announcement_events),
        "excluded_announcement_events": sorted(excluded_events),
        "required_market_levels": sorted(required_market_levels),
        "required_signal_tags": sorted(required_signal_tags),
        "require_all_signal_tags": bool(require_all_signal_tags),
        "excluded_signal_tags": sorted(excluded_signal_tags),
        "min_prior_win_rate": min_prior_win_rate,
        "min_prior_avg_return": min_prior_avg_return,
        "max_prior_avg_adverse": max_prior_avg_adverse,
        "announcement_blocked_count": announcement_blocked_count,
        "announcement_scored_count": announcement_scored_count,
        "announcement_positive_count": announcement_positive_count,
        "announcement_watch_risk_count": announcement_watch_risk_count,
        "announcement_fetch_errors": announcement_fetch_errors,
        "announcement_blocked_by_event": dict(sorted(announcement_blocked_by_event.items())),
        "margin_eligibility_context_enabled": bool(use_margin_eligibility_context),
        "margin_eligibility_scope": "sse_szse_current" if use_margin_eligibility_context else None,
        "margin_eligibility_days": 1 if margin_symbol_map else 0,
        "margin_eligibility_fetch_errors": margin_fetch_errors,
        "margin_eligibility_caveat": (
            "research-backtest uses the current official SSE/SZSE margin list and can introduce "
            "lookahead in historical slices. Use research-historical-universe --margin-eligibility-context "
            "for SZSE as-of historical tags."
        )
        if use_margin_eligibility_context
        else None,
        "trade_win_rate_pct": round(len(wins) / len(selected) * 100, 2) if selected else None,
        "trade_avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        "trade_median_return_pct": round(median_return, 2) if returns else None,
        "trade_avg_max_adverse_pct": round(
            sum(item["max_adverse_pct"] for item in adverse_items) / len(adverse_items), 2
        )
        if adverse_items
        else None,
        "basket_win_rate_pct": round(
            len([item for item in basket_returns if item["return_pct"] > 0]) / len(basket_returns) * 100,
            2,
        )
        if basket_returns
        else None,
        "basket_avg_return_pct": round(
            sum(item["return_pct"] for item in basket_returns) / len(basket_returns),
            2,
        )
        if basket_returns
        else None,
        "portfolio_compounded_return_pct": round((compound - 1) * 100, 2) if basket_returns else None,
        "portfolio_max_drawdown_pct": _max_drawdown_pct(equity_curve) if basket_returns else None,
        "rolling_1y": rolling_1y,
        "hs300etf_buy_hold_pct": round(_benchmark_return(provider, "510300", start_date, lookback_days), 2),
        "cybetf_buy_hold_pct": round(_benchmark_return(provider, "159915", start_date, lookback_days), 2),
    }
    return {
        "summary": summary,
        "announcement_group_stats": _announcement_group_stats(selected) if use_announcement_context else {},
        "research_group_stats": _research_group_stats(selected),
        "qualified_trades": all_trades if include_qualified_trades else [],
        "best": sorted(selected, key=lambda item: item["return_pct"], reverse=True)[:5],
        "worst": sorted(selected, key=lambda item: item["return_pct"])[:5],
        "errors": errors[:20],
    }


def run_historical_universe_research_backtest(
    settings: Settings,
    provider: AkshareDataProvider,
    start_date: str,
    max_deep: int,
    top_n: int,
    hold_days: int,
    lookback_days: int,
    max_universe_symbols: int = 300,
    use_live_snapshot: bool = False,
    cache_dir: str = "data/research_cache",
    progress_every: int = 0,
    buy_only: bool = False,
    min_score: float = None,
    stop_loss_pct: float = None,
    take_profit_pct: float = None,
    trailing_stop_pct: float = None,
    symbol_cooldown_days: int = 0,
    max_active_positions: int = 0,
    use_announcement_context: bool = False,
    announcement_lookback_days: int = None,
    require_announcement_events: Any = None,
    require_all_announcement_events: bool = False,
    exclude_announcement_events: Any = None,
    require_market_levels: Any = None,
    require_signal_tags: Any = None,
    require_all_signal_tags: bool = False,
    exclude_signal_tags: Any = None,
    min_prior_win_rate: float = None,
    min_prior_avg_return: float = None,
    max_prior_avg_adverse: float = None,
    use_industry_rotation_context: bool = False,
    industry_rotation_max_boards: int = 40,
    use_margin_eligibility_context: bool = False,
    use_dragon_tiger_context: bool = False,
    include_qualified_trades: bool = False,
) -> Dict[str, Any]:
    universe_items = _load_research_universe_items(
        settings,
        use_live_snapshot=use_live_snapshot,
        max_universe_symbols=max_universe_symbols,
    )

    proxy_frames = []
    for proxy in MARKET_PROXY_SYMBOLS:
        frame, _source = _history_with_file_cache(
            provider,
            proxy["market"],
            proxy["symbol"],
            lookback_days,
            "qfq",
            cache_dir,
        )
        proxy_frames.append(add_indicators(frame))

    symbol_frames: Dict[str, Dict[str, Any]] = {}
    errors = []
    fetched_symbols = 0
    for position, item in enumerate(universe_items, 1):
        symbol = item.get("symbol")
        try:
            frame, _source = _history_with_file_cache(provider, "a", symbol, lookback_days, "qfq", cache_dir)
            symbol_frames[str(symbol)] = {"base": item, "frame": add_indicators(frame)}
            fetched_symbols += 1
        except Exception as exc:
            errors.append({"symbol": symbol, "name": item.get("name"), "message": str(exc)})
        if progress_every and position % progress_every == 0:
            print(
                json.dumps(
                    {
                        "phase": "fetch_history",
                        "processed": position,
                        "universe_symbol_count": len(universe_items),
                        "fetched_symbols": fetched_symbols,
                        "errors": len(errors),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    candidate_maps = _build_historical_candidate_maps(
        symbol_frames,
        start_date=start_date,
        hold_days=hold_days,
        max_deep=max_deep,
        settings=settings,
    )
    market_breadth_by_date = _historical_market_breadth(
        symbol_frames,
        start_date=start_date,
        hold_days=hold_days,
    )
    industry_rotation_by_date: Dict[str, Dict[str, Any]] = {}
    if use_industry_rotation_context:
        try:
            end_date = max(
                [str(payload["frame"]["date"].max()) for payload in symbol_frames.values()],
                default=start_date,
            )
            industry_rotation_by_date = _historical_industry_rotation_contexts(
                settings,
                start_date=start_date,
                end_date=end_date,
                max_boards=industry_rotation_max_boards,
            )
        except Exception as exc:
            errors.append({"stage": "industry_rotation_context", "message": str(exc)})
    dragon_tiger_by_date: Dict[str, Dict[str, Any]] = {}
    dragon_tiger_fetch_errors = 0
    if use_dragon_tiger_context:
        try:
            end_date = max(
                [str(payload["frame"]["date"].max()) for payload in symbol_frames.values()],
                default=start_date,
            )
            dragon_tiger_payload = DragonTigerProvider(cache_dir).build_contexts(
                start_date=start_date,
                end_date=end_date,
                use_cache_on_error=True,
            )
            dragon_tiger_by_date = dragon_tiger_payload.get("by_date") or {}
        except Exception as exc:
            dragon_tiger_fetch_errors += 1
            errors.append({"stage": "dragon_tiger_context", "message": str(exc)})

    all_trades: List[Dict[str, Any]] = []
    by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    market_cache: Dict[str, Dict[str, Any]] = {}
    proxy_return_cache: Dict[str, Dict[str, Any]] = {}
    announcement_blocked_count = 0
    announcement_scored_count = 0
    announcement_positive_count = 0
    announcement_watch_risk_count = 0
    announcement_fetch_errors = 0
    announcement_blocked_by_event: Dict[str, int] = {}
    announcement_lookback_days = announcement_lookback_days or settings.announcement_lookback_days
    required_events = set(_split_events(require_announcement_events))
    excluded_events = set(_split_events(exclude_announcement_events))
    required_market_levels = set(_split_events(require_market_levels))
    required_signal_tags = set(_split_events(require_signal_tags))
    excluded_signal_tags = set(_split_events(exclude_signal_tags))
    margin_provider = MarginEligibilityProvider(
        settings.margin_eligibility_cache_path,
        settings.enable_margin_eligibility_context,
    )
    margin_by_date: Dict[str, Dict[str, Any]] = {}
    margin_fetch_errors = 0

    def margin_context_for(signal_date: str, symbol: str) -> Dict[str, Any]:
        nonlocal margin_fetch_errors
        if not use_margin_eligibility_context:
            return {}
        if not str(symbol).startswith(("0", "3")):
            return {}
        if signal_date not in margin_by_date:
            try:
                margin_by_date[signal_date] = margin_provider.build_szse_underlying_map(
                    signal_date,
                    cache_dir=cache_dir,
                    use_cache_on_error=True,
                )
            except Exception as exc:
                margin_fetch_errors += 1
                margin_by_date[signal_date] = {
                    "symbol_map": {},
                    "summary": {
                        "enabled": True,
                        "scope": "szse_underlying_asof",
                        "requested_as_of": signal_date,
                        "error": str(exc),
                    },
                    "errors": [{"source": "szse_underlying_asof", "message": str(exc)}],
                }
        return (margin_by_date.get(signal_date, {}).get("symbol_map") or {}).get(str(symbol), {})

    for position, (symbol, payload) in enumerate(symbol_frames.items(), 1):
        frame = payload["frame"]
        base = payload["base"]
        try:
            announcement_items: List[Dict[str, Any]] = []
            if use_announcement_context:
                announcement_start = (
                    _date_value(start_date) - pd.Timedelta(days=announcement_lookback_days)
                ).strftime("%Y%m%d")
                announcement_end = _date_yyyymmdd(frame["date"].max())
                try:
                    announcement_items = _announcements_with_file_cache(
                        str(symbol),
                        announcement_start,
                        announcement_end,
                        cache_dir,
                    )
                except Exception as exc:
                    announcement_fetch_errors += 1
                    errors.append(
                        {
                            "symbol": symbol,
                            "name": base.get("name"),
                            "message": "announcement_fetch_failed: %s" % exc,
                        }
                    )
            prior_outcomes = []
            for index in range(90, len(frame) - hold_days - 1):
                signal_date = str(frame.iloc[index]["date"])
                signal = evaluate_signal(frame.iloc[: index + 1])
                if signal["action"] not in {"BUY", "WATCH"} or signal["score"] < 2:
                    continue

                entry_index = index + 1
                exit_index = entry_index + hold_days
                executable = assess_entry_executability(
                    frame.iloc[index],
                    frame.iloc[entry_index],
                    max_gap_up_pct=settings.max_entry_gap_up_pct,
                    max_gap_down_pct=settings.max_entry_gap_down_pct,
                    locked_limit_gap_pct=settings.locked_limit_gap_pct,
                    max_intraday_range_pct=settings.max_entry_intraday_range_pct,
                )
                if not executable["executable"]:
                    continue

                realized = {
                    **_realized_trade_from_future(
                        frame,
                        entry_index,
                        exit_index,
                        stop_loss_pct=stop_loss_pct,
                        take_profit_pct=take_profit_pct,
                        trailing_stop_pct=trailing_stop_pct,
                    ),
                    "signal_date": signal_date,
                }
                prior_outcomes.append(realized)
                if signal_date < start_date:
                    continue

                candidate = (candidate_maps.get(signal_date) or {}).get(str(symbol))
                if not candidate:
                    continue
                margin_eligibility = margin_context_for(signal_date, str(symbol))
                if margin_eligibility:
                    candidate = {**candidate, "margin_eligibility": margin_eligibility}

                matured_prior = [item for item in prior_outcomes if item["exit_date"] < signal_date]
                quality = _quality_from_prior(matured_prior, settings)
                if not quality:
                    continue
                if min_prior_win_rate is not None and quality["win_rate_pct"] < float(min_prior_win_rate):
                    continue
                if min_prior_avg_return is not None and quality["avg_return_pct"] < float(min_prior_avg_return):
                    continue
                if max_prior_avg_adverse is not None and quality["avg_adverse_pct"] > float(max_prior_avg_adverse):
                    continue
                if signal_date not in market_cache:
                    market_cache[signal_date] = _historical_market_context(proxy_frames, signal_date)
                market_context = market_cache[signal_date]
                if signal_date not in proxy_return_cache:
                    proxy_return_cache[signal_date] = _historical_proxy_returns(proxy_frames, signal_date)
                relative_strength = _relative_strength_context(frame.iloc[index], proxy_return_cache[signal_date])
                market_breadth = market_breadth_by_date.get(signal_date, {})
                price_action = _price_action_context(frame, index, symbol)
                industry_rotation = industry_rotation_by_date.get(signal_date, {})
                dragon_tiger = (dragon_tiger_by_date.get(signal_date) or {}).get(str(symbol), {})
                if required_market_levels and market_context["level"] not in required_market_levels:
                    continue
                allowed_actions = {"BUY"} if buy_only else {"BUY", "WATCH"}
                if not market_context["allow_watch"]:
                    allowed_actions = {"BUY"}
                score_threshold = max(market_context["min_signal_score"], min_score or 0)
                if signal["action"] not in allowed_actions or signal["score"] < score_threshold:
                    continue
                signal_tags = sorted(
                    set(
                        _signal_tags(signal)
                        + list(relative_strength.get("tags") or [])
                        + list(market_breadth.get("tags") or [])
                        + list(price_action.get("tags") or [])
                        + list(industry_rotation.get("tags") or [])
                        + list(dragon_tiger.get("tags") or [])
                        + build_candidate_context_tags(candidate, quality)
                    )
                )
                signal_tag_set = set(signal_tags)
                if required_signal_tags and require_all_signal_tags and not required_signal_tags.issubset(signal_tag_set):
                    continue
                if required_signal_tags and not require_all_signal_tags and not (signal_tag_set & required_signal_tags):
                    continue
                if excluded_signal_tags and signal_tag_set & excluded_signal_tags:
                    continue

                action_bonus = {"BUY": 25, "WATCH": 12}.get(signal["action"], 0)
                announcement_context = {}
                if use_announcement_context:
                    announcement_context = build_announcement_context(
                        announcement_items,
                        as_of_date=signal_date,
                        lookback_days=announcement_lookback_days,
                        updated_at=signal_date,
                    )
                    if announcement_context.get("level") != "neutral":
                        announcement_scored_count += 1
                    if announcement_context.get("level") == "positive":
                        announcement_positive_count += 1
                    if announcement_context.get("level") == "watch_risk":
                        announcement_watch_risk_count += 1
                    if not announcement_context.get("allow_recommendation", True):
                        announcement_blocked_count += 1
                        _add_counts(announcement_blocked_by_event, announcement_context.get("event_counts") or {})
                        continue
                    event_set = set((announcement_context.get("event_counts") or {}).keys())
                    if required_events and require_all_announcement_events and not required_events.issubset(event_set):
                        continue
                    if required_events and not require_all_announcement_events and not (event_set & required_events):
                        continue
                    if excluded_events and event_set & excluded_events:
                        continue
                rank_score = (
                    float(signal["score"]) * 12
                    + float(signal["confidence"])
                    + action_bonus
                    + float(candidate.get("prefilter_score") or 0)
                    + quality["score_bonus"]
                    + _num(announcement_context.get("score_adjustment"))
                    + market_context["score_adjustment"]
                )
                trade = {
                    **realized,
                    "symbol": symbol,
                    "name": base.get("name"),
                    "candidate_rank": candidate.get("candidate_rank"),
                    "candidate_rank_pct": candidate.get("candidate_rank_pct"),
                    "candidate_prefilter_score": candidate.get("prefilter_score"),
                    "candidate_amount": candidate.get("amount"),
                    "candidate_amount_rank": candidate.get("amount_rank"),
                    "candidate_amount_rank_pct": candidate.get("amount_rank_pct"),
                    "candidate_latest": candidate.get("latest"),
                    "candidate_change_pct": candidate.get("change_pct"),
                    "action": signal["action"],
                    "score": float(signal["score"]),
                    "rank_score": round(rank_score, 4),
                    "market_level": market_context["level"],
                    "signal_tags": signal_tags,
                    "relative_strength": {
                        key: value for key, value in relative_strength.items() if key != "tags"
                    },
                    "market_breadth": {
                        key: value for key, value in market_breadth.items() if key != "tags"
                    },
                    "price_action": {
                        key: value for key, value in price_action.items() if key != "tags"
                    },
                    "industry_rotation": {
                        key: value for key, value in industry_rotation.items() if key != "tags"
                    },
                    "dragon_tiger": {
                        key: value for key, value in dragon_tiger.items() if key != "tags"
                    },
                    "margin_eligibility": margin_eligibility,
                    "prior_count": quality["trade_count"],
                    "prior_win_rate_pct": round(quality["win_rate_pct"], 2),
                    "prior_avg_return_pct": round(quality["avg_return_pct"], 2),
                    "prior_avg_adverse_pct": round(quality["avg_adverse_pct"], 2),
                    "entry_executability": executable,
                    "announcement_context": _compact_announcement_context(announcement_context)
                    if use_announcement_context
                    else {},
                }
                all_trades.append(trade)
                by_signal_date[signal_date].append(trade)
        except Exception as exc:
            errors.append({"symbol": symbol, "name": base.get("name"), "message": str(exc)})
        if progress_every and position % progress_every == 0:
            print(
                json.dumps(
                    {
                        "phase": "evaluate_signals",
                        "processed": position,
                        "fetched_symbols": fetched_symbols,
                        "raw_qualified_trade_count": len(all_trades),
                        "historical_candidate_days": len(candidate_maps),
                        "errors": len(errors),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    selected = _select_with_portfolio_controls(
        by_signal_date,
        top_n,
        symbol_cooldown_days=symbol_cooldown_days,
        max_active_positions=max_active_positions,
    )
    return _research_payload_from_trades(
        selected=selected,
        all_trades=all_trades,
        errors=errors,
        settings=settings,
        provider=provider,
        start_date=start_date,
        max_deep=max_deep,
        top_n=top_n,
        hold_days=hold_days,
        lookback_days=lookback_days,
        buy_only=buy_only,
        min_score=min_score,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        symbol_cooldown_days=symbol_cooldown_days,
        max_active_positions=max_active_positions,
        required_events=required_events,
        require_all_announcement_events=require_all_announcement_events,
        excluded_events=excluded_events,
        required_market_levels=required_market_levels,
        required_signal_tags=required_signal_tags,
        require_all_signal_tags=require_all_signal_tags,
        excluded_signal_tags=excluded_signal_tags,
        min_prior_win_rate=min_prior_win_rate,
        min_prior_avg_return=min_prior_avg_return,
        max_prior_avg_adverse=max_prior_avg_adverse,
        use_announcement_context=use_announcement_context,
        announcement_lookback_days=announcement_lookback_days,
        announcement_blocked_count=announcement_blocked_count,
        announcement_scored_count=announcement_scored_count,
        announcement_positive_count=announcement_positive_count,
        announcement_watch_risk_count=announcement_watch_risk_count,
        announcement_fetch_errors=announcement_fetch_errors,
        announcement_blocked_by_event=announcement_blocked_by_event,
        candidate_count=len(universe_items),
        fetched_symbols=fetched_symbols,
        include_qualified_trades=include_qualified_trades,
        extra_summary={
            "candidate_mode": "historical_daily_prefilter",
            "max_universe_symbols": max_universe_symbols,
            "daily_prefilter_max_deep": max_deep,
            "historical_candidate_days": len(candidate_maps),
            "historical_market_breadth_days": len(market_breadth_by_date),
            "industry_rotation_context_enabled": bool(use_industry_rotation_context),
            "industry_rotation_max_boards": industry_rotation_max_boards if use_industry_rotation_context else None,
            "industry_rotation_days": len(industry_rotation_by_date),
            "dragon_tiger_context_enabled": bool(use_dragon_tiger_context),
            "dragon_tiger_days": len(dragon_tiger_by_date),
            "dragon_tiger_fetch_errors": dragon_tiger_fetch_errors,
            "dragon_tiger_caveat": DRAGON_TIGER_CAVEAT if use_dragon_tiger_context else None,
            "margin_eligibility_context_enabled": bool(use_margin_eligibility_context),
            "margin_eligibility_scope": "szse_underlying_asof" if use_margin_eligibility_context else None,
            "margin_eligibility_days": len(margin_by_date),
            "margin_eligibility_fetch_errors": margin_fetch_errors,
            "margin_eligibility_caveat": (
                "Historical margin tags currently use SZSE as-of official reports only. "
                "SSE current lists are not used as historical filters because the tested date parameters "
                "return the current list and would introduce lookahead."
            )
            if use_margin_eligibility_context
            else None,
            "research_caveat": (
                "Historical prefilter is rebuilt by signal date, but the fetched symbol seed still comes "
                "from the current A-share snapshot and may contain survivorship/current-liquidity bias."
            ),
        },
    )
