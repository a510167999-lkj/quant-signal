import pandas as pd

from app.mootdx_l1 import MootdxL1QuoteProvider


def test_mootdx_l1_normalizes_quotes_and_tags():
    provider = MootdxL1QuoteProvider(enabled=False)
    frame = pd.DataFrame(
        [
            {
                "code": "301308",
                "price": 695.26,
                "last_close": 618.02,
                "open": 680,
                "high": 710,
                "low": 659.16,
                "amount": 15_651_550_000,
                "volume": 228836,
                "servertime": "11:23:02.850",
                "bid1": 695.26,
                "ask1": 695.28,
                "bid_vol1": 4,
                "ask_vol1": 2,
            }
        ]
    )

    quotes = provider._normalize_frame(frame)

    assert quotes["301308"]["change_pct"] == 12.5
    assert quotes["301308"]["open_gap_pct"] == 10.03
    assert quotes["301308"]["spread_pct"] == 0.0029
    assert quotes["301308"]["near_limit_up"] is False
    assert "l1_open_gap_gt_5" in quotes["301308"]["tags"]
    assert "l1_spread_lte_0_2" in quotes["301308"]["tags"]


def test_mootdx_l1_filters_unsupported_symbols():
    provider = MootdxL1QuoteProvider(enabled=False)
    assert provider.quotes(["600519", "920001", "abc", "600519"])["quotes"] == {}
