import math
from typing import Any, Dict


_ABSOLUTE_COMPARISON_TOLERANCE = 1e-9


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _strictly_above(value: float, boundary: float) -> bool:
    return value > boundary and not math.isclose(
        value,
        boundary,
        rel_tol=0.0,
        abs_tol=_ABSOLUTE_COMPARISON_TOLERANCE,
    )


def _strictly_below(value: float, boundary: float) -> bool:
    return value < boundary and not math.isclose(
        value,
        boundary,
        rel_tol=0.0,
        abs_tol=_ABSOLUTE_COMPARISON_TOLERANCE,
    )


def _at_or_above(value: float, boundary: float) -> bool:
    return value > boundary or math.isclose(
        value,
        boundary,
        rel_tol=0.0,
        abs_tol=_ABSOLUTE_COMPARISON_TOLERANCE,
    )


def assess_entry_executability(
    signal_bar: Dict[str, Any],
    entry_bar: Dict[str, Any],
    max_gap_up_pct: float = 6.0,
    max_gap_down_pct: float = 7.0,
    locked_limit_gap_pct: float = 9.3,
    max_intraday_range_pct: float = 8.0,
    decision_cutoff: str = "next_open",
) -> Dict[str, Any]:
    if decision_cutoff not in {"next_open", "session_close"}:
        raise ValueError("decision_cutoff must be next_open or session_close")
    previous_close = _num(signal_bar.get("close"))
    entry_open = _num(
        entry_bar.get("open")
        if decision_cutoff == "next_open"
        else entry_bar.get("open") or entry_bar.get("close")
    )
    entry_high = (
        _num(entry_bar.get("high") or entry_open) if decision_cutoff == "session_close" else None
    )
    entry_low = (
        _num(entry_bar.get("low") or entry_open) if decision_cutoff == "session_close" else None
    )
    reasons = []

    if previous_close <= 0 or entry_open <= 0:
        return {
            "executable": False,
            "gap_pct": None,
            "intraday_range_pct": None,
            "decision_cutoff": decision_cutoff,
            "reasons": ["入场价或前收价缺失。"],
        }

    gap_pct = (entry_open / previous_close - 1) * 100
    intraday_range_pct = (
        (entry_high / entry_low - 1) * 100
        if entry_high is not None and entry_low is not None and entry_low > 0
        else None
    )
    if _strictly_above(gap_pct, max_gap_up_pct):
        reasons.append("次日高开 %.2f%%，超过追价阈值 %.2f%%。" % (gap_pct, max_gap_up_pct))
    if _strictly_below(gap_pct, -abs(max_gap_down_pct)):
        reasons.append("次日低开 %.2f%%，信号已明显受损。" % gap_pct)

    locked_limit = _at_or_above(gap_pct, locked_limit_gap_pct) and (
        decision_cutoff == "next_open"
        or (intraday_range_pct is not None and intraday_range_pct <= 0.35)
    )
    if locked_limit:
        reasons.append("疑似一字涨停或极难成交开盘。")
    if intraday_range_pct is not None and intraday_range_pct > max_intraday_range_pct:
        reasons.append(
            "入场日振幅 %.2f%%，超过波动阈值 %.2f%%。"
            % (intraday_range_pct, max_intraday_range_pct)
        )

    return {
        "executable": not reasons,
        "gap_pct": round(gap_pct, 2),
        "intraday_range_pct": round(intraday_range_pct, 2)
        if intraday_range_pct is not None
        else None,
        "decision_cutoff": decision_cutoff,
        "reasons": reasons,
    }
