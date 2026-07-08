import pandas as pd

from app.industry_history import IndustryHistoryProvider, MEMBERSHIP_CAVEAT


def fake_history_records():
    dates = pd.date_range("2025-01-01", periods=65, freq="B")
    prices = [100 + index for index in range(len(dates))]
    return [
        {
            "date": date.strftime("%Y-%m-%d"),
            "open": price,
            "close": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "volume": 1000000,
            "amount": 100000000,
            "change_pct": 1.0,
            "turnover": 1.5,
        }
        for date, price in zip(dates, prices)
    ]


def test_industry_history_source_check_normalizes_and_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.industry_history._akshare_board_items",
        lambda: [
            {"name": "测试行业", "code": "BK0001", "change_pct": 2.5, "turnover": 1.2},
            {"name": "其他行业", "code": "BK0002", "change_pct": 1.0, "turnover": 0.8},
        ],
    )
    monkeypatch.setattr(
        "app.industry_history._akshare_board_history",
        lambda symbol, start_date, end_date: fake_history_records(),
    )
    provider = IndustryHistoryProvider(str(tmp_path / "industry_history"))

    result = provider.source_check("2025-01-01", "2025-04-01", max_boards=1)

    assert result["board_count"] == 2
    assert result["checked"][0]["name"] == "测试行业"
    assert result["checked"][0]["history_rows"] == 65
    assert result["checked"][0]["strength"]["available"] is True
    assert result["checked"][0]["strength"]["above_ma20"] is True
    assert MEMBERSHIP_CAVEAT in result["membership_caveat"]


def test_industry_history_rotation_contexts_build_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.industry_history._akshare_board_items",
        lambda: [
            {"name": "测试行业", "code": "BK0001", "change_pct": 2.5, "turnover": 1.2},
            {"name": "其他行业", "code": "BK0002", "change_pct": 1.0, "turnover": 0.8},
        ],
    )
    monkeypatch.setattr(
        "app.industry_history._akshare_board_history",
        lambda symbol, start_date, end_date: fake_history_records(),
    )
    provider = IndustryHistoryProvider(str(tmp_path / "industry_history"))

    result = provider.rotation_contexts("2025-01-01", "2025-04-01", max_boards=2)

    latest = result["2025-04-01"]
    assert latest["sample_count"] == 2
    assert latest["above_ma20_pct"] == 100.0
    assert latest["return_20d_positive_pct"] == 100.0
    assert any(tag.startswith("industry_") for tag in latest["tags"])


def test_industry_history_uses_akshare_boards_as_primary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.industry_history._akshare_board_items",
        lambda: [
            {
                "name": "备用行业",
                "code": "BK9999",
                "change_pct": 1.2,
                "turnover": 2.3,
                "rising": 10,
                "falling": 2,
            }
        ],
    )
    provider = IndustryHistoryProvider(str(tmp_path / "industry_history"))

    result = provider.boards(use_cache_on_error=False)

    assert result[0]["name"] == "备用行业"
    assert result[0]["code"] == "BK9999"


def test_industry_history_source_check_reports_board_fetch_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.industry_history._akshare_board_items",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare unavailable")),
    )
    provider = IndustryHistoryProvider(str(tmp_path / "industry_history"))

    result = provider.source_check("2025-01-01", "2025-04-01", max_boards=1)

    assert result["board_count"] == 0
    assert result["checked"] == []
    assert result["errors"][0]["stage"] == "boards"
