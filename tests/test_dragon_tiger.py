import pandas as pd

from app.dragon_tiger import build_dragon_tiger_tags, normalize_lhb_frame


def test_normalize_lhb_frame_merges_reasons_and_drops_future_columns():
    frame = pd.DataFrame(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "上榜日": "2026-06-15",
                "解读": "1家机构买入，成功率52.94%",
                "收盘价": 12.3,
                "涨跌幅": 9.8,
                "龙虎榜净买额": 60_000_000,
                "龙虎榜买入额": 160_000_000,
                "龙虎榜卖出额": 100_000_000,
                "龙虎榜成交额": 260_000_000,
                "市场总成交额": 1_000_000_000,
                "净买额占总成交比": 6,
                "成交额占总成交比": 26,
                "换手率": 21,
                "流通市值": 20_000_000_000,
                "上榜原因": "日涨幅偏离值达到7%的前5只证券",
                "上榜后5日": -99,
            },
            {
                "代码": "000001",
                "名称": "平安银行",
                "上榜日": "2026-06-15",
                "解读": "1家机构买入，成功率52.94%",
                "收盘价": 12.3,
                "涨跌幅": 9.8,
                "龙虎榜净买额": 60_000_000,
                "龙虎榜买入额": 160_000_000,
                "龙虎榜卖出额": 100_000_000,
                "龙虎榜成交额": 260_000_000,
                "市场总成交额": 1_000_000_000,
                "净买额占总成交比": 6,
                "成交额占总成交比": 26,
                "换手率": 21,
                "流通市值": 20_000_000_000,
                "上榜原因": "日换手率达到20%的前5只证券",
                "上榜后5日": 99,
            },
        ]
    )

    items = normalize_lhb_frame(frame)

    assert len(items) == 1
    item = items[0]
    assert item["symbol"] == "000001"
    assert item["trade_date"] == "2026-06-15"
    assert item["institution_buy_count"] == 1
    assert "日涨幅偏离值达到7%的前5只证券" in item["reasons"]
    assert "日换手率达到20%的前5只证券" in item["reasons"]
    assert "上榜后5日" not in item
    assert "lhb_net_buy_ratio_gte_5" in item["tags"]
    assert "lhb_reason_up" in item["tags"]
    assert "lhb_reason_turnover" in item["tags"]


def test_build_dragon_tiger_tags_marks_negative_sell_pressure():
    tags = build_dragon_tiger_tags(
        {
            "net_buy_amount": -20_000_000,
            "net_buy_ratio_pct": -4,
            "lhb_turnover_ratio_pct": 45,
            "turnover_rate_pct": 8,
            "change_pct": -8,
            "institution_sell_count": 2,
            "reasons": ["日跌幅偏离值达到7%的前5只证券"],
        }
    )

    assert "lhb_on_list" in tags
    assert "lhb_net_buy_negative" in tags
    assert "lhb_net_buy_ratio_lte_neg3" in tags
    assert "lhb_turnover_ratio_gte_40" in tags
    assert "lhb_institution_sell" in tags
    assert "lhb_reason_down" in tags
