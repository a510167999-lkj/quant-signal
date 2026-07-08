from dataclasses import replace

from app.config import Settings
from app.research_backtest import (
    _realized_trade_from_future,
    _relative_strength_context,
    _select_with_portfolio_controls,
    run_candidate_research_backtest,
    run_historical_universe_research_backtest,
)
from app.storage import write_json
from tests.test_signals import sample_frame


class FakeProvider:
    def history(self, symbol, market, lookback_days=620, adjust="qfq"):
        frame = sample_frame("up", periods=180)
        if market == "a":
            frame.loc[91:, "low"] = frame.loc[91:, "open"] * 0.94
        return frame, "fake-provider"


def test_research_backtest_stop_loss_caps_trade_return(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
    })
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=FakeProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        buy_only=True,
        min_score=4,
        stop_loss_pct=5,
    )

    assert result["summary"]["selected_trade_count"] > 0
    assert result["summary"]["rolling_1y"]["latest"]["return_pct"] is not None
    assert any(item["exit_reason"] == "stop_loss" for item in result["worst"])
    assert result["worst"][0]["return_pct"] >= -5


def test_research_backtest_take_profit_exits_early(tmp_path, monkeypatch):
    class TakeProfitProvider:
        def history(self, symbol, market, lookback_days=620, adjust="qfq"):
            frame = sample_frame("up", periods=180)
            if market == "a":
                frame.loc[91:, "high"] = frame.loc[91:, "open"] * 1.06
                frame.loc[91:, "low"] = frame.loc[91:, "open"] * 0.99
            return frame, "fake-provider"

    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
    })
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=TakeProfitProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        buy_only=True,
        min_score=4,
        take_profit_pct=5,
    )

    assert result["summary"]["selected_trade_count"] > 0
    assert result["summary"]["take_profit_pct"] == 5
    assert any(item["exit_reason"] == "take_profit" for item in result["best"])
    assert result["best"][0]["return_pct"] == 5


def test_realized_trade_trailing_stop_uses_prior_high():
    frame = sample_frame("up", periods=120)
    frame.loc[91:, "high"] = frame.loc[91:, "open"] * 1.08
    frame.loc[91:, "low"] = frame.loc[91:, "open"] * 1.01
    frame.loc[92:, "low"] = frame.loc[92:, "open"] * 0.94

    trade = _realized_trade_from_future(
        frame,
        entry_index=91,
        exit_index=101,
        trailing_stop_pct=3,
    )

    assert trade["exit_reason"] == "trailing_stop"
    assert trade["return_pct"] > 0
    assert trade["max_favorable_pct"] >= trade["return_pct"]
    assert trade["mark_to_market_path"]
    assert trade["mark_to_market_path"][-1]["date"] == trade["exit_date"]
    assert trade["mark_to_market_path"][-1]["close_return_pct"] == trade["return_pct"]
    assert "open_return_pct" in trade["mark_to_market_path"][-1]
    assert "high_return_pct" in trade["mark_to_market_path"][-1]


def test_research_backtest_announcement_context_blocks_known_risk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
    })
    monkeypatch.setattr(
        "app.research_cache.fetch_cninfo_announcements",
        lambda symbol, start_date, end_date: [
            {
                "title": "测试股票关于收到行政处罚决定书的公告",
                "published_at": "2025-06-10",
                "url": "https://www.cninfo.com.cn/test",
                "score": -14,
                "hard_blocker": True,
                "categories": ["regulatory_penalty"],
            }
        ],
    )
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
        announcement_lookback_days=365,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=FakeProvider(),
        start_date="2025-06-15",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        buy_only=True,
        min_score=4,
        use_announcement_context=True,
    )

    assert result["summary"]["selected_trade_count"] == 0
    assert result["summary"]["announcement_blocked_count"] > 0
    assert result["summary"]["announcement_blocked_by_event"]["regulatory_penalty"] > 0


def test_research_backtest_announcement_context_ignores_future_risk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
    })
    monkeypatch.setattr(
        "app.research_cache.fetch_cninfo_announcements",
        lambda symbol, start_date, end_date: [
            {
                "title": "测试股票关于收到行政处罚决定书的公告",
                "published_at": "2026-06-10",
                "url": "https://www.cninfo.com.cn/test",
                "score": -14,
                "hard_blocker": True,
                "categories": ["regulatory_penalty"],
            }
        ],
    )
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
        announcement_lookback_days=365,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=FakeProvider(),
        start_date="2025-06-15",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        buy_only=True,
        min_score=4,
        use_announcement_context=True,
    )

    assert result["summary"]["selected_trade_count"] > 0
    assert result["summary"]["announcement_blocked_count"] == 0
    assert result["announcement_group_stats"]["by_event"]["no_announcement_event"]["trade_count"] > 0


def test_research_backtest_can_require_announcement_event(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
    })
    monkeypatch.setattr(
        "app.research_cache.fetch_cninfo_announcements",
        lambda symbol, start_date, end_date: [
            {
                "title": "测试股票关于回购公司股份进展的公告",
                "published_at": "2025-06-10",
                "url": "https://www.cninfo.com.cn/test",
                "score": 5,
                "hard_blocker": False,
                "categories": ["buyback_or_increase"],
            }
        ],
    )
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
        announcement_lookback_days=365,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=FakeProvider(),
        start_date="2025-06-15",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        buy_only=True,
        min_score=4,
        use_announcement_context=True,
        require_announcement_events="buyback_or_increase",
        require_all_announcement_events=True,
    )

    assert result["summary"]["selected_trade_count"] > 0
    assert result["summary"]["required_announcement_events"] == ["buyback_or_increase"]
    assert result["summary"]["require_all_announcement_events"] is True
    assert "buyback_or_increase" in result["announcement_group_stats"]["by_event"]


def test_research_backtest_can_require_signal_tag(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
        "reasons": [],
        "confirmations": [],
        "risks": [],
        "indicators": {},
    })
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=FakeProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        require_signal_tags="score_gte_5",
        require_all_signal_tags=True,
    )

    assert result["summary"]["selected_trade_count"] > 0
    assert result["summary"]["required_signal_tags"] == ["score_gte_5"]
    assert result["summary"]["require_all_signal_tags"] is True
    assert result["research_group_stats"]["by_signal_tag"]["score_gte_5"]["trade_count"] > 0


def test_research_backtest_can_include_qualified_trades(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
        "reasons": [],
        "confirmations": [],
        "risks": [],
        "indicators": {},
    })
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=FakeProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        include_qualified_trades=True,
    )

    assert result["qualified_trades"]
    assert result["qualified_trades"][0]["signal_tags"]
    assert "rs20_nonnegative" in result["qualified_trades"][0]["signal_tags"]
    assert any(tag.startswith("rs60_") for tag in result["qualified_trades"][0]["signal_tags"])
    assert result["qualified_trades"][0]["candidate_rank"] == 1
    assert "relative_strength" in result["qualified_trades"][0]
    assert "by_candidate_rank_bucket" in result["research_group_stats"]
    assert "by_rs20_bucket" in result["research_group_stats"]


def test_research_backtest_can_attach_current_margin_tags(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
        "reasons": [],
        "confirmations": [],
        "risks": [],
        "indicators": {},
    })
    monkeypatch.setattr(
        "app.research_backtest.MarginEligibilityProvider.build_map",
        lambda self, use_cache_on_error=True, as_of=None: {
            "symbol_map": {
                "600519": {
                    "symbol": "600519",
                    "exchange": "SSE",
                    "financing_underlying": True,
                    "financing_eligible": True,
                    "short_underlying": True,
                    "short_eligible": True,
                    "collateral_eligible": True,
                    "price_limit": "10%",
                }
            },
            "summary": {"enabled": True},
            "errors": [],
        },
    )
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 100000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_candidate_research_backtest(
        settings=settings,
        provider=FakeProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        use_margin_eligibility_context=True,
        require_signal_tags="margin_financing_eligible",
        require_all_signal_tags=True,
        include_qualified_trades=True,
    )

    assert result["summary"]["margin_eligibility_context_enabled"] is True
    assert result["summary"]["margin_eligibility_scope"] == "sse_szse_current"
    assert result["qualified_trades"]
    trade = result["qualified_trades"][0]
    assert "margin_financing_eligible" in trade["signal_tags"]
    assert "margin_exchange_sse" in trade["signal_tags"]
    assert trade["margin_eligibility"]["exchange"] == "SSE"


def test_historical_universe_rebuilds_daily_prefilter_from_history(tmp_path, monkeypatch):
    class IndustryMap(dict):
        def get(self, key, default=None):
            return {
                "sample_count": 30,
                "above_ma20_pct": 70,
                "return_20d_positive_pct": 75,
                "advancing_pct": 60,
                "median_return_20d_pct": 5,
                "top_return_20d_pct": 20,
                "return_20d_dispersion_pct": 15,
                "tags": ["industry_rotation_broad", "industry_ret20_pos_gte_70"],
            }

    class HistoricalUniverseProvider:
        def history(self, symbol, market, lookback_days=620, adjust="qfq"):
            frame = sample_frame("up", periods=180)
            if market == "a" and symbol == "000001":
                frame["volume"] = 1_000
            elif market == "a":
                frame["volume"] = 2_000_000
            frame["amount"] = frame["close"] * frame["volume"]
            return frame, "fake-provider"

    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
        "reasons": [],
        "confirmations": [],
        "risks": [],
            "indicators": {},
        })
    monkeypatch.setattr(
        "app.research_backtest._historical_industry_rotation_contexts",
        lambda *args, **kwargs: IndustryMap(),
    )
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "000001",
                    "market": "a",
                    "name": "低成交额",
                    "latest": 100,
                    "amount": 1000000000,
                    "change_pct": 2,
                },
                {
                    "symbol": "600001",
                    "market": "a",
                    "name": "高成交额",
                    "latest": 100,
                    "amount": 1,
                    "change_pct": 2,
                },
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_historical_universe_research_backtest(
        settings=settings,
        provider=HistoricalUniverseProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        max_universe_symbols=2,
        cache_dir=str(tmp_path / "cache"),
        use_industry_rotation_context=True,
        include_qualified_trades=True,
    )

    assert result["summary"]["candidate_mode"] == "historical_daily_prefilter"
    assert result["summary"]["selected_trade_count"] > 0
    assert result["summary"]["historical_candidate_days"] > 0
    assert {item["symbol"] for item in result["qualified_trades"]} == {"600001"}
    assert result["qualified_trades"][0]["candidate_rank"] == 1
    assert result["qualified_trades"][0]["candidate_rank_pct"] == 100.0
    assert "candidate_change_0_to_3" in result["qualified_trades"][0]["signal_tags"]
    assert "market_breadth" in result["qualified_trades"][0]
    assert any(tag.startswith("breadth_") for tag in result["qualified_trades"][0]["signal_tags"])
    assert "price_action" in result["qualified_trades"][0]
    assert any(tag.startswith("price_") for tag in result["qualified_trades"][0]["signal_tags"])
    assert "industry_rotation" in result["qualified_trades"][0]
    assert "industry_rotation_broad" in result["qualified_trades"][0]["signal_tags"]
    assert result["summary"]["historical_market_breadth_days"] > 0
    assert result["summary"]["industry_rotation_context_enabled"] is True
    assert result["summary"]["industry_rotation_days"] == 0


def test_historical_universe_can_attach_szse_margin_asof_tags(tmp_path, monkeypatch):
    class HistoricalProvider:
        def history(self, symbol, market, lookback_days=620, adjust="qfq"):
            frame = sample_frame("up", periods=180)
            if market == "a":
                frame["volume"] = 2_000_000
                frame["amount"] = frame["close"] * frame["volume"]
            return frame, "fake-provider"

    def fake_margin_map(self, as_of, cache_dir=None, use_cache_on_error=True):
        return {
            "summary": {
                "enabled": True,
                "scope": "szse_underlying_asof",
                "requested_as_of": as_of,
                "source_as_of": as_of,
            },
            "symbol_map": {
                "000001": {
                    "symbol": "000001",
                    "exchange": "SZSE",
                    "name": "平安银行",
                    "financing_underlying": True,
                    "financing_eligible": True,
                    "short_underlying": True,
                    "short_eligible": False,
                    "price_limit": "10%",
                    "as_of": as_of,
                }
            },
            "errors": [],
        }

    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
        "reasons": [],
        "confirmations": [],
        "risks": [],
        "indicators": {},
    })
    monkeypatch.setattr(
        "app.research_backtest.MarginEligibilityProvider.build_szse_underlying_map",
        fake_margin_map,
    )
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "000001",
                    "market": "a",
                    "name": "平安银行",
                    "latest": 100,
                    "amount": 1000000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_historical_universe_research_backtest(
        settings=settings,
        provider=HistoricalProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        max_universe_symbols=1,
        cache_dir=str(tmp_path / "cache"),
        use_margin_eligibility_context=True,
        require_signal_tags="margin_financing_eligible",
        require_all_signal_tags=True,
        include_qualified_trades=True,
    )

    assert result["summary"]["margin_eligibility_context_enabled"] is True
    assert result["summary"]["margin_eligibility_scope"] == "szse_underlying_asof"
    assert result["qualified_trades"]
    trade = result["qualified_trades"][0]
    assert "margin_financing_eligible" in trade["signal_tags"]
    assert "margin_price_limit_10" in trade["signal_tags"]
    assert trade["margin_eligibility"]["exchange"] == "SZSE"


def test_historical_universe_can_attach_dragon_tiger_tags(tmp_path, monkeypatch):
    class AnyDateMap(dict):
        def get(self, key, default=None):
            return {
                "600519": {
                    "symbol": "600519",
                    "trade_date": str(key),
                    "name": "测试股票",
                    "net_buy_amount": 80_000_000,
                    "net_buy_ratio_pct": 6.5,
                    "lhb_turnover_ratio_pct": 28,
                    "turnover_rate_pct": 22,
                    "institution_buy_count": 1,
                    "reasons": ["日涨幅偏离值达到7%的前5只证券"],
                    "tags": [
                        "lhb_on_list",
                        "lhb_net_buy_positive",
                        "lhb_net_buy_ratio_gte_5",
                        "lhb_reason_up",
                    ],
                }
            }

    class HistoricalProvider:
        def history(self, symbol, market, lookback_days=620, adjust="qfq"):
            frame = sample_frame("up", periods=180)
            if market == "a":
                frame["volume"] = 2_000_000
                frame["amount"] = frame["close"] * frame["volume"]
            return frame, "fake-provider"

    monkeypatch.setattr("app.research_backtest.evaluate_signal", lambda history: {
        "action": "BUY",
        "score": 5.0,
        "confidence": 90,
        "reasons": [],
        "confirmations": [],
        "risks": [],
        "indicators": {},
    })
    monkeypatch.setattr(
        "app.research_backtest.DragonTigerProvider.build_contexts",
        lambda self, start_date, end_date, use_cache_on_error=True: {
            "by_date": AnyDateMap({"2025-06-01": {}}),
            "summary": {"item_count": 1, "day_count": 1},
            "errors": [],
        },
    )
    universe_path = tmp_path / "universe.json"
    write_json(
        str(universe_path),
        {
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "latest": 100,
                    "amount": 1000000000,
                    "change_pct": 2,
                }
            ]
        },
    )
    settings = replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    result = run_historical_universe_research_backtest(
        settings=settings,
        provider=HistoricalProvider(),
        start_date="2025-06-01",
        max_deep=1,
        top_n=1,
        hold_days=10,
        lookback_days=620,
        max_universe_symbols=1,
        cache_dir=str(tmp_path / "cache"),
        use_dragon_tiger_context=True,
        require_signal_tags="lhb_net_buy_positive",
        require_all_signal_tags=True,
        include_qualified_trades=True,
    )

    assert result["summary"]["dragon_tiger_context_enabled"] is True
    assert result["summary"]["dragon_tiger_days"] == 1
    assert result["qualified_trades"]
    trade = result["qualified_trades"][0]
    assert "lhb_net_buy_positive" in trade["signal_tags"]
    assert "lhb_reason_up" in trade["signal_tags"]
    assert trade["dragon_tiger"]["net_buy_amount"] == 80_000_000


def test_relative_strength_context_adds_broad_tags():
    payload = _relative_strength_context(
        {"return_20d": 0.2, "return_60d": 0.35},
        {
            "proxy_return_20d_avg_pct": 5,
            "proxy_return_20d_max_pct": 8,
            "proxy_return_60d_avg_pct": 10,
            "proxy_return_60d_max_pct": 15,
        },
    )

    assert "rs20_nonnegative" in payload["tags"]
    assert "rs20_strong" in payload["tags"]
    assert "rs60_nonnegative" in payload["tags"]
    assert "rs60_strong" in payload["tags"]
    assert "proxy20_avg_gte_5" in payload["tags"]
    assert "proxy60_avg_gte_10" in payload["tags"]
    assert "proxy_market_bullish" in payload["tags"]


def test_portfolio_controls_skip_overlapping_same_symbol():
    selected = _select_with_portfolio_controls(
        {
            "2026-01-02": [
                {
                    "symbol": "600519",
                    "signal_date": "2026-01-02",
                    "exit_date": "2026-01-16",
                    "rank_score": 10,
                }
            ],
            "2026-01-05": [
                {
                    "symbol": "600519",
                    "signal_date": "2026-01-05",
                    "exit_date": "2026-01-19",
                    "rank_score": 20,
                },
                {
                    "symbol": "000001",
                    "signal_date": "2026-01-05",
                    "exit_date": "2026-01-19",
                    "rank_score": 8,
                },
            ],
        },
        top_n=2,
        symbol_cooldown_days=10,
        max_active_positions=3,
    )

    assert [item["symbol"] for item in selected] == ["600519", "000001"]
