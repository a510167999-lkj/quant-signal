import pandas as pd
import pytest

from app.market_data import AkshareDataProvider, MarketDataError, TushareDataProvider, build_market_data_provider


def _raw_daily_frame(days=80):
    dates = pd.date_range("2025-01-01", periods=days, freq="D")
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": [10 + index * 0.01 for index in range(days)],
            "最高": [10.5 + index * 0.01 for index in range(days)],
            "最低": [9.8 + index * 0.01 for index in range(days)],
            "收盘": [10.2 + index * 0.01 for index in range(days)],
            "成交量": [1000000 for _ in range(days)],
            "成交额": [100000000 for _ in range(days)],
        }
    )


def test_a_share_history_passes_configured_akshare_timeout(monkeypatch):
    calls = {}

    class FakeAkshare:
        def stock_zh_a_hist(self, **kwargs):
            calls.update(kwargs)
            return _raw_daily_frame()

    monkeypatch.setenv("MARKET_DATA_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setattr("app.market_data._load_akshare", lambda: FakeAkshare())
    monkeypatch.setattr("app.market_data._date_strings", lambda lookback_days: ("20250101", "20250331"))

    frame, source = AkshareDataProvider().history("600519", "a", lookback_days=90)

    assert source == "AKShare stock_zh_a_hist"
    assert calls["timeout"] == 3.5
    assert len(frame) >= 60


def test_history_uses_persistent_sqlite_cache(monkeypatch, tmp_path):
    calls = {"fetch": 0}

    class FakeAkshare:
        def stock_zh_a_hist(self, **kwargs):
            calls["fetch"] += 1
            return _raw_daily_frame()

    cache_path = tmp_path / "market_data_cache.sqlite"
    monkeypatch.setattr("app.market_data._load_akshare", lambda: FakeAkshare())
    monkeypatch.setattr("app.market_data._date_strings", lambda lookback_days: ("20250101", "20250331"))
    monkeypatch.setattr("app.market_data.is_trade_day", lambda target: False)
    monkeypatch.setattr(
        "app.market_data.latest_trade_date_on_or_before",
        lambda target: pd.Timestamp("2025-03-21").date(),
    )

    first_provider = AkshareDataProvider(disk_cache_path=str(cache_path))
    first_frame, first_source = first_provider.history("600519", "a", lookback_days=90)

    class BrokenAkshare:
        def stock_zh_a_hist(self, **kwargs):
            raise RuntimeError("network unavailable")

        def stock_zh_a_daily(self, **kwargs):
            raise RuntimeError("fallback unavailable")

    monkeypatch.setattr("app.market_data._load_akshare", lambda: BrokenAkshare())
    second_provider = AkshareDataProvider(disk_cache_path=str(cache_path))
    second_frame, second_source = second_provider.history("600519", "a", lookback_days=90)

    assert first_source == "AKShare stock_zh_a_hist"
    assert second_source == "SQLite daily cache"
    assert calls["fetch"] == 1
    assert len(second_frame) == len(first_frame)


def test_tushare_history_normalizes_pro_bar(monkeypatch):
    calls = {}

    class FakeTushare:
        def set_token(self, token):
            calls["token"] = token

        def pro_api(self, token):
            calls["pro_token"] = token
            return object()

        def pro_bar(self, **kwargs):
            calls.update(kwargs)
            dates = pd.date_range("2025-01-01", periods=80, freq="D")
            return pd.DataFrame(
                {
                    "trade_date": dates.strftime("%Y%m%d"),
                    "open": [10 + index * 0.01 for index in range(80)],
                    "high": [10.5 + index * 0.01 for index in range(80)],
                    "low": [9.8 + index * 0.01 for index in range(80)],
                    "close": [10.2 + index * 0.01 for index in range(80)],
                    "vol": [1000000 for _ in range(80)],
                    "amount": [100000000 for _ in range(80)],
                }
            )

    monkeypatch.setattr("app.market_data._load_tushare", lambda: FakeTushare())
    monkeypatch.setattr("app.market_data._date_strings", lambda lookback_days: ("20250101", "20250331"))

    frame, source = TushareDataProvider(fallback_to_akshare=False, token="test-token").history(
        "600519", "a", lookback_days=90
    )

    assert source == "Tushare pro_bar"
    assert calls["token"] == "test-token"
    assert calls["ts_code"] == "600519.SH"
    assert calls["adj"] == "qfq"
    assert calls["asset"] == "E"
    assert len(frame) == 80
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]


def test_tushare_history_can_fallback_to_akshare(monkeypatch):
    class BrokenTushare:
        def set_token(self, token):
            pass

        def pro_api(self, token):
            return object()

        def pro_bar(self, **kwargs):
            raise RuntimeError("Tushare unavailable")

    class FakeAkshare:
        def stock_zh_a_hist(self, **kwargs):
            return _raw_daily_frame()

    monkeypatch.setattr("app.market_data._load_tushare", lambda: BrokenTushare())
    monkeypatch.setattr("app.market_data._load_akshare", lambda: FakeAkshare())
    monkeypatch.setattr("app.market_data._date_strings", lambda lookback_days: ("20250101", "20250331"))

    frame, source = TushareDataProvider(fallback_to_akshare=True, token="test-token").history(
        "600519", "a", lookback_days=90
    )

    assert source == "AKShare stock_zh_a_hist"
    assert len(frame) >= 60


def test_market_data_provider_factory_rejects_unknown_provider():
    try:
        build_market_data_provider("unknown", 1800, "")
    except MarketDataError as exc:
        assert "Unsupported MARKET_DATA_PROVIDER" in str(exc)
    else:
        raise AssertionError("Expected MarketDataError")


class FakeMootdxDaily:
    def __init__(self, bars, actions=None):
        self.bars = bars
        self.actions = list(actions or [])
        self.history_calls = []

    def history(self, symbol, start_date, end_date):
        self.history_calls.append((symbol, start_date, end_date))
        return self.bars.copy()

    def corporate_actions(self, symbol, after_date):
        return [item for item in self.actions if item["date"] > after_date]


def normalized_frame(start="2025-01-01", days=80):
    dates = pd.bdate_range(start, periods=days)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [10.0] * days,
            "high": [10.5] * days,
            "low": [9.8] * days,
            "close": [10.2] * days,
            "volume": [1_000_000.0] * days,
            "amount": [100_000_000.0] * days,
        }
    )


def broken_fetch(*args, **kwargs):
    raise MarketDataError("AKShare unavailable")


def test_mootdx_raw_fallback_does_not_require_cache(monkeypatch, tmp_path):
    bars = normalized_frame()
    provider = AkshareDataProvider(
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        enable_mootdx_daily_fallback=True,
        mootdx_daily=FakeMootdxDaily(bars),
    )
    monkeypatch.setattr(provider, "_fetch", broken_fetch)
    monkeypatch.setattr("app.market_data._date_strings", lambda days: ("20250101", "20250430"))

    frame, source = provider.history("600519", "a", adjust="")

    assert len(frame) == 80
    assert source == "MOOTDX raw daily fallback"


def test_mootdx_fallback_also_handles_provider_import_failure(monkeypatch, tmp_path):
    provider = AkshareDataProvider(
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        enable_mootdx_daily_fallback=True,
        mootdx_daily=FakeMootdxDaily(normalized_frame()),
    )
    monkeypatch.setattr(provider, "_fetch", lambda *args: (_ for _ in ()).throw(RuntimeError("import")))
    monkeypatch.setattr("app.market_data._date_strings", lambda days: ("20250101", "20250430"))

    frame, source = provider.history("600519", "a", adjust="")

    assert len(frame) == 80
    assert source == "MOOTDX raw daily fallback"


def test_mootdx_qfq_requires_trusted_cache(monkeypatch, tmp_path):
    provider = AkshareDataProvider(
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        enable_mootdx_daily_fallback=True,
        mootdx_daily=FakeMootdxDaily(normalized_frame()),
    )
    monkeypatch.setattr(provider, "_fetch", broken_fetch)
    monkeypatch.setattr("app.market_data._date_strings", lambda days: ("20250101", "20250430"))

    with pytest.raises(MarketDataError, match="trusted qfq cache"):
        provider.history("600519", "a", adjust="qfq")


def test_mootdx_qfq_appends_only_new_rows_when_no_action(monkeypatch, tmp_path):
    cached = normalized_frame(days=80)
    latest = cached.iloc[-1]["date"]
    new_bars = normalized_frame(start=str(pd.Timestamp(latest) + pd.offsets.BDay(1)), days=2)
    provider = AkshareDataProvider(
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        enable_mootdx_daily_fallback=True,
        mootdx_daily=FakeMootdxDaily(new_bars),
    )
    provider._write_disk_cache("600519", "a", "qfq", cached, "trusted")
    monkeypatch.setattr(provider, "_minimum_fresh_cache_date", lambda: "2099-01-01")
    monkeypatch.setattr(provider, "_fetch", broken_fetch)
    monkeypatch.setattr("app.market_data._date_strings", lambda days: ("20250101", "20251231"))

    frame, source = provider.history("600519", "a", adjust="qfq")

    assert len(frame) == 82
    assert frame.iloc[-1]["date"] == new_bars.iloc[-1]["date"]
    assert source == "MOOTDX incremental qfq fallback"


@pytest.mark.parametrize("category", [1, 11])
def test_mootdx_qfq_corporate_action_blocks_merge(monkeypatch, tmp_path, category):
    cached = normalized_frame(days=80)
    latest = cached.iloc[-1]["date"]
    new_bars = normalized_frame(start=str(pd.Timestamp(latest) + pd.offsets.BDay(1)), days=2)
    action = {"date": new_bars.iloc[0]["date"], "category": category, "fenhong": 1.0}
    provider = AkshareDataProvider(
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        enable_mootdx_daily_fallback=True,
        mootdx_daily=FakeMootdxDaily(new_bars, [action]),
    )
    provider._write_disk_cache("600519", "a", "qfq", cached, "trusted")
    monkeypatch.setattr(provider, "_minimum_fresh_cache_date", lambda: "2099-01-01")
    monkeypatch.setattr(provider, "_fetch", broken_fetch)
    monkeypatch.setattr("app.market_data._date_strings", lambda days: ("20250101", "20251231"))

    frame, source = provider.history("600519", "a", adjust="qfq")

    assert len(frame) == 80
    assert source == "SQLite daily cache stale fallback"


def test_mootdx_hfq_is_never_used(monkeypatch, tmp_path):
    provider = AkshareDataProvider(
        disk_cache_path=str(tmp_path / "cache.sqlite"),
        enable_mootdx_daily_fallback=True,
        mootdx_daily=FakeMootdxDaily(normalized_frame()),
    )
    monkeypatch.setattr(provider, "_fetch", broken_fetch)
    monkeypatch.setattr("app.market_data._date_strings", lambda days: ("20250101", "20250430"))

    with pytest.raises(MarketDataError, match="hfq"):
        provider.history("600519", "a", adjust="hfq")
