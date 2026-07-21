import sys
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from app import trading_calendar
from app.storage import read_json, write_json


def _fake_akshare(frame):
    return SimpleNamespace(tool_trade_date_hist_sina=lambda: frame)


def test_provider_success_writes_only_configured_calendar_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "configured" / "trade-calendar.json"
    frame = pd.DataFrame({"trade_date": ["2026-07-09", "2026-07-10"]})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRADE_CALENDAR_CACHE_PATH", str(cache_path))
    monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(frame))
    monkeypatch.setattr(
        trading_calendar,
        "akshare_call",
        lambda _endpoint, operation: operation(),
    )

    dates = trading_calendar._load_trade_dates()

    assert dates == {"2026-07-09", "2026-07-10"}
    assert read_json(str(cache_path), {})["dates"] == ["2026-07-09", "2026-07-10"]
    assert not (tmp_path / "data" / "trade_calendar.json").exists()


def test_provider_failure_uses_configured_cache_for_calendar_queries(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "configured" / "trade-calendar.json"
    write_json(
        str(cache_path),
        {
            "updated_at": "2026-07-10T15:00:00+08:00",
            "dates": ["2026-07-09", "2026-07-10", "2026-07-13"],
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRADE_CALENDAR_CACHE_PATH", str(cache_path))
    monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(pd.DataFrame()))
    monkeypatch.setattr(
        trading_calendar,
        "akshare_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    assert trading_calendar.is_trade_day(date(2026, 7, 10)) is True
    assert trading_calendar.is_trade_day(date(2026, 7, 11)) is False
    assert trading_calendar.previous_trade_date(date(2026, 7, 13)) == date(2026, 7, 10)
    assert trading_calendar.next_trade_date(date(2026, 7, 10)) == date(2026, 7, 13)
    assert trading_calendar.next_calendar_gap(date(2026, 7, 9), 3) == (
        date(2026, 7, 10),
        date(2026, 7, 13),
    )
    assert trading_calendar.is_a_share_trading_time(
        datetime(2026, 7, 10, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert not (tmp_path / "data" / "trade_calendar.json").exists()
