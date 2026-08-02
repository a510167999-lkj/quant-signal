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
    _daily_frame,
)
from app.config import get_settings
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


def test_client_requires_exact_jiaoch_response_contract_and_keeps_token_out_of_rows():
    transport = _Transport(_envelope(ADJ_FACTOR_FIELDS, [["600519.SH", "20250101", 1.0]]))
    client = JiaochHttpClient(transport=transport, points_token="points-secret")

    rows = client.fetch(
        "adj_factor",
        params={"ts_code": "600519.SH", "start_date": "20250101", "end_date": "20250101"},
        fields=ADJ_FACTOR_FIELDS,
    )

    assert rows == [{"ts_code": "600519.SH", "trade_date": "20250101", "adj_factor": 1.0}]
    assert transport.calls[0]["url"] == "https://jiaoch.site/adj_factor"
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
    daily = _daily_rows()
    factors = _factor_rows()

    class Client:
        def fetch(self, api_name, *, params, fields):
            assert fields == (DAILY_FIELDS if api_name == "daily" else ADJ_FACTOR_FIELDS)
            rows = daily if api_name == "daily" else factors
            return [dict(zip(fields, row, strict=True)) for row in rows]

    monkeypatch.setattr("app.jiaoch_live_market._date_strings", lambda _: ("20250101", "20250430"))
    provider = JiaochMarketDataProvider(
        client=Client(),
        now_provider=lambda: datetime(2025, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    frame, source = provider.history("600519", "a", lookback_days=90, adjust="qfq")

    assert len(frame) == 80
    assert source == "Jiaoch daily qfq"
    assert frame.iloc[0]["close"] < frame.iloc[-1]["close"]
    assert frame.iloc[0]["amount"] == 100_000_000


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


def test_runtime_settings_default_to_jiaoch_without_a_fallback(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("TUSHARE_FALLBACK_TO_AKSHARE", raising=False)

    settings = get_settings()

    assert settings.market_data_provider == "jiaoch"
    assert settings.tushare_fallback_to_akshare is False


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
