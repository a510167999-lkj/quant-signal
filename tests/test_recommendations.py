from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.config import Settings
from app.recommendations import RecommendationService
from app.storage import append_jsonl
from tests.test_signals import sample_frame


class FakeProvider:
    def history(self, symbol, market, lookback_days=360, adjust="qfq"):
        return sample_frame("up"), "fake-provider"


class GapProvider:
    def history(self, symbol, market, lookback_days=360, adjust="qfq"):
        frame = sample_frame("up")
        if market == "a":
            last = frame.index[-1]
            previous_close = frame.loc[last - 1, "close"]
            frame.loc[last, "open"] = previous_close * 1.03
            frame.loc[last, "low"] = previous_close * 1.025
            frame.loc[last, "high"] = previous_close * 1.06
            frame.loc[last, "close"] = previous_close * 1.045
        return frame, "fake-provider"


class FakeUniverse:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self, use_cache_on_error=True):
        return list(self._snapshot)


class FakeIndustry:
    def build_map(self, use_cache_on_error=True):
        return {
            "industries": [
                {
                    "name": "测试行业",
                    "change_pct": 2.5,
                    "turnover": 3.2,
                    "breadth": 0.6,
                    "industry_score": 8,
                }
            ],
            "symbol_map": {
                "600519": {
                    "industry": "测试行业",
                    "industry_rank": 1,
                    "industry_change_pct": 2.5,
                    "industry_score": 8,
                }
            },
        }


class FakeIndustryHistory:
    def boards(self, use_cache_on_error=True):
        return [{"name": "测试行业", "code": "BK0001"}]

    def history(
        self,
        board_name,
        start_date,
        end_date,
        board_code=None,
        use_cache_on_error=True,
    ):
        return [
            {
                "date": f"2026-07-{day:02d}",
                "close": 100 + day,
                "change_pct": 1.0,
                "turnover": 2.0,
            }
            for day in range(1, 12)
        ]


@pytest.fixture(autouse=True)
def fake_industry_history_provider(monkeypatch):
    monkeypatch.setattr("app.recommendations.IndustryHistoryProvider", lambda cache_dir: FakeIndustryHistory())


class FakeNews:
    def __init__(self, payload=None):
        self.payload = payload or {
            "level": "neutral",
            "score": 0,
            "score_adjustment": 0,
            "allow_recommendation": True,
            "article_count": 0,
            "negative_count": 0,
            "positive_count": 0,
            "headlines": [],
            "errors": [],
        }
        self.calls = []

    def evaluate(self, symbol, use_cache_on_error=True):
        self.calls.append(symbol)
        return dict(self.payload)


class FakeAnnouncement:
    def __init__(self, payload=None):
        self.payload = payload or {
            "level": "neutral",
            "score": 0,
            "score_adjustment": 0,
            "allow_recommendation": True,
            "announcement_count": 0,
            "negative_count": 0,
            "positive_count": 0,
            "announcements": [],
            "errors": [],
        }
        self.calls = []

    def evaluate(self, symbol, use_cache_on_error=True):
        self.calls.append(symbol)
        return dict(self.payload)


class FakeFundFlow:
    def __init__(self, payload=None):
        self.payload = payload or {
            "level": "neutral",
            "score_adjustment": 0,
            "allow_recommendation": True,
            "main_net_amount_3d": 0,
            "main_net_ratio_3d": 0,
            "positive_days_3d": 0,
            "latest_main_net_ratio": 0,
            "errors": [],
        }
        self.calls = []

    def evaluate(self, symbol, use_cache_on_error=True):
        self.calls.append(symbol)
        return dict(self.payload)


class FakeMarginEligibility:
    def __init__(self, payload=None):
        self.payload = payload or {
            "summary": {"enabled": False},
            "symbol_map": {},
            "errors": [],
        }
        self.calls = 0

    def build_map(self, use_cache_on_error=True):
        self.calls += 1
        return dict(self.payload)


class FakeL1Quotes:
    def __init__(self, quotes=None):
        self.quotes_payload = quotes or {}
        self.calls = []

    def quotes(self, symbols):
        self.calls.append(list(symbols))
        return {
            "enabled": True,
            "available": True,
            "quotes": dict(self.quotes_payload),
            "errors": [],
            "elapsed_seconds": 0.01,
        }


def make_settings(tmp_path):
    return Settings(
        cors_origins=[],
        watchlist_path=str(tmp_path / "watchlist.json"),
        latest_recommendations_path=str(tmp_path / "recommendations_latest.json"),
        recommendation_history_path=str(tmp_path / "recommendations_history.jsonl"),
        alerts_path=str(tmp_path / "alerts.jsonl"),
        universe_cache_path=str(tmp_path / "universe.json"),
        recommendation_lock_path=str(tmp_path / "recommendations.lock"),
        scan_max_deep=3,
        scan_result_limit=2,
        scan_min_amount=1,
        scan_min_price=1,
        scan_max_price=500,
        recommendation_min_signal_score=2,
        recommendation_required_signal_tags=[],
        recommendation_allowed_market_levels=[],
        recommendation_symbol_cooldown_days=0,
        enable_margin_eligibility_context=False,
        monitor_recent_days=10,
        monitor_intraday_drop_pct=4,
    )


def test_generate_daily_recommendations_from_a_share_universe(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 7.0,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["candidate_industry_count"] == 1
    assert result["summary"]["industry_top_n"] == 3
    assert result["summary"]["per_industry_top_n"] == 3
    assert result["summary"]["hot_industries"][0]["name"] == "测试行业"
    assert result["summary"]["hot_industries"][0]["candidate_count"] == 1
    assert set(result["summary"]["hot_industries_by_window"]) == {"1d", "3d", "5d", "10d"}
    assert result["summary"]["hot_industries_by_window"]["3d"][0]["return_pct"] == 3.03
    assert result["items"]
    assert result["items"][0]["symbol"] == "600519"
    assert result["items"][0]["market"] == "a"
    # 操作建议完整性：每条推荐必须给出 action + levels(止损/止盈) + 入场区间 + 短线/长线计划
    assert result["items"][0]["action"] in {"BUY", "WATCH", "HOLD", "REDUCE", "SELL"}
    assert {"stop_loss", "take_profit"} <= set(result["items"][0]["levels"])
    assert result["items"][0]["entry_zone"]
    assert result["items"][0]["trade_plans"]
    assert result["items"][0]["industry"]["industry"] == "测试行业"
    assert result["items"][0]["news_context"]["level"] == "neutral"
    assert result["items"][0]["announcement_context"]["level"] == "neutral"
    assert result["items"][0]["fund_flow_context"]["level"] == "neutral"
    assert result["items"][0]["signal_tags"]
    assert result["items"][0]["market_breadth"]["sample_count"] == 1
    assert "breadth_ma20_gte_60" in result["items"][0]["signal_tags"]
    assert result["items"][0]["relative_strength"]["relative_strength_60d_pct"] is not None
    assert any(tag.startswith("rs60_") for tag in result["items"][0]["signal_tags"])
    assert result["items"][0]["price_action"]["gap_pct"] is not None
    assert any(tag.startswith("price_") for tag in result["items"][0]["signal_tags"])
    assert result["items"][0]["strict_signal"]["passed"] is True
    assert result["items"][0]["strategy_quality"]["passed"] is True
    assert result["summary"]["market_breadth"]["sample_count"] == 1
    assert "breadth_ma20_gte_60" in result["summary"]["market_breadth_tags"]
    assert result["summary"]["relative_strength_proxy"]["proxy_return_60d_avg_pct"] is not None
    assert result["summary"]["margin_eligibility"]["enabled"] is False
    assert result["summary"]["market_context"]["level"] in {"favorable", "neutral", "cautious", "defensive"}


def test_pre_open_run_slot_skips_l1_context(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.l1_quotes = FakeL1Quotes(
        {
            "600519": {
                "price": 101,
                "change_pct": 1,
                "amount": 100000000,
            }
        }
    )
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True, run_slot="pre_open")

    assert result["run_slot"] == "pre_open"
    assert result["run_slot_label"] == "09:00 盘前候选"
    assert result["summary"]["run_slot"]["use_l1_context"] is False
    assert result["summary"]["l1_quote"]["skipped"] is True
    assert service.l1_quotes.calls == []


def test_intraday_run_slot_uses_fast_scan_cap(tmp_path):
    settings = replace(make_settings(tmp_path), scan_max_deep=500, intraday_scan_max_deep=1)
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.l1_quotes = FakeL1Quotes({})
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            },
            {
                "symbol": "000001",
                "market": "a",
                "name": "测试股票2",
                "latest": 10,
                "amount": 90000000,
                "change_pct": 2.2,
                "volume": 10000,
            },
        ]
    )

    result = service.generate_daily_recommendations(force=True, run_slot="open_confirm")

    assert result["run_slot"] == "open_confirm"
    assert result["summary"]["max_deep"] == 1
    assert result["summary"]["candidate_count"] == 1
    assert service.l1_quotes.calls == [["600519"]]


def test_generate_daily_recommendations_can_require_breadth_and_relative_strength_tags(tmp_path):
    settings = replace(
        make_settings(tmp_path),
        recommendation_required_signal_tags=["breadth_ma20_gte_60", "rs60_nonnegative"],
        recommendation_require_all_signal_tags=True,
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 7.0,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["items"]
    tags = set(result["items"][0]["signal_tags"])
    assert {"breadth_ma20_gte_60", "rs60_nonnegative"}.issubset(tags)
    assert result["items"][0]["strict_signal"]["passed"] is True


def test_generate_daily_recommendations_can_require_margin_financing_tag(tmp_path):
    settings = replace(
        make_settings(tmp_path),
        recommendation_required_signal_tags=["margin_financing_eligible"],
        recommendation_require_all_signal_tags=True,
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility(
        {
            "summary": {"enabled": True, "financing_count": 1},
            "symbol_map": {
                "600519": {
                    "symbol": "600519",
                    "exchange": "SSE",
                    "financing_underlying": True,
                    "financing_eligible": True,
                    "short_underlying": True,
                    "short_eligible": False,
                    "collateral_eligible": True,
                    "as_of": "2026-07-05 07:30:00",
                    "sources": ["sse"],
                }
            },
            "errors": [],
        }
    )
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["items"]
    item = result["items"][0]
    assert "margin_financing_eligible" in item["signal_tags"]
    assert "margin_financing_underlying" in item["signal_tags"]
    assert item["margin_eligibility"]["exchange"] == "SSE"
    assert item["margin_eligibility"]["financing_eligible"] is True
    assert item["margin_eligibility"]["financing_underlying"] is True
    assert result["summary"]["margin_eligibility"]["financing_count"] == 1


def test_generate_daily_recommendations_can_require_price_action_tags(tmp_path):
    settings = replace(
        make_settings(tmp_path),
        recommendation_required_signal_tags=["price_gap_up_2_to_5"],
        recommendation_require_all_signal_tags=True,
    )
    service = RecommendationService(settings, GapProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 4.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["items"]
    assert result["items"][0]["price_action"]["gap_pct"] >= 2
    assert "price_gap_up_2_to_5" in result["items"][0]["signal_tags"]


def test_generate_daily_recommendations_default_strict_filter_blocks_weak_signal(tmp_path):
    settings = Settings(
        cors_origins=[],
        latest_recommendations_path=str(tmp_path / "recommendations_latest.json"),
        recommendation_history_path=str(tmp_path / "recommendations_history.jsonl"),
        alerts_path=str(tmp_path / "alerts.jsonl"),
        universe_cache_path=str(tmp_path / "universe.json"),
        recommendation_lock_path=str(tmp_path / "recommendations.lock"),
        scan_max_deep=1,
        scan_result_limit=1,
        scan_min_amount=1,
        scan_min_price=1,
        scan_max_price=500,
        enable_margin_eligibility_context=False,
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["qualified_count"] == 0
    assert result["summary"]["strict_signal_filter"]["min_signal_score"] == 2.0
    assert result["summary"]["strict_signal_filter"]["required_signal_tags"] == [
        "breadth_advancing_gte_50",
        "breakout_20d",
        "price_gap_up_2_to_5",
    ]
    assert result["summary"]["strict_signal_filter"]["allowed_market_levels"] == ["favorable", "neutral"]
    assert result["items"] == []


def test_generate_daily_recommendations_skips_recent_symbol_in_cooldown(tmp_path):
    settings = make_settings(tmp_path)
    settings = replace(settings, recommendation_symbol_cooldown_days=10)
    append_jsonl(
        settings.recommendation_history_path,
        {
            "generated_at": "2026-07-04T09:00:00+08:00",
            "items": [{"symbol": "600519", "market": "a"}],
        },
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["cooldown_skipped_count"] == 1
    assert result["summary"]["qualified_count"] == 0
    assert result["items"] == []


def test_generate_daily_recommendations_blocks_high_risk_news(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews(
        {
            "level": "high_risk",
            "score": -18,
            "score_adjustment": -18,
            "allow_recommendation": False,
            "article_count": 1,
            "negative_count": 1,
            "positive_count": 0,
            "headlines": [{"title": "测试股票被立案调查", "score": -14}],
            "errors": [],
        }
    )
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["qualified_count"] == 0
    assert result["items"] == []
    assert service.news.calls == ["600519"]


def test_generate_daily_recommendations_blocks_high_risk_announcement(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement(
        {
            "level": "high_risk",
            "score": -20,
            "score_adjustment": -20,
            "allow_recommendation": False,
            "announcement_count": 1,
            "negative_count": 1,
            "positive_count": 0,
            "announcements": [{"title": "测试股票关于收到行政处罚决定书的公告", "score": -14}],
            "errors": [],
        }
    )
    service.fund_flow = FakeFundFlow()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["qualified_count"] == 0
    assert result["items"] == []
    assert service.announcements.calls == ["600519"]


def test_generate_daily_recommendations_blocks_high_fund_outflow(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow(
        {
            "level": "high_outflow",
            "score_adjustment": -16,
            "allow_recommendation": False,
            "main_net_amount_3d": -100000000,
            "main_net_ratio_3d": -6.5,
            "positive_days_3d": 0,
            "latest_main_net_ratio": -9,
            "errors": [],
        }
    )
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.5,
                "volume": 10000,
            }
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["qualified_count"] == 0
    assert result["items"] == []
    assert service.fund_flow.calls == ["600519"]


def test_monitor_recommendations_alerts_when_stop_loss_breaks(tmp_path):
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    append_jsonl(
        settings.recommendation_history_path,
        {
            "generated_at": "2026-07-05T09:00:00+08:00",
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "action": "BUY",
                    "score": 5,
                    "levels": {"stop_loss": 95, "support": 96},
                }
            ],
        },
    )
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 94,
                "amount": 100000000,
                "change_pct": -2,
            }
        ]
    )

    result = service.monitor_recommendations(force=True)

    assert result["alerts"]
    assert result["alerts"][0]["event_type"] == "stop_loss"


def test_monitor_recommendations_alerts_when_take_profit_reached(tmp_path):
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    append_jsonl(
        settings.recommendation_history_path,
        {
            "generated_at": "2026-07-05T09:00:00+08:00",
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "action": "BUY",
                    "score": 5,
                    "levels": {"stop_loss": 95, "support": 96, "take_profit": 108},
                }
            ],
        },
    )
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 109,
                "amount": 100000000,
                "change_pct": 3,
            }
        ]
    )

    result = service.monitor_recommendations(force=True)

    assert result["alerts"]
    assert result["alerts"][0]["event_type"] == "take_profit"
    assert result["alerts"][0]["severity"] == "info"


def test_monitor_recommendations_uses_l1_quotes_before_snapshot(tmp_path):
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.l1_quotes = FakeL1Quotes(
        {
            "600519": {
                "price": 94,
                "change_pct": -3,
                "amount": 100000000,
            }
        }
    )
    append_jsonl(
        settings.recommendation_history_path,
        {
            "generated_at": "2026-07-05T09:00:00+08:00",
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "action": "BUY",
                    "score": 5,
                    "levels": {"stop_loss": 95, "support": 96},
                }
            ],
        },
    )
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "测试股票",
                "latest": 110,
                "amount": 100000000,
                "change_pct": 2,
            }
        ]
    )

    result = service.monitor_recommendations(force=True)

    assert service.l1_quotes.calls == [["600519"]]
    assert result["l1_quote"]["quote_count"] == 1
    assert result["alerts"]
    assert result["alerts"][0]["event_type"] == "stop_loss"
    assert result["alerts"][0]["latest_price"] == 94


def test_recommendation_run_returns_current_status_when_already_running(tmp_path):
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    token = service._acquire_recommendation_lock()
    try:
        started = service.mark_recommendations_started(max_deep=20)
        result, next_token = service.begin_recommendation_run(max_deep=20)
    finally:
        service._release_recommendation_lock(token)

    assert token
    assert next_token is None
    assert result["generated_at"] == started["generated_at"]
    assert result["summary"]["running"] is True
    assert result["summary"]["reason"] == "already_running"


def _write_profit_lock_history(settings, last_close, rec_date):
    append_jsonl(
        settings.recommendation_history_path,
        {
            "generated_at": rec_date.isoformat() + "T09:00:00+08:00",
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "action": "BUY",
                    "score": 5,
                    "last_close": last_close,
                    "levels": {"stop_loss": 95, "support": 96, "take_profit": 108},
                }
            ],
        },
    )


def test_monitor_planned_exits_alerts_when_profit_lock_activated(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rec_date = today - timedelta(days=3)
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_profit_lock_history(settings, 100.0, rec_date)
    frame = pd.DataFrame(
        [
            {"date": (rec_date - timedelta(days=1)).isoformat(), "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": (rec_date + timedelta(days=1)).isoformat(), "open": 101, "high": 120, "low": 100, "close": 119},
            {"date": today.isoformat(), "open": 119, "high": 122, "low": 118, "close": 121},
        ]
    )
    monkeypatch.setattr(service.data_provider, "history", lambda *a, **k: (frame, "fake"))
    monkeypatch.setattr("app.recommendations.next_trade_date", lambda target: today + timedelta(days=1))
    monkeypatch.setattr("app.recommendations.next_calendar_gap", lambda *a, **k: None)

    result = service.monitor_planned_exits(force=True)

    assert result["planned_exits"]
    alert = result["planned_exits"][0]
    assert alert["event_type"] == "planned_profit_lock_exit"
    assert alert["severity"] == "warning"
    assert alert["symbol"] == "600519"
    assert alert["exit_date"] == (today + timedelta(days=1)).isoformat()
    assert alert["exit_price_type"] == "next_open"
    assert alert["exit_price"] is None
    assert alert["prior_high"] == 122.0
    assert alert["trigger_price"] == 118.0
    assert alert["reference_price"] == 100.0
    assert "600519" in alert["id"]
    # 执行性 alert 必须落盘
    from app.storage import read_jsonl
    persisted = read_jsonl(settings.alerts_path, limit=10)
    assert any(item.get("event_type") == "planned_profit_lock_exit" for item in persisted)


def test_monitor_planned_exits_no_alert_when_prior_high_below_trigger(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rec_date = today - timedelta(days=3)
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_profit_lock_history(settings, 100.0, rec_date)
    frame = pd.DataFrame(
        [
            {"date": (rec_date + timedelta(days=1)).isoformat(), "open": 101, "high": 105, "low": 100, "close": 104},
            {"date": today.isoformat(), "open": 104, "high": 106, "low": 103, "close": 105},
        ]
    )
    monkeypatch.setattr(service.data_provider, "history", lambda *a, **k: (frame, "fake"))
    monkeypatch.setattr("app.recommendations.next_calendar_gap", lambda *a, **k: None)

    result = service.monitor_planned_exits(force=True)

    assert result["planned_exits"] == []
    assert result["monitored_count"] == 1


def test_monitor_planned_exits_dedups_within_same_day(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rec_date = today - timedelta(days=3)
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_profit_lock_history(settings, 100.0, rec_date)
    frame = pd.DataFrame(
        [
            {"date": (rec_date + timedelta(days=1)).isoformat(), "open": 101, "high": 120, "low": 100, "close": 119},
            {"date": today.isoformat(), "open": 119, "high": 122, "low": 118, "close": 121},
        ]
    )
    monkeypatch.setattr(service.data_provider, "history", lambda *a, **k: (frame, "fake"))
    monkeypatch.setattr("app.recommendations.next_trade_date", lambda target: today + timedelta(days=1))
    monkeypatch.setattr("app.recommendations.next_calendar_gap", lambda *a, **k: None)

    first = service.monitor_planned_exits(force=True)
    second = service.monitor_planned_exits(force=True)

    assert len(first["planned_exits"]) == 1
    assert second["planned_exits"] == []


def _write_calendar_gap_history(settings, rec_date):
    append_jsonl(
        settings.recommendation_history_path,
        {
            "generated_at": rec_date.isoformat() + "T09:00:00+08:00",
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "action": "BUY",
                    "score": 5,
                    "last_close": 100.0,
                    "levels": {"stop_loss": 95, "support": 96, "take_profit": 108},
                }
            ],
        },
    )


def _low_high_frame(rec_date, today):
    # high 远低于 profit-lock 触发价（100×1.18=118），确保 profit-lock 不激活以隔离长假测试
    return pd.DataFrame(
        [
            {"date": (rec_date + timedelta(days=1)).isoformat(), "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": today.isoformat(), "open": 100, "high": 102, "low": 99, "close": 101},
        ]
    )


def test_monitor_planned_exits_alerts_for_calendar_gap(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rec_date = today - timedelta(days=3)
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_calendar_gap_history(settings, rec_date)
    monkeypatch.setattr(
        service.data_provider, "history", lambda *a, **k: (_low_high_frame(rec_date, today), "fake")
    )
    # 构造 7 日历日休市跳空：假前最后一交易日 = today+5，假后首日 = today+12
    monkeypatch.setattr(
        "app.recommendations.next_calendar_gap",
        lambda target, min_gap: (today + timedelta(days=5), today + timedelta(days=12)),
    )

    result = service.monitor_planned_exits(force=True)

    gap_alerts = [a for a in result["planned_exits"] if a["event_type"] == "planned_calendar_gap_exit"]
    assert gap_alerts
    alert = gap_alerts[0]
    assert alert["symbol"] == "600519"
    assert alert["severity"] == "warning"
    assert alert["exit_date"] == (today + timedelta(days=5)).isoformat()
    assert alert["exit_price_type"] == "close"
    assert alert["exit_price"] is None
    assert alert["calendar_gap_days"] == 7
    assert alert["id"] == "calendar_gap_exit:%s:600519" % (today + timedelta(days=5)).isoformat()


def test_monitor_planned_exits_no_calendar_gap_alert_when_disabled(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rec_date = today - timedelta(days=3)
    settings = replace(make_settings(tmp_path), monitor_pre_exit_calendar_gap_days=0)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_calendar_gap_history(settings, rec_date)
    monkeypatch.setattr(
        service.data_provider, "history", lambda *a, **k: (_low_high_frame(rec_date, today), "fake")
    )
    monkeypatch.setattr(
        "app.recommendations.next_calendar_gap",
        lambda target, min_gap: (today + timedelta(days=5), today + timedelta(days=12)),
    )

    result = service.monitor_planned_exits(force=True)

    assert [a for a in result["planned_exits"] if a["event_type"] == "planned_calendar_gap_exit"] == []


def test_monitor_planned_exits_dedups_calendar_gap_by_exit_date(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rec_date = today - timedelta(days=3)
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_calendar_gap_history(settings, rec_date)
    monkeypatch.setattr(
        service.data_provider, "history", lambda *a, **k: (_low_high_frame(rec_date, today), "fake")
    )
    monkeypatch.setattr(
        "app.recommendations.next_calendar_gap",
        lambda target, min_gap: (today + timedelta(days=5), today + timedelta(days=12)),
    )

    first = service.monitor_planned_exits(force=True)
    second = service.monitor_planned_exits(force=True)

    first_gap = [a for a in first["planned_exits"] if a["event_type"] == "planned_calendar_gap_exit"]
    second_gap = [a for a in second["planned_exits"] if a["event_type"] == "planned_calendar_gap_exit"]
    assert len(first_gap) == 1
    assert second_gap == []
