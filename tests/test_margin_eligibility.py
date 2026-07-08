import pandas as pd

from app.margin_eligibility import MarginEligibilityProvider, parse_sse_margin_js


def test_parse_sse_margin_js_extracts_rows_and_static_date():
    rows, static_date = parse_sse_margin_js(
        """
        var showdate = '20260706'
        tableData['tableData_961'] = {
          staticDate:"2026-07-05 07:30:00",
          list:[
            '', ['1', '510300 ', '300ETF '],
            '', ['2', '600519 ', '贵州茅台 '],
            '', ['x', 'bad ', '坏数据 ']
          ]
        };
        """
    )

    assert static_date == "2026-07-05 07:30:00"
    assert rows == [
        {"seq": "1", "symbol": "510300", "name": "300ETF"},
        {"seq": "2", "symbol": "600519", "name": "贵州茅台"},
    ]


def test_margin_provider_merges_sse_and_szse_flags(monkeypatch, tmp_path):
    provider = MarginEligibilityProvider(str(tmp_path / "margin.json"))

    def fake_fetch_sse(symbol_map):
        from app.margin_eligibility import _merge_symbol

        _merge_symbol(
            symbol_map,
            "600519",
            exchange="SSE",
            name="贵州茅台",
            financing_eligible=True,
            collateral_eligible=True,
            as_of="2026-07-05 07:30:00",
            sources=["sse"],
        )

    def fake_fetch_szse(symbol_map, as_of=None):
        from app.margin_eligibility import _merge_symbol

        _merge_symbol(
            symbol_map,
            "000001",
            exchange="SZSE",
            name="平安银行",
            financing_eligible=True,
            short_eligible=True,
            collateral_eligible=True,
            price_limit="10%",
            as_of="2026-07-03",
            sources=["szse"],
        )

    monkeypatch.setattr(provider, "_fetch_sse", fake_fetch_sse)
    monkeypatch.setattr(provider, "_fetch_szse", fake_fetch_szse)

    payload = provider.build_map(use_cache_on_error=False)

    assert payload["summary"]["financing_count"] == 2
    assert payload["summary"]["collateral_count"] == 2
    assert payload["symbol_map"]["600519"]["exchange"] == "SSE"
    assert payload["symbol_map"]["000001"]["price_limit"] == "10%"


def test_fetch_szse_xlsx_uses_first_non_empty_date(monkeypatch, tmp_path):
    provider = MarginEligibilityProvider(str(tmp_path / "margin.json"))
    calls = []

    def fake_request(catalog_id, referer, item_date):
        calls.append(item_date)
        if item_date == "2026-07-05":
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "证券代码": "000001",
                    "证券简称": "平安银行",
                    "融资标的": "Y",
                    "融券标的": "N",
                    "当日可融资": "Y",
                    "当日可融券": "N",
                    "涨跌幅限制": "10%",
                }
            ]
        )

    monkeypatch.setattr(provider, "_request_szse_xlsx", fake_request)

    frame = provider._fetch_szse_xlsx("catalog", "referer", ["2026-07-05", "2026-07-03"])

    assert calls == ["2026-07-05", "2026-07-03"]
    assert frame.attrs["as_of"] == "2026-07-03"
    assert frame.iloc[0]["证券代码"] == "000001"


def test_szse_today_eligibility_is_stricter_than_underlying(monkeypatch, tmp_path):
    provider = MarginEligibilityProvider(str(tmp_path / "margin.json"))

    def fake_fetch_sse(symbol_map):
        return None

    def fake_fetch_xlsx(catalog_id, referer, dates):
        if catalog_id == "1834_xxpl":
            frame = pd.DataFrame(
                [
                    {
                        "证券代码": "000001",
                        "证券简称": "平安银行",
                        "融资标的": "Y",
                        "融券标的": "Y",
                        "当日可融资": "N",
                        "当日可融券": "N",
                        "涨跌幅限制": "10%",
                    }
                ]
            )
        else:
            frame = pd.DataFrame([{"证券代码": "000001", "证券简称": "平安银行"}])
        frame.attrs["as_of"] = "2026-07-03"
        return frame

    monkeypatch.setattr(provider, "_fetch_sse", fake_fetch_sse)
    monkeypatch.setattr(provider, "_fetch_szse_xlsx", fake_fetch_xlsx)

    payload = provider.build_map(use_cache_on_error=False)
    item = payload["symbol_map"]["000001"]

    assert item["financing_underlying"] is True
    assert item["financing_eligible"] is False
    assert item["short_underlying"] is True
    assert item["short_eligible"] is False
    assert item["collateral_eligible"] is True


def test_build_szse_underlying_map_caches_by_requested_date(monkeypatch, tmp_path):
    provider = MarginEligibilityProvider(str(tmp_path / "margin.json"))
    calls = {"count": 0}

    def fake_fetch_xlsx(catalog_id, referer, dates):
        calls["count"] += 1
        frame = pd.DataFrame(
            [
                {
                    "证券代码": "000001",
                    "证券简称": "平安银行",
                    "融资标的": "Y",
                    "融券标的": "Y",
                    "当日可融资": "Y",
                    "当日可融券": "N",
                    "涨跌幅限制": "10%",
                }
            ]
        )
        frame.attrs["as_of"] = "2026-07-03"
        return frame

    monkeypatch.setattr(provider, "_fetch_szse_xlsx", fake_fetch_xlsx)

    first = provider.build_szse_underlying_map("2026-07-05", cache_dir=str(tmp_path / "cache"))
    second = provider.build_szse_underlying_map("2026-07-05", cache_dir=str(tmp_path / "cache"))

    assert calls["count"] == 1
    assert first == second
    assert first["summary"]["source_as_of"] == "2026-07-03"
    assert first["symbol_map"]["000001"]["financing_eligible"] is True
