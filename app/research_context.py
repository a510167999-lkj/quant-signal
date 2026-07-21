"""研究回测的信号日上下文计算。

从 research_backtest.py 抽出，把横截面特征转成信号日上下文 dict 的纯计算逻辑
（大盘强度 / 代理收益 / 相对强度 / K 线形态 / 市场宽度 / 行业轮动 / 历史质量），
与回测编排解耦，供 research_backtest 调用。
"""
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd

from app.a_share_universe import _is_excluded_name
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


STOCK_MARKET_CONTEXT_SCHEMA_VERSION = "stock-breadth-market-context/v1"
STOCK_MARKET_MIN_ELIGIBLE = 100
STOCK_MARKET_MIN_COVERAGE_PCT = 95.0

# 状态机所依赖的必需 breadth 字段；任一缺失即 fail closed 到 unknown。
# median_return_60d_pct 纳入必需：60d 中位收益缺失不得伪装成强势判定。
_REQUIRED_BREADTH_FIELDS = (
    "above_ma20_pct",
    "above_ma60_pct",
    "advancing_pct",
    "median_return_20d_pct",
    "median_return_60d_pct",
    "return_20d_positive_pct",
)


def _finite_number(value: Any) -> Any:
    """有限数字校验：None / NaN / inf / -inf / 非数字字符串 → None，否则返回 float。

    用于在状态机阈值比较前杜绝 NaN（比较恒 False 绕过判定）、inf（扭曲阈值）和非数字
    （TypeError 泄漏）三类污染。不做 broad except。
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number) or not math.isfinite(number):
        return None
    return number


def _pct_of(value: Any) -> Any:
    """把分数形式的收益（如 0.02）转成百分比（2.0）；None / NaN / inf / 非数字透传 None。"""
    number = _finite_number(value)
    if number is None:
        return None
    return round(number * 100, 2)


def _strict_positive(raw: Any, symbol: str, signal_date: str, field: str) -> float:
    """batch 路径严格校验：raw 必须是有限正数，否则 ValueError（fail closed）。

    用于 close/ma20/ma60 —— 这些价格指标缺失时 _num 会得到 0，使 `close >= ma` 恒真，
    把缺失伪装成强势；此处直接对原始值校验，杜绝该路径。
    """
    if raw is None:
        raise ValueError(f"missing {field} for {symbol} @ {signal_date}")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"non-numeric {field} for {symbol} @ {signal_date}: {raw!r}")
    if pd.isna(number) or not math.isfinite(number) or number <= 0:
        raise ValueError(f"invalid {field} for {symbol} @ {signal_date}: {raw!r}")
    return number


def _strict_pct(raw: Any, symbol: str, signal_date: str, field: str) -> float:
    """batch 路径严格校验：raw 必须是有限收益（分数形式），返回百分比；否则 ValueError。"""
    if raw is None:
        raise ValueError(f"missing {field} for {symbol} @ {signal_date}")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"non-numeric {field} for {symbol} @ {signal_date}: {raw!r}")
    if pd.isna(number) or not math.isfinite(number):
        raise ValueError(f"invalid {field} for {symbol} @ {signal_date}: {raw!r}")
    return round(number * 100, 2)


def _pct_denom(rows: List[Dict[str, Any]], key: str, denominator: int) -> float:
    """rows 中 key 为真的占比（%），分母显式给定（用于把 missing 成员按 False 计入）。"""
    if denominator <= 0:
        return 0.0
    return round(sum(1 for row in rows if row[key]) / denominator * 100, 2)


def _historical_market_breadth(
    symbol_frames: Dict[str, Dict[str, Any]],
    start_date: str,
    hold_days: int,
    universe_source: Any = None,
) -> Dict[str, Dict[str, Any]]:
    # 优先走批量 items_as_of（每个 signal_date 一次，缓存），没有才回退到逐股票 item_as_of。
    has_items_as_of = universe_source is not None and hasattr(universe_source, "items_as_of")
    has_item_as_of = universe_source is not None and hasattr(universe_source, "item_as_of")

    eligible_cache: Dict[str, Any] = {}
    seen_pairs: set = set()
    rows_by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def _eligible_set(signal_date: str) -> Any:
        """返回该日的 eligible symbol 集合；批量路径返回 frozenset，否则 None（兼容回退/无 universe）。"""
        cached = eligible_cache.get(signal_date, False)
        if cached is not False:
            return cached
        if has_items_as_of:
            members = universe_source.items_as_of(signal_date)
            eligible = frozenset(
                str(member.get("symbol") or "")
                for member in members
                if not _is_excluded_name(str(member.get("name") or ""))
            )
            eligible_cache[signal_date] = eligible
        else:
            eligible_cache[signal_date] = None
        return eligible_cache[signal_date]

    for payload in symbol_frames.values():
        frame = payload["frame"]
        symbol = str((payload.get("base") or {}).get("symbol") or "")
        for index in range(60, len(frame)):
            row = frame.iloc[index]
            signal_date = str(row.get("date"))
            if signal_date < start_date:
                continue
            eligible = _eligible_set(signal_date)
            if eligible is not None:
                # 批量路径：observed 价格行按 eligible membership 过滤。
                if symbol not in eligible:
                    continue
            elif has_item_as_of:
                # 兼容回退：仅 item_as_of 可用的 fake universe。
                member = universe_source.item_as_of(symbol, signal_date)
                if member is None or _is_excluded_name(str(member.get("name") or "")):
                    continue
            # 同一 (symbol, signal_date) 出现两次 → 数据损坏，显式失败（fail closed）。
            pair = (symbol, signal_date)
            if pair in seen_pairs:
                raise ValueError(
                    f"duplicate symbol/date in market breadth: {symbol} @ {signal_date}"
                )
            seen_pairs.add(pair)

            previous = frame.iloc[index - 1] if index > 0 else row
            close = _num(row.get("close"))
            # 严格指标校验只在 items_as_of batch stock-only 路径开启（新研究合同边界）：
            # observed 行的 close/ma20/ma60 必须有限正、return_20d/60d 必须有限；任一
            # None/NaN/非数字/ma<=0 必须 ValueError fail closed，杜绝把 ma 缺失当 0 伪装成
            # above_ma20=强势，也不得降低 observed_count 后静默继续。
            # item_as_of fake fallback 与无 universe 的 legacy 路径保持兼容，旧 fixture
            # 无需补 return_60d。
            if eligible is not None:
                close = _strict_positive(row.get("close"), symbol, signal_date, "close")
                ma20 = _strict_positive(row.get("ma20"), symbol, signal_date, "ma20")
                ma60 = _strict_positive(row.get("ma60"), symbol, signal_date, "ma60")
                return_20d_pct = _strict_pct(row.get("return_20d"), symbol, signal_date, "return_20d")
                return_60d_pct = _strict_pct(row.get("return_60d"), symbol, signal_date, "return_60d")
            else:
                if close <= 0:
                    continue
                ma20 = _num(row.get("ma20"))
                ma60 = _num(row.get("ma60"))
                return_20d_pct = _pct_of(row.get("return_20d"))
                return_60d_pct = _pct_of(row.get("return_60d"))
            previous_close = _num(previous.get("close"))
            # change_pct 仍允许为 0，并按已有规则（缺失时由 close/previous_close 推算）计算。
            change_pct = _num(row.get("change_pct"))
            if not change_pct and previous_close > 0:
                change_pct = (close / previous_close - 1) * 100
            amount = _num(row.get("amount"))
            if amount <= 0:
                amount = close * _num(row.get("volume"))
            rows_by_date[signal_date].append(
                {
                    "above_ma20": close >= ma20,
                    "above_ma60": close >= ma60,
                    "return_20d_positive": return_20d_pct is not None and return_20d_pct > 0,
                    "return_20d_pct": return_20d_pct,
                    "return_60d_pct": return_60d_pct,
                    "daily_change_pct": round(change_pct, 2),
                    "advancing": change_pct > 0,
                    "liquid_300m": amount >= 300_000_000,
                }
            )

    result: Dict[str, Dict[str, Any]] = {}
    for signal_date, rows in rows_by_date.items():
        observed_count = len(rows)
        eligible = _eligible_set(signal_date)
        # 无 universe / 回退路径：eligible = observed，coverage = 100（兼容旧路径）。
        eligible_count = len(eligible) if eligible is not None else observed_count
        missing_count = max(0, eligible_count - observed_count)
        coverage_pct = (
            round(observed_count / eligible_count * 100, 2) if eligible_count else 100.0
        )
        # 布尔宽度分母 = eligible，eligible 但当天无行的成员按 False 计入（不静默排除）。
        denominator = eligible_count
        valid_20d = [row["return_20d_pct"] for row in rows if row["return_20d_pct"] is not None]
        valid_60d = [row["return_60d_pct"] for row in rows if row["return_60d_pct"] is not None]
        daily_changes = [row["daily_change_pct"] for row in rows]
        context = {
            "sample_count": eligible_count,
            "eligible_count": eligible_count,
            "observed_count": observed_count,
            "missing_count": missing_count,
            "coverage_pct": coverage_pct,
            "above_ma20_pct": _pct_denom(rows, "above_ma20", denominator),
            "above_ma60_pct": _pct_denom(rows, "above_ma60", denominator),
            "return_20d_positive_pct": _pct_denom(rows, "return_20d_positive", denominator),
            "advancing_pct": _pct_denom(rows, "advancing", denominator),
            "liquid_300m_pct": _pct_denom(rows, "liquid_300m", denominator),
            "median_return_20d_pct": round(float(pd.Series(valid_20d).median()), 2) if valid_20d else None,
            "median_return_60d_pct": round(float(pd.Series(valid_60d).median()), 2) if valid_60d else None,
            "p75_return_20d_pct": round(float(pd.Series(valid_20d).quantile(0.75)), 2) if valid_20d else None,
            "p75_return_60d_pct": round(float(pd.Series(valid_60d).quantile(0.75)), 2) if valid_60d else None,
            "equal_weight_daily_return_pct": round(sum(daily_changes) / len(daily_changes), 2) if daily_changes else None,
            "median_daily_return_pct": round(float(pd.Series(daily_changes).median()), 2) if daily_changes else None,
            "observed_return_sample_count": len(valid_20d),
        }
        context["tags"] = build_market_breadth_tags(context)
        result[signal_date] = context
    return result


def _stock_market_returns(breadth: Dict[str, Any]) -> Dict[str, Any]:
    """从单日 breadth context 抽出市场收益基准（median / p75）。

    只透传有限数字；None / NaN / inf / 非数字统一输出 None（保持字段名），杜绝把
    非有限值灌进下游 relative strength 阈值。
    """
    getter = breadth.get if breadth else (lambda _key: None)
    return {
        "market_median_return_20d_pct": _finite_number(getter("median_return_20d_pct")),
        "market_median_return_60d_pct": _finite_number(getter("median_return_60d_pct")),
        "market_p75_return_20d_pct": _finite_number(getter("p75_return_20d_pct")),
        "market_p75_return_60d_pct": _finite_number(getter("p75_return_60d_pct")),
    }


def _stock_breadth_state(
    level: str,
    min_signal_score: float,
    allow_buy: bool,
    allow_watch: bool,
    score_adjustment: int,
    reasons: List[str],
) -> Dict[str, Any]:
    return {
        "schema_version": STOCK_MARKET_CONTEXT_SCHEMA_VERSION,
        "level": level,
        "allow_buy": allow_buy,
        "allow_watch": allow_watch,
        "min_signal_score": min_signal_score,
        "score_adjustment": score_adjustment,
        "reasons": reasons,
    }


def _stock_breadth_market_context(breadth: Dict[str, Any]) -> Dict[str, Any]:
    """把单日市场宽度映射成 stock-only 大盘环境状态（纯函数，exact 边界，不扫参）。

    fail closed 优先：eligible/coverage 不足或必需字段缺失 → unknown（allow_buy=False）。
    判定顺序：unknown → defensive → cautious → favorable → neutral。
    """
    if not breadth:
        return _stock_breadth_state("unknown", 999, False, False, 0, ["breadth context missing"])

    # 必需字段必须都是有限数字：None/缺键/NaN/inf/非数字字符串均先于状态机 fail closed
    # （NaN 在比较中恒 False 会跳过 defensive 落 neutral，inf 扭曲阈值，'bad' 触发 TypeError）。
    validated = {
        field: _finite_number(breadth.get(field)) for field in _REQUIRED_BREADTH_FIELDS
    }
    invalid_fields = [field for field, value in validated.items() if value is None]
    if invalid_fields:
        return _stock_breadth_state(
            "unknown", 999, False, False, 0,
            [f"invalid required breadth fields: {','.join(invalid_fields)}"],
        )

    # eligible_count 必须非负且整数语义（100.5 不可数 → unknown），杜绝 NaN/inf/'bad'。
    eligible_raw = breadth.get("eligible_count")
    eligible = _finite_number(eligible_raw)
    if eligible is None or eligible < 0 or not float(eligible).is_integer():
        return _stock_breadth_state(
            "unknown", 999, False, False, 0,
            [f"invalid eligible_count {eligible_raw!r}"],
        )
    eligible = int(eligible)
    if eligible < STOCK_MARKET_MIN_ELIGIBLE:
        return _stock_breadth_state(
            "unknown", 999, False, False, 0,
            [f"eligible_count {eligible} below minimum {STOCK_MARKET_MIN_ELIGIBLE}"],
        )

    # coverage_pct 必须 0..100 的有限数字。
    coverage_raw = breadth.get("coverage_pct")
    coverage = _finite_number(coverage_raw)
    if coverage is None or coverage < 0 or coverage > 100:
        return _stock_breadth_state(
            "unknown", 999, False, False, 0,
            [f"invalid coverage_pct {coverage_raw!r}"],
        )
    if coverage < STOCK_MARKET_MIN_COVERAGE_PCT:
        return _stock_breadth_state(
            "unknown", 999, False, False, 0,
            [f"coverage_pct {coverage} below minimum {STOCK_MARKET_MIN_COVERAGE_PCT}"],
        )

    above_ma20 = validated["above_ma20_pct"]
    above_ma60 = validated["above_ma60_pct"]
    advancing = validated["advancing_pct"]
    median_20d = validated["median_return_20d_pct"]
    median_60d = validated["median_return_60d_pct"]
    positive_20d = validated["return_20d_positive_pct"]

    # defensive（先判）
    if above_ma20 <= 25:
        return _stock_breadth_state("defensive", 4, True, False, -18, ["above_ma20_pct<=25"])
    if above_ma60 <= 30 and median_60d is not None and median_60d < 0:
        return _stock_breadth_state(
            "defensive", 4, True, False, -18, ["above_ma60_pct<=30 and median_return_60d_pct<0"]
        )
    if median_20d <= -8:
        return _stock_breadth_state("defensive", 4, True, False, -18, ["median_return_20d_pct<=-8"])
    if advancing <= 20 and median_20d < 0:
        return _stock_breadth_state(
            "defensive", 4, True, False, -18, ["advancing_pct<=20 and median_return_20d_pct<0"]
        )

    # cautious
    if above_ma20 < 45:
        return _stock_breadth_state("cautious", 3, True, True, -8, ["above_ma20_pct<45"])
    if above_ma60 < 45:
        return _stock_breadth_state("cautious", 3, True, True, -8, ["above_ma60_pct<45"])
    if median_20d < 0:
        return _stock_breadth_state("cautious", 3, True, True, -8, ["median_return_20d_pct<0"])
    if advancing < 40:
        return _stock_breadth_state("cautious", 3, True, True, -8, ["advancing_pct<40"])

    # favorable
    if above_ma20 >= 65 and above_ma60 >= 60 and positive_20d >= 60 and median_20d >= 3:
        return _stock_breadth_state("favorable", 2, True, True, 8, ["breadth strongly favorable"])

    return _stock_breadth_state("neutral", 2.5, True, True, 0, ["breadth neutral"])


def _stock_relative_strength_context(
    latest_row: Any, market_returns: Dict[str, Any]
) -> Dict[str, Any]:
    """stock 相对市场强度（纯映射）；字段 / tags 仅用 stock_market / stock_rs 命名，禁用 proxy/ETF。

    None/NaN 安全：candidate 的 return_20d/return_60d 经 _pct_of 解析，缺失/NaN/非数字
    → 该 horizon 的 stock_return / relative_strength 都为 None 且不产生任何 stock_rs tag；
    另一个 horizon 有值仍可独立计算。绝不 _num(None)->0 把缺失伪装成 0/强势。
    """
    stock_20d = _pct_of(latest_row.get("return_20d"))
    stock_60d = _pct_of(latest_row.get("return_60d"))
    # market median/p75 用 _finite_number 二次校验：即便 caller 绕过 _stock_market_returns
    # 直接塞 NaN/inf/'bad'，也不得泄漏、不得输出 NaN/inf、不得产生 relative/leader tag。
    median_20d = _finite_number(market_returns.get("market_median_return_20d_pct")) if market_returns else None
    median_60d = _finite_number(market_returns.get("market_median_return_60d_pct")) if market_returns else None
    p75_20d = _finite_number(market_returns.get("market_p75_return_20d_pct")) if market_returns else None
    p75_60d = _finite_number(market_returns.get("market_p75_return_60d_pct")) if market_returns else None

    relative_20d = (
        round(stock_20d - median_20d, 2)
        if stock_20d is not None and median_20d is not None
        else None
    )
    relative_60d = (
        round(stock_60d - median_60d, 2)
        if stock_60d is not None and median_60d is not None
        else None
    )

    tags: List[str] = []
    if relative_20d is not None:
        if relative_20d >= 0:
            tags.append("stock_rs20_nonnegative")
        if relative_20d >= 10:
            tags.append("stock_rs20_gte_10")
        elif relative_20d >= 5:
            tags.append("stock_rs20_5_to_10")
        elif relative_20d >= 0:
            tags.append("stock_rs20_0_to_5")
        else:
            tags.append("stock_rs20_lt_0")
        if relative_20d >= 5:
            tags.append("stock_rs20_strong")
        # leader：stock 收益 >= 市场 p75 + 5（20d）
        if p75_20d is not None and stock_20d >= p75_20d + 5:
            tags.append("stock_rs20_market_leader")
    if relative_60d is not None:
        if relative_60d >= 0:
            tags.append("stock_rs60_nonnegative")
        if relative_60d >= 20:
            tags.append("stock_rs60_gte_20")
        elif relative_60d >= 10:
            tags.append("stock_rs60_10_to_20")
        elif relative_60d >= 0:
            tags.append("stock_rs60_0_to_10")
        else:
            tags.append("stock_rs60_lt_0")
        if relative_60d >= 10:
            tags.append("stock_rs60_strong")
        # leader：stock 收益 >= 市场 p75 + 10（60d）
        if p75_60d is not None and stock_60d >= p75_60d + 10:
            tags.append("stock_rs60_market_leader")

    return {
        "stock_return_20d_pct": stock_20d,
        "stock_return_60d_pct": stock_60d,
        "relative_strength_20d_pct": relative_20d,
        "relative_strength_60d_pct": relative_60d,
        # 只回显经 _finite_number 校验的市场基准，避免 raw market_returns 携带 NaN/inf
        # 穿透到输出（保证 json.dumps(allow_nan=False) 可序列化）。
        "market_median_return_20d_pct": median_20d,
        "market_median_return_60d_pct": median_60d,
        "market_p75_return_20d_pct": p75_20d,
        "market_p75_return_60d_pct": p75_60d,
        "tags": tags,
    }


STOCK_UNIVERSE_BENCHMARK_SCHEMA_VERSION = "stock-universe-equal-weight-benchmark/v2"


def _stock_universe_equal_weight_benchmark(
    market_breadth_by_date: Dict[str, Dict[str, Any]],
    start_date: str,
    analysis_end_date: Optional[str],
    *,
    expected_sessions: Optional[List[str]],
) -> Dict[str, Any]:
    """development diagnostic：等权日再平衡复利收益，基于 point-in-time stock breadth。

    纯诊断口径（非回测标尺、非 buy-hold）：对 [start_date, analysis_end_date] 内的每个
    breadth 日，按 date 排序后用其 `equal_weight_daily_return_pct` 做日复利。
    只有「所有纳入日 eligible>=100、coverage>=95、daily return 有限」才给值；
    任一不满足 / 空窗口 / 缺 analysis_end_date → return_pct=None、eligible=False 并附 reasons。
    空数据 fail closed，绝不静默给 0。
    """
    reasons: List[str] = []
    expected_day_count = (
        len(expected_sessions) if isinstance(expected_sessions, list) else None
    )

    def _window_fields() -> Dict[str, Any]:
        return {
            "stock_universe_benchmark_start_date": start_date,
            "stock_universe_benchmark_end_date": analysis_end_date,
            "stock_universe_benchmark_expected_days": expected_day_count,
        }

    if not analysis_end_date:
        reasons.append("analysis_end_date missing")
        return {
            "schema_version": STOCK_UNIVERSE_BENCHMARK_SCHEMA_VERSION,
            **_window_fields(),
            "stock_universe_equal_weight_daily_rebalanced_return_pct": None,
            "stock_universe_benchmark_days": 0,
            "stock_universe_benchmark_min_coverage_pct": None,
            "stock_universe_benchmark_required_coverage_pct": (
                STOCK_MARKET_MIN_COVERAGE_PCT
            ),
            "stock_universe_benchmark_eligible": False,
            "stock_universe_benchmark_reasons": reasons,
        }

    included = sorted(
        date
        for date, context in (market_breadth_by_date or {}).items()
        if start_date <= date <= analysis_end_date
    )
    included_coverages = [
        _finite_number(market_breadth_by_date[date].get("coverage_pct"))
        for date in included
    ]
    observed_min_coverage = (
        min(value for value in included_coverages if value is not None)
        if any(value is not None for value in included_coverages)
        else None
    )

    def _fail(reason: str) -> Dict[str, Any]:
        reasons.append(reason)
        return {
            "schema_version": STOCK_UNIVERSE_BENCHMARK_SCHEMA_VERSION,
            **_window_fields(),
            "stock_universe_equal_weight_daily_rebalanced_return_pct": None,
            "stock_universe_benchmark_days": len(included),
            "stock_universe_benchmark_min_coverage_pct": observed_min_coverage,
            "stock_universe_benchmark_required_coverage_pct": (
                STOCK_MARKET_MIN_COVERAGE_PCT
            ),
            "stock_universe_benchmark_eligible": False,
            "stock_universe_benchmark_reasons": reasons,
        }

    if expected_sessions is None:
        return _fail("covered open sessions are missing")
    expected = [str(session) for session in expected_sessions]
    if expected != sorted(expected) or len(expected) != len(set(expected)):
        return _fail("covered open sessions are not unique and sorted")
    if any(session < start_date or session > analysis_end_date for session in expected):
        return _fail("covered open session is outside benchmark range")
    if not expected:
        return _fail("covered open sessions are empty")
    if included != expected:
        missing = sorted(set(expected) - set(included))
        extra = sorted(set(included) - set(expected))
        return _fail(
            "breadth sessions differ from covered open sessions: "
            f"missing={missing}, extra={extra}"
        )
    if not included:
        return _fail("no breadth days in [start_date, analysis_end_date]")

    factor = 1.0
    for date in included:
        context = market_breadth_by_date[date]
        eligible = _finite_number(context.get("eligible_count"))
        coverage = _finite_number(context.get("coverage_pct"))
        daily = _finite_number(context.get("equal_weight_daily_return_pct"))
        if eligible is None or eligible < STOCK_MARKET_MIN_ELIGIBLE:
            return _fail("day %s eligible_count below minimum" % date)
        if coverage is None or coverage < STOCK_MARKET_MIN_COVERAGE_PCT:
            return _fail("day %s coverage_pct below minimum" % date)
        if daily is None:
            return _fail(
                "day %s equal_weight_daily_return_pct missing/non-finite" % date
            )
        if daily <= -100:
            return _fail(
                "day %s equal_weight_daily_return_pct is a total loss or worse" % date
            )
        factor *= 1.0 + daily / 100.0

    reasons.append("compounded across %d eligible breadth days" % len(included))
    return {
        "schema_version": STOCK_UNIVERSE_BENCHMARK_SCHEMA_VERSION,
        **_window_fields(),
        "stock_universe_equal_weight_daily_rebalanced_return_pct": round(
            (factor - 1.0) * 100.0, 2
        ),
        "stock_universe_benchmark_days": len(included),
        "stock_universe_benchmark_min_coverage_pct": observed_min_coverage,
        "stock_universe_benchmark_required_coverage_pct": (
            STOCK_MARKET_MIN_COVERAGE_PCT
        ),
        "stock_universe_benchmark_eligible": True,
        "stock_universe_benchmark_reasons": reasons,
    }


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
