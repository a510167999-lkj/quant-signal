import json
import sqlite3
import threading
from collections import deque
from datetime import datetime, timezone

import pytest

from app.research_partitions import load_temporal_partition_contract
from app.research_pit_collector import (
    ControlledTushareCollector,
    HttpEntityResponse,
    PITCollectionError,
    build_bak_basic_specs,
)
from app.research_pit_store import NORMALIZED_FIELDS, PITReceiptStore


TOKEN = "membership-collector-test-token"
ANCHOR = "2016-09-30"
TARGET = "2016-10-10"
BASE = datetime(2016, 10, 10, 8, tzinfo=timezone.utc)


def _native_body(dataset, rows, *, code=0, fields=None):
    return json.dumps(
        {
            "request_id": f"membership-{dataset}",
            "code": code,
            "msg": "" if code == 0 else "api failure",
            "data": {
                "fields": list(fields or NORMALIZED_FIELDS[dataset]),
                "items": rows,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _response(body, *, complete=True, status=200):
    return HttpEntityResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=body,
        body_complete=complete,
    )


def _wire_date(value):
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _rows(api_name, params):
    if api_name == "trade_cal":
        return [[params["exchange"], params["start_date"], 1, "20160930"]]
    if api_name == "stock_basic":
        if (params["exchange"], params["list_status"]) == ("SSE", "L"):
            return [
                [
                    "600001.SH",
                    "600001",
                    "Anchor Co",
                    "SSE",
                    "主板",
                    "L",
                    "20000101",
                    None,
                ]
            ]
        return []
    compact = params["trade_date"]
    if api_name == "bak_basic":
        if _wire_date(compact) == TARGET:
            return []
        return [[compact, "600001.SH", "Anchor Co", "Industry", "20000101"]]
    codes = ("600001.SH", "600002.SH")
    if api_name == "daily":
        return [
            [code, compact, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100] for code in codes
        ]
    if api_name == "adj_factor":
        return [[code, compact, 1.5] for code in codes]
    if api_name == "stk_limit":
        return [[compact, code, 10.0, 11.0, 9.0] for code in codes]
    if api_name == "suspend_d":
        return []
    raise AssertionError(f"unexpected API: {api_name}")


class TrustedClock:
    def __init__(self):
        self.monotonic = 0

    def assert_synchronized(self):
        return {"source": "membership-test-clock", "synchronized": True}

    def now_utc(self):
        return BASE

    def monotonic_ns(self):
        self.monotonic += 1_000_000
        return self.monotonic


class ScenarioTransport:
    def __init__(self, outcomes_by_key=None):
        self.outcomes = {key: deque(values) for key, values in dict(outcomes_by_key or {}).items()}
        self.calls = []

    @staticmethod
    def _key(api_name, params):
        if api_name == "stock_basic":
            return api_name, f"{params['exchange']}:{params['list_status']}"
        if api_name == "trade_cal":
            return api_name, str(params["exchange"])
        return api_name, _wire_date(params["trade_date"])

    def post(self, *, url, headers, body, timeout_s, max_body_bytes):
        del headers, timeout_s, max_body_bytes
        request = json.loads(body)
        api_name = request["api_name"]
        params = dict(request["params"])
        key = self._key(api_name, params)
        self.calls.append({"api_name": api_name, "params": params, "url": url})
        scripted = self.outcomes.get(key)
        if scripted is None:
            return _response(_native_body(api_name, _rows(api_name, params)))
        if not scripted:
            raise AssertionError(f"scripted outcomes exhausted for {key}")
        outcome = scripted.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _collector(
    store,
    transport,
    *,
    sleeper=lambda _seconds: None,
    source_profile="fixture-source",
):
    contract = load_temporal_partition_contract("data/research_partitions/frozen-v1.json")
    return ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://data.example.test",
        allowed_hosts=("data.example.test",),
        transport=transport,
        clock=TrustedClock(),
        max_attempts=3,
        sleeper=sleeper,
        source_profile=source_profile,
        request_protocol="tushare-path-per-interface/v1",
        temporal_contract=contract,
        temporal_role="development",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date=ANCHOR,
        temporal_end_date=TARGET,
        workers=1,
    )


def _target_attempts(store):
    return store.fetch_attempts(partition_key=TARGET)


def _assert_no_membership_head(store):
    assert store.active_membership_generation(TARGET) is None
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM membership_session_head WHERE trade_date=?",
                (TARGET,),
            ).fetchone()[0]
            == 0
        )


def _empty_response():
    return _response(_native_body("bak_basic", []))


def test_membership_snapshot_wrapper_accepts_only_bounded_raw_empty_cohort(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = ScenarioTransport(
        {("bak_basic", TARGET): [_empty_response(), _empty_response(), _empty_response()]}
    )
    collector = _collector(store, transport)

    result = collector.fetch_membership_snapshot(build_bak_basic_specs([TARGET])[0])

    assert result["status"] == "semantic_empty"
    assert len(result["attempts"]) == 3
    assert len(transport.calls) == 3
    attempts = _target_attempts(store)
    assert len(attempts) == 3
    assert {attempt["error_kind"] for attempt in attempts} == {"invalid_json"}
    assert {attempt["error_message"] for attempt in attempts} == {"planned response is empty"}
    assert {attempt["terminal_status"] for attempt in attempts} == {"invalid_json"}
    assert len({attempt["request_semantics_sha256"] for attempt in attempts}) == 1
    assert all(len(attempt["promotion_events"]) == 1 for attempt in attempts)
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (TARGET,),
            ).fetchone()
            is None
        )


def _bad_cohort(case):
    empty = _empty_response()
    if case == "mixed_nonempty":
        nonempty = _response(
            _native_body(
                "bak_basic",
                [["20161010", "600001.SH", "Target Co", "Industry", "20000101"]],
            )
        )
        return [empty, nonempty], ["invalid_json", "stored"]
    if case == "api_error":
        return [empty, _response(_native_body("bak_basic", [], code=-1))], [
            "invalid_json",
            "api_error",
        ]
    if case == "transport_error":
        return [empty, TimeoutError("planned timeout"), empty], [
            "invalid_json",
            "transport_error",
            "invalid_json",
        ]
    if case == "malformed":
        return [_response(b"not-json") for _ in range(3)], ["invalid_json"] * 3
    if case == "incomplete":
        return [_response(_native_body("bak_basic", []), complete=False) for _ in range(3)], [
            "invalid_json"
        ] * 3
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    ["mixed_nonempty", "api_error", "transport_error", "malformed", "incomplete"],
)
def test_mixed_or_invalid_membership_cohort_fails_closed(tmp_path, case):
    outcomes, expected_terminals = _bad_cohort(case)
    store = PITReceiptStore(str(tmp_path / case))
    transport = ScenarioTransport({("bak_basic", TARGET): outcomes})
    collector = _collector(store, transport)

    with pytest.raises(PITCollectionError):
        collector.fetch_membership_snapshot(build_bak_basic_specs([TARGET])[0])

    attempts = _target_attempts(store)
    assert len(attempts) == len(expected_terminals)
    assert [attempt["terminal_status"] for attempt in attempts] == expected_terminals
    assert all(len(attempt["promotion_events"]) == 1 for attempt in attempts)
    assert len(transport.calls) == len(expected_terminals)
    _assert_no_membership_head(store)


def test_collect_publishes_target_market_and_quarantined_membership_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = ScenarioTransport(
        {("bak_basic", TARGET): [_empty_response(), _empty_response(), _empty_response()]}
    )
    collector = _collector(store, transport)
    anchor = collector.fetch_partition(build_bak_basic_specs([ANCHOR])[0])
    assert anchor["status"] == "stored"
    transport.calls.clear()

    report = collector.collect(start_date=TARGET, end_date=TARGET, resume=True)

    assert len(transport.calls) == 17
    assert report["open_sessions"] == [TARGET]
    assert report["open_session_count"] == 1
    assert report["exact_snapshot_session_count"] == 0
    assert report["semantic_empty_session_count"] == 1
    assert report["pre_anchor_session_count"] == 0
    assert report["membership_authority_count"] == 1
    assert report["derived_membership_generation_count"] == 1
    assert report["market_session_generation_count"] == 1
    assert report["controlled_request_lineage_complete"] is True
    assert TOKEN not in json.dumps(report, ensure_ascii=False, allow_nan=False)

    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (TARGET,),
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM market_session_generation_shards AS shard "
                "JOIN market_session_generation_head AS head "
                "ON head.generation_id=shard.generation_id WHERE head.trade_date=?",
                (TARGET,),
            ).fetchone()[0]
            == 4
        )
    active = store.active_membership_generation(TARGET)
    assert active is not None
    assert active["source_kind"] == "causal_carry_forward"
    assert active["signal_session_eligible"] is False
    rows = {row["ts_code"]: row for row in active["rows"]}
    assert set(rows) == {"600001.SH", "600002.SH"}
    assert rows["600001.SH"]["membership_present"] is True
    assert rows["600001.SH"]["unknown_metadata"] is False
    assert rows["600002.SH"]["membership_present"] is False
    assert rows["600002.SH"]["unknown_metadata"] is True
    assert all(row["signal_eligible"] is False for row in rows.values())
    assert len(active["manifest_sha256"]) == 64
    assert len(active["lineage_sha256"]) == 64


def test_resume_reuses_sparse_membership_and_stock_market_heads_without_network(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = ScenarioTransport(
        {("bak_basic", TARGET): [_empty_response(), _empty_response(), _empty_response()]}
    )
    collector = _collector(store, transport)
    collector.fetch_partition(build_bak_basic_specs([ANCHOR])[0])
    first = collector.collect(start_date=TARGET, end_date=TARGET, resume=True)
    first_heads = {
        "stock": first["generation_id"],
        "market": first["market_session_generations"][0]["generation_id"],
        "membership": store.active_membership_generation(TARGET)["generation_id"],
    }
    transport.calls.clear()

    def fail_on_post(**_kwargs):
        raise AssertionError("resume must not make a network request")

    transport.post = fail_on_post
    second = collector.collect(start_date=TARGET, end_date=TARGET, resume=True)

    assert transport.calls == []
    assert second["attempt_count"] == 0
    assert second["stock_basic_generation"]["reused_published"] is True
    assert second["market_session_generations"][0]["generation_id"] == first_heads["market"]
    assert store.active_membership_generation(TARGET)["generation_id"] == first_heads["membership"]


def test_membership_snapshot_cancellation_stops_retry_without_head(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = ScenarioTransport({("bak_basic", TARGET): [_empty_response()]})
    cancellation = threading.Event()

    def cancel_after_first_retry(_seconds):
        cancellation.set()

    collector = _collector(store, transport, sleeper=cancel_after_first_retry)
    collector._active_cancellation = cancellation
    try:
        with pytest.raises(PITCollectionError, match="cancel|concurrent"):
            collector.fetch_membership_snapshot(build_bak_basic_specs([TARGET])[0])
    finally:
        collector._active_cancellation = None
    assert len(transport.calls) == 1
    _assert_no_membership_head(store)


def test_market_failure_prevents_membership_publication(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = ScenarioTransport(
        {
            ("bak_basic", TARGET): [_empty_response(), _empty_response(), _empty_response()],
            ("stk_limit", TARGET): [TimeoutError("planned market failure")] * 3,
        }
    )
    collector = _collector(store, transport)
    collector.fetch_partition(build_bak_basic_specs([ANCHOR])[0])

    with pytest.raises(PITCollectionError):
        collector.collect(start_date=TARGET, end_date=TARGET, resume=True)

    _assert_no_membership_head(store)
    assert store.active_market_session_generation(TARGET) is None


def test_changed_source_authority_does_not_zero_network_reuse_old_membership(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first_transport = ScenarioTransport(
        {("bak_basic", TARGET): [_empty_response(), _empty_response(), _empty_response()]}
    )
    first = _collector(store, first_transport)
    first.fetch_partition(build_bak_basic_specs([ANCHOR])[0])
    first.collect(start_date=TARGET, end_date=TARGET, resume=True)

    second_transport = ScenarioTransport(
        {("bak_basic", TARGET): [TimeoutError("authority changed")] * 3}
    )
    second = _collector(store, second_transport, source_profile="other-source")
    with pytest.raises(PITCollectionError):
        second.fetch_membership_snapshot(build_bak_basic_specs([TARGET])[0], resume=True)
    assert len(second_transport.calls) == 3
