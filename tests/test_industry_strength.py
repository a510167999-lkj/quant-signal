import pandas as pd

from app.industry_strength import IndustryStrengthProvider
from app.storage import write_json


def test_industry_strength_uses_akshare_board_code_for_constituents(tmp_path, monkeypatch):
    requested_codes = []

    class FakeAkshare:
        @staticmethod
        def stock_board_industry_name_em():
            return pd.DataFrame(
                [
                    {
                        "板块名称": "测试行业",
                        "板块代码": "BK0001",
                        "涨跌幅": 2.5,
                        "换手率": 1.2,
                        "上涨家数": 20,
                        "下跌家数": 5,
                    }
                ]
            )

        @staticmethod
        def stock_board_industry_cons_em(symbol):
            requested_codes.append(symbol)
            return pd.DataFrame([{"代码": "600001", "名称": "测试股票", "涨跌幅": 3.1}])

    monkeypatch.setattr("app.industry_strength._load_akshare", lambda: FakeAkshare())
    provider = IndustryStrengthProvider(str(tmp_path / "industry_strength.json"), top_n=1)

    result = provider.build_map(use_cache_on_error=False)

    assert requested_codes == ["BK0001"]
    assert result["source"] == "AKShare stock_board_industry_*"
    assert result["industries"][0]["code"] == "BK0001"
    assert result["symbol_map"]["600001"]["industry"] == "测试行业"
    assert result["symbol_map"]["600001"]["industry_code"] == "BK0001"


def test_invalid_research_cache_is_not_used_as_live_industry_fallback(tmp_path, monkeypatch):
    cache_path = tmp_path / "industry_strength.json"
    write_json(
        str(cache_path),
        {
            "updated_at": "full_backtest_membership",
            "source": "research_fixture",
            "industries": [],
            "symbol_map": {"600001": {"industry": "污染缓存"}},
        },
    )
    provider = IndustryStrengthProvider(str(cache_path), top_n=1)

    def fail_fetch():
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(provider, "_fetch", fail_fetch)
    result = provider.build_map(use_cache_on_error=True)

    assert result["symbol_map"] == {}
    assert "industry_cache_invalid" in result["errors"]
