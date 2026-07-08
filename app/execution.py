from typing import Any, Dict


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def assess_entry_executability(
    signal_bar: Dict[str, Any],
    entry_bar: Dict[str, Any],
    max_gap_up_pct: float = 6.0,
    max_gap_down_pct: float = 7.0,
    locked_limit_gap_pct: float = 9.3,
    max_intraday_range_pct: float = 8.0,
) -> Dict[str, Any]:
    previous_close = _num(signal_bar.get("close"))
    entry_open = _num(entry_bar.get("open") or entry_bar.get("close"))
    entry_high = _num(entry_bar.get("high") or entry_open)
    entry_low = _num(entry_bar.get("low") or entry_open)
    reasons = []

    if previous_close <= 0 or entry_open <= 0:
        return {
            "executable": False,
            "gap_pct": None,
            "intraday_range_pct": None,
            "reasons": ["入场价或前收价缺失。"],
        }

    gap_pct = (entry_open / previous_close - 1) * 100
    intraday_range_pct = (entry_high / entry_low - 1) * 100 if entry_low > 0 else 0
    if gap_pct > max_gap_up_pct:
        reasons.append("次日高开 %.2f%%，超过追价阈值 %.2f%%。" % (gap_pct, max_gap_up_pct))
    if gap_pct < -abs(max_gap_down_pct):
        reasons.append("次日低开 %.2f%%，信号已明显受损。" % gap_pct)

    locked_limit = gap_pct >= locked_limit_gap_pct and intraday_range_pct <= 0.35
    if locked_limit:
        reasons.append("疑似一字涨停或极难成交开盘。")
    if intraday_range_pct > max_intraday_range_pct:
        reasons.append(
            "入场日振幅 %.2f%%，超过波动阈值 %.2f%%。"
            % (intraday_range_pct, max_intraday_range_pct)
        )

    return {
        "executable": not reasons,
        "gap_pct": round(gap_pct, 2),
        "intraday_range_pct": round(intraday_range_pct, 2),
        "reasons": reasons,
    }
