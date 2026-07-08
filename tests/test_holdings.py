from app.holdings import DEFAULT_HOLDINGS, _normalize_l1_quote, load_holdings


def test_load_holdings_uses_default_positions(tmp_path):
    path = tmp_path / "holdings.json"

    items = load_holdings(str(path))

    assert [item["symbol"] for item in items] == [item["symbol"] for item in DEFAULT_HOLDINGS]
    assert path.exists()


def test_normalize_l1_quote_scales_ten_times_etf_quote():
    quote = {
        "price": 14.8,
        "previous_close": 14.39,
        "open": 14.41,
        "high": 15.22,
        "low": 14.41,
        "bid1": 14.8,
        "ask1": 14.81,
        "change_pct": 2.85,
    }
    analysis = {"last_close": 1.48}

    normalized = _normalize_l1_quote(quote, analysis)

    assert normalized["price"] == 1.48
    assert normalized["previous_close"] == 1.439
    assert normalized["ask1"] == 1.481
    assert normalized["normalization_scale"] == 0.1
