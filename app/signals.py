from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.indicators import ensure_indicator_columns


REQUIRED_COLUMNS = [
    "ma5",
    "ma20",
    "ma60",
    "macd_hist",
    "rsi14",
    "atr14",
    "high20_prev",
    "low20_prev",
    "volume_ratio",
    "return_20d",
    "drawdown_60d",
    "volatility_20d",
]


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


def _round(value: Any, digits: int = 4) -> Optional[float]:
    number = _num(value)
    if number is None:
        return None
    return round(number, digits)


def _pct(value: Any) -> Optional[float]:
    number = _num(value)
    if number is None:
        return None
    return round(number * 100, 2)


def _append(target: List[str], message: str) -> None:
    if message not in target:
        target.append(message)


def _trade_plans(
    action: str,
    action_label: str,
    close: float,
    entry_zone: Dict[str, Optional[float]],
    levels: Dict[str, Optional[float]],
    ma20: Optional[float],
    ma60: Optional[float],
    rsi: Optional[float],
    return_20d: Optional[float],
    drawdown_60d: Optional[float],
    volatility_20d: Optional[float],
) -> Dict[str, Dict[str, Any]]:
    short_conditions = []
    short_risks = []
    if action in {"BUY", "WATCH"}:
        short_conditions.append("只在入场区内分批，突破后放量确认再加仓。")
    elif action == "HOLD":
        short_conditions.append("已有仓位按支撑和止损管理，未持有不追高。")
    elif action == "REDUCE":
        short_conditions.append("反弹接近压力位优先降低仓位，等待重新站上均线。")
    else:
        short_conditions.append("防守优先，跌破支撑或止损时退出。")
    short_risks.append("短线计划以 3-10 个交易日为主，信号失效后不摊平。")

    long_action = "AVOID"
    long_label = "不适合长期配置"
    long_conditions = []
    long_risks = []

    trend_positive = bool(ma20 and ma60 and close > ma20 > ma60)
    above_ma60 = bool(ma60 and close > ma60)
    extended = bool(return_20d is not None and return_20d >= 0.25)
    weak_trend = bool(ma60 and close < ma60)
    deep_drawdown = bool(drawdown_60d is not None and drawdown_60d <= -0.25)
    hot_rsi = bool(rsi is not None and rsi >= 72)
    high_volatility = bool(volatility_20d is not None and volatility_20d >= 0.75)

    if trend_positive and not extended and not hot_rsi:
        long_action = "ACCUMULATE"
        long_label = "长期可分批配置"
        long_conditions.append("20 日线在 60 日线上方且价格保持强趋势，可用回踩分批。")
    elif trend_positive:
        long_action = "HOLD_CORE"
        long_label = "长期持有核心仓 / 不追高"
        long_conditions.append("趋势仍强，但短期涨幅或情绪偏热，适合持有已有核心仓。")
        long_conditions.append("新增仓位等待回踩 20 日线附近或下一次温和突破。")
    elif above_ma60 and not deep_drawdown:
        long_action = "WATCH_BASE"
        long_label = "长期观察底仓"
        long_conditions.append("价格仍在 60 日线上方，但趋势确认不够，底仓需轻。")
    elif weak_trend or deep_drawdown:
        long_action = "REDUCE_AVOID"
        long_label = "长期减仓 / 暂不配置"
        long_conditions.append("中期趋势或回撤结构偏弱，长期仓位不占优。")
    else:
        long_conditions.append("长期趋势信号不足，等待 20/60 日线重新走顺。")

    if high_volatility:
        long_risks.append("中期波动率过高，长期仓位也需要分批和更小单票权重。")
    if deep_drawdown:
        long_risks.append("距 60 日高点回撤较深，先确认趋势修复再谈长期持有。")
    if extended:
        long_risks.append("20 日涨幅过大，长期好票也不适合在短线情绪高点追。")
    if not long_risks:
        long_risks.append("长期计划以趋势破坏为退出条件，不因单日波动频繁交易。")

    accumulation_low = None
    accumulation_high = None
    if ma20:
        accumulation_low = ma20 * 0.97
        accumulation_high = ma20 * 1.03
    elif ma60:
        accumulation_low = ma60 * 0.98
        accumulation_high = ma60 * 1.04

    trend_stop = None
    if ma60:
        trend_stop = ma60 * 0.97
    elif levels.get("support"):
        trend_stop = levels["support"]

    return {
        "short_term": {
            "horizon": "3-10 个交易日",
            "action": action,
            "label": action_label,
            "entry_zone": entry_zone,
            "stop_loss": levels.get("stop_loss"),
            "take_profit": levels.get("take_profit"),
            "support": levels.get("support"),
            "resistance": levels.get("resistance"),
            "positioning": "信号仓/短线仓，单票风险优先受控。",
            "conditions": short_conditions,
            "risks": short_risks,
        },
        "long_term": {
            "horizon": "1-6 个月滚动复核",
            "action": long_action,
            "label": long_label,
            "accumulation_zone": {
                "low": _round(accumulation_low),
                "high": _round(accumulation_high),
            },
            "trend_stop": _round(trend_stop),
            "core_support": _round(ma60),
            "review_line": _round(ma20),
            "positioning": "核心仓/配置仓，按 20/60 日趋势和回撤管理。",
            "conditions": long_conditions,
            "risks": long_risks,
        },
    }


def evaluate_signal(frame: pd.DataFrame) -> Dict[str, Any]:
    enriched = ensure_indicator_columns(frame, REQUIRED_COLUMNS)
    latest = enriched.iloc[-1]
    previous = enriched.iloc[-2]

    close = float(latest["close"])
    score = 0.0
    reasons: List[str] = []
    risks: List[str] = []
    confirmations: List[str] = []

    ma5 = _num(latest.get("ma5"))
    ma20 = _num(latest.get("ma20"))
    ma60 = _num(latest.get("ma60"))
    macd_hist = _num(latest.get("macd_hist"))
    prev_macd_hist = _num(previous.get("macd_hist"))
    rsi = _num(latest.get("rsi14"))
    prev_rsi = _num(previous.get("rsi14"))
    high20_prev = _num(latest.get("high20_prev"))
    low20_prev = _num(latest.get("low20_prev"))
    atr14 = _num(latest.get("atr14"))
    volume_ratio = _num(latest.get("volume_ratio"))
    return_20d = _num(latest.get("return_20d"))
    drawdown_60d = _num(latest.get("drawdown_60d"))
    volatility_20d = _num(latest.get("volatility_20d"))

    if ma20 and ma60:
        if close > ma20 > ma60:
            score += 2.0
            _append(reasons, "价格站上 20/60 日均线，趋势结构偏多。")
        elif close < ma20 < ma60:
            score -= 2.0
            _append(risks, "价格跌破 20/60 日均线，趋势结构偏空。")
        elif close > ma20:
            score += 0.8
            _append(confirmations, "价格在 20 日均线上方，短线仍有支撑。")
        elif close < ma20:
            score -= 0.8
            _append(risks, "价格在 20 日均线下方，短线动能不足。")

    if macd_hist is not None and prev_macd_hist is not None:
        if prev_macd_hist <= 0 < macd_hist:
            score += 2.0
            _append(reasons, "MACD 柱线由负转正，出现动能金叉信号。")
        elif prev_macd_hist >= 0 > macd_hist:
            score -= 2.0
            _append(risks, "MACD 柱线由正转负，出现动能死叉信号。")
        elif macd_hist > 0:
            score += 0.5
            _append(confirmations, "MACD 动能仍在零轴上方。")
        else:
            score -= 0.5
            _append(risks, "MACD 动能仍在零轴下方。")

    if rsi is not None:
        if prev_rsi is not None and 32 <= rsi <= 58 and rsi > prev_rsi and ma5 and close >= ma5:
            score += 1.2
            _append(reasons, "RSI 从中低位回升，且价格守住 5 日均线。")
        elif rsi < 30:
            score += 0.5
            _append(confirmations, "RSI 进入超卖区，适合观察止跌反转。")
            _append(risks, "超卖不等于立即反弹，需要等待价格确认。")
        elif rsi > 75:
            score -= 1.5
            _append(risks, "RSI 高于 75，短线过热，追高风险上升。")

    if high20_prev and volume_ratio:
        if close > high20_prev and volume_ratio >= 1.2:
            score += 1.6
            _append(reasons, "放量突破近 20 日高点，趋势确认度提升。")
        elif close > high20_prev:
            score += 0.8
            _append(confirmations, "突破近 20 日高点，但量能确认不足。")

    if low20_prev and close < low20_prev:
        score -= 2.0
        _append(risks, "跌破近 20 日低点，防守位失效。")

    if return_20d is not None:
        if return_20d > 0.12:
            score -= 0.8
            _append(risks, "20 日涨幅偏大，短线回撤概率上升。")
        elif return_20d < -0.10 and rsi is not None and rsi < 40:
            score -= 0.5
            _append(risks, "20 日跌幅较大且 RSI 偏弱，左侧接入风险高。")

    if drawdown_60d is not None and drawdown_60d < -0.18:
        score -= 0.8
        _append(risks, "距 60 日高点回撤超过 18%，中期趋势仍需修复。")

    if volatility_20d is not None and volatility_20d > 0.45:
        _append(risks, "20 日年化波动率偏高，仓位应更保守。")

    support_candidates = [item for item in [ma20, ma60, low20_prev] if item is not None and item < close]
    resistance_candidates = [item for item in [high20_prev, _num(latest.get("boll_upper"))] if item is not None and item > close]
    support = max(support_candidates) if support_candidates else low20_prev
    resistance = min(resistance_candidates) if resistance_candidates else high20_prev

    if atr14 and atr14 > 0:
        atr_stop = close - 2 * atr14
        swing_stop = low20_prev if low20_prev else atr_stop
        stop_loss = max(min(close * 0.97, close - 0.5 * atr14), min(atr_stop, swing_stop))
        take_profit = close + 2.5 * atr14
    else:
        stop_loss = close * 0.95
        take_profit = close * 1.08

    if score >= 4.0:
        action = "BUY"
        action_label = "买入观察 / 可分批试仓"
    elif score >= 2.0:
        action = "WATCH"
        action_label = "观察等待确认"
    elif score <= -3.5:
        action = "SELL"
        action_label = "卖出 / 避险"
    elif score <= -1.5:
        action = "REDUCE"
        action_label = "减仓 / 暂缓买入"
    else:
        action = "HOLD"
        action_label = "持有观察"

    confidence = min(95, 45 + int(abs(score) * 10))
    if not reasons and confirmations:
        reasons.extend(confirmations[:2])
    if not reasons:
        reasons.append("当前信号强度一般，等待趋势、动能或量能给出更明确确认。")

    entry_zone = {
        "low": _round(close * 0.99),
        "high": _round(close * 1.01),
    }
    levels = {
        "support": _round(support),
        "resistance": _round(resistance),
        "stop_loss": _round(stop_loss),
        "take_profit": _round(take_profit),
    }

    return {
        "as_of": str(latest["date"]),
        "action": action,
        "action_label": action_label,
        "score": round(score, 2),
        "confidence": confidence,
        "last_close": round(close, 4),
        "entry_zone": entry_zone,
        "levels": levels,
        "trade_plans": _trade_plans(
            action,
            action_label,
            close,
            entry_zone,
            levels,
            ma20,
            ma60,
            rsi,
            return_20d,
            drawdown_60d,
            volatility_20d,
        ),
        "reasons": reasons[:5],
        "risks": risks[:5],
        "confirmations": confirmations[:5],
        "indicators": {
            "ma5": _round(ma5),
            "ma20": _round(ma20),
            "ma60": _round(ma60),
            "rsi14": _round(rsi, 2),
            "macd_hist": _round(macd_hist, 4),
            "atr14": _round(atr14, 4),
            "volume_ratio": _round(volume_ratio, 2),
            "return_20d_pct": _pct(return_20d),
            "drawdown_60d_pct": _pct(drawdown_60d),
            "volatility_20d_pct": _pct(volatility_20d),
        },
    }
