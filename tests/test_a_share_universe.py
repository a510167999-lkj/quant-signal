from app.a_share_universe import AShareUniverseProvider, select_deep_scan_candidates
from app.storage import write_json


def test_universe_snapshot_uses_cache_when_akshare_import_fails(tmp_path, monkeypatch):
    cache_path = tmp_path / "a_share_universe.json"
    write_json(
        str(cache_path),
        {
            "updated_at": "2026-07-06T09:00:00+08:00",
            "items": [
                {
                    "symbol": "600001",
                    "market": "a",
                    "name": "缓存股票",
                    "latest": 10,
                    "amount": 100000000,
                    "change_pct": 2.0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.a_share_universe._load_akshare",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare missing")),
    )

    items = AShareUniverseProvider(str(cache_path)).snapshot(use_cache_on_error=True)

    assert items[0]["symbol"] == "600001"
    assert items[0]["market_snapshot_source"] == "unknown:legacy_cache"


def test_select_deep_scan_candidates_adds_rank_percentiles():
    snapshot = [
        {
            "symbol": "600001",
            "market": "a",
            "name": "高成交",
            "latest": 10,
            "amount": 1_000_000_000,
            "change_pct": 2.0,
        },
        {
            "symbol": "600002",
            "market": "a",
            "name": "中成交",
            "latest": 10,
            "amount": 500_000_000,
            "change_pct": 2.0,
        },
        {
            "symbol": "600003",
            "market": "a",
            "name": "低成交",
            "latest": 10,
            "amount": 100_000_000,
            "change_pct": 2.0,
        },
    ]

    result = select_deep_scan_candidates(snapshot, max_deep=2, min_amount=1, min_price=1, max_price=100)

    assert len(result) == 2
    assert result[0]["candidate_rank"] == 1
    assert result[0]["candidate_rank_pct"] == 50.0
    assert result[0]["amount_rank"] == 1
    assert result[0]["amount_rank_pct"] == 33.33
    assert result[1]["candidate_rank"] == 2
    assert result[1]["candidate_rank_pct"] == 100.0


def test_select_deep_scan_candidates_round_robins_industry_leaders():
    snapshot = [
        {
            "symbol": "600001",
            "market": "a",
            "name": "行业A一",
            "latest": 10,
            "amount": 1_000_000_000,
            "change_pct": 2.0,
        },
        {
            "symbol": "600002",
            "market": "a",
            "name": "行业A二",
            "latest": 10,
            "amount": 900_000_000,
            "change_pct": 2.0,
        },
        {
            "symbol": "600003",
            "market": "a",
            "name": "行业A三",
            "latest": 10,
            "amount": 800_000_000,
            "change_pct": 2.0,
        },
        {
            "symbol": "600101",
            "market": "a",
            "name": "行业B一",
            "latest": 10,
            "amount": 100_000_000,
            "change_pct": 2.0,
        },
        {
            "symbol": "600102",
            "market": "a",
            "name": "行业B二",
            "latest": 10,
            "amount": 90_000_000,
            "change_pct": 2.0,
        },
    ]
    industry_map = {
        "600001": {"industry": "行业A", "industry_rank": 1, "industry_score": 9},
        "600002": {"industry": "行业A", "industry_rank": 1, "industry_score": 9},
        "600003": {"industry": "行业A", "industry_rank": 1, "industry_score": 9},
        "600101": {"industry": "行业B", "industry_rank": 2, "industry_score": 8},
        "600102": {"industry": "行业B", "industry_rank": 2, "industry_score": 8},
    }

    result = select_deep_scan_candidates(
        snapshot,
        max_deep=4,
        min_amount=1,
        min_price=1,
        max_price=100,
        industry_map=industry_map,
        per_industry_top_n=2,
    )

    symbols = {item["symbol"] for item in result}
    assert symbols == {"600001", "600002", "600101", "600102"}
    assert {item["industry"] for item in result} == {"行业A", "行业B"}


def test_select_deep_scan_candidates_limits_top_industries_and_top_symbols():
    snapshot = []
    industry_map = {}
    for industry_index, industry in enumerate(["行业A", "行业B", "行业C", "行业D"], start=1):
        for stock_index in range(1, 5):
            symbol = f"60{industry_index:02d}{stock_index:02d}"
            snapshot.append(
                {
                    "symbol": symbol,
                    "market": "a",
                    "name": f"{industry}{stock_index}",
                    "latest": 10,
                    "amount": 1_000_000_000 - industry_index * 10_000_000 - stock_index,
                    "change_pct": 2.0,
                }
            )
            industry_map[symbol] = {
                "industry": industry,
                "industry_rank": industry_index,
                "industry_score": 10 - industry_index,
            }

    result = select_deep_scan_candidates(
        snapshot,
        max_deep=20,
        min_amount=1,
        min_price=1,
        max_price=100,
        industry_map=industry_map,
        per_industry_top_n=3,
        industry_top_n=3,
    )

    assert len(result) == 9
    assert {item["industry"] for item in result} == {"行业A", "行业B", "行业C"}
    assert all(item["symbol"][-2:] in {"01", "02", "03"} for item in result)
