from __future__ import annotations

from app import factor_v3_path_a_protocol_bounce_daily as daily
from app.factor_v3_path_a_protocol_signal_specs import DN2_BOUNCE_TAG


def _trade(
    symbol: str,
    signal_date: str,
    *,
    ret20: float,
    market_level: str = "favorable",
    tags: list[str] | None = None,
    name: str = "n",
    exit_date: str | None = None,
) -> dict:
    entry = signal_date[:8] + f"{int(signal_date[8:10]) + 1:02d}"
    leave = exit_date or signal_date[:8] + f"{int(signal_date[8:10]) + 6:02d}"
    return {
        "symbol": symbol,
        "name": name,
        "signal_date": signal_date,
        "entry_date": entry,
        "exit_date": leave,
        "return_pct": 1.0,
        "rank_score": 0.0,
        "relative_strength": {"stock_return_20d_pct": ret20},
        "market_level": market_level,
        "signal_tags": tags or [DN2_BOUNCE_TAG],
        "mark_to_market_path": [
            {"date": entry, "close_return_pct": 0.0, "low_return_pct": 0.0},
            {"date": leave, "close_return_pct": 1.0, "low_return_pct": 0.0},
        ],
    }


def test_picks_least_extended_name_only() -> None:
    trades = [
        _trade("000002", "2026-08-14", ret20=12.0, name="追高"),
        _trade("000001", "2026-08-14", ret20=-3.0, name="抗追高"),
        _trade("000003", "2026-08-14", ret20=4.0, name="中间"),
    ]
    selected = daily.select_bounce_trades(trades)
    assert [row["symbol"] for row in selected] == ["000001"]
    report = daily.build_bounce_daily_report(trades, as_of="2026-08-14")
    assert report["status"] == "buy"
    assert report["pick"]["symbol"] == "000001"
    assert report["pick"]["name"] == "抗追高"
    assert report["effective_strategy"] is False
    assert report["automatic_trading_allowed"] is False


def test_gap_down_and_defensive_market_are_skipped() -> None:
    trades = [
        _trade(
            "000001",
            "2026-08-14",
            ret20=-5.0,
            tags=[DN2_BOUNCE_TAG, "price_gap_down"],
        ),
        _trade("000002", "2026-08-14", ret20=-8.0, market_level="defensive"),
    ]
    report = daily.build_bounce_daily_report(trades, as_of="2026-08-14")
    assert report["status"] == "cash"
    assert report["pick"] is None


def test_holding_blocks_new_name_same_slot() -> None:
    trades = [
        _trade("000001", "2026-08-03", ret20=-2.0, exit_date="2026-08-10", name="旧仓"),
        _trade("000002", "2026-08-05", ret20=-9.0, name="新信号"),
    ]
    report = daily.build_bounce_daily_report(trades, as_of="2026-08-05")
    assert report["status"] == "holding"
    assert report["holding"]["symbol"] == "000001"
    assert report["pick"] is None


def test_public_view_never_looks_like_an_order() -> None:
    missing = daily.public_bounce_daily_view(None)
    assert missing["available"] is False
    assert missing["auto_order"] is False
    assert missing["effective_strategy"] is False
    trades = [_trade("000001", "2026-08-14", ret20=-1.0, name="平安银行")]
    view = daily.public_bounce_daily_view(
        daily.build_bounce_daily_report(trades, as_of="2026-08-14")
    )
    assert view["available"] is True
    assert view["status"] == "buy"
    assert "000001" in view["headline"]
    assert view["auto_order"] is False
    assert "不自动下单" in view["detail"]


def test_table_names_the_stock() -> None:
    trades = [_trade("000001", "2026-08-14", ret20=-1.0, name="平安银行")]
    text = daily.format_bounce_daily_table(
        daily.build_bounce_daily_report(trades, as_of="2026-08-14")
    )
    assert "000001" in text
    assert "平安银行" in text
    assert "不自动交易" in text
