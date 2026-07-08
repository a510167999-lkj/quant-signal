import os
from datetime import date, datetime, time
from typing import Optional, Set, Tuple
from zoneinfo import ZoneInfo

from app.akshare_client import akshare_call
from app.storage import read_json, write_json


def now_cn() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_trade_dates() -> Set[str]:
    cache_path = os.getenv("TRADE_CALENDAR_CACHE_PATH", "data/trade_calendar.json")
    try:
        import akshare as ak  # type: ignore

        frame = akshare_call(
            "tool_trade_date_hist_sina",
            lambda: ak.tool_trade_date_hist_sina(),
        )
    except Exception:
        cached = read_json(cache_path, {})
        dates = cached.get("dates", []) if isinstance(cached, dict) else []
        return set(str(item)[:10] for item in dates)

    dates = set()
    for value in frame["trade_date"].tolist():
        if hasattr(value, "strftime"):
            dates.add(value.strftime("%Y-%m-%d"))
        else:
            dates.add(str(value)[:10])
    write_json(cache_path, {"updated_at": now_cn().isoformat(), "dates": sorted(dates)})
    return dates


def is_trade_day(target: date) -> bool:
    trade_dates = _load_trade_dates()
    if trade_dates:
        return target.strftime("%Y-%m-%d") in trade_dates
    return False


def latest_trade_date_on_or_before(target: date) -> Optional[date]:
    trade_dates = _load_trade_dates()
    selected = [item for item in trade_dates if item <= target.strftime("%Y-%m-%d")]
    if not selected:
        return None
    return date.fromisoformat(max(selected))


def previous_trade_date(target: date) -> Optional[date]:
    trade_dates = _load_trade_dates()
    selected = [item for item in trade_dates if item < target.strftime("%Y-%m-%d")]
    if not selected:
        return None
    return date.fromisoformat(max(selected))


def next_trade_date(target: date) -> Optional[date]:
    trade_dates = _load_trade_dates()
    selected = [item for item in trade_dates if item > target.strftime("%Y-%m-%d")]
    if not selected:
        return None
    return date.fromisoformat(min(selected))


def next_calendar_gap(
    target: date, min_gap_days: int
) -> Optional[Tuple[date, date]]:
    """从 target(含) 向后扫交易日序列，返回首个相邻日历差 >= min_gap_days 的
    休市跳空 (last_trade_before_gap, first_trade_after_gap)；无则 None。
    min_gap_days<=0 直接返回 None。
    """
    if min_gap_days <= 0:
        return None
    target_str = target.strftime("%Y-%m-%d")
    future = sorted(item for item in _load_trade_dates() if item >= target_str)
    for left_str, right_str in zip(future, future[1:]):
        gap = (date.fromisoformat(right_str) - date.fromisoformat(left_str)).days
        if gap >= min_gap_days:
            return (date.fromisoformat(left_str), date.fromisoformat(right_str))
    return None


def is_a_share_trading_time(moment: datetime) -> bool:
    local = moment.astimezone(ZoneInfo("Asia/Shanghai"))
    if not is_trade_day(local.date()):
        return False
    current = local.time()
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)
