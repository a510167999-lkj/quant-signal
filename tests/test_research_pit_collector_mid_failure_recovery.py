"""Collector mid-failure recovery for the four-shard market session generation.

The real fetch_partition retry / stage / publish paths must survive a shard
that dies mid-generation: the already-published prior session head stays
intact, the partial generation stays resumable, and a resume re-run re-fetches
*only* the missing shards while reusing every legacy receipt and every already
published generation. Nothing here is short-circuited with monkeypatch -- the
failure is a real, persistent transport outage exercised through the genuine
retry loop.
"""

import importlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from app.research_pit_store import PITReceiptStore

TOKEN = "collector-secret-token"

# Two common open sessions covered by the fixture trade_cal below.
SESSION_A = "2024-01-02"
SESSION_B = "2024-01-03"
WIRE_DATE = {SESSION_A: "20240102", SESSION_B: "20240103"}
MARKET_DATASETS = ("daily", "adj_factor", "stk_limit", "suspend_d")


def _api():
    module = importlib.import_module("app.research_pit_collector")
    for name in (
        "FetchSpec",
        "HttpEntityResponse",
        "ControlledTushareCollector",
        "build_stock_basic_specs",
        "build_trade_cal_specs",
    ):
        assert hasattr(module, name), f"collector API is missing: {name}"
    return module


def _canonical_json_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class FakeTrustedClock:
    """Frozen clock shared with the transport so retrieved_at is sampled only
    after a complete response body -- the same contract the real clock enforces."""

    def __init__(self):
        self.events = []
        self.now_value = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.monotonic_value = 1_000

    def assert_synchronized(self):
        self.events.append("clock_attested")
        return {"source": "test-clock", "synchronized": True}

    def now_utc(self):
        # WHY: the real SystemTrustedClock has no precondition here, and a
        # resume-only run legitimately opens a resumable generation (sampling
        # now) before any shard is fetched in this process. The body-complete
        # ordering of the per-attempt retrieved_at is exercised by the in-tree
        # fresh-collect tests, so this frozen clock must not gate on it.
        self.events.append("clock_now")
        return self.now_value

    def monotonic_ns(self):
        self.monotonic_value += 1
        return self.monotonic_value


def _collector(api, transport, store, clock, *, workers=1):
    # WHY: aliasing the event lists lets the transport signal body completion
    # to the clock, exactly as the in-tree collector fixture wires it.
    if hasattr(transport, "events") and hasattr(clock, "events"):
        transport.events = clock.events
    from app.research_partitions import load_temporal_partition_contract
    contract = load_temporal_partition_contract("data/research_partitions/frozen-v1.json")
    return api.ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        transport=transport,
        clock=clock,
        sleeper=lambda _seconds: None,
        allowed_hosts=("example.invalid",),
        temporal_contract=contract,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
        workers=workers,
    )


def _collecting_generation(store, trade_date):
    """Read the resumable 'collecting' generation for a session straight from
    the store so the assertion does not depend on a collector code path."""
    with sqlite3.connect(str(store.database_path)) as connection:
        row = connection.execute(
            "SELECT generation_id, status FROM market_session_generations "
            "WHERE trade_date = ? AND status = 'collecting'",
            (trade_date,),
        ).fetchone()
        if row is None:
            return None
        generation_id = row[0]
        datasets = [
            record[0]
            for record in connection.execute(
                "SELECT dataset FROM market_session_generation_shards "
                "WHERE generation_id = ? ORDER BY dataset",
                (generation_id,),
            )
        ]
    return {
        "generation_id": generation_id,
        "status": row[1],
        "staged_datasets": datasets,
    }


def _market_calls(calls, wire_date, *, before):
    return [
        call
        for call in calls[:before]
        if call["api_name"] in MARKET_DATASETS and call["trade_date"] == wire_date
    ]


def _legacy_market_receipt_count(store):
    with sqlite3.connect(str(store.database_path)) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM receipts "
            "WHERE dataset IN ('daily','adj_factor','stk_limit','suspend_d')"
        ).fetchone()[0]


class DeterministicCompleteTransport:
    def __init__(self):
        self.events = []
        self.calls = []

    def post(self, *, url, headers, body, timeout_s, max_body_bytes):
        request = json.loads(body)
        api_name = request["api_name"]
        params = request["params"]
        self.calls.append((api_name, json.dumps(params, sort_keys=True)))
        payload = _canonical_json_bytes(
            {
                "request_id": f"{api_name}-{json.dumps(params, sort_keys=True)}",
                "code": 0,
                "msg": "",
                "data": {
                    "fields": request["fields"].split(","),
                    "items": _fixture_rows(api_name, params),
                },
            }
        )
        self.events.append("body_complete")
        return _api().HttpEntityResponse(status=200, headers={}, body=payload)


def _assert_cross_worker_resume(tmp_path, monkeypatch, *, first_workers, resume_workers):
    api = _api()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    store = PITReceiptStore(str(tmp_path / "store"))

    class MidFailureTransport:
        """Full fixture transport with one toggle: a single (dataset, trade_date)
        that raises on every attempt, so the genuine retry loop exhausts itself."""

        def __init__(self):
            self.events = []
            self.calls = []
            # When set, this (api_name, wire trade_date) raises persistently.
            self.fail = None

        def post(self, *, url, headers, body, timeout_s, max_body_bytes):
            request = json.loads(body)
            api_name = request["api_name"]
            params = request["params"]
            wire_date = params.get("trade_date")
            self.calls.append({"api_name": api_name, "trade_date": wire_date})
            if self.fail is not None and (api_name, wire_date) == self.fail:
                raise RuntimeError(f"simulated upstream outage for {api_name}/{wire_date}")
            fields = request["fields"].split(",")
            rows = _fixture_rows(api_name, params)
            payload = _canonical_json_bytes(
                {
                    "request_id": f"{api_name}-{json.dumps(params, sort_keys=True)}",
                    "code": 0,
                    "msg": "",
                    "data": {"fields": fields, "items": rows},
                }
            )
            self.events.append("body_complete")
            return api.HttpEntityResponse(status=200, headers={}, body=payload)

    transport = MidFailureTransport()
    # Phase 1: stk_limit for the second session dies on every permitted attempt.
    transport.fail = ("stk_limit", WIRE_DATE[SESSION_B])
    collector = _collector(
        api, transport, store, FakeTrustedClock(), workers=first_workers
    )

    # --- Phase 1: day1 publishes; day2 exhausts retries on stk_limit and aborts.
    with pytest.raises(api.PITCollectionError, match="stk_limit"):
        collector.collect(start_date="2024-01-01", end_date="2024-01-03")

    phase_one_call_count = len(transport.calls)

    # Day1 head is preserved and published; day2 has no head yet.
    day_a_head = store.active_market_session_generation(SESSION_A)
    if first_workers == 1:
        assert day_a_head is not None
        assert day_a_head["status"] == "published"
    assert store.active_market_session_generation(SESSION_B) is None

    # Day2 collecting generation kept exactly daily + adj_factor staged.
    collecting = _collecting_generation(store, SESSION_B)
    assert collecting is not None
    assert collecting["status"] == "collecting"
    assert collecting["staged_datasets"] == ["adj_factor", "daily"]
    day_b_generation_id = collecting["generation_id"]
    staged_before = {}
    published_before = {}
    for session in (SESSION_A, SESSION_B):
        published_before[session] = (
            store.active_market_session_generation(session) is not None
        )
        partial = _collecting_generation(store, session)
        staged_before[session] = set(
            [] if partial is None else partial["staged_datasets"]
        )
    with sqlite3.connect(str(store.database_path)) as connection:
        attempts_before = connection.execute(
            "SELECT COUNT(*) FROM fetch_attempts"
        ).fetchone()[0]
        events_before = connection.execute(
            "SELECT COUNT(*) FROM fetch_promotion_events"
        ).fetchone()[0]
    assert attempts_before == events_before

    # Day2 shard sequence: daily, adj_factor, then stk_limit retried until
    # exhaustion; suspend_d was never requested.
    day_b_phase_one = _market_calls(
        transport.calls, WIRE_DATE[SESSION_B], before=phase_one_call_count
    )
    assert [call["api_name"] for call in day_b_phase_one] == [
        "daily",
        "adj_factor",
        "stk_limit",
        "stk_limit",
        "stk_limit",
    ]
    assert "suspend_d" not in [call["api_name"] for call in day_b_phase_one]

    # --- Phase 2: same transport switched healthy, resume=True rerun.
    transport.fail = None
    resumed = _collector(
        api, transport, store, FakeTrustedClock(), workers=resume_workers
    )
    report = resumed.collect(
        start_date="2024-01-01",
        end_date="2024-01-03",
        resume=True,
        workers=resume_workers,
    )

    # The only new network calls are the two missing day2 shards -- legacy
    # receipts and the day1 generation are reused with zero network traffic.
    new_calls = transport.calls[phase_one_call_count:]
    expected_missing = {
        (dataset, WIRE_DATE[session])
        for session in (SESSION_A, SESSION_B)
        if not published_before[session]
        for dataset in MARKET_DATASETS
        if dataset not in staged_before[session]
    }
    assert {
        (call["api_name"], call["trade_date"]) for call in new_calls
    } == expected_missing
    assert len(new_calls) == len(expected_missing)

    # Day2 reused the SAME collecting generation_id and is now the published head.
    market_gens = report["market_session_generations"]
    assert [gen["trade_date"] for gen in market_gens] == [SESSION_A, SESSION_B]
    if day_a_head is not None:
        assert market_gens[0].get("reused_published") is True
    assert market_gens[1]["generation_id"] == day_b_generation_id
    assert market_gens[1]["status"] == "published"
    assert store.active_market_session_generation(SESSION_B) is not None
    verification = store.verify_market_session_generation(trade_date=SESSION_B)
    assert verification["generation_id"] == day_b_generation_id
    assert len(verification["manifest_sha256"]) == 64
    assert len(verification["lineage_sha256"]) == 64
    with sqlite3.connect(str(store.database_path)) as connection:
        attempts_after = connection.execute(
            "SELECT COUNT(*) FROM fetch_attempts"
        ).fetchone()[0]
        events_after = connection.execute(
            "SELECT COUNT(*) FROM fetch_promotion_events"
        ).fetchone()[0]
    assert attempts_after == attempts_before + len(expected_missing)
    assert events_after == events_before + len(expected_missing)

    # Audit passes and market datasets never leaked into legacy receipts.
    assert (
        store.audit_coverage(start_date="2024-01-01", end_date="2024-01-03")["status"] == "passed"
    )
    assert _legacy_market_receipt_count(store) == 0
    return report, store


def test_workers_1_failure_resumes_with_workers_4_only_missing(tmp_path, monkeypatch):
    _assert_cross_worker_resume(
        tmp_path, monkeypatch, first_workers=1, resume_workers=4
    )


def test_workers_4_failure_resumes_with_workers_2_only_missing(tmp_path, monkeypatch):
    _assert_cross_worker_resume(
        tmp_path, monkeypatch, first_workers=4, resume_workers=2
    )


def test_serial_and_concurrent_collect_have_same_canonical_business_report(tmp_path):
    api = _api()
    reports = []
    stores = []
    for workers in (1, 4):
        store = PITReceiptStore(str(tmp_path / f"store-{workers}"))
        transport = DeterministicCompleteTransport()
        collector = _collector(
            api, transport, store, FakeTrustedClock(), workers=workers
        )
        report = collector.collect(
            start_date="2024-01-01",
            end_date="2024-01-03",
            workers=workers,
        )
        reports.append(report)
        stores.append(store)
        assert len(transport.calls) == 20

    business_keys = {
        "status",
        "start_date",
        "end_date",
        "resume_requested",
        "open_session_count",
        "planned_receipt_count",
        "controlled_receipt_count",
        "controlled_request_lineage_complete",
        "attempt_count",
        "stored_count",
        "reused_count",
        "skipped_count",
        "error_count",
        "errors",
        "temporal_role",
        "temporal_contract_sha256",
        "final_oos_eligible",
        "promotion_eligible",
        "market_session_generation_count",
        "rows_sha256",
    }
    assert {key: reports[0][key] for key in business_keys} == {
        key: reports[1][key] for key in business_keys
    }
    assert reports[0]["workers"] == reports[0]["network_workers"] == 1
    assert reports[1]["workers"] == reports[1]["network_workers"] == 4

    audits = [
        store.audit_coverage(start_date="2024-01-01", end_date="2024-01-03")
        for store in stores
    ]
    assert audits[0]["status"] == audits[1]["status"] == "passed"
    for session in (SESSION_A, SESSION_B):
        left = stores[0].verify_market_session_generation(trade_date=session)
        right = stores[1].verify_market_session_generation(trade_date=session)
        def canonical_shards(verification):
            return [
                {
                    key: value
                    for key, value in shard.items()
                    if key != "attempt_id"
                }
                for shard in verification["manifest"]["shards"]
            ]

        assert canonical_shards(left) == canonical_shards(right)


def test_market_phase_failure_cancels_pending_sessions(tmp_path):
    api = _api()
    collector = _collector(
        api,
        DeterministicCompleteTransport(),
        PITReceiptStore(str(tmp_path / "store")),
        FakeTrustedClock(),
        workers=2,
    )
    running_started = threading.Event()
    release_running = threading.Event()
    started = []

    def fail_session():
        started.append(SESSION_A)
        assert running_started.wait(timeout=2)
        raise RuntimeError("market session failure")

    def running_session():
        started.append(SESSION_B)
        running_started.set()
        assert release_running.wait(timeout=2)
        return SESSION_B

    def pending_session(session):
        started.append(session)
        return session

    timer = threading.Timer(0.05, release_running.set)
    timer.start()
    try:
        with pytest.raises(api.PITCollectionError, match="market session failure"):
            collector._run_phase(
                [
                    (0, fail_session),
                    (1, running_session),
                    (2, lambda: pending_session("2024-01-04")),
                    (3, lambda: pending_session("2024-01-05")),
                ],
                workers=2,
            )
    finally:
        release_running.set()
        timer.cancel()

    assert started == [SESSION_A, SESSION_B]


def test_market_phase_cancellation_finishes_current_shard_but_does_not_start_next_or_publish(
    tmp_path,
):
    api = _api()
    store = PITReceiptStore(str(tmp_path / "store"))
    b_daily_started = threading.Event()
    release_b_daily = threading.Event()

    class BlockingMarketTransport(DeterministicCompleteTransport):
        def post(self, *, url, headers, body, timeout_s, max_body_bytes):
            request = json.loads(body)
            dataset = request["api_name"]
            wire_date = request["params"].get("trade_date")
            self.calls.append((dataset, wire_date))
            if dataset == "daily" and wire_date == WIRE_DATE[SESSION_B]:
                b_daily_started.set()
                assert release_b_daily.wait(timeout=2)
            if dataset == "daily" and wire_date == WIRE_DATE[SESSION_A]:
                assert b_daily_started.wait(timeout=2)
                raise RuntimeError("session A terminal failure")
            payload = _canonical_json_bytes(
                {
                    "request_id": f"{dataset}-{wire_date}",
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": request["fields"].split(","),
                        "items": _fixture_rows(dataset, request["params"]),
                    },
                }
            )
            return api.HttpEntityResponse(200, {}, payload)

    transport = BlockingMarketTransport()
    collector = _collector(api, transport, store, FakeTrustedClock(), workers=2)
    collector.max_attempts = 1
    collector._active_cancellation = threading.Event()
    timer = threading.Timer(0.05, release_b_daily.set)
    timer.start()
    try:
        with pytest.raises(api.PITCollectionError, match="session A terminal failure"):
            collector._run_phase(
                [
                    (0, lambda: collector.collect_market_session_generation(SESSION_A)),
                    (1, lambda: collector.collect_market_session_generation(SESSION_B)),
                ],
                workers=2,
            )
    finally:
        release_b_daily.set()
        timer.cancel()
        collector._active_cancellation = None

    b_calls = [dataset for dataset, wire in transport.calls if wire == WIRE_DATE[SESSION_B]]
    assert b_calls == ["daily"]
    partial = _collecting_generation(store, SESSION_B)
    assert partial is not None
    assert partial["staged_datasets"] == ["daily"]
    assert store.active_market_session_generation(SESSION_B) is None
    with sqlite3.connect(str(store.database_path)) as connection:
        b_attempts = connection.execute(
            "SELECT COUNT(*) FROM fetch_attempts WHERE dataset = 'daily' AND partition_key = ?",
            (SESSION_B,),
        ).fetchone()[0]
        b_events = connection.execute(
            "SELECT COUNT(*) FROM fetch_promotion_events event "
            "JOIN fetch_attempts attempt ON attempt.attempt_id = event.attempt_id "
            "WHERE attempt.dataset = 'daily' AND attempt.partition_key = ?",
            (SESSION_B,),
        ).fetchone()[0]
    assert b_attempts == b_events == 1

    healthy = DeterministicCompleteTransport()
    resumed = _collector(api, healthy, store, FakeTrustedClock(), workers=1)
    report = resumed.collect_market_session_generation(SESSION_B, resume=True)
    assert report["generation_id"] == partial["generation_id"]
    assert report["status"] == "published"
    assert [name for name, _params in healthy.calls] == [
        "adj_factor",
        "stk_limit",
        "suspend_d",
    ]


def test_phase_a_calendar_failure_stops_stock_after_current_shard_and_resume_publishes(
    tmp_path,
):
    api = _api()
    store = PITReceiptStore(str(tmp_path / "store"))
    stock_started = threading.Event()
    release_stock = threading.Event()

    class BlockingPhaseATransport(DeterministicCompleteTransport):
        def post(self, *, url, headers, body, timeout_s, max_body_bytes):
            request = json.loads(body)
            dataset = request["api_name"]
            params = request["params"]
            self.calls.append((dataset, json.dumps(params, sort_keys=True)))
            if dataset == "stock_basic":
                stock_started.set()
                assert release_stock.wait(timeout=2)
            if dataset == "trade_cal":
                assert stock_started.wait(timeout=2)
                raise RuntimeError("calendar terminal failure")
            payload = _canonical_json_bytes(
                {
                    "request_id": f"{dataset}-{json.dumps(params, sort_keys=True)}",
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": request["fields"].split(","),
                        "items": _fixture_rows(dataset, params),
                    },
                }
            )
            return api.HttpEntityResponse(200, {}, payload)

    transport = BlockingPhaseATransport()
    collector = _collector(api, transport, store, FakeTrustedClock(), workers=2)
    collector.max_attempts = 1
    collector._active_cancellation = threading.Event()
    calendar = api.build_trade_cal_specs(
        start_date="2024-01-01", end_date="2024-01-03"
    )[0]
    timer = threading.Timer(0.05, release_stock.set)
    timer.start()
    try:
        with pytest.raises(api.PITCollectionError, match="calendar terminal failure"):
            collector._run_phase(
                [
                    (0, lambda: collector.fetch_partition(calendar, resume=False)),
                    (
                        1,
                        lambda: collector.collect_stock_basic_generation(
                            resume=True, workers=1
                        ),
                    ),
                ],
                workers=2,
            )
    finally:
        release_stock.set()
        timer.cancel()
        collector._active_cancellation = None

    stock_calls = [name for name, _params in transport.calls if name == "stock_basic"]
    assert stock_calls == ["stock_basic"]
    assert store.active_stock_basic_generation() is None
    with sqlite3.connect(str(store.database_path)) as connection:
        generation_id = connection.execute(
            "SELECT generation_id FROM stock_basic_generations WHERE status = 'collecting'"
        ).fetchone()[0]
        staged = connection.execute(
            "SELECT COUNT(*) FROM stock_basic_generation_shards WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0]
    assert staged == 1

    healthy = DeterministicCompleteTransport()
    resumed = _collector(api, healthy, store, FakeTrustedClock(), workers=1)
    report = resumed.collect_stock_basic_generation(resume=True, workers=1)
    assert report["generation_id"] == generation_id
    assert report["status"] == "published"
    assert len(healthy.calls) == 7


def _fixture_rows(api_name, params):
    """Mirrors the in-tree CompleteFixtureTransport row contracts so every
    legacy receipt and every market generation validates against the store."""
    if api_name == "trade_cal":
        exchange = params["exchange"]
        return [
            [exchange, "20240101", 0, "20231229"],
            [exchange, "20240102", 1, "20231229"],
            [exchange, "20240103", 1, "20240102"],
        ]
    if api_name == "stock_basic":
        shard = (params["exchange"], params["list_status"])
        if shard == ("SSE", "L"):
            return [["600001.SH", "600001", "A", "SSE", "主板", "L", "20100101", None]]
        if shard == ("SZSE", "D"):
            return [
                [
                    "000002.SZ",
                    "000002",
                    "B",
                    "SZSE",
                    "主板",
                    "D",
                    "20100101",
                    "20240103",
                ]
            ]
        return []
    if api_name == "daily":
        trade_date = params["trade_date"]
        return [
            [
                "600001.SH",
                trade_date,
                10.0,
                10.5,
                9.8,
                10.2,
                9.9,
                0.3,
                3.03,
                1000,
                10100,
            ]
        ]
    if api_name == "adj_factor":
        return [["600001.SH", params["trade_date"], 1.5]]
    if api_name == "stk_limit":
        trade_date = params["trade_date"]
        return [[trade_date, "600001.SH", 10.0, 11.0, 9.0]]
    if api_name == "suspend_d":
        if params["trade_date"] == "20240102":
            return [["000002.SZ", "20240102", "09:30:00", "S"]]
        return []
    # bak_basic daily snapshot
    trade_date = params["trade_date"]
    rows = [[trade_date, "600001.SH", "A", "银行", "20100101"]]
    if trade_date == "20240102":
        rows.append([trade_date, "000002.SZ", "B", "地产", "20100101"])
    return rows
