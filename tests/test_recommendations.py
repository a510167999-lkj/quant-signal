import hashlib
import json
import socket
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import app.recommendations as recommendations_module
from app.config import Settings
from app.current_pool import POLICY_ID
from app.recommendations import (
    RecommendationService,
    _selection_funnel_explanation,
    _selection_rejection_reason,
)
from app.recommendation_contract import (
    build_publication_ledger_record,
    recommendation_operation_contract_errors,
    recommendation_publication_receipt_errors,
    recommendation_snapshot_publication_errors,
    recommendation_snapshot_sha256,
)
from app.recommendation_profile import DEFAULT_PROFILE, profile_to_dict
from app.schemas import RecommendationSnapshot
from app.storage import append_jsonl, read_jsonl, write_json
from tests.test_signals import sample_frame


class FakeProvider:
    def history(self, symbol, market, lookback_days=360, adjust="qfq"):
        return sample_frame("up"), "fake-provider"


class FakeJiaochProvider(FakeProvider):
    def history(self, symbol, market, lookback_days=360, adjust="qfq"):
        return sample_frame("up"), "Jiaoch fixture"


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
        return [
            {
                **item,
                "market_snapshot_source": item.get(
                    "market_snapshot_source", "Jiaoch fixture"
                ),
            }
            for item in self._snapshot
        ]


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
def fake_industry_history_provider(monkeypatch, tmp_path):
    network_attempts = []

    def deny_network(*args, **kwargs):
        network_attempts.append(args[1:] if len(args) > 1 else args)
        raise AssertionError("recommendation unit tests must not use the network")

    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr("app.recommendations.IndustryHistoryProvider", lambda cache_dir: FakeIndustryHistory())
    monkeypatch.setenv(
        "TRADE_CALENDAR_CACHE_PATH", str(tmp_path / "global-trade-calendar.json")
    )
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda _value: True)
    monkeypatch.setattr(
        "app.recommendations.next_trade_date", lambda value: value + timedelta(days=1)
    )
    monkeypatch.setattr(
        "app.recommendations.next_calendar_gap", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "app.recommendations.is_a_share_trading_time", lambda _moment: True
    )
    monkeypatch.setattr(
        "app.trading_calendar._load_trade_dates",
        lambda: (_ for _ in ()).throw(
            AssertionError("recommendation tests must not load the global calendar")
        ),
    )
    yield
    assert network_attempts == []


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
            "source": "jiaoch:anns_d",
            "source_profile": "jiaoch_first",
            "fallback_used": False,
            "fallback_reason_code": None,
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
    current_pool_audit_path = tmp_path / "current-pool-audit.json"
    _write_current_pool_audit(
        current_pool_audit_path,
        ["000001", "000002", "000003", *[str(600519 + index) for index in range(12)]],
    )
    return Settings(
        cors_origins=[],
        watchlist_path=str(tmp_path / "watchlist.json"),
        latest_recommendations_path=str(tmp_path / "recommendations_latest.json"),
        recommendation_history_path=str(tmp_path / "recommendations_history.jsonl"),
        recommendation_audit_path=str(tmp_path / "recommendations_audit.jsonl"),
        alerts_path=str(tmp_path / "alerts.jsonl"),
        universe_cache_path=str(tmp_path / "universe.json"),
        trade_calendar_cache_path=str(tmp_path / "trading_calendar.json"),
        recommendation_lock_path=str(tmp_path / "recommendations.lock"),
        current_pool_audit_path=str(current_pool_audit_path),
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


def _write_current_pool_audit(path, symbols, *, source_as_of=None):
    source_as_of = source_as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    universe_items = []
    statuses = []
    for symbol in symbols:
        exchange = "SSE" if symbol.startswith("6") else "SZSE"
        suffix = "SH" if exchange == "SSE" else "SZ"
        market = "创业板" if symbol.startswith(("300", "301")) else "主板"
        universe_items.append(
            {
                "ts_code": f"{symbol}.{suffix}",
                "name": f"测试{symbol}",
                "market": market,
                "exchange": exchange,
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            }
        )
        statuses.append(
            {
                "symbol": symbol,
                "eligible": True,
                "bar_count": 120,
                "fetch_status": "success",
                "signal_ready": True,
                "signal_allowed_today": True,
            }
        )
    payload = {
        "schema": "current-pool-coverage-audit",
        "schema_version": "current-pool-coverage-audit/v1",
        "policy_id": POLICY_ID,
        "development_only": True,
        "evidence_scope": "development_only",
        "source_as_of": source_as_of,
        "source_ids": {"universe": "jiaoch", "history_summary": "jiaoch", "risk_snapshot": "jiaoch"},
        "input_descriptor_sha256": {"universe": "1" * 64, "history_summary": "2" * 64, "risk_snapshot": "3" * 64},
        "risk_snapshot_complete": True,
        "risk_gate_passed": True,
        "production_recommendation_eligible": False,
        "min_signal_bars": 90,
        "universe_items": universe_items,
        "item_history_status": statuses,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload["canonical_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    write_json(str(path), payload)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"action": "SELL"}, "market_action"),
        ({"score": 1.0}, "score_below_floor"),
        ({"strict_signal": {"passed": False}}, "strict_signal_failed"),
        ({"strategy_quality": {"passed": False}}, "strategy_quality_failed"),
    ],
)
def test_selection_rejection_reason_uses_first_decisive_gate(overrides, reason):
    compact = {
        "action": "BUY",
        "score": 5.0,
        "strict_signal": {"passed": True},
        "strategy_quality": {"passed": True},
    }
    compact.update(overrides)
    assert _selection_rejection_reason(compact, {"BUY", "WATCH"}, 2.0) == reason


def complete_operation_contract_item():
    return {
        "action": "BUY",
        "score": 5.0,
        "strict_signal": {"passed": True},
        "strategy_quality": {"passed": True},
        "symbol": "600519",
        "market": "a",
        "market_data_source": "Jiaoch fixture",
        "market_snapshot_source": "Jiaoch fixture",
        "auto_order": False,
        "entry_zone": {"low": 99.0, "high": 101.0},
        "levels": {
            "support": 98.0,
            "resistance": 105.0,
            "stop_loss": 96.0,
            "take_profit": 108.0,
        },
        "trade_plans": {"short_term": {"horizon": "3-10 trading days"}},
        "operation_advice": {
            "action": "buy",
            "entry_zone": {"low": 99.0, "high": 101.0},
            "stop_loss": 96.0,
            "take_profit": 108.0,
            "holding_period": "3-10 trading days",
            "invalidation": "close_below_stop",
            "trigger_conditions": ["entry_zone_and_signal_confirmed"],
            "take_profit_or_reduce": {
                "condition": "price_gte_take_profit",
                "trigger_price": 108.0,
                "action": "reduce_or_take_profit",
            },
            "invalidation_conditions": ["close_below_stop"],
            "expected_holding_period": "3-10 trading days",
        },
        "risks": ["止损纪律"],
    }


def publication_payload(symbols, prior_symbols=(), target="2026-07-13"):
    items = []
    for symbol in symbols:
        item = complete_operation_contract_item()
        item["symbol"] = symbol
        items.append(item)
    prior = set(prior_symbols)
    published = set(symbols)
    return {
        "generated_at": f"{target}T15:02:00+08:00",
        "trade_date": target,
        "signal_date": target,
        "target_trade_date": target,
        "items": items,
        "errors": [],
        "recommendation_status": "live_proven",
        "evidence_scope": "live_proof",
        "live_proof": True,
        "auto_order": False,
        "publication_gate": {"status": "allowed", "reason": None},
        "market_source_gate": {
            "required": "jiaoch",
            "passed": True,
            "observed": ["Jiaoch fixture"],
        },
        "profile_gate": {
            "live_proof": True,
            "evidence_receipt_sha256": "b" * 64,
        },
        "daily_publication_cap": {
            "target_trade_date": target,
            "limit": 3,
            "prior_symbols": sorted(prior),
            "prior_count": len(prior),
            "remaining_before_run": max(0, 3 - len(prior)),
            "published_this_run": len(published - prior),
            "rejected_this_run": 0,
            "valid": True,
        },
        "current_pool_audit_sha256": "a" * 64,
        "summary": {},
    }


@pytest.mark.parametrize(
    "missing_path",
    [
        ("entry_zone", "low"),
        ("levels", "support"),
        ("operation_advice", "entry_zone", "high"),
        ("operation_advice", "stop_loss"),
        ("operation_advice", "take_profit"),
        ("operation_advice", "holding_period"),
        ("operation_advice", "invalidation"),
        ("operation_advice", "trigger_conditions"),
        ("operation_advice", "take_profit_or_reduce"),
        ("operation_advice", "invalidation_conditions"),
        ("operation_advice", "expected_holding_period"),
        ("risks",),
        ("trade_plans", "short_term", "horizon"),
    ],
)
def test_selection_rejects_incomplete_operation_contract(missing_path):
    compact = complete_operation_contract_item()
    mutated = json.loads(json.dumps(compact))
    parent = mutated
    for key in missing_path[:-1]:
        parent = parent[key]
    parent.pop(missing_path[-1])

    assert (
        _selection_rejection_reason(mutated, {"BUY", "WATCH"}, 2.0)
        == "operation_contract_incomplete"
    )


@pytest.mark.parametrize(
    ("path", "value", "expected_error"),
    [
        (("entry_zone", "low"), float("nan"), "entry_zone"),
        (("levels", "stop_loss"), float("inf"), "levels"),
        (
            ("operation_advice", "trigger_conditions"),
            [None],
            "trigger_conditions",
        ),
        (("risks",), ["   "], "risks"),
        (
            ("operation_advice", "holding_period"),
            123,
            "holding_period",
        ),
        (
            ("operation_advice", "entry_zone", "low"),
            100.0,
            "entry_zone_mismatch",
        ),
        (
            ("operation_advice", "stop_loss"),
            95.0,
            "stop_loss_mismatch",
        ),
        (
            ("operation_advice", "expected_holding_period"),
            "10-20 trading days",
            "holding_period_mismatch",
        ),
    ],
)
def test_operation_contract_rejects_invalid_values(
    path, value, expected_error
):
    item = complete_operation_contract_item()
    parent = item
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value

    assert expected_error in recommendation_operation_contract_errors(item)


def test_daily_publication_cap_counts_prior_target_trade_date_symbols(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    append_jsonl(
        service.settings.recommendation_history_path,
        {
            "target_trade_date": "2026-07-13",
            "items": [
                {"symbol": "600001", "market": "a"},
                {"symbol": "600002", "market": "a"},
            ],
        },
    )
    append_jsonl(
        service.settings.recommendation_history_path,
        {
            "target_trade_date": "2026-07-14",
            "items": [{"symbol": "600099", "market": "a"}],
        },
    )

    prior = service._published_symbols_for_target_trade_date("2026-07-13")
    published, rejected = service._cap_daily_publications(
        [
            {"symbol": "600003", "market": "a"},
            {"symbol": "600004", "market": "a"},
        ],
        prior,
    )

    assert prior == {"600001", "600002"}
    assert [item["symbol"] for item in published] == ["600003"]
    assert [item["symbol"] for item in rejected] == ["600004"]


def test_publication_ledger_reserves_symbols_before_latest_snapshot(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    item = complete_operation_contract_item()
    payload = {
        "generated_at": "2026-07-13T15:02:00+08:00",
        "target_trade_date": "2026-07-13",
        "items": [item],
        "recommendation_status": "live_proven",
        "evidence_scope": "live_proof",
        "live_proof": True,
        "auto_order": False,
        "publication_gate": {"status": "allowed", "reason": None},
        "profile_gate": {
            "live_proof": True,
            "evidence_receipt_sha256": "b" * 64,
        },
        "current_pool_audit_sha256": "a" * 64,
        "daily_publication_cap": {
            "target_trade_date": "2026-07-13",
            "limit": 3,
            "prior_symbols": [],
            "prior_count": 0,
            "remaining_before_run": 3,
            "published_this_run": 1,
            "rejected_this_run": 0,
            "valid": True,
        },
    }

    receipt = service._commit_publication_ledger(payload)

    assert receipt["symbols_after_commit"] == ["600519"]
    assert payload["publication_receipt"]["record_hash"] == receipt["record_hash"]
    assert not Path(service.settings.latest_recommendations_path).exists()
    resumed = RecommendationService(
        service.settings,
        FakeProvider(),
        "risk",
    )
    assert resumed._published_symbols_for_target_trade_date(
        "2026-07-13"
    ) == {"600519"}


def test_daily_publication_legacy_history_is_counted_conservatively(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    append_jsonl(
        service.settings.recommendation_history_path,
        {
            "target_trade_date": "2026-07-13",
            "recommendation_status": "blocked_profile_gate",
            "items": [{"symbol": " 600001.SH "}],
        },
    )

    assert service._published_symbols_for_target_trade_date(
        "2026-07-13"
    ) == {"600001"}


def test_daily_publication_corrupt_ledger_fails_closed(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    ledger_path = Path(
        f"{service.settings.recommendation_history_path}.publications"
    )
    ledger_path.write_text('{"broken":', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSONL record"):
        service._published_symbols_for_target_trade_date("2026-07-13")


def test_latest_fails_closed_on_inconsistent_publication_state(
    tmp_path, monkeypatch
):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    payload = {
        "generated_at": "2026-07-13T15:02:00+08:00",
        "target_trade_date": "2026-07-13",
        "items": [complete_operation_contract_item()],
        "errors": [],
        "recommendation_status": "blocked_profile_gate",
        "evidence_scope": "development_only",
        "live_proof": False,
        "auto_order": False,
        "publication_gate": {
            "status": "blocked",
            "reason": "profile_not_live_proven",
        },
        "profile_gate": {"live_proof": False},
        "daily_publication_cap": {
            "target_trade_date": "2026-07-13",
            "limit": 3,
            "prior_symbols": [],
            "prior_count": 0,
            "remaining_before_run": 3,
            "published_this_run": 1,
            "rejected_this_run": 0,
            "valid": True,
        },
        "current_pool_audit_sha256": "a" * 64,
        "summary": {},
    }
    write_json(service.settings.latest_recommendations_path, payload)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {"600519"},
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )

    result = service.latest()

    assert result["items"] == []
    assert result["publication_gate"] == {
        "status": "blocked",
        "reason": "snapshot_contract_invalid",
    }
    assert result["errors"][-1]["stage"] == "snapshot_contract"


@pytest.mark.parametrize("items", [[None], [42]])
def test_latest_fails_closed_for_non_mapping_items(
    tmp_path, monkeypatch, items
):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    payload = {
        "generated_at": "2026-07-13T15:02:00+08:00",
        "target_trade_date": "2026-07-13",
        "items": items,
        "errors": [],
        "recommendation_status": "live_proven",
        "evidence_scope": "live_proof",
        "live_proof": True,
        "auto_order": False,
        "publication_gate": {"status": "allowed", "reason": None},
        "profile_gate": {
            "live_proof": True,
            "evidence_receipt_sha256": "b" * 64,
        },
        "daily_publication_cap": {
            "target_trade_date": "2026-07-13",
            "limit": 3,
            "prior_symbols": [],
            "prior_count": 0,
            "remaining_before_run": 3,
            "published_this_run": 1,
            "rejected_this_run": 0,
            "valid": True,
        },
        "current_pool_audit_sha256": "a" * 64,
        "summary": {},
    }
    write_json(service.settings.latest_recommendations_path, payload)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {"600519"},
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )

    result = service.latest()

    assert result["items"] == []
    assert result["publication_gate"]["reason"] == "snapshot_contract_invalid"
    assert result["recommendation_status"] == "blocked_snapshot_contract"
    assert result["summary"]["recommendation_status"] == (
        "blocked_snapshot_contract"
    )


def test_latest_requires_receipt_committed_to_ledger(tmp_path, monkeypatch):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    payload = {
        "generated_at": "2026-07-13T15:02:00+08:00",
        "target_trade_date": "2026-07-13",
        "items": [complete_operation_contract_item()],
        "errors": [],
        "recommendation_status": "live_proven",
        "evidence_scope": "live_proof",
        "live_proof": True,
        "auto_order": False,
        "publication_gate": {"status": "allowed", "reason": None},
        "profile_gate": {
            "live_proof": True,
            "evidence_receipt_sha256": "b" * 64,
        },
        "daily_publication_cap": {
            "target_trade_date": "2026-07-13",
            "limit": 3,
            "prior_symbols": [],
            "prior_count": 0,
            "remaining_before_run": 3,
            "published_this_run": 1,
            "rejected_this_run": 0,
            "valid": True,
        },
        "current_pool_audit_sha256": "a" * 64,
        "summary": {},
    }
    write_json(service.settings.latest_recommendations_path, payload)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {"600519"},
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )

    result = service.latest()

    assert result["items"] == []
    assert result["publication_gate"]["reason"] == "snapshot_contract_invalid"


def test_latest_accepts_snapshot_with_receipt_in_ledger(tmp_path, monkeypatch):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    payload = publication_payload(["600519"])
    service._commit_publication_ledger(payload)
    write_json(service.settings.latest_recommendations_path, payload)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {"600519"},
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )

    result = service.latest()

    assert [item["symbol"] for item in result["items"]] == ["600519"]
    assert result["publication_gate"] == {
        "status": "allowed",
        "reason": None,
    }
    validated = RecommendationSnapshot.model_validate(result)
    assert [item.symbol for item in validated.items] == ["600519"]
    assert validated.model_extra["current_pool_revalidation"][
        "canonical_sha256"
    ] == "a" * 64


@pytest.mark.parametrize("field", ["market_data_source", "market_snapshot_source"])
def test_latest_rejects_non_jiaoch_market_source(tmp_path, monkeypatch, field):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    payload = publication_payload(["600519"])
    payload["items"][0][field] = "AKShare fixture"
    service._commit_publication_ledger(payload)
    write_json(service.settings.latest_recommendations_path, payload)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {"600519"},
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )

    result = service.latest()

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_market_source_gate"
    assert result["publication_gate"] == {
        "status": "blocked",
        "reason": "market_data_source_not_jiaoch",
    }
    assert result["market_source_gate"]["passed"] is False


def test_latest_rejects_non_jiaoch_l1_source(tmp_path, monkeypatch):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    payload = publication_payload(["600519"])
    payload["items"][0]["l1_quote"] = {"source": "mootdx", "price": 100.0}
    service._commit_publication_ledger(payload)
    write_json(service.settings.latest_recommendations_path, payload)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {"600519"},
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )

    result = service.latest()

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_market_source_gate"
    assert result["market_source_gate"]["passed"] is False


def test_latest_rejects_self_hashed_receipt_not_in_ledger(
    tmp_path, monkeypatch
):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    committed = publication_payload(["600519"], target="2026-07-13")
    committed_receipt = service._commit_publication_ledger(committed)
    forged = publication_payload(["600520"], target="2026-07-14")
    forged["publication_receipt"] = build_publication_ledger_record(
        sequence=2,
        generated_at=forged["generated_at"],
        target_trade_date=forged["target_trade_date"],
        prior_symbols=set(),
        published_symbols={"600520"},
        previous_record_hash=committed_receipt["record_hash"],
        seeded_from_legacy_history=False,
        snapshot_sha256=recommendation_snapshot_sha256(forged),
        current_pool_audit_sha256="a" * 64,
        profile_evidence_receipt_sha256="b" * 64,
    )
    write_json(service.settings.latest_recommendations_path, forged)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {"600520"},
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-14",
        },
    )

    result = service.latest()

    assert result["items"] == []
    assert result["publication_gate"]["reason"] == "snapshot_contract_invalid"
    assert "publication_receipt_not_in_ledger" in result["errors"][-1][
        "reasons"
    ]


def test_latest_rejects_fourth_forged_symbol_after_daily_ledger_is_full(
    tmp_path, monkeypatch
):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    committed = publication_payload(["600519", "600520", "600521"])
    service._commit_publication_ledger(committed)
    forged = publication_payload(
        ["600522"],
        prior_symbols=["600519", "600520", "600521"],
    )
    write_json(service.settings.latest_recommendations_path, forged)
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": {
                "600519",
                "600520",
                "600521",
                "600522",
            },
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )

    result = service.latest()

    assert result["items"] == []
    assert result["publication_gate"]["reason"] == "snapshot_contract_invalid"


@pytest.mark.parametrize(
    "field",
    ["prior_symbols", "published_symbols", "symbols_after_commit"],
)
def test_malformed_publication_receipt_symbols_fail_closed(field):
    payload = publication_payload(["600519"])
    payload["publication_receipt"] = build_publication_ledger_record(
        sequence=1,
        generated_at=payload["generated_at"],
        target_trade_date=payload["target_trade_date"],
        prior_symbols=set(),
        published_symbols={"600519"},
        previous_record_hash=None,
        seeded_from_legacy_history=False,
        snapshot_sha256=recommendation_snapshot_sha256(payload),
        current_pool_audit_sha256="a" * 64,
        profile_evidence_receipt_sha256="b" * 64,
    )
    payload["publication_receipt"][field] = 42

    errors = recommendation_publication_receipt_errors(payload)

    assert "publication_receipt_binding" in errors


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prior_count", "not-an-int"),
        ("prior_count", True),
        ("published_this_run", []),
        ("remaining_before_run", -1),
        ("rejected_this_run", -1),
    ],
)
def test_snapshot_daily_cap_rejects_malformed_counts_without_raising(
    field, value
):
    payload = {
        "generated_at": "2026-07-13T15:02:00+08:00",
        "target_trade_date": "2026-07-13",
        "items": [complete_operation_contract_item()],
        "recommendation_status": "live_proven",
        "evidence_scope": "live_proof",
        "live_proof": True,
        "auto_order": False,
        "publication_gate": {"status": "allowed", "reason": None},
        "profile_gate": {
            "live_proof": True,
            "evidence_receipt_sha256": "b" * 64,
        },
        "daily_publication_cap": {
            "target_trade_date": "2026-07-13",
            "limit": 3,
            "prior_symbols": [],
            "prior_count": 0,
            "remaining_before_run": 3,
            "published_this_run": 1,
            "rejected_this_run": 0,
            "valid": True,
        },
        "current_pool_audit_sha256": "a" * 64,
    }
    payload["daily_publication_cap"][field] = value

    errors = recommendation_snapshot_publication_errors(payload)

    assert "daily_publication_cap" in errors


def test_empty_live_claim_must_still_satisfy_live_state_contract():
    payload = {
        "items": [],
        "recommendation_status": "live_proven",
        "evidence_scope": "development_only",
        "live_proof": False,
        "auto_order": False,
        "publication_gate": {"status": "blocked", "reason": "forged"},
        "profile_gate": {"live_proof": False},
    }

    errors = recommendation_snapshot_publication_errors(payload)

    assert "live_proof" in errors
    assert "evidence_scope" in errors
    assert "publication_gate" in errors


def test_non_live_empty_snapshot_rejects_explicit_auto_order_true():
    errors = recommendation_snapshot_publication_errors(
        {
            "items": [],
            "recommendation_status": "no_snapshot",
            "auto_order": True,
        }
    )

    assert "auto_order" in errors


def test_empty_live_claim_requires_valid_generated_and_target_dates():
    payload = publication_payload([])
    payload["generated_at"] = ""
    payload["target_trade_date"] = ""
    payload["daily_publication_cap"]["target_trade_date"] = ""

    errors = recommendation_snapshot_publication_errors(payload)

    assert "generated_at" in errors
    assert "target_trade_date" in errors


def test_generation_enforces_target_trade_date_publication_cap(
    tmp_path, monkeypatch
):
    evidence_path = tmp_path / "profile-evidence.json"
    write_json(str(evidence_path), _live_profile_evidence_payload())
    settings = replace(
        make_settings(tmp_path),
        recommendation_profile_id="primary_50_return_15_drawdown",
        recommendation_profile_evidence_path=str(evidence_path),
        scan_max_deep=6,
        scan_result_limit=6,
    )
    service = RecommendationService(settings, FakeJiaochProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    symbols = ["600519", "600520", "600521"]
    service.universe = FakeUniverse(
        [
            {
                "symbol": symbol,
                "market": "a",
                "name": f"测试股票{index}",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 7.0,
                "volume": 10000,
            }
            for index, symbol in enumerate(symbols)
        ]
    )
    industry_payload = FakeIndustry().build_map()
    industry_payload["symbol_map"] = {
        symbol: {
            "industry": "测试行业",
            "industry_rank": 1,
            "industry_change_pct": 2.5,
            "industry_score": 8,
        }
        for symbol in symbols
    }
    monkeypatch.setattr(
        service.industry,
        "build_map",
        lambda use_cache_on_error=True: industry_payload,
    )
    append_jsonl(
        settings.recommendation_history_path,
        {
            "target_trade_date": "2026-07-13",
            "items": [
                {"symbol": "600001", "market": "a"},
                {"symbol": "600002", "market": "a"},
            ],
        },
    )
    fixed_now = datetime(
        2026, 7, 13, 15, 2, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    monkeypatch.setattr("app.recommendations.now_cn", lambda: fixed_now)
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: True)
    monkeypatch.setattr(
        "app.recommendations.build_production_status",
        lambda settings: {"status": "healthy", "checks": []},
    )
    monkeypatch.setattr(
        "app.recommendations._selection_rejection_reason",
        lambda compact, allowed_actions, min_score: None,
    )
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": set(symbols),
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )
    commit_events = []
    real_append_jsonl_durable = (
        recommendations_module.append_jsonl_durable
    )
    real_write_json = recommendations_module.write_json

    def record_append(path, payload):
        commit_events.append(("append", str(path)))
        real_append_jsonl_durable(path, payload)

    def record_write(path, payload):
        commit_events.append(("write", str(path)))
        real_write_json(path, payload)

    monkeypatch.setattr(
        recommendations_module,
        "append_jsonl_durable",
        record_append,
    )
    monkeypatch.setattr(
        recommendations_module,
        "write_json",
        record_write,
    )

    result = service.generate_daily_recommendations(
        force=True,
        run_slot="post_close",
        target_trade_date="2026-07-13",
    )

    assert result["live_proof"] is True
    assert len(result["items"]) == 1, json.dumps(
        result["summary"]["selection_funnel"], ensure_ascii=False
    )
    assert result["daily_publication_cap"] == {
        "target_trade_date": "2026-07-13",
        "limit": 3,
        "prior_symbols": ["600001", "600002"],
        "prior_count": 2,
        "remaining_before_run": 1,
        "published_this_run": 1,
        "rejected_this_run": 2,
        "valid": True,
    }
    assert result["summary"]["selection_funnel"]["rejection_reasons"][
        "daily_result_limit"
    ] == 2
    assert commit_events == [
        (
            "append",
            f"{settings.recommendation_history_path}.publications",
        ),
        ("append", settings.recommendation_history_path),
        ("write", settings.latest_recommendations_path),
    ]


def test_selection_funnel_explanation_names_top_zero_result_reasons():
    explanation = _selection_funnel_explanation(
        {
            "considered": 5,
            "returned": 0,
            "rejection_reasons": {"strict_signal_failed": 3, "score_below_floor": 2},
        }
    )

    assert explanation == "本次分析 5 只，最终 0 只；主要原因：严格信号未通过 3 只、评分不足 2 只。"


def test_skipped_run_still_writes_empty_funnel_and_audit(tmp_path, monkeypatch):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: False)

    result = service.generate_daily_recommendations(force=False)

    assert result["summary"]["selection_funnel"]["considered"] == 0
    audit = read_jsonl(service.settings.recommendation_audit_path, limit=10)[-1]
    assert audit["selection_funnel"]["returned"] == 0


def test_generation_can_target_the_next_trade_date(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.recommendations.now_cn",
        lambda: datetime(2026, 7, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
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
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: True)

    result = service.generate_daily_recommendations(
        force=True,
        target_trade_date="2026-07-13",
    )

    assert result["trade_date"] == result["signal_date"]
    assert result["target_trade_date"] == "2026-07-13"
    assert result["summary"]["target_trade_date"] == "2026-07-13"
    assert result["summary"]["recommendation_status"] == "blocked_current_pool_gate"


def test_post_close_generation_defaults_to_next_trade_date(tmp_path, monkeypatch):
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
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    next_day = today + timedelta(days=1)
    monkeypatch.setattr("app.recommendations.next_trade_date", lambda value: next_day)
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: value == next_day)

    result = service.generate_daily_recommendations(force=True, run_slot="post_close")

    assert result["trade_date"] == result["signal_date"]
    assert result["target_trade_date"] == next_day.isoformat()
    assert result["target_trade_date_semantics"] == "next_trading_session"


def test_failed_run_still_writes_failure_funnel_and_audit(tmp_path):
    service = RecommendationService(make_settings(tmp_path), FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.margin_eligibility = FakeMarginEligibility()

    class BrokenUniverse:
        def snapshot(self, use_cache_on_error=True):
            raise RuntimeError("snapshot failed")

    service.universe = BrokenUniverse()
    result = service.generate_daily_recommendations(force=True)

    assert result["summary"]["failed"] is True
    assert result["summary"]["selection_funnel"]["rejection_reasons"] == {"run_failed": 1}
    assert read_jsonl(service.settings.recommendation_audit_path, limit=10)[-1]["failed"] is True


def test_generation_blocks_missing_current_pool_audit_before_reading_live_snapshot(tmp_path):
    settings = replace(
        make_settings(tmp_path),
        current_pool_audit_path=str(tmp_path / "missing-current-pool.json"),
    )
    service = RecommendationService(settings, FakeProvider(), "risk")

    class SnapshotMustNotBeRead:
        def snapshot(self, use_cache_on_error=True):
            raise AssertionError("current-pool gate must run before the live snapshot")

    service.universe = SnapshotMustNotBeRead()

    result = service.generate_daily_recommendations(force=True)

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_current_pool_gate"
    assert result["current_pool_gate"]["passed"] is False
    assert result["auto_order"] is False
    assert result["summary"]["selection_funnel"]["considered"] == 0
    assert "股票池审计门禁" in result["summary"]["selection_funnel"]["explanation"]


@pytest.mark.parametrize("audit_state", ["missing", "tampered", "development_only"])
def test_latest_revalidates_current_pool_and_never_replays_old_advice(tmp_path, audit_state):
    settings = make_settings(tmp_path)
    write_json(
        settings.latest_recommendations_path,
        {
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "items": [{"symbol": "600519", "auto_order": False}],
            "recommendation_status": "live_proven",
            "live_proof": True,
            "auto_order": False,
            "summary": {},
        },
    )
    audit_path = Path(settings.current_pool_audit_path)
    if audit_state == "missing":
        audit_path.unlink()
    elif audit_state == "tampered":
        payload = json.loads(audit_path.read_text(encoding="utf-8"))
        payload["source_as_of"] = "2000-01-01"
        write_json(str(audit_path), payload)

    result = RecommendationService(settings, FakeProvider(), "risk").latest()

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_current_pool_gate"
    assert result["live_proof"] is False
    assert result["auto_order"] is False
    if audit_state == "development_only":
        assert result["current_pool_gate"]["passed"] is True
        assert result["current_pool_gate"]["production_recommendation_eligible"] is False
    else:
        assert result["current_pool_gate"]["passed"] is False


@pytest.mark.parametrize(
    ("status", "running"),
    [("running", True), ("generation_failed", False), ("not_run_not_trade_day", False)],
)
def test_latest_preserves_execution_state_while_publication_gate_clears_items(
    tmp_path, status, running
):
    settings = make_settings(tmp_path)
    audit_sha = json.loads(Path(settings.current_pool_audit_path).read_text(encoding="utf-8"))[
        "canonical_sha256"
    ]
    write_json(
        settings.latest_recommendations_path,
        {
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "items": [{"symbol": "600519", "auto_order": False}],
            "recommendation_status": status,
            "live_proof": status == "live_proven",
            "auto_order": False,
            "current_pool_audit_sha256": audit_sha,
            "summary": {"running": running},
        },
    )

    result = RecommendationService(settings, FakeProvider(), "risk").latest()

    assert result["items"] == []
    assert result["recommendation_status"] == status
    assert result["summary"]["running"] is running
    assert result["publication_gate"]["status"] == "blocked"
    assert result["publication_gate"]["reason"] == "current_pool_not_production_eligible"


def test_latest_revalidates_pre_open_snapshot_for_current_time_after_open(
    tmp_path, monkeypatch
):
    settings = replace(
        make_settings(tmp_path),
        trade_calendar_cache_path=str(tmp_path / "calendar.json"),
    )
    write_json(
        settings.trade_calendar_cache_path,
        {"dates": ["2026-07-10", "2026-07-13"]},
    )
    _write_current_pool_audit(
        Path(settings.current_pool_audit_path),
        ["600519"],
        source_as_of="2026-07-10",
    )
    audit_sha = json.loads(
        Path(settings.current_pool_audit_path).read_text(encoding="utf-8")
    )["canonical_sha256"]
    write_json(
        settings.latest_recommendations_path,
        {
            "generated_at": "2026-07-13T09:00:00+08:00",
            "trade_date": "2026-07-13",
            "target_trade_date": "2026-07-13",
            "run_slot": "pre_open",
            "items": [],
            "recommendation_status": "running",
            "current_pool_audit_sha256": audit_sha,
            "summary": {"running": True},
        },
    )
    monkeypatch.setattr(
        "app.recommendations.now_cn",
        lambda: datetime(2026, 7, 13, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    result = RecommendationService(settings, FakeProvider(), "risk").latest()

    assert result["current_pool_gate"]["passed"] is False
    assert "trade data date" in result["current_pool_gate"]["reasons"][0]
    assert result["publication_gate"]["reason"] == "current_pool_gate_failed"
    assert result["recommendation_status"] == "running"
    assert result["summary"]["running"] is True


@pytest.mark.parametrize(
    ("stored_hash", "item_symbol", "expected_reason"),
    [
        (None, "600519", "current_pool_audit_binding_missing"),
        ("0" * 64, "600519", "current_pool_audit_binding_mismatch"),
        ("current", "601999", "current_pool_symbol_outside_audit"),
    ],
)
def test_latest_fails_closed_when_snapshot_is_not_bound_to_current_pool(
    tmp_path, stored_hash, item_symbol, expected_reason
):
    settings = make_settings(tmp_path)
    audit_sha = json.loads(Path(settings.current_pool_audit_path).read_text(encoding="utf-8"))[
        "canonical_sha256"
    ]
    if stored_hash == "current":
        stored_hash = audit_sha
    write_json(
        settings.latest_recommendations_path,
        {
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "items": [{"symbol": item_symbol, "auto_order": False}],
            "recommendation_status": "live_proven",
            "live_proof": True,
            "auto_order": False,
            "current_pool_audit_sha256": stored_hash,
            "summary": {},
        },
    )

    result = RecommendationService(settings, FakeProvider(), "risk").latest()

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_current_pool_gate"
    assert result["publication_gate"] == {
        "status": "blocked",
        "reason": expected_reason,
    }


def test_live_snapshot_can_only_shrink_audited_current_pool(tmp_path):
    settings = make_settings(tmp_path)
    _write_current_pool_audit(Path(settings.current_pool_audit_path), ["600519"])

    class TrackingProvider(FakeProvider):
        def __init__(self):
            self.a_share_symbols = []

        def history(self, symbol, market, lookback_days=360, adjust="qfq"):
            if market == "a":
                self.a_share_symbols.append(symbol)
            return super().history(symbol, market, lookback_days, adjust)

    provider = TrackingProvider()
    service = RecommendationService(settings, provider, "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {"symbol": "600519", "market": "a", "name": "审计内", "latest": 100, "amount": 100000000, "change_pct": 7.0, "volume": 10000},
            {"symbol": "600520", "market": "a", "name": "实时扩池", "latest": 100, "amount": 100000000, "change_pct": 7.0, "volume": 10000},
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["current_pool_gate"]["passed"] is True
    audit_sha = json.loads(Path(settings.current_pool_audit_path).read_text(encoding="utf-8"))["canonical_sha256"]
    assert result["current_pool_audit_sha256"] == audit_sha
    assert result["summary"]["snapshot_count"] == 1
    assert provider.a_share_symbols == ["600519"]
    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_current_pool_gate"
    assert {item["symbol"] for item in service._last_development_candidates} <= {"600519"}
    assert len(service._last_development_candidates) <= 3
    assert result["auto_order"] is False
    assert all(item["auto_order"] is False for item in result["items"])


def test_generate_revalidates_current_pool_hash_at_commit(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [{"symbol": "600519", "market": "a", "name": "甲", "latest": 100, "amount": 100000000, "change_pct": 7.0, "volume": 10000}]
    )
    first = service._current_pool_gate(datetime.now(ZoneInfo("Asia/Shanghai")))
    second = {**first, "canonical_sha256": "f" * 64}
    gates = iter([first, second])
    monkeypatch.setattr(service, "_current_pool_gate", lambda *args, **kwargs: next(gates))

    result = service.generate_daily_recommendations(force=True)

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_current_pool_gate"
    assert result["current_pool_audit_sha256"] == "f" * 64
    assert "current_pool_audit_changed" in result["publication_gate"]["reason"]


def test_current_pool_freshness_is_run_slot_aware_by_trade_date(tmp_path):
    settings = replace(
        make_settings(tmp_path),
        trade_calendar_cache_path=str(tmp_path / "calendar.json"),
    )
    write_json(
        settings.trade_calendar_cache_path,
        {"dates": ["2026-07-10", "2026-07-13"]},
    )
    _write_current_pool_audit(
        Path(settings.current_pool_audit_path), ["600519"], source_as_of="2026-07-10"
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    pre_open = datetime(2026, 7, 13, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    open_confirm = datetime(2026, 7, 13, 9, 32, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert service._current_pool_gate(pre_open, "pre_open")["passed"] is True
    opened = service._current_pool_gate(open_confirm, "open_confirm")
    assert opened["passed"] is False
    assert "trade data date" in opened["reasons"][0]


def test_recommendation_data_as_of_includes_current_pool_source_date(
    tmp_path, monkeypatch
):
    fixed = datetime(2026, 7, 13, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    settings = replace(
        make_settings(tmp_path),
        trade_calendar_cache_path=str(tmp_path / "calendar.json"),
    )
    write_json(
        settings.trade_calendar_cache_path,
        {"dates": ["2026-07-10", "2026-07-13"]},
    )
    _write_current_pool_audit(
        Path(settings.current_pool_audit_path),
        ["600519"],
        source_as_of="2026-07-10",
    )

    class CurrentDataProvider(FakeProvider):
        def history(self, symbol, market, lookback_days=360, adjust="qfq"):
            frame = sample_frame("up")
            frame["date"] = pd.bdate_range(
                end="2026-07-13", periods=len(frame)
            ).strftime("%Y-%m-%d")
            return frame, "current-data-provider"

    service = RecommendationService(settings, CurrentDataProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.l1_quotes = FakeL1Quotes()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "600519",
                "market": "a",
                "name": "甲",
                "latest": 100,
                "amount": 100000000,
                "change_pct": 2.0,
                "volume": 10000,
            }
        ]
    )
    monkeypatch.setattr("app.recommendations.now_cn", lambda: fixed)
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda _value: True)

    result = service.generate_daily_recommendations(
        force=True,
        run_slot="pre_open",
    )

    assert result["summary"]["development_selected_count"] == 1
    assert result["current_pool_gate"]["source_as_of"] == "2026-07-10"
    assert result["data_as_of"] == "2026-07-10"
    assert result["summary"]["data_as_of"] == "2026-07-10"


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
    assert result["items"] == []
    development_items = service._last_development_candidates
    assert development_items
    assert development_items[0]["symbol"] == "600519"
    assert development_items[0]["market"] == "a"
    assert development_items[0]["market_data_source"] == "fake-provider"
    # 操作建议完整性：每条推荐必须给出 action + levels(止损/止盈) + 入场区间 + 短线/长线计划
    assert development_items[0]["action"] in {"BUY", "WATCH", "HOLD", "REDUCE", "SELL"}
    assert {"stop_loss", "take_profit"} <= set(development_items[0]["levels"])
    assert development_items[0]["entry_zone"]
    assert development_items[0]["trade_plans"]
    advice = development_items[0]["operation_advice"]
    assert advice["trigger_conditions"]
    assert advice["take_profit_or_reduce"]["trigger_price"] == advice["take_profit"]
    assert advice["invalidation_conditions"]
    assert advice["expected_holding_period"] == advice["holding_period"]
    assert development_items[0]["industry"]["industry"] == "测试行业"
    assert development_items[0]["news_context"]["level"] == "neutral"
    assert development_items[0]["announcement_context"]["level"] == "neutral"
    assert development_items[0]["announcement_context"]["source"] == "jiaoch:anns_d"
    assert development_items[0]["announcement_context"]["fallback_used"] is False
    assert development_items[0]["fund_flow_context"]["level"] == "neutral"
    assert development_items[0]["signal_tags"]
    assert development_items[0]["market_breadth"]["sample_count"] == 1
    assert "breadth_ma20_gte_60" in development_items[0]["signal_tags"]
    assert development_items[0]["relative_strength"]["relative_strength_60d_pct"] is not None
    assert any(tag.startswith("rs60_") for tag in development_items[0]["signal_tags"])
    assert development_items[0]["price_action"]["gap_pct"] is not None
    assert any(tag.startswith("price_") for tag in development_items[0]["signal_tags"])
    assert development_items[0]["strict_signal"]["passed"] is True
    assert development_items[0]["strategy_quality"]["passed"] is True
    assert result["summary"]["market_breadth"]["sample_count"] == 1
    assert "breadth_ma20_gte_60" in result["summary"]["market_breadth_tags"]
    assert result["summary"]["relative_strength_proxy"]["proxy_return_60d_avg_pct"] is not None
    assert result["summary"]["margin_eligibility"]["enabled"] is False
    funnel = result["summary"]["selection_funnel"]
    assert funnel["considered"] == 1
    assert funnel["returned"] == 0
    assert funnel["development_selected"] == 1
    assert funnel["rejection_reasons"] == {}
    audit = read_jsonl(service.settings.recommendation_audit_path, limit=10)[-1]
    assert audit["selection_funnel"]["returned"] == 0
    assert audit["selection_funnel"]["development_selected"] == 1
    assert audit["selected"] == []
    assert result["summary"]["market_context"]["level"] in {"favorable", "neutral", "cautious", "defensive"}


def _profile_evidence_payload():
    profile = profile_to_dict(DEFAULT_PROFILE)
    payload = {
        "profile_id": profile["profile_id"],
        "version": profile["version"],
        "profile_hash": profile["profile_hash"],
        "status": "qualified",
        "blocking_gates": [],
        "gates": {
            "annualized_return": True,
            "max_drawdown": True,
            "observed_win_rate": True,
            "wilson_lower": True,
            "payoff_ratio": True,
            "profit_factor": True,
            "calmar": True,
            "minimum_sample": True,
            "signal_days_120": True,
            "all_rolling_12m": True,
            "pit_contract": True,
            "temporal_contract": True,
            "cost_slippage": True,
            "artifact_execution": True,
            "strategy_signal_replay": True,
            "outcome_replay": True,
            "double_cost": True,
            "regime": True,
        },
        "metrics": {
            "annualized_return_pct": 52.0,
            "max_drawdown_pct": 12.0,
            "win_rate_pct": 56.0,
            "win_rate_wilson_lower_pct": 52.1,
            "payoff_ratio": 1.45,
            "profit_factor": 1.6,
            "calmar": 2.0,
            "signal_days": 132,
            "rolling_12m": [
                {
                    "annualized_return_pct": 50.2,
                    "max_drawdown_pct": 13.0,
                    "payoff_ratio": 1.4,
                    "profit_factor": 1.5,
                    "calmar": 1.8,
                }
            ],
        },
        "evidence": {
            "pit_contract": True,
            "temporal_contract": True,
            "final_oos": False,
            "cost_slippage": True,
            "artifact_execution": True,
            "strategy_signal_replay": True,
            "outcome_replay": True,
        },
    }
    payload["receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _live_profile_evidence_payload():
    payload = _profile_evidence_payload()
    payload.pop("receipt_sha256")
    payload.update({"status": "live_proven", "live_proof": True})
    payload["evidence"].update(
        {
            "final_oos": True,
            "shadow": True,
            "live_monitoring": True,
        }
    )
    payload["receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return payload


def test_incomplete_profile_receipt_blocks_even_when_point_metrics_pass(tmp_path):
    evidence_path = tmp_path / "profile-evidence.json"
    payload = _profile_evidence_payload()
    payload["status"] = "incomplete"
    payload["blocking_gates"] = ["double_cost", "regime"]
    write_json(str(evidence_path), payload)
    settings = replace(
        make_settings(tmp_path),
        recommendation_profile_id="primary_50_return_15_drawdown",
        recommendation_profile_evidence_path=str(evidence_path),
    )
    service = RecommendationService(settings, FakeProvider(), "risk")

    gate = service._profile_gate()

    assert gate["development_ready"] is False
    assert "receipt_incomplete" in gate["reasons"]
    assert "double_cost" in gate["reasons"]


def test_profile_enabled_generation_fails_closed_without_audited_evidence(tmp_path, monkeypatch):
    evidence_path = tmp_path / "profile-evidence.json"
    settings = replace(
        make_settings(tmp_path),
        recommendation_profile_id="primary_50_return_15_drawdown",
        recommendation_profile_evidence_path=str(evidence_path),
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
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
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: True)
    monkeypatch.setattr(
        "app.recommendations.build_production_status",
        lambda settings: {"status": "healthy", "checks": []},
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_profile_gate"
    assert result["current_pool_gate"]["passed"] is True
    assert result["profile_gate"]["development_ready"] is False
    assert "profile_evidence_missing" in result["profile_gate"]["reasons"]
    assert result["auto_order"] is False


def test_profile_enabled_generation_caps_advice_and_records_profile_evidence(tmp_path, monkeypatch):
    evidence_path = tmp_path / "profile-evidence.json"
    write_json(str(evidence_path), _profile_evidence_payload())
    settings = replace(
        make_settings(tmp_path),
        recommendation_profile_id="primary_50_return_15_drawdown",
        recommendation_profile_evidence_path=str(evidence_path),
        scan_max_deep=6,
        scan_result_limit=6,
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "%06d" % (600519 + index),
                "market": "a",
                "name": "测试股票%d" % index,
                "latest": 100,
                "amount": 100000000,
                "change_pct": 7.0,
                "volume": 10000,
            }
            for index in range(5)
        ]
    )
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: True)
    monkeypatch.setattr(
        "app.recommendations.build_production_status",
        lambda settings: {"status": "healthy", "checks": []},
    )

    result = service.generate_daily_recommendations(force=True)

    assert len(result["items"]) <= 3
    assert result["profile_gate"]["development_ready"] is True
    assert result["profile_gate"]["live_proof"] is False
    assert result["evidence_scope"] == "development_only"
    assert result["summary"]["strategy_profile"]["profile_id"] == "primary_50_return_15_drawdown"
    assert all(item["market"] == "a" for item in result["items"])
    assert all(item["auto_order"] is False for item in result["items"])


def test_live_profile_cannot_publish_when_current_pool_is_development_only(tmp_path, monkeypatch):
    evidence_path = tmp_path / "profile-evidence.json"
    write_json(str(evidence_path), _live_profile_evidence_payload())
    settings = replace(
        make_settings(tmp_path),
        recommendation_profile_id="primary_50_return_15_drawdown",
        recommendation_profile_evidence_path=str(evidence_path),
        scan_max_deep=6,
        scan_result_limit=6,
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "%06d" % (600519 + index),
                "market": "a",
                "name": "测试股票%d" % index,
                "latest": 100,
                "amount": 100000000,
                "change_pct": 7.0,
                "volume": 10000,
            }
            for index in range(5)
        ]
    )
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: True)
    monkeypatch.setattr(
        "app.recommendations.build_production_status",
        lambda settings: {"status": "healthy", "checks": []},
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["profile_gate"]["development_ready"] is True
    assert result["profile_gate"]["live_proof"] is True
    assert result["recommendation_status"] == "blocked_current_pool_gate"
    assert result["evidence_scope"] == "development_only"
    assert result["live_proof"] is False
    assert result["summary"]["recommendation_status"] == "blocked_current_pool_gate"
    assert result["summary"]["evidence_scope"] == "development_only"
    assert result["summary"]["live_proof"] is False
    assert result["items"] == []
    assert len(service._last_development_candidates) <= 3
    assert result["auto_order"] is False
    assert result["summary"]["auto_order"] is False
    assert all(item["auto_order"] is False for item in result["items"])


def test_live_profile_cannot_publish_non_jiaoch_market_source(tmp_path, monkeypatch):
    evidence_path = tmp_path / "profile-evidence.json"
    write_json(str(evidence_path), _live_profile_evidence_payload())
    symbols = ["%06d" % (600519 + index) for index in range(5)]
    settings = replace(
        make_settings(tmp_path),
        recommendation_profile_id="primary_50_return_15_drawdown",
        recommendation_profile_evidence_path=str(evidence_path),
        scan_max_deep=6,
        scan_result_limit=6,
    )
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": symbol,
                "market": "a",
                "name": "娴嬭瘯鑲＄エ%d" % index,
                "latest": 100,
                "amount": 100000000,
                "change_pct": 7.0,
                "volume": 10000,
            }
            for index, symbol in enumerate(symbols)
        ]
    )
    monkeypatch.setattr("app.recommendations.is_trade_day", lambda value: True)
    monkeypatch.setattr(
        "app.recommendations.build_production_status",
        lambda settings: {"status": "healthy", "checks": []},
    )
    monkeypatch.setattr(
        service,
        "_current_pool_gate",
        lambda moment, run_slot=None: {
            "passed": True,
            "production_recommendation_eligible": True,
            "allowed_symbols": set(symbols),
            "canonical_sha256": "a" * 64,
            "source_as_of": "2026-07-13",
        },
    )
    monkeypatch.setattr(
        "app.recommendations._selection_rejection_reason",
        lambda compact, allowed_actions, min_score: None,
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["profile_gate"]["live_proof"] is True
    assert result["recommendation_status"] == "blocked_market_source_gate"
    assert result["publication_gate"] == {
        "status": "blocked",
        "reason": "market_data_source_not_jiaoch",
    }
    assert result["market_source_gate"]["passed"] is False
    assert result["items"] == []
    assert result["auto_order"] is False


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

    assert result["items"] == []
    assert service._last_development_candidates
    tags = set(service._last_development_candidates[0]["signal_tags"])
    assert {"breadth_ma20_gte_60", "rs60_nonnegative"}.issubset(tags)
    assert service._last_development_candidates[0]["strict_signal"]["passed"] is True


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

    assert result["items"] == []
    assert service._last_development_candidates
    item = service._last_development_candidates[0]
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

    assert result["items"] == []
    assert service._last_development_candidates
    assert service._last_development_candidates[0]["price_action"]["gap_pct"] >= 2
    assert "price_gap_up_2_to_5" in service._last_development_candidates[0]["signal_tags"]


def test_generate_daily_recommendations_default_strict_filter_blocks_weak_signal(tmp_path):
    settings = replace(
        make_settings(tmp_path),
        scan_max_deep=1,
        scan_result_limit=1,
        recommendation_required_signal_tags=[
            "breadth_advancing_gte_50",
            "breakout_20d",
            "price_gap_up_2_to_5",
        ],
        recommendation_allowed_market_levels=["favorable", "neutral"],
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


def test_generate_daily_recommendations_skips_recent_symbol_in_cooldown(
    tmp_path,
):
    settings = make_settings(tmp_path)
    settings = replace(settings, recommendation_symbol_cooldown_days=10)
    append_jsonl(
        settings.recommendation_history_path,
        {
            "generated_at": (
                datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)
            ).isoformat(),
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


def test_monitor_recommendations_alerts_when_stop_loss_breaks(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.recommendations.now_cn",
        lambda: datetime(2026, 7, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
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


def test_monitor_recommendations_alerts_when_take_profit_reached(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.recommendations.now_cn",
        lambda: datetime(2026, 7, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
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


def test_monitor_recommendations_uses_l1_quotes_before_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.recommendations.now_cn",
        lambda: datetime(2026, 7, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
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


def test_begin_validates_target_before_lock_and_releases_lock_if_mark_fails(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")

    with pytest.raises(ValueError, match="earlier"):
        service.begin_recommendation_run(target_trade_date="2000-01-01")
    assert not Path(settings.recommendation_lock_path).exists()

    monkeypatch.setattr(
        service,
        "mark_recommendations_started",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("mark failed")),
    )
    with pytest.raises(RuntimeError, match="mark failed"):
        service.begin_recommendation_run()
    assert not Path(settings.recommendation_lock_path).exists()


def test_running_snapshot_does_not_republish_previous_recommendations(tmp_path):
    settings = make_settings(tmp_path)
    write_json(
        settings.latest_recommendations_path,
        {
            "generated_at": "2026-07-10T09:00:00+08:00",
            "trade_date": "2026-07-10",
            "items": [{"symbol": "600519", "market": "a"}],
            "summary": {},
        },
    )
    service = RecommendationService(settings, FakeProvider(), "risk")

    started = service.mark_recommendations_started(max_deep=20)

    assert started["items"] == []


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


def _entry_frame(signal_date, entry_date, signal_close, entry_open):
    return pd.DataFrame(
        [
            {"date": signal_date.isoformat(), "open": signal_close, "high": signal_close, "low": signal_close, "close": signal_close},
            {"date": entry_date.isoformat(), "open": entry_open, "high": entry_open, "low": entry_open, "close": entry_open},
        ]
    )


def test_monitor_planned_exits_alerts_for_entry_weak_gap(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    signal_date = today - timedelta(days=3)
    entry_date = today - timedelta(days=2)  # entry 已完成（< today）
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_profit_lock_history(settings, 100.0, signal_date)
    frame = _entry_frame(signal_date, entry_date, signal_close=100, entry_open=97)  # gap=-3%
    monkeypatch.setattr(service.data_provider, "history", lambda *a, **k: (frame, "fake"))
    monkeypatch.setattr("app.recommendations.next_trade_date", lambda d: entry_date)
    monkeypatch.setattr("app.recommendations.next_calendar_gap", lambda *a, **k: None)

    result = service.monitor_planned_exits(force=True)

    entry_alerts = [a for a in result["planned_exits"] if a["event_type"] == "planned_entry_weak"]
    assert entry_alerts
    alert = entry_alerts[0]
    assert alert["symbol"] == "600519"
    assert alert["gap_pct"] == -3.0
    assert alert["entry_date"] == entry_date.isoformat()
    assert alert["id"] == "entry_weak:%s:600519" % entry_date.isoformat()


def test_monitor_planned_exits_no_entry_alert_when_gap_ok(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    signal_date = today - timedelta(days=3)
    entry_date = today - timedelta(days=2)
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_profit_lock_history(settings, 100.0, signal_date)
    frame = _entry_frame(signal_date, entry_date, signal_close=100, entry_open=102)  # gap=+2% >= -1
    monkeypatch.setattr(service.data_provider, "history", lambda *a, **k: (frame, "fake"))
    monkeypatch.setattr("app.recommendations.next_trade_date", lambda d: entry_date)
    monkeypatch.setattr("app.recommendations.next_calendar_gap", lambda *a, **k: None)

    result = service.monitor_planned_exits(force=True)

    assert [a for a in result["planned_exits"] if a["event_type"] == "planned_entry_weak"] == []


def test_monitor_planned_exits_skip_entry_review_when_not_completed(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    signal_date = today - timedelta(days=1)
    settings = make_settings(tmp_path)
    service = RecommendationService(settings, FakeProvider(), "risk")
    _write_profit_lock_history(settings, 100.0, signal_date)
    monkeypatch.setattr("app.recommendations.next_trade_date", lambda d: today + timedelta(days=1))  # entry 未来
    monkeypatch.setattr("app.recommendations.next_calendar_gap", lambda *a, **k: None)

    result = service.monitor_planned_exits(force=True)

    assert [a for a in result["planned_exits"] if a["event_type"] == "planned_entry_weak"] == []


def test_generate_produces_three_recommendations_with_full_action_advice(tmp_path):
    """goal 证据：generate 在 3 只强信号输入下产出恰好 3 只 + 每只完整操作建议。"""
    settings = replace(make_settings(tmp_path), scan_result_limit=3)
    service = RecommendationService(settings, FakeProvider(), "risk")
    service.industry = FakeIndustry()
    service.industry_history = FakeIndustryHistory()
    service.news = FakeNews()
    service.announcements = FakeAnnouncement()
    service.fund_flow = FakeFundFlow()
    service.margin_eligibility = FakeMarginEligibility()
    service.universe = FakeUniverse(
        [
            {
                "symbol": "000001",
                "market": "a",
                "name": "甲测试",
                "latest": 100,
                "amount": 10 ** 9,
                "change_pct": 5.0,
                "volume": 10 ** 5,
                "industry": "测试行业",
            },
            {
                "symbol": "000002",
                "market": "a",
                "name": "乙测试",
                "latest": 100,
                "amount": 10 ** 9,
                "change_pct": 5.0,
                "volume": 10 ** 5,
                "industry": "测试行业",
            },
            {
                "symbol": "000003",
                "market": "a",
                "name": "丙测试",
                "latest": 100,
                "amount": 10 ** 9,
                "change_pct": 5.0,
                "volume": 10 ** 5,
                "industry": "测试行业",
            },
        ]
    )

    result = service.generate_daily_recommendations(force=True)

    assert result["items"] == []
    assert result["recommendation_status"] == "blocked_current_pool_gate"
    development_items = service._last_development_candidates
    assert len(development_items) == 3
    returned_symbols = {item["symbol"] for item in development_items}
    assert returned_symbols == {"000001", "000002", "000003"}
    for item in development_items:
        assert item["action"] in {"BUY", "WATCH", "HOLD", "REDUCE", "SELL"}
        assert {"stop_loss", "take_profit"} <= set(item["levels"])
        assert item["entry_zone"]
        assert item["trade_plans"]
