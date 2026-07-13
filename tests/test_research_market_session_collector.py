"""Collector integration for atomic market session generations.

The official market session collection path must drive the four-dataset
generation state machine and must never fall back to legacy per-dataset
receipts. A collector whose store lacks market generation capabilities must
refuse to collect instead of silently emitting legacy receipts.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.research_pit_collector import ControlledTushareCollector, HttpEntityResponse
from app.research_pit_store import PITReceiptStore

TOKEN = "market-session-collector-token"
TRADE_DATE = "2024-01-02"
WIRE_DATE = "20240102"

DATASET_FIELDS = {
    "daily": [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ],
    "adj_factor": ["ts_code", "trade_date", "adj_factor"],
    "stk_limit": ["trade_date", "ts_code", "pre_close", "up_limit", "down_limit"],
    "suspend_d": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
}
DATASET_ROWS = {
    "daily": [["600001.SH", WIRE_DATE, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]],
    "adj_factor": [["600001.SH", WIRE_DATE, 1.5]],
    "stk_limit": [[WIRE_DATE, "600001.SH", 10.0, 11.0, 9.0]],
    "suspend_d": [],
}


class TrustedClock:
    def __init__(self, now):
        self.now = now
        self.monotonic = 0

    def assert_synchronized(self):
        return {"source": "market-session-test-clock", "synchronized": True}

    def now_utc(self):
        return self.now

    def monotonic_ns(self):
        self.monotonic += 1_000_000
        return self.monotonic


class MarketSessionTransport:
    def __init__(self):
        self.calls = []

    def post(self, **kwargs):
        request = json.loads(kwargs["body"])
        dataset = request["api_name"]
        self.calls.append({"dataset": dataset, **kwargs})
        body = json.dumps(
            {
                "request_id": f"market-collector-{dataset}",
                "code": 0,
                "msg": "",
                "data": {"fields": DATASET_FIELDS[dataset], "items": DATASET_ROWS[dataset]},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return HttpEntityResponse(200, {}, body)


class LegacyOnlyStore:
    """Store double that only supports legacy receipts, no generation capability."""

    def __init__(self):
        self.recorded = []

    def record_fetch_attempt(self, **metadata):
        self.recorded.append(metadata)
        return {"attempt_id": "legacy-attempt"}

    def promote_fetch_attempt(self, attempt_id):
        return {"status": "stored", "attempt_id": attempt_id}


def _collector(*, store, transport, now):
    from app.research_partitions import load_temporal_partition_contract
    contract = load_temporal_partition_contract("data/research_partitions/frozen-v1.json")
    return ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=transport,
        clock=TrustedClock(now),
        max_attempts=1,
        sleeper=lambda _seconds: None,
        temporal_contract=contract,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
    )


def test_collect_market_session_generation_publishes_four_shards(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = MarketSessionTransport()
    collector = _collector(store=store, transport=transport, now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))

    report = collector.collect_market_session_generation(TRADE_DATE)

    assert report["status"] == "published"
    assert report["trade_date"] == TRADE_DATE
    assert set(report["staged_datasets"]) == set(DATASET_FIELDS)
    assert report["active"]["generation_id"] == report["generation_id"]
    assert {call["dataset"] for call in transport.calls} == set(DATASET_FIELDS)


def test_market_session_collection_never_emits_legacy_receipts(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = MarketSessionTransport()
    collector = _collector(store=store, transport=transport, now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))

    collector.collect_market_session_generation(TRADE_DATE)

    with sqlite3.connect(store.database_path) as connection:
        legacy = connection.execute(
            "SELECT COUNT(*) FROM receipts WHERE dataset IN ('daily','adj_factor','stk_limit','suspend_d')"
        ).fetchone()[0]
        head = connection.execute(
            "SELECT COUNT(*) FROM market_session_generation_head WHERE trade_date = ?",
            (TRADE_DATE,),
        ).fetchone()[0]
    assert legacy == 0
    assert head == 1


def test_market_session_collection_resumes_partial_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = MarketSessionTransport()
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    collector = _collector(store=store, transport=transport, now=now)

    first = collector.collect_market_session_generation(TRADE_DATE)
    # A second pass for the same trade_date reuses the published head.
    second = collector.collect_market_session_generation(TRADE_DATE, resume=True)
    assert first["generation_id"] == second["generation_id"]


def test_collector_refuses_legacy_fallback_without_generation_capability():
    store = LegacyOnlyStore()
    transport = MarketSessionTransport()
    collector = _collector(store=store, transport=transport, now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc))
    with pytest.raises(Exception, match="generation"):
        collector.collect_market_session_generation(TRADE_DATE)
    assert store.recorded == []


def test_collector_default_vintage_matches_store_live_forward(tmp_path):
    # WHY: the collector is the official begin API; its default vintage must be
    # the same frozen enum value the store uses, not a free-form string.
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = MarketSessionTransport()
    collector = _collector(
        store=store, transport=transport, now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    )

    report = collector.collect_market_session_generation(TRADE_DATE)

    assert report["active"]["vintage"] == "live_forward"
    assert report["active"]["final_oos_eligible"] is False
