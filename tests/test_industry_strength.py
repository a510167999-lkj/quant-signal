import pandas as pd

from app.industry_strength import IndustryStrengthProvider


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
