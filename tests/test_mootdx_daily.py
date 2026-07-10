from datetime import datetime

import pandas as pd
import pytest

from app.market_data import MarketDataError
from app.mootdx_daily import MootdxDailyProvider, market_code_for_symbol


def page(start_date, count):
    dates = pd.bdate_range(start_date, periods=count).to_pydatetime().tolist()
    return pd.DataFrame(
        {
            "datetime": [value.strftime("%Y-%m-%d 15:00") for value in dates],
            "open": [10.0] * count,
            "high": [11.0] * count,
            "low": [9.0] * count,
            "close": [10.5] * count,
            "vol": [1000.0] * count,
            "amount": [1_050_000.0] * count,
        }
    )


class FakeClient:
    def __init__(self, pages=None, connect=True, error=None):
        self.pages = list(pages or [])
        self.connect_result = connect
        self.error = error
        self.calls = []
        self.closed = False

    def connect(self, host, port):
        self.calls.append(("connect", host, port))
        return self.connect_result

    def bars(self, **kwargs):
        self.calls.append(("bars", kwargs))
        if self.error:
            raise self.error
        return self.pages.pop(0) if self.pages else pd.DataFrame()

    def xdxr(self, symbol):
        self.calls.append(("xdxr", symbol))
        return pd.DataFrame(
            [{"year": 2026, "month": 7, "day": 1, "category": 1, "fenhong": 1.0}]
        )

    def close(self):
        self.closed = True


def factory_from(clients):
    queue = list(clients)

    def factory(server, timeout):
        return queue.pop(0)

    return factory


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [("600519", 1), ("510300", 1), ("588000", 1), ("000001", 0), ("159915", 0), ("430001", 2), ("830001", 2), ("920001", 2)],
)
def test_market_code_routes_all_supported_exchanges(symbol, expected):
    assert market_code_for_symbol(symbol) == expected


def test_history_pages_with_frequency_nine_and_normalizes_rows():
    newest = page(datetime(2026, 1, 1), 800)
    older = page(datetime(2023, 1, 1), 800)
    oldest = page(datetime(2020, 1, 1), 200)
    client = FakeClient([newest, older, oldest])
    provider = MootdxDailyProvider(
        servers="good:7709", client_factory=factory_from([client]), max_pages=3
    )

    frame = provider.history("600519", "2020-01-01", "2028-12-31")

    bar_calls = [call[1] for call in client.calls if call[0] == "bars"]
    assert [value["start"] for value in bar_calls] == [0, 800, 1600]
    assert {value["frequency"] for value in bar_calls} == {9}
    assert frame.columns.tolist() == ["date", "open", "high", "low", "close", "volume", "amount"]
    assert frame["date"].is_monotonic_increasing
    assert not frame["date"].duplicated().any()
    assert client.closed is True


def test_history_fails_over_on_connect_false_exception_and_empty_result():
    dead = FakeClient(connect=False)
    broken = FakeClient(error=RuntimeError("protocol"))
    empty = FakeClient([])
    good = FakeClient([page(datetime(2026, 1, 1), 100)])
    provider = MootdxDailyProvider(
        servers="dead:1,broken:2,empty:3,good:4",
        client_factory=factory_from([dead, broken, empty, good]),
    )

    frame = provider.history("000001", "2026-01-01", "2026-12-31")

    assert len(frame) == 100
    assert all(client.closed for client in (dead, broken, empty, good))


def test_history_rejects_invalid_ohlc_and_negative_volume():
    invalid = page(datetime(2026, 1, 1), 100)
    invalid.loc[0, "low"] = 12
    invalid.loc[1, "vol"] = -1
    provider = MootdxDailyProvider(
        servers="bad:1", client_factory=factory_from([FakeClient([invalid])])
    )

    with pytest.raises(MarketDataError, match="quality"):
        provider.history("600519", "2026-01-01", "2026-12-31")


def test_history_prefers_existing_volume_when_mootdx_returns_vol_and_volume():
    raw = page(datetime(2026, 1, 1), 100)
    raw["volume"] = raw["vol"]
    provider = MootdxDailyProvider(
        servers="good:1", client_factory=factory_from([FakeClient([raw])])
    )

    frame = provider.history("600519", "2026-01-01", "2026-12-31")

    assert frame.columns.tolist().count("volume") == 1
    assert frame["volume"].iloc[0] == 1000


def test_corporate_actions_returns_only_events_after_date():
    client = FakeClient([page(datetime(2026, 1, 1), 100)])
    provider = MootdxDailyProvider(
        servers="good:7709", client_factory=factory_from([client])
    )

    actions = provider.corporate_actions("600519", "2026-06-30")

    assert len(actions) == 1
    assert actions[0]["date"] == "2026-07-01"
    assert actions[0]["category"] == 1
    assert client.closed is True
