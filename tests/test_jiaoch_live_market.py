from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.jiaoch_live_market import (
    ADJ_FACTOR_FIELDS,
    DAILY_FIELDS,
    JiaochHttpClient,
    JiaochLiveMarketError,
    JiaochMarketDataProvider,
    _apply_qfq,
    _aggregate_daily_from_minutes,
    _daily_cache_source,
    _daily_frame,
)
from app.config import Settings, get_settings
from app.market_data import build_market_data_provider
from app.research_pit_transport import HttpEntityResponse


def _envelope(fields, items):
    return json.dumps(
        {"code": 0, "data": {"fields": list(fields), "items": items}, "msg": "success"},
        separators=(",", ":"),
    ).encode()


class _Transport:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return HttpEntityResponse(status=200, headers={}, body=self.body, body_complete=True)


def _daily_rows(days=80, code="600519.SH"):
    dates = pd.bdate_range("2025-01-01", periods=days)
    return [
        [
            code,
            day.strftime("%Y%m%d"),
            10.0 + index * 0.01,
            10.5 + index * 0.01,
            9.8 + index * 0.01,
            10.2 + index * 0.01,
            10.0 + index * 0.01,
            0.2,
            0.1,
            1000,
            100000,
        ]
        for index, day in enumerate(dates)
    ]


def _factor_rows(days=80, code="600519.SH"):
    dates = pd.bdate_range("2025-01-01", periods=days)
    return [[code, day.strftime("%Y%m%d"), 1.0 + index * 0.01] for index, day in enumerate(dates)]


def _minute_rows(days=80, code="600519.SH"):
    dates = pd.bdate_range("2025-01-01", periods=days)
    rows = []
    for index, day in enumerate(dates):
        rows.extend(
            [
                {
                    "ts_code": code,
                    "trade_time": f"{day:%Y-%m-%d} 15:00:00",
                    "open": 10.1 + index * 0.01,
                    "close": 10.2 + index * 0.01,
                    "high": 10.5 + index * 0.01,
                    "low": 10.0 + index * 0.01,
                    "vol": 734,
                    "amount": 60_000.75,
                },
                {
                    "ts_code": code,
                    "trade_time": f"{day:%Y-%m-%d} 09:30:00",
                    "open": 10.0 + index * 0.01,
                    "close": 10.1 + index * 0.01,
                    "high": 10.2 + index * 0.01,
                    "low": 9.8 + index * 0.01,
                    "vol": 500,
                    "amount": 40_000.5,
                },
            ]
        )
    return rows


def test_client_requires_exact_jiaoch_response_contract_and_keeps_token_out_of_rows():
    transport = _Transport(_envelope(ADJ_FACTOR_FIELDS, [["600519.SH", "20250101", 1.0]]))
    client = JiaochHttpClient(transport=transport, points_token="points-secret")

    rows = client.fetch(
        "adj_factor",
        params={"ts_code": "600519.SH", "start_date": "20250101", "end_date": "20250101"},
        fields=ADJ_FACTOR_FIELDS,
    )

    assert rows == [{"ts_code": "600519.SH", "trade_date": "20250101", "adj_factor": 1.0}]
    assert transport.calls[0]["url"] == "http://jiaoch.site/adj_factor"
    assert b"points-secret" in transport.calls[0]["body"]


@pytest.mark.parametrize(
    "body",
    [
        b'{"code":0,"data":{"fields":["ts_code"],"items":[["secret"]]},"msg":"success"}',
        b'{"code":0,"data":{"fields":["ts_code"],"items":[]},"msg":"success","code":0}',
    ],
)
def test_client_rejects_credential_echo_or_duplicate_json(body):
    transport = _Transport(body)
    client = JiaochHttpClient(transport=transport, points_token="secret")

    with pytest.raises(JiaochLiveMarketError):
        client.fetch("adj_factor", params={}, fields=ADJ_FACTOR_FIELDS)


def test_history_uses_causal_qfq_and_jiaoch_source_only(monkeypatch):
    minutes = _minute_rows()
    factors = _factor_rows()

    class Client:
        def fetch_stk_mins(self, *, ts_code, start_date, end_date, freq):
            assert (ts_code, start_date, end_date, freq) == (
                "600519.SH",
                "20250101",
                "20250430",
                "5min",
            )
            return minutes

        def fetch(self, api_name, *, params, fields):
            assert api_name == "adj_factor"
            assert fields == ADJ_FACTOR_FIELDS
            return [dict(zip(fields, row, strict=True)) for row in factors]

    monkeypatch.setattr("app.jiaoch_live_market._date_strings", lambda _: ("20250101", "20250430"))
    provider = JiaochMarketDataProvider(
        client=Client(),
        now_provider=lambda: datetime(2025, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    frame, source = provider.history("600519", "a", lookback_days=90, adjust="qfq")

    assert len(frame) == 80
    assert source == _daily_cache_source("qfq")
    assert frame.iloc[0]["close"] < frame.iloc[-1]["close"]
    assert frame.iloc[0]["volume"] == 1234


def test_history_allows_identity_qfq_when_etf_adj_factor_is_empty(monkeypatch):
    minutes = _minute_rows(code="510300.SH")

    class Client:
        def fetch_stk_mins(self, *, ts_code, start_date, end_date, freq):
            assert ts_code == "510300.SH"
            return minutes

        def fetch(self, api_name, *, params, fields):
            assert api_name == "adj_factor"
            return []

    monkeypatch.setattr("app.jiaoch_live_market._date_strings", lambda _: ("20250101", "20250430"))
    provider = JiaochMarketDataProvider(
        client=Client(),
        now_provider=lambda: datetime(2025, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    frame, source = provider.history("510300", "etf", lookback_days=90, adjust="qfq")
    assert len(frame) == 80
    assert source == _daily_cache_source("qfq")
    assert float(frame.iloc[-1]["close"]) == pytest.approx(10.2 + 79 * 0.01)


def test_history_allows_identity_qfq_when_etf_adj_factor_is_empty(monkeypatch):
    minutes = _minute_rows(code="510300.SH")

    class Client:
        def fetch_stk_mins(self, *, ts_code, start_date, end_date, freq):
            assert ts_code == "510300.SH"
            return minutes

        def fetch(self, api_name, *, params, fields):
            assert api_name == "adj_factor"
            return []

    monkeypatch.setattr("app.jiaoch_live_market._date_strings", lambda _: ("20250101", "20250430"))
    provider = JiaochMarketDataProvider(
        client=Client(),
        now_provider=lambda: datetime(2025, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    frame, source = provider.history("510300", "etf", lookback_days=90, adjust="qfq")
    assert len(frame) == 80
    assert source == _daily_cache_source("qfq")
    assert float(frame.iloc[-1]["close"]) == pytest.approx(10.2 + 79 * 0.01)
    assert frame.iloc[0]["amount"] == pytest.approx(100_001.25)


def test_aggregate_daily_from_minutes_orders_bars_and_converts_units_once():
    rows = _minute_rows(days=2)

    daily = _aggregate_daily_from_minutes(rows)

    assert list(daily[0]) == list(DAILY_FIELDS)
    assert daily[0] == {
        "ts_code": "600519.SH",
        "trade_date": "20250101",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "pre_close": 10.0,
        "change": pytest.approx(0.2),
        "pct_chg": pytest.approx(2.0),
        "vol": 12.34,
        "amount": pytest.approx(100.00125),
    }
    assert daily[1]["pre_close"] == 10.2
    frame = _daily_frame(daily, symbol="600519", market="a")
    assert frame.iloc[0]["volume"] == 1234.0
    assert frame.iloc[0]["amount"] == pytest.approx(100_001.25)


def test_qfq_uses_one_unrounded_causal_factor_ratio():
    raw = _daily_frame(
        [dict(zip(DAILY_FIELDS, _daily_rows(days=2)[index], strict=True)) for index in range(2)],
        symbol="600519",
        market="a",
    )
    factors = [
        {"ts_code": "600519.SH", "trade_date": "20250101", "adj_factor": 1.234567},
        {"ts_code": "600519.SH", "trade_date": "20250102", "adj_factor": 2.345678},
        {"ts_code": "600519.SH", "trade_date": "20250103", "adj_factor": 99.0},
    ]

    adjusted = _apply_qfq(raw, factors, expected_code="600519.SH")

    for column in ("open", "high", "low", "close"):
        expected = raw.iloc[0][column] * 1.234567 / 2.345678
        assert adjusted.iloc[0][column] == pytest.approx(expected)
        assert adjusted.iloc[0][column] != round(expected, 2)
        assert adjusted.iloc[1][column] == raw.iloc[1][column]
    assert adjusted.attrs["adjustment_as_of_date"] == "2025-01-02"


def test_snapshot_uses_jiaoch_daily_and_identity_rows():
    daily = [["600519.SH", "20250501", 10, 10.5, 9.8, 10.2, 10, 0.2, 2.0, 1000, 100000]]

    class Client:
        def fetch(self, api_name, *, params, fields):
            assert api_name == "daily"
            return [dict(zip(fields, row, strict=True)) for row in daily]

    provider = JiaochMarketDataProvider(
        client=Client(),
        identity_loader=lambda: {"600519": {"name": "fixture"}},
        now_provider=lambda: datetime(2025, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    snapshot = provider.snapshot()

    assert snapshot[0]["symbol"] == "600519"
    assert snapshot[0]["name"] == "fixture"
    assert snapshot[0]["market_snapshot_source"].startswith("Jiaoch")
    assert snapshot[0]["amount"] == 100_000_000


def test_factory_exposes_explicit_jiaoch_provider_without_network_call():
    provider = build_market_data_provider("jiaoch", 1800, "")

    assert isinstance(provider, JiaochMarketDataProvider)


def test_factory_defaults_to_jiaoch_provider_without_network_call():
    provider = build_market_data_provider("", 1800, "")

    assert isinstance(provider, JiaochMarketDataProvider)


def test_runtime_settings_default_to_jiaoch_without_a_fallback(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("TUSHARE_FALLBACK_TO_AKSHARE", raising=False)

    settings = get_settings()

    assert settings.market_data_provider == "jiaoch"
    assert settings.tushare_fallback_to_akshare is False


def test_direct_settings_default_to_jiaoch_without_a_fallback():
    settings = Settings()

    assert settings.market_data_provider == "jiaoch"
    assert settings.tushare_fallback_to_akshare is False
    assert settings.enable_fund_flow_context is False


def test_non_jiaoch_disk_cache_is_not_a_runtime_fallback(tmp_path, monkeypatch):
    class BrokenClient:
        def fetch(self, *args, **kwargs):
            raise JiaochLiveMarketError("fixture failure")

    provider = JiaochMarketDataProvider(
        client=BrokenClient(),
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        now_provider=lambda: datetime(2025, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    frame = _daily_frame(
        [dict(zip(DAILY_FIELDS, row, strict=True)) for row in _daily_rows()],
        symbol="600519",
        market="a",
    )
    provider._write_cache("600519", "a", "qfq", frame, "AKShare stale cache")
    monkeypatch.setattr("app.jiaoch_live_market._date_strings", lambda _: ("20250101", "20250430"))

    with pytest.raises(JiaochLiveMarketError):
        provider.history("600519", "a", lookback_days=90, adjust="qfq")


def test_jiaoch_disk_cache_rejects_legacy_units_and_accepts_versioned_units(tmp_path):
    provider = JiaochMarketDataProvider(
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        now_provider=lambda: datetime(2025, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    frame = _daily_frame(
        [dict(zip(DAILY_FIELDS, row, strict=True)) for row in _daily_rows()],
        symbol="600519",
        market="a",
    )
    provider._write_cache("600519", "a", "qfq", frame, "Jiaoch stk_mins daily qfq")

    assert provider._read_cache("600519", "a", "qfq") is None

    source = _daily_cache_source("qfq")
    provider._write_cache("600519", "a", "qfq", frame, source)
    cached = provider._read_cache("600519", "a", "qfq")

    assert cached is not None
    cached_frame, cached_source = cached
    assert cached_source == source
    assert cached_frame.iloc[0]["volume"] == frame.iloc[0]["volume"]
    assert cached_frame.iloc[0]["amount"] == frame.iloc[0]["amount"]


def test_jiaoch_disk_cache_rejects_basis_mismatch_and_partial_legacy_overlap(tmp_path):
    provider = JiaochMarketDataProvider(disk_cache_path=str(tmp_path / "cache.sqlite"))
    frame = _daily_frame(
        [dict(zip(DAILY_FIELDS, row, strict=True)) for row in _daily_rows()],
        symbol="600519",
        market="a",
    )
    provider._write_cache("600519", "a", "qfq", frame, _daily_cache_source("raw"))

    assert provider._read_cache("600519", "a", "qfq") is None

    recent = frame.iloc[20:].copy()
    provider._write_cache("600519", "a", "qfq", recent, _daily_cache_source("qfq"))

    assert provider._read_cache("600519", "a", "qfq") is None
