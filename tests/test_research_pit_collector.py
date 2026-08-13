import hashlib
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import jobs
from app.research_partitions import load_temporal_partition_contract
from app.research_pit_store import PITReceiptStore


def test_private_phase_runner_validates_worker_range_and_preserves_serial_calls():
    api = _api()
    collector = _collector(
        api, ScriptedTransport([]), object(), FakeTrustedClock()
    )
    calls = []
    tasks = [
        (index, lambda index=index: calls.append(index) or f"result-{index}")
        for index in range(3)
    ]

    for workers in (0, 9, True, 1.5, "2"):
        with pytest.raises(api.PITCollectionError, match="workers.*1.*8"):
            collector._run_phase(tasks, workers=workers)

    assert collector._run_phase([], workers=8) == []

    assert collector._run_phase(tasks, workers=1) == [
        "result-0",
        "result-1",
        "result-2",
    ]
    assert calls == [0, 1, 2]


def test_private_phase_runner_overlaps_workers_and_returns_plan_order():
    api = _api()
    collector = _collector(
        api, ScriptedTransport([]), object(), FakeTrustedClock()
    )
    barrier = threading.Barrier(3)
    release = [threading.Event() for _ in range(3)]
    finished = [threading.Event() for _ in range(3)]
    completed = []

    def task(index):
        barrier.wait(timeout=2)
        assert release[index].wait(timeout=2)
        completed.append(index)
        finished[index].set()
        return f"result-{index}"

    def release_in_reverse_order():
        release[2].set()
        assert finished[2].wait(timeout=2)
        release[1].set()
        assert finished[1].wait(timeout=2)
        release[0].set()

    controller = threading.Thread(target=release_in_reverse_order)
    controller.start()

    results = collector._run_phase(
        [(index, lambda index=index: task(index)) for index in range(3)],
        workers=3,
    )
    controller.join(timeout=2)

    assert not controller.is_alive()
    assert completed == [2, 1, 0]
    assert results == ["result-0", "result-1", "result-2"]


def test_private_phase_runner_cancels_pending_and_waits_for_started_tasks():
    api = _api()
    collector = _collector(
        api, ScriptedTransport([]), object(), FakeTrustedClock()
    )
    failing_started = threading.Event()
    running_started = threading.Event()
    release_running = threading.Event()
    events = []

    def fail():
        events.append("fail-started")
        failing_started.set()
        assert running_started.wait(timeout=2)
        raise RuntimeError(f"upstream exposed {TOKEN}")

    def running():
        assert failing_started.wait(timeout=2)
        events.append("running-started")
        running_started.set()
        assert release_running.wait(timeout=2)
        events.append("running-terminal")
        raise RuntimeError(f"second failure also exposed {TOKEN}")

    def pending():
        events.append("pending-started")
        return "should-not-run"

    timer = threading.Timer(0.1, release_running.set)
    timer.start()
    try:
        with pytest.raises(api.PITCollectionError) as exc_info:
            collector._run_phase(
                [(0, fail), (1, running), (2, pending), (3, pending)], workers=2
            )
    finally:
        timer.cancel()
        release_running.set()

    assert TOKEN not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
    assert TOKEN not in " ".join(exc_info.value.__notes__)
    assert "additional concurrent failure" in " ".join(exc_info.value.__notes__)
    assert "running-terminal" in events
    assert "pending-started" not in events


def test_private_phase_runner_identifies_the_only_failing_task_as_primary():
    api = _api()
    collector = _collector(api, ScriptedTransport([]), object(), FakeTrustedClock())
    barrier = threading.Barrier(3)

    def task(index):
        barrier.wait(timeout=2)
        if index == 7:
            raise RuntimeError(f"failure-{index}")
        return index

    with pytest.raises(api.PITCollectionError) as exc_info:
        collector._run_phase(
            [
                (7, lambda: task(7)),
                (2, lambda: task(2)),
                (5, lambda: task(5)),
            ],
            workers=3,
        )

    assert "task 7" in str(exc_info.value)
    assert not getattr(exc_info.value, "__notes__", [])


def test_private_phase_runner_reports_multiple_failures_in_stable_index_order():
    api = _api()
    collector = _collector(api, ScriptedTransport([]), object(), FakeTrustedClock())
    barrier = threading.Barrier(3)

    def fail(index):
        barrier.wait(timeout=2)
        raise RuntimeError(f"failure-{index}-{TOKEN}")

    with pytest.raises(api.PITCollectionError) as exc_info:
        collector._run_phase(
            [
                (7, lambda: fail(7)),
                (2, lambda: fail(2)),
                (5, lambda: fail(5)),
            ],
            workers=3,
        )

    primary_index = int(str(exc_info.value).split("phase task ", 1)[1].split()[0])
    notes = exc_info.value.__notes__
    note_indexes = [
        int(note.split("at task ", 1)[1].split(":", 1)[0]) for note in notes
    ]
    assert {primary_index, *note_indexes} == {2, 5, 7}
    assert note_indexes == sorted({2, 5, 7} - {primary_index})
    assert TOKEN not in str(exc_info.value)
    assert TOKEN not in " ".join(notes)
    assert "[REDACTED]" in str(exc_info.value) + " ".join(notes)


def test_serial_phase_redacts_task_failure_and_handles_base_exceptions():
    api = _api()
    collector = _collector(api, ScriptedTransport([]), object(), FakeTrustedClock())

    with pytest.raises(api.PITCollectionError) as exc_info:
        collector._run_phase(
            [(0, lambda: (_ for _ in ()).throw(SystemExit(TOKEN)))], workers=1
        )
    assert TOKEN not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)

    with pytest.raises(KeyboardInterrupt) as interrupt:
        collector._run_phase(
            [(0, lambda: (_ for _ in ()).throw(KeyboardInterrupt(TOKEN)))],
            workers=1,
        )
    assert interrupt.value.args == ()


def test_single_flight_base_exception_cleans_loading_and_wakes_waiters():
    api = _api()
    clock_entered = threading.Event()
    release_clock = threading.Event()

    class RecoveringClock(FakeTrustedClock):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def assert_synchronized(self):
            self.calls += 1
            if self.calls == 1:
                clock_entered.set()
                assert release_clock.wait(timeout=2)
                raise SystemExit(TOKEN)
            return {"source": "test-clock", "synchronized": True}

    clock = RecoveringClock()
    collector = _collector(api, ScriptedTransport([]), object(), clock)
    outcomes = []

    def attest():
        try:
            outcomes.append(collector._attest_clock())
        except BaseException as exc:
            outcomes.append(exc)

    first = threading.Thread(target=attest)
    second = threading.Thread(target=attest)
    first.start()
    assert clock_entered.wait(timeout=2)
    second.start()
    release_clock.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert clock.calls == 2
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(errors) == 1 and isinstance(errors[0], api.PITCollectionError)
    assert TOKEN not in str(errors[0])
    assert any(isinstance(outcome, dict) for outcome in outcomes)
    assert collector._clock_attestation_loading is False


def test_resume_index_base_exception_cleans_loading_and_wakes_waiters():
    api = _api()
    index_entered = threading.Event()
    release_index = threading.Event()

    class RecoveringStore(RecordingStore):
        def __init__(self):
            super().__init__()
            self.index_calls = 0

        def controlled_receipt_index(self):
            self.index_calls += 1
            if self.index_calls == 1:
                index_entered.set()
                assert release_index.wait(timeout=2)
                raise GeneratorExit(TOKEN)
            return {}

    store = RecoveringStore()
    collector = _collector(
        api,
        ScriptedTransport(
            [api.HttpEntityResponse(status=200, headers={}, body=_success_body())]
        ),
        store,
        FakeTrustedClock(),
    )
    outcomes = []

    def fetch():
        try:
            outcomes.append(collector.fetch_partition(_spec(api), resume=True))
        except BaseException as exc:
            outcomes.append(exc)

    first = threading.Thread(target=fetch)
    second = threading.Thread(target=fetch)
    first.start()
    assert index_entered.wait(timeout=2)
    second.start()
    release_index.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert store.index_calls == 2
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(errors) == 1 and isinstance(errors[0], api.PITCollectionError)
    assert TOKEN not in str(errors[0])
    assert any(isinstance(outcome, dict) for outcome in outcomes)
    assert collector._resume_index_loading is False


def test_single_flight_loaders_execute_outside_shared_lock():
    api = _api()
    probe_entered = threading.Event()
    probe_release = threading.Event()
    index_entered = threading.Event()
    index_release = threading.Event()

    class BlockingClock(FakeTrustedClock):
        def assert_synchronized(self):
            probe_entered.set()
            assert probe_release.wait(timeout=2)
            return {"source": "test-clock", "synchronized": True}

    class BlockingStore(RecordingStore):
        def controlled_receipt_index(self):
            index_entered.set()
            assert index_release.wait(timeout=2)
            return {}

    collector = _collector(
        api,
        ScriptedTransport([RuntimeError("stop")]),
        BlockingStore(),
        BlockingClock(),
    )

    clock_thread = threading.Thread(target=collector._attest_clock)
    clock_thread.start()
    assert probe_entered.wait(timeout=2)
    assert collector._shared_state_lock.acquire(timeout=0.1)
    collector._shared_state_lock.release()
    probe_release.set()
    clock_thread.join(timeout=2)

    def fetch_until_stopped():
        with pytest.raises(api.PITCollectionError):
            collector.fetch_partition(_spec(api), resume=True)

    fetch_thread = threading.Thread(target=fetch_until_stopped)
    fetch_thread.start()
    assert index_entered.wait(timeout=2)
    assert collector._shared_state_lock.acquire(timeout=0.1)
    collector._shared_state_lock.release()
    index_release.set()
    fetch_thread.join(timeout=2)

    assert not clock_thread.is_alive()
    assert not fetch_thread.is_alive()


def test_concurrent_phase_keeps_terminal_attempt_evidence_and_cancels_pending(
    tmp_path, monkeypatch
):
    api = _api()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    store = PITReceiptStore(str(tmp_path / "store"))
    failure_started = threading.Event()
    release_success = threading.Event()

    class BlockingSuccessTransport(ScriptedTransport):
        def post(self, **kwargs):
            assert failure_started.wait(timeout=2)
            assert release_success.wait(timeout=2)
            return super().post(**kwargs)

    class SignallingFailureTransport(ScriptedTransport):
        def post(self, **kwargs):
            failure_started.set()
            return super().post(**kwargs)

    success_spec, pending_spec, _, _, failure_spec, *_ = api.build_stock_basic_specs()
    success = _collector(
        api,
        BlockingSuccessTransport(
            [api.HttpEntityResponse(status=200, headers={}, body=_success_body())]
        ),
        store,
        FakeTrustedClock(),
    )
    failure = _collector(
        api,
        SignallingFailureTransport([RuntimeError("outage")] * 3),
        store,
        FakeTrustedClock(),
    )
    pending = _collector(
        api,
        ScriptedTransport(
            [api.HttpEntityResponse(status=200, headers={}, body=_success_body())]
        ),
        store,
        FakeTrustedClock(),
    )
    timer = threading.Timer(0.1, release_success.set)
    timer.start()
    try:
        with pytest.raises(api.PITCollectionError, match="outage"):
            success._run_phase(
                [
                    (0, lambda: success.fetch_partition(success_spec)),
                    (1, lambda: failure.fetch_partition(failure_spec)),
                    (2, lambda: pending.fetch_partition(pending_spec)),
                ],
                workers=2,
            )
    finally:
        timer.cancel()
        release_success.set()

    success_attempts = store.fetch_attempts(
        partition_key=success_spec.partition_key
    )
    failure_attempts = store.fetch_attempts(
        partition_key=failure_spec.partition_key
    )
    pending_attempts = store.fetch_attempts(
        partition_key=pending_spec.partition_key
    )
    assert [attempt["terminal_status"] for attempt in success_attempts] == ["stored"]
    assert [attempt["terminal_status"] for attempt in failure_attempts] == [
        "transport_error",
        "transport_error",
        "transport_error",
    ]
    assert all(len(attempt["promotion_events"]) == 1 for attempt in failure_attempts)
    assert pending_attempts == []

    resumed = _collector(
        api,
        ScriptedTransport(
            [
                api.HttpEntityResponse(status=200, headers={}, body=_success_body()),
                api.HttpEntityResponse(status=200, headers={}, body=_success_body()),
            ]
        ),
        store,
        FakeTrustedClock(),
    )
    resumed_results = resumed._run_phase(
        [
            (0, lambda: resumed.fetch_partition(success_spec, resume=True)),
            (1, lambda: resumed.fetch_partition(failure_spec, resume=True)),
            (2, lambda: resumed.fetch_partition(pending_spec, resume=True)),
        ],
        workers=3,
    )
    assert [result["status"] for result in resumed_results] == [
        "skipped",
        "stored",
        "stored",
    ]
    assert len(store.fetch_attempts(partition_key=success_spec.partition_key)) == 1
    assert len(store.fetch_attempts(partition_key=failure_spec.partition_key)) == 4
    assert len(store.fetch_attempts(partition_key=pending_spec.partition_key)) == 1


def test_thread_shared_attestation_and_resume_index_initialize_once():
    api = _api()

    probe_started = threading.Event()
    release_probe = threading.Event()
    second_clock_observed = threading.Event()

    class CountingStore:
        def __init__(self):
            self.calls = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def controlled_receipt_index(self):
            self.calls += 1
            self.started.set()
            assert self.release.wait(timeout=2)
            return {}

    class CountingClock(FakeTrustedClock):
        def __init__(self):
            super().__init__()
            self.attestations = 0
            self.monotonic_calls = 0

        def assert_synchronized(self):
            self.attestations += 1
            probe_started.set()
            assert release_probe.wait(timeout=2)
            return {"source": "test-clock", "synchronized": True}

        def monotonic_ns(self):
            self.monotonic_calls += 1
            if self.monotonic_calls == 2:
                second_clock_observed.set()
            return super().monotonic_ns()

    clock = CountingClock()
    clock_collector = _collector(api, ScriptedTransport([]), object(), clock)
    attestations = []

    def attest():
        attestations.append(clock_collector._attest_clock())

    first = threading.Thread(target=attest)
    second = threading.Thread(target=attest)
    first.start()
    assert probe_started.wait(timeout=2)
    second.start()
    assert second_clock_observed.wait(timeout=2)
    assert clock.attestations == 1
    release_probe.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert len(attestations) == 2
    assert clock.attestations == 1

    store = CountingStore()
    collector = _collector(api, ScriptedTransport([]), store, FakeTrustedClock())
    collector._attest_clock()
    waiter_entered = threading.Event()
    original_wait = collector._shared_state_changed.wait

    def tracked_wait(*args, **kwargs):
        waiter_entered.set()
        return original_wait(*args, **kwargs)

    collector._shared_state_changed.wait = tracked_wait
    outcomes = []

    def load_resume_index():
        try:
            collector.fetch_partition(_spec(api), resume=True)
        except BaseException as exc:
            outcomes.append(exc)

    first = threading.Thread(target=load_resume_index)
    second = threading.Thread(target=load_resume_index)
    first.start()
    assert store.started.wait(timeout=2)
    second.start()
    assert waiter_entered.wait(timeout=2)
    assert store.calls == 1
    store.release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert store.calls == 1
    assert len(outcomes) == 2


TOKEN = "collector-secret-token"
TEMPORAL_CONTRACT = load_temporal_partition_contract(
    Path("data/research_partitions/frozen-v1.json")
)
STOCK_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
)
CALENDAR_FIELDS = ("exchange", "cal_date", "is_open", "pretrade_date")
DAILY_FIELDS = ("trade_date", "ts_code", "name", "industry", "list_date")


def _api():
    try:
        module = importlib.import_module("app.research_pit_collector")
    except ModuleNotFoundError:
        pytest.fail("app.research_pit_collector is required", pytrace=False)
    required = (
        "FetchSpec",
        "HttpEntityResponse",
        "ControlledTushareCollector",
        "build_stock_basic_specs",
        "build_trade_cal_specs",
    )
    missing = [name for name in required if not hasattr(module, name)]
    assert not missing, f"collector API is missing: {', '.join(missing)}"
    return module


def _canonical_json_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def test_urllib_transport_disables_environment_proxies_and_redirects(monkeypatch):
    api = _api()
    captured = []
    opener = object()
    monkeypatch.setenv("HTTPS_PROXY", "http://environment-proxy.invalid:8080")
    monkeypatch.setattr(
        api,
        "build_opener",
        lambda *handlers: captured.extend(handlers) or opener,
    )

    transport = api.UrllibTushareTransport()

    assert transport._opener is opener
    assert len(captured) == 2
    assert isinstance(captured[0], api.ProxyHandler)
    assert captured[0].proxies == {}
    assert isinstance(captured[1], api._NoRedirectHandler)


def test_urllib_transport_configures_only_explicit_https_proxy(monkeypatch):
    api = _api()
    captured = []
    opener = object()
    monkeypatch.setenv("HTTP_PROXY", "http://environment-http-proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://environment-https-proxy.invalid:8080")
    monkeypatch.setattr(
        api,
        "build_opener",
        lambda *handlers: captured.extend(handlers) or opener,
    )

    transport = api.UrllibTushareTransport(proxy_url="http://127.0.0.1:7897")

    assert transport._opener is opener
    assert len(captured) == 2
    assert isinstance(captured[0], api.ProxyHandler)
    assert captured[0].proxies == {"https": "http://127.0.0.1:7897"}
    assert isinstance(captured[1], api._NoRedirectHandler)


def test_urllib_transport_closes_redirect_response_before_refusing_redirect():
    api = _api()

    class RedirectResponse:
        closed = False

        def close(self):
            self.closed = True

    response = RedirectResponse()

    with pytest.raises(
        api.PITCollectionError,
        match="Tushare transport refused HTTP redirect 302",
    ):
        api._NoRedirectHandler().redirect_request(
            None,
            response,
            302,
            "Found",
            {},
            "https://redirected.invalid",
        )

    assert response.closed is True


def test_urllib_transport_preserves_falsy_custom_opener(monkeypatch):
    api = _api()

    class FalsyOpener:
        def __bool__(self):
            return False

    opener = FalsyOpener()

    def unexpected_build_opener(*_handlers):
        pytest.fail("build_opener must not replace an explicitly supplied opener")

    monkeypatch.setattr(api, "build_opener", unexpected_build_opener)

    transport = api.UrllibTushareTransport(opener)

    assert transport._opener is opener


def _success_body(msg=""):
    return _canonical_json_bytes(
        {
            "request_id": "test",
            "code": 0,
            "msg": msg,
            "data": {"fields": list(STOCK_FIELDS), "items": []},
        }
    )


class FakeTrustedClock:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.now_value = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.monotonic_value = 1_000

    def assert_synchronized(self):
        self.events.append("clock_attested")
        return {"source": "test-clock", "synchronized": True}

    def now_utc(self):
        assert "body_complete" in self.events, "retrieved_at sampled before body completion"
        self.events.append("clock_now")
        return self.now_value

    def monotonic_ns(self):
        self.monotonic_value += 1
        return self.monotonic_value


class ScriptedTransport:
    def __init__(self, outcomes, events=None):
        self.outcomes = list(outcomes)
        self.events = events if events is not None else []
        self.calls = []

    def post(self, *, url, headers, body, timeout_s, max_body_bytes):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_s": timeout_s,
                "max_body_bytes": max_body_bytes,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome.body_complete is True
        self.events.append("body_complete")
        return outcome


class MarketShardTransport:
    def __init__(self, api):
        self.api = api
        self.calls = []
        self.events = []

    def post(self, **kwargs):
        request = json.loads(kwargs["body"])
        api_name = request["api_name"]
        trade_date = request["params"]["trade_date"]
        self.calls.append(api_name)
        self.events.append("body_complete")
        items = {
            "daily": [
                ["600001.SH", trade_date, 10, 11, 9, 10, 10, 0, 0, 1, 1]
            ],
            "adj_factor": [["600001.SH", trade_date, 1]],
            "stk_limit": [[trade_date, "600001.SH", 10, 11, 9]],
            "suspend_d": [],
        }[api_name]
        return self.api.HttpEntityResponse(
            status=200,
            headers={},
            body=_canonical_json_bytes(
                {
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": request["fields"].split(","),
                        "items": items,
                    },
                }
            ),
        )


class RecordingStore:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.attempts = []
        self.promotions = []

    def record_fetch_attempt(self, **metadata):
        self.events.append("attempt_recorded")
        self.attempts.append(metadata)
        return f"attempt-{len(self.attempts)}"

    def promote_fetch_attempt(self, attempt_id):
        self.events.append("attempt_promoted")
        self.promotions.append(attempt_id)
        return {"status": "stored", "attempt_id": attempt_id}


def _spec(api):
    return api.FetchSpec(
        dataset="stock_basic",
        partition_key="SSE:L",
        api_name="stock_basic",
        wire_params={"exchange": "SSE", "list_status": "L"},
        receipt_params={"exchange": "SSE", "list_status": "L"},
        fields=STOCK_FIELDS,
        row_cap=6000,
    )


def _collector(
    api,
    transport,
    store,
    clock,
    sleeper=lambda _seconds: None,
    *,
    network_route="direct",
    proxy_endpoint=None,
    source_profile="official",
    api_url="https://example.invalid/tushare",
    allowed_hosts=("example.invalid",),
    request_protocol="tushare-root-post/v1",
    temporal_role="contaminated_diagnostic",
    temporal_start_date="2024-01-01",
    temporal_end_date="2026-07-03",
    workers=1,
    row_cap_overrides=None,
):
    if hasattr(transport, "events") and hasattr(clock, "events"):
        transport.events = clock.events
    return api.ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url=api_url,
        transport=transport,
        clock=clock,
        sleeper=sleeper,
        allowed_hosts=allowed_hosts,
        network_route=network_route,
        proxy_endpoint=proxy_endpoint,
        source_profile=source_profile,
        request_protocol=request_protocol,
        row_cap_overrides=row_cap_overrides,
        temporal_contract=(contract := load_temporal_partition_contract(
            Path("data/research_partitions/frozen-v1.json")
        )),
        temporal_role=temporal_role,
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date=temporal_start_date,
        temporal_end_date=temporal_end_date,
        workers=workers,
    )


def test_jiaoch_profile_rejects_more_than_two_workers_before_any_network_request():
    api = _api()
    transport = ScriptedTransport([])

    with pytest.raises(api.PITCollectionError, match=r"jiaoch.*workers.*1\.\.2"):
        _collector(
            api,
            transport,
            RecordingStore(),
            FakeTrustedClock(),
            source_profile="jiaoch",
            workers=3,
        )

    assert transport.calls == []


@pytest.mark.parametrize("workers", [2, 3, 8])
def test_frozen_network_budget_bounds_actual_transport_requests(workers):
    api = _api()

    class InstrumentedTransport:
        def __init__(self):
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def post(self, *, url, headers, body, timeout_s, max_body_bytes):
            request = json.loads(body)
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.02)
                payload = _canonical_json_bytes(
                    {
                        "request_id": request["params"]["exchange"],
                        "code": 0,
                        "msg": "",
                        "data": {
                            "fields": request["fields"].split(","),
                            "items": [],
                        },
                    }
                )
                return api.HttpEntityResponse(status=200, headers={}, body=payload)
            finally:
                with self.lock:
                    self.active -= 1

    transport = InstrumentedTransport()
    clock = FakeTrustedClock()
    clock.events.append("body_complete")
    collector = _collector(
        api, transport, RecordingStore(), clock, workers=workers
    )
    specs = api.build_stock_basic_specs()

    collector._run_phase(
        [
            (
                index,
                lambda spec=spec: collector.fetch_partition(spec, resume=False),
            )
            for index, spec in enumerate(specs)
        ],
        workers=8,
    )

    assert transport.max_active == workers
    assert collector.workers == workers
    with pytest.raises(AttributeError):
        collector._workers = 1
    semantics = [collector._request_semantics(spec) for spec in specs]
    assert all("workers" not in row and "network_workers" not in row for row in semantics)


def test_semaphore_waiter_cancelled_after_acquire_does_not_post():
    api = _api()
    entered = threading.Event()
    release = threading.Event()

    class FailingTransport:
        def __init__(self):
            self.calls = 0

        def post(self, **_kwargs):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=2)
            raise RuntimeError("terminal source failure")

    transport = FailingTransport()
    store = RecordingStore()
    clock = FakeTrustedClock()
    collector = _collector(
        api,
        transport,
        store,
        clock,
        workers=1,
    )
    collector.max_attempts = 1
    collector._active_cancellation = threading.Event()
    specs = api.build_stock_basic_specs()[:2]
    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        with pytest.raises(api.PITCollectionError, match="terminal source failure"):
            collector._run_phase(
                [
                    (
                        index,
                        lambda spec=spec: collector.fetch_partition(
                            spec, resume=False
                        ),
                    )
                    for index, spec in enumerate(specs)
                ],
                workers=2,
            )
    finally:
        release.set()
        timer.cancel()
        collector._active_cancellation = None

    assert entered.is_set()
    assert transport.calls == 1
    assert len(store.attempts) == 1
    assert store.attempts[0]["partition_key"] == specs[0].partition_key
    assert sum(event == "attempt_recorded" for event in store.events) == 1

    successful = ScriptedTransport(
        [api.HttpEntityResponse(200, {}, _success_body())], events=clock.events
    )
    collector.transport = successful
    resumed = collector.fetch_partition(specs[1], resume=False)
    assert resumed["status"] == "stored"
    assert len(store.attempts) == 2
    assert store.attempts[1]["partition_key"] == specs[1].partition_key
    assert len(successful.calls) == 1


def test_spec_builders_freeze_eight_stock_and_two_calendar_requests():
    api = _api()

    stock_specs = api.build_stock_basic_specs()
    assert all(isinstance(spec, api.FetchSpec) for spec in stock_specs)
    assert [spec.partition_key for spec in stock_specs] == [
        f"{exchange}:{status}"
        for exchange in ("SSE", "SZSE")
        for status in ("L", "D", "P", "G")
    ]
    for spec in stock_specs:
        exchange, status = spec.partition_key.split(":")
        expected_params = {"exchange": exchange, "list_status": status}
        assert spec.dataset == "stock_basic"
        assert spec.api_name == "stock_basic"
        assert spec.wire_params == expected_params
        assert spec.receipt_params == expected_params
        assert spec.fields == STOCK_FIELDS
        assert spec.row_cap == 6000

    calendar_specs = api.build_trade_cal_specs(
        start_date="2024-01-01", end_date="2024-01-31"
    )
    assert [spec.partition_key for spec in calendar_specs] == [
        "SSE:2024-01-01:2024-01-31",
        "SZSE:2024-01-01:2024-01-31",
    ]
    for spec in calendar_specs:
        exchange = spec.partition_key.split(":", 1)[0]
        assert spec.dataset == "trade_cal"
        assert spec.api_name == "trade_cal"
        assert spec.wire_params == {
            "exchange": exchange,
            "start_date": "20240101",
            "end_date": "20240131",
        }
        assert spec.receipt_params == {
            "exchange": exchange,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        }
        assert spec.fields == CALENDAR_FIELDS


@pytest.mark.parametrize(
    "value", ["20240101", "2024-01-01T00:00:00Z", " 2024-01-01 ", datetime(2024, 1, 1)]
)
def test_iso_date_rejects_compact_timestamp_whitespace_and_datetime(value):
    api = _api()
    with pytest.raises((TypeError, ValueError, api.PITCollectionError)):
        api._iso_date(value)


def test_system_clock_subprocess_environment_scrubs_all_project_credentials(monkeypatch):
    api = _api()
    captured = []
    for key in ("TUSHARE_TOKEN", "TUSHARE_API_KEY", "JIAOCH_TOKEN"):
        monkeypatch.setenv(key, f"secret-{key}")
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setattr(api.shutil, "which", lambda name: "/usr/bin/timedatectl")

    def run(*args, **kwargs):
        captured.append(kwargs["env"])
        return subprocess.CompletedProcess(args[0], 0, stdout="yes\n", stderr="")

    monkeypatch.setattr(api.subprocess, "run", run)
    assert api.SystemTrustedClock._system_probe()["synchronized"] is True
    assert captured[0]["PATH"] == "/safe/bin"
    assert not {"TUSHARE_TOKEN", "TUSHARE_API_KEY", "JIAOCH_TOKEN"} & set(captured[0])


def test_macos_systemsetup_and_sntp_subprocesses_both_receive_scrubbed_env(monkeypatch):
    api = _api()
    captured = []
    credential_keys = {"TUSHARE_TOKEN", "TUSHARE_API_KEY", "JIAOCH_TOKEN"}
    for key in credential_keys:
        monkeypatch.setenv(key, f"secret-{key}")
    monkeypatch.setattr(api.shutil, "which", lambda name: "/usr/bin/sntp" if name == "sntp" else None)
    monkeypatch.setattr(api.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(api.Path, "exists", lambda self: True)

    def run(command, **kwargs):
        captured.append((command[0], kwargs["env"]))
        if command[0].endswith("systemsetup"):
            return subprocess.CompletedProcess(command, 0, stdout="Network Time: On\n", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

    monkeypatch.setattr(api.subprocess, "run", run)
    assert api.SystemTrustedClock._system_probe()["source"] == "systemsetup-unmeasured"
    assert [name for name, _env in captured] == ["/usr/sbin/systemsetup", "/usr/bin/sntp"]
    assert all(not credential_keys & set(env) for _name, env in captured)


def test_windows_w32tm_subprocess_receives_scrubbed_env_and_accepts_recent_sync(monkeypatch):
    """Windows 上 _system_probe 应调用 w32tm /query /status,验证最近同步时间并报告 synchronized=True。"""
    api = _api()
    credential_keys = {"TUSHARE_TOKEN", "TUSHARE_API_KEY", "JIAOCH_TOKEN"}
    for key in credential_keys:
        monkeypatch.setenv(key, f"secret-{key}")
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    monkeypatch.setattr(api.platform, "system", lambda: "Windows")
    # w32tm 输出"最近同步时间",需要被解析为 aware UTC,然后和 now_utc 对比。
    # 固定 now,让同步时间落在 24 小时窗口内。
    fixed_now = api.datetime(2026, 7, 21, 7, 30, tzinfo=api.timezone.utc)
    monkeypatch.setattr(api.SystemTrustedClock, "now_utc", staticmethod(lambda: fixed_now))

    captured_env: list[dict] = []

    def run(command, **kwargs):
        captured_env.append(kwargs["env"])
        # 真实中文 Windows w32tm 输出(同步时间 = UTC 当天 15:06 ≈ 上海时间 23:06,
        # 这里用 UTC 当天早些时候让"距今 < 24h"成立)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Leap 指示符: 0(无警告)\r\n"
                "层次: 3 (次引用 - 与(S)NTP 同步)\r\n"
                "精度: -23 (每刻度 119.209ns)\r\n"
                "根延迟: 0.0313876s\r\n"
                "根分散: 1.3653103s\r\n"
                "引用 ID: 0xCB6B0658 (源 IP:  203.107.6.88)\r\n"
                "上次成功同步时间: 2026/7/21 15:06:14\r\n"
                "源: ntp.aliyun.com,0x9\r\n"
                "轮询间隔: 11 (2048s)\r\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(api.subprocess, "run", run)
    evidence = api.SystemTrustedClock._system_probe()
    assert evidence["source"] == "w32tm"
    assert evidence["synchronized"] is True
    assert evidence["sync_source"] == "ntp.aliyun.com,0x9"
    assert evidence["stratum"] == 3
    # 任何 credential 都不能传给 w32tm 子进程
    assert all(not credential_keys & set(env) for env in captured_env)


def test_windows_w32tm_fails_closed_when_last_sync_too_old(monkeypatch):
    """w32tm 报告同步时间超过 24 小时时必须 fail-closed。"""
    api = _api()
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    monkeypatch.setattr(api.platform, "system", lambda: "Windows")
    fixed_now = api.datetime(2026, 7, 21, 12, 0, tzinfo=api.timezone.utc)
    monkeypatch.setattr(api.SystemTrustedClock, "now_utc", staticmethod(lambda: fixed_now))

    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Leap 指示符: 0(无警告)\r\n"
                "上次成功同步时间: 2026/7/19 09:00:00\r\n"  # 51 小时前
                "源: ntp.aliyun.com,0x9\r\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(api.subprocess, "run", run)
    evidence = api.SystemTrustedClock._system_probe()
    assert evidence["source"] == "w32tm"
    assert evidence["synchronized"] is False
    assert "stale" in evidence.get("reason", "")


def test_windows_w32tm_fails_closed_when_source_is_local_cmos(monkeypatch):
    """w32tm 源是 Local CMOS Clock(硬件时钟,未同步)时必须 fail-closed。"""
    api = _api()
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    monkeypatch.setattr(api.platform, "system", lambda: "Windows")
    fixed_now = api.datetime(2026, 7, 21, 12, 0, tzinfo=api.timezone.utc)
    monkeypatch.setattr(api.SystemTrustedClock, "now_utc", staticmethod(lambda: fixed_now))

    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Leap 指示符: 0(无警告)\r\n"
                "上次成功同步时间: 2026/7/21 11:55:00\r\n"  # 很近
                "源: Local CMOS Clock\r\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(api.subprocess, "run", run)
    evidence = api.SystemTrustedClock._system_probe()
    assert evidence["source"] == "w32tm"
    assert evidence["synchronized"] is False
    assert "local_cmos" in evidence.get("reason", "")


def test_windows_w32tm_fails_closed_when_command_fails(monkeypatch):
    """w32tm 命令不可用或返回非零时必须 fail-closed(而不是 crash)。"""
    api = _api()
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    monkeypatch.setattr(api.platform, "system", lambda: "Windows")

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="服务未启动")

    monkeypatch.setattr(api.subprocess, "run", run)
    evidence = api.SystemTrustedClock._system_probe()
    assert evidence["source"] == "w32tm"
    assert evidence["synchronized"] is False


def test_windows_w32tm_parses_english_output_too(monkeypatch):
    """英文 Windows 的 w32tm 输出也应被识别(中英双解析)。"""
    api = _api()
    monkeypatch.setattr(api.shutil, "which", lambda name: None)
    monkeypatch.setattr(api.platform, "system", lambda: "Windows")
    fixed_now = api.datetime(2026, 7, 21, 12, 0, tzinfo=api.timezone.utc)
    monkeypatch.setattr(api.SystemTrustedClock, "now_utc", staticmethod(lambda: fixed_now))

    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Leap Indicator: 0(no warning)\r\n"
                "Stratum: 3 (secondary reference - synchronized)\r\n"
                "Last Successful Sync Time: 7/21/2026 11:00:00 AM\r\n"
                "Source: time.windows.com,0x8\r\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(api.subprocess, "run", run)
    evidence = api.SystemTrustedClock._system_probe()
    assert evidence["source"] == "w32tm"
    assert evidence["synchronized"] is True
    assert evidence["sync_source"] == "time.windows.com,0x8"


def test_fetch_partition_sends_one_canonical_four_field_post_body():
    api = _api()
    response = api.HttpEntityResponse(status=200, headers={}, body=_success_body())
    transport = ScriptedTransport([response])
    collector = _collector(api, transport, RecordingStore(), FakeTrustedClock())

    collector.fetch_partition(_spec(api))

    assert len(transport.calls) == 1
    body = transport.calls[0]["body"]
    expected = {
        "api_name": "stock_basic",
        "token": TOKEN,
        "params": {"exchange": "SSE", "list_status": "L"},
        "fields": ",".join(STOCK_FIELDS),
    }
    assert body == _canonical_json_bytes(expected)
    assert set(json.loads(body)) == {"api_name", "token", "params", "fields"}
    assert transport.calls[0]["headers"]["Connection"] == "close"
    assert TOKEN not in repr(transport.calls[0]["headers"])


def test_path_per_interface_protocol_posts_to_canonical_api_name_path():
    api = _api()
    transport = ScriptedTransport(
        [api.HttpEntityResponse(status=200, headers={}, body=_success_body())]
    )
    collector = api.ControlledTushareCollector(
        store=RecordingStore(),
        token=TOKEN,
        api_url="http://jiaoch.site",
        transport=transport,
        clock=FakeTrustedClock(),
        allowed_hosts=("jiaoch.site",),
        source_profile="jiaoch",
        request_protocol="tushare-path-per-interface/v1",
        temporal_contract=TEMPORAL_CONTRACT,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=TEMPORAL_CONTRACT["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
    )
    transport.events = collector.clock.events

    collector.fetch_partition(_spec(api))

    assert transport.calls[0]["url"] == "http://jiaoch.site/stock_basic"


def test_source_routing_identity_is_read_only_and_cannot_redirect_token():
    api = _api()
    transport = ScriptedTransport(
        [api.HttpEntityResponse(status=200, headers={}, body=_success_body())]
    )
    collector = _collector(api, transport, RecordingStore(), FakeTrustedClock())
    for name, value in (
        ("api_url", "https://evil.invalid/steal"),
        ("source_profile", "evil"),
        ("request_protocol", "tushare-path-per-interface/v1"),
        ("network_route", "loopback_http_proxy"),
        ("proxy_endpoint", "http://evil.invalid:8080"),
        ("allowed_hosts", ("evil.invalid",)),
        ("row_cap_overrides", {"stock_basic": 1}),
    ):
        with pytest.raises(AttributeError):
            setattr(collector, name, value)
    exposed_caps = collector.row_cap_overrides
    exposed_caps["stock_basic"] = 1
    assert collector.row_cap_overrides == {}
    with pytest.raises(AttributeError):
        collector._source_authority = collector._source_authority

    collector.fetch_partition(_spec(api))

    assert transport.calls[0]["url"] == "https://example.invalid/tushare"
    assert b"evil.invalid" not in transport.calls[0]["body"]


def test_retrieved_at_comes_from_trusted_clock_after_body_completion():
    api = _api()
    events = []
    response = api.HttpEntityResponse(
        status=200,
        headers={"Date": "Tue, 01 Jan 2030 00:00:00 GMT"},
        body=_success_body(),
    )
    store = RecordingStore(events)
    collector = _collector(
        api,
        ScriptedTransport([response], events),
        store,
        FakeTrustedClock(events),
    )

    collector.fetch_partition(_spec(api))

    assert events.index("body_complete") < events.index("clock_now")
    assert events.index("clock_now") < events.index("attempt_recorded")
    assert store.attempts[0]["retrieved_at"] == "2024-01-02T08:00:00+00:00"


def test_timeout_then_503_then_success_reuses_hashes_and_promotes_once():
    api = _api()
    transport = ScriptedTransport(
        [
            TimeoutError("socket timeout"),
            api.HttpEntityResponse(status=503, headers={}, body=b"unavailable"),
            api.HttpEntityResponse(status=200, headers={}, body=_success_body()),
        ]
    )
    store = RecordingStore()
    collector = _collector(api, transport, store, FakeTrustedClock())

    result = collector.fetch_partition(_spec(api))

    assert len(transport.calls) == 3
    assert len({call["body"] for call in transport.calls}) == 1
    assert len(store.attempts) == 3
    for hash_field in ("request_body_sha256", "request_semantics_sha256"):
        assert len({attempt[hash_field] for attempt in store.attempts}) == 1
    assert store.attempts[0]["request_body_sha256"] == hashlib.sha256(
        transport.calls[0]["body"]
    ).hexdigest()
    assert store.promotions == ["attempt-3"]
    assert result["receipt"] == {"status": "stored", "attempt_id": "attempt-3"}
    assert len(result["attempts"]) == 3


def test_token_is_absent_from_attempt_metadata_and_terminal_exception():
    api = _api()
    store = RecordingStore()
    collector = _collector(
        api,
        ScriptedTransport([TimeoutError("timeout")] * 3),
        store,
        FakeTrustedClock(),
    )

    with pytest.raises(Exception) as raised:
        collector.fetch_partition(_spec(api))

    assert len(store.attempts) == 3
    assert TOKEN not in repr(store.attempts)
    assert TOKEN not in str(raised.value)
    assert TOKEN not in repr(vars(raised.value))
    assert store.promotions == []


def test_native_nonzero_code_is_terminal_and_never_promoted():
    api = _api()
    body = _canonical_json_bytes(
        {"request_id": "test", "code": 2002, "msg": "permission denied", "data": None}
    )
    transport = ScriptedTransport(
        [api.HttpEntityResponse(status=200, headers={}, body=body)]
    )
    store = RecordingStore()
    collector = _collector(api, transport, store, FakeTrustedClock())

    with pytest.raises(Exception, match="2002|permission denied"):
        collector.fetch_partition(_spec(api))

    assert len(transport.calls) == 1
    assert len(store.attempts) == 1
    assert store.promotions == []


def test_code_zero_accepts_exact_compatible_success_message():
    api = _api()
    transport = ScriptedTransport(
        [
            api.HttpEntityResponse(status=200, headers={}, body=_success_body("success"))
            for _ in range(3)
        ]
    )
    store = RecordingStore()

    result = _collector(api, transport, store, FakeTrustedClock()).fetch_partition(
        _spec(api)
    )

    assert result["status"] == "stored"


def test_code_zero_still_rejects_other_nonempty_messages():
    api = _api()
    transport = ScriptedTransport(
        [
            api.HttpEntityResponse(status=200, headers={}, body=_success_body("warning"))
            for _ in range(3)
        ]
    )
    store = RecordingStore()

    with pytest.raises(Exception, match="message is not empty"):
        _collector(api, transport, store, FakeTrustedClock()).fetch_partition(_spec(api))

    assert store.promotions == []


def test_real_attempt_store_binds_promotion_without_persisting_token(tmp_path, monkeypatch):
    api = _api()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    store = PITReceiptStore(str(tmp_path / "store"))
    collector = _collector(
        api,
        ScriptedTransport([api.HttpEntityResponse(status=200, headers={}, body=_success_body())]),
        store,
        FakeTrustedClock(),
    )

    result = collector.fetch_partition(_spec(api))

    assert result["receipt"]["status"] == "stored"
    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert len(attempts) == 1
    assert attempts[0]["terminal_status"] == "stored"
    assert attempts[0]["request_semantics"]["fields"] == list(STOCK_FIELDS)
    assert attempts[0]["request_semantics"]["source_profile"] == "official"
    assert (
        attempts[0]["request_semantics"]["request_protocol"]
        == "tushare-root-post/v1"
    )
    assert "token" not in attempts[0]["request_semantics"]
    assert TOKEN not in repr(attempts)
    assert TOKEN.encode("utf-8") not in store.database_path.read_bytes()
    assert all(TOKEN.encode("utf-8") not in path.read_bytes() for path in store.raw_root.rglob("*.*"))


@pytest.mark.parametrize(
    ("network_route", "proxy_endpoint"),
    [
        ("direct", None),
        ("loopback_http_proxy", "http://127.0.0.1:7897"),
    ],
)
def test_request_semantics_bind_exact_network_route_without_token(
    network_route, proxy_endpoint
):
    api = _api()
    collector = _collector(
        api,
        ScriptedTransport([]),
        RecordingStore(),
        FakeTrustedClock(),
        network_route=network_route,
        proxy_endpoint=proxy_endpoint,
    )

    semantics = collector._request_semantics(_spec(api))

    assert semantics["temporal_role"] == "contaminated_diagnostic"
    assert semantics["temporal_contract_sha256"] == load_temporal_partition_contract(
        Path("data/research_partitions/frozen-v1.json")
    )["contract_sha256"]
    assert "temporal_contract" not in semantics

    assert semantics == {
        "schema_version": "tushare-wire-request/v1",
        "source_profile": "official",
        "request_protocol": "tushare-root-post/v1",
        "network_route": network_route,
        "proxy_endpoint": proxy_endpoint,
        "dataset": "stock_basic",
        "partition_key": "SSE:L",
        "api_name": "stock_basic",
        "method": "POST",
        "url": "https://example.invalid/tushare",
        "wire_params": {"exchange": "SSE", "list_status": "L"},
        "receipt_params": {"exchange": "SSE", "list_status": "L"},
        "fields": list(STOCK_FIELDS),
        "row_cap": 6000,
        "temporal_role": "contaminated_diagnostic",
        "temporal_contract_sha256": load_temporal_partition_contract(
            Path("data/research_partitions/frozen-v1.json")
        )["contract_sha256"],
    }
    assert "token" not in repr(semantics).lower()
    assert TOKEN not in repr(semantics)


def test_temporal_role_changes_request_identity_and_contract_hash_mismatch_is_rejected():
    api = _api()
    diagnostic = _collector(
        api, ScriptedTransport([]), RecordingStore(), FakeTrustedClock()
    )
    development = _collector(
        api,
        ScriptedTransport([]),
        RecordingStore(),
        FakeTrustedClock(),
        temporal_role="development",
        temporal_start_date="2016-01-01",
        temporal_end_date="2023-12-31",
    )

    assert api._sha256_json(diagnostic._request_semantics(_spec(api))) != api._sha256_json(
        development._request_semantics(_spec(api))
    )
    with pytest.raises(api.PITCollectionError, match="contract hash"):
        api.ControlledTushareCollector(
            store=RecordingStore(),
            token=TOKEN,
            api_url="https://example.invalid/tushare",
            transport=ScriptedTransport([]),
            clock=FakeTrustedClock(),
            allowed_hosts=("example.invalid",),
            temporal_contract=TEMPORAL_CONTRACT,
            temporal_role="development",
            temporal_contract_sha256="0" * 64,
            temporal_start_date="2016-01-01",
            temporal_end_date="2023-12-31",
        )


@pytest.mark.parametrize("generation_kind", ["stock", "market"])
@pytest.mark.parametrize("semantic_change", ["route", "source"])
def test_partial_generation_semantics_switch_starts_fresh_without_mixing_shards(
    tmp_path, generation_kind, semantic_change
):
    api = _api()
    store = PITReceiptStore(str(tmp_path / generation_kind))
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)

    class EmptyShardTransport:
        def __init__(self):
            self.calls = []
            self.events = []

        def post(self, **kwargs):
            request = json.loads(kwargs["body"])
            self.calls.append(request["api_name"])
            self.events.append("body_complete")
            trade_date = request["params"].get("trade_date")
            items = {
                "daily": [["600001.SH", trade_date, 10, 11, 9, 10, 10, 0, 0, 1, 1]],
                "adj_factor": [["600001.SH", trade_date, 1]],
                "stk_limit": [[trade_date, "600001.SH", 10, 11, 9]],
            }.get(request["api_name"], [])
            return api.HttpEntityResponse(
                status=200,
                headers={},
                body=_canonical_json_bytes(
                    {
                        "request_id": f"partial-{len(self.calls)}",
                        "code": 0,
                        "msg": "",
                        "data": {
                            "fields": request["fields"].split(","),
                            "items": items,
                        },
                    }
                ),
            )

    initial_transport = EmptyShardTransport()
    initial = _collector(api, initial_transport, store, FakeTrustedClock())
    if generation_kind == "stock":
        specs = api.build_stock_basic_specs()
        manifest, manifest_sha256 = initial._expected_request_semantics_manifest(
            specs, key="partition_key"
        )
        generation = store.begin_or_resume_stock_basic_generation(
            now,
            expected_request_semantics=manifest,
            expected_request_semantics_sha256=manifest_sha256,
        )
        spec = specs[0]
        initial.fetch_partition(
            spec, stock_generation_id=generation["generation_id"]
        )
    else:
        specs = api.build_market_session_fetch_specs("2024-01-02")
        manifest, manifest_sha256 = initial._expected_request_semantics_manifest(
            specs, key="dataset"
        )
        generation = store.begin_or_resume_market_session_generation(
            now,
            "2024-01-02",
            expected_request_semantics=manifest,
            expected_request_semantics_sha256=manifest_sha256,
        )
        spec = specs[0]
        initial.fetch_partition(
            spec, market_generation_id=generation["generation_id"]
        )
    old_generation_id = generation["generation_id"]
    old_attempt_id = store.fetch_attempts()[0]["attempt_id"]

    switched_transport = EmptyShardTransport()
    changed_options = (
        {
            "network_route": "loopback_http_proxy",
            "proxy_endpoint": "http://127.0.0.1:7897",
        }
        if semantic_change == "route"
        else {"source_profile": "alternate"}
    )
    switched = _collector(
        api,
        switched_transport,
        store,
        FakeTrustedClock(),
        **changed_options,
    )
    if generation_kind == "stock":
        report = switched.collect_stock_basic_generation(now=now, resume=True)
        assert report["fetched_partition_count"] == 8
        assert len(switched_transport.calls) == 8
    else:
        report = switched.collect_market_session_generation(
            "2024-01-02", now=now, resume=True
        )
        assert report["fetched_dataset_count"] == 4
        assert len(switched_transport.calls) == 4

    assert report["generation_id"] != old_generation_id
    assert store.fetch_attempts(attempt_id=old_attempt_id)[0]["attempt_id"] == old_attempt_id


@pytest.mark.parametrize("generation_kind", ["stock", "market"])
def test_partial_generation_same_semantics_fetches_only_missing_shards(
    tmp_path, generation_kind
):
    api = _api()
    store = PITReceiptStore(str(tmp_path / generation_kind))
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)

    class EmptyShardTransport:
        def __init__(self):
            self.calls = []
            self.events = []

        def post(self, **kwargs):
            request = json.loads(kwargs["body"])
            self.calls.append(request["api_name"])
            self.events.append("body_complete")
            trade_date = request["params"].get("trade_date")
            items = {
                "daily": [["600001.SH", trade_date, 10, 11, 9, 10, 10, 0, 0, 1, 1]],
                "adj_factor": [["600001.SH", trade_date, 1]],
                "stk_limit": [[trade_date, "600001.SH", 10, 11, 9]],
            }.get(request["api_name"], [])
            return api.HttpEntityResponse(
                200,
                {},
                _canonical_json_bytes(
                    {
                        "code": 0,
                        "msg": "",
                        "data": {
                            "fields": request["fields"].split(","),
                            "items": items,
                        },
                    }
                ),
            )

    transport = EmptyShardTransport()
    collector = _collector(api, transport, store, FakeTrustedClock())
    if generation_kind == "stock":
        specs = api.build_stock_basic_specs()
        manifest, manifest_sha256 = collector._expected_request_semantics_manifest(
            specs, key="partition_key"
        )
        generation = store.begin_or_resume_stock_basic_generation(
            now,
            expected_request_semantics=manifest,
            expected_request_semantics_sha256=manifest_sha256,
        )
        collector.fetch_partition(
            specs[0],
            stock_generation_id=generation["generation_id"],
        )
        transport.calls.clear()
        report = collector.collect_stock_basic_generation(now=now, resume=True)
        assert report["generation_id"] == generation["generation_id"]
        assert report["fetched_partition_count"] == 7
        assert len(transport.calls) == 7
    else:
        specs = api.build_market_session_fetch_specs("2024-01-02")
        manifest, manifest_sha256 = collector._expected_request_semantics_manifest(
            specs, key="dataset"
        )
        generation = store.begin_or_resume_market_session_generation(
            now,
            "2024-01-02",
            expected_request_semantics=manifest,
            expected_request_semantics_sha256=manifest_sha256,
        )
        collector.fetch_partition(
            specs[0],
            market_generation_id=generation["generation_id"],
        )
        transport.calls.clear()
        report = collector.collect_market_session_generation(
            "2024-01-02", now=now, resume=True
        )
        assert report["generation_id"] == generation["generation_id"]
        assert report["fetched_dataset_count"] == 3
        assert len(transport.calls) == 3


@pytest.mark.parametrize(
    ("network_route", "proxy_endpoint"),
    [
        ("invalid", None),
        ("direct", "http://127.0.0.1:7897"),
        ("loopback_http_proxy", None),
    ],
)
def test_collector_rejects_invalid_network_route_combinations(
    network_route, proxy_endpoint
):
    api = _api()

    with pytest.raises(api.PITCollectionError, match="network route|proxy endpoint"):
        _collector(
            api,
            ScriptedTransport([]),
            RecordingStore(),
            FakeTrustedClock(),
            network_route=network_route,
            proxy_endpoint=proxy_endpoint,
        )


def test_real_attempt_store_persists_terminal_api_error_without_a_receipt(
    tmp_path, monkeypatch
):
    api = _api()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    body = _canonical_json_bytes(
        {"request_id": "test", "code": 2002, "msg": "permission denied", "data": None}
    )
    store = PITReceiptStore(str(tmp_path / "store"))
    collector = _collector(
        api,
        ScriptedTransport([api.HttpEntityResponse(status=200, headers={}, body=body)]),
        store,
        FakeTrustedClock(),
    )

    with pytest.raises(api.PITCollectionError, match="2002"):
        collector.fetch_partition(_spec(api))

    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert len(attempts) == 1
    assert attempts[0]["terminal_status"] == "api_error"
    assert store.receipt_count() == 0


def test_resume_skips_network_only_after_existing_controlled_receipt_reverifies(
    tmp_path, monkeypatch
):
    api = _api()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    store = PITReceiptStore(str(tmp_path / "store"))
    first = _collector(
        api,
        ScriptedTransport([api.HttpEntityResponse(status=200, headers={}, body=_success_body())]),
        store,
        FakeTrustedClock(),
    )
    assert first.fetch_partition(_spec(api))["receipt"]["status"] == "stored"
    no_network = ScriptedTransport([])
    resumed = _collector(api, no_network, store, FakeTrustedClock())

    result = resumed.fetch_partition(_spec(api), resume=True)

    assert result["status"] == "skipped"
    assert result["receipt"]["status"] == "skipped"
    assert no_network.calls == []


def test_controlled_collector_fails_closed_on_immutable_success_body_conflict(
    tmp_path, monkeypatch
):
    api = _api()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    store = PITReceiptStore(str(tmp_path / "store"))
    first = _collector(
        api,
        ScriptedTransport([api.HttpEntityResponse(status=200, headers={}, body=_success_body())]),
        store,
        FakeTrustedClock(),
    )
    assert first.fetch_partition(_spec(api))["receipt"]["status"] == "stored"
    conflicting_body = _canonical_json_bytes(
        {
            "request_id": "different-success-body",
            "code": 0,
            "msg": "",
            "data": {"fields": list(STOCK_FIELDS), "items": []},
        }
    )
    second = _collector(
        api,
        ScriptedTransport(
            [api.HttpEntityResponse(status=200, headers={}, body=conflicting_body)]
        ),
        store,
        FakeTrustedClock(),
    )

    with pytest.raises(api.PITCollectionError, match="immutable conflict"):
        second.fetch_partition(_spec(api))

    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert [attempt["terminal_status"] for attempt in attempts] == [
        "stored",
        "immutable_conflict",
    ]
    assert store.receipt_count() == 1


def test_collect_trade_calendars_jiaoch_fetches_sse_only_and_verifies_lineage(
    tmp_path,
):
    """jiaoch source 的 trade_cal 只维护 SSE(SZSE 空),collector 只抓 SSE 一个交易所。

    官方 tushare 等其他 source 仍要求两市完整 controlled receipt(见其他测试)。
    """
    api = _api()

    class CalendarOnlyTransport:
        def __init__(self):
            self.events = []
            self.calls = []

        def post(self, *, url, headers, body, timeout_s, max_body_bytes):
            request = json.loads(body)
            self.calls.append(request)
            assert request["api_name"] == "trade_cal"
            exchange = request["params"]["exchange"]
            rows = [
                [exchange, "20221231", 0, "20221230"],
                [exchange, "20230101", 0, "20221230"],
                [exchange, "20230102", 1, "20221230"],
            ]
            payload = _canonical_json_bytes(
                {
                    "request_id": f"trade-cal-{exchange}",
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": request["fields"].split(","),
                        "items": rows,
                    },
                }
            )
            self.events.append("body_complete")
            return api.HttpEntityResponse(status=200, headers={}, body=payload)

    store = PITReceiptStore(str(tmp_path / "store"))
    transport = CalendarOnlyTransport()
    collector = _collector(
        api,
        transport,
        store,
        FakeTrustedClock(),
        source_profile="jiaoch",
        temporal_role="development",
        temporal_start_date="2016-01-01",
        temporal_end_date="2023-12-31",
        workers=2,
    )

    report = collector.collect_trade_calendars(
        start_date="2022-12-31",
        end_date="2023-01-02",
        resume=True,
    )

    assert report["status"] == "complete"
    assert report["mode"] == "trade_calendars_only"
    assert report["planned_receipt_count"] == 1
    assert report["controlled_receipt_count"] == 1
    assert report["controlled_request_lineage_complete"] is True
    assert report["attempt_count"] == 1
    assert report["stored_count"] == 1
    assert report["reused_count"] == 0
    assert report["skipped_count"] == 0
    assert report["workers"] == 2
    assert report["final_oos_eligible"] is False
    assert report["promotion_eligible"] is True
    assert {call["api_name"] for call in transport.calls} == {"trade_cal"}
    assert {call["params"]["exchange"] for call in transport.calls} == {"SSE"}
    assert len(transport.calls) == 1
    assert {row["exchange"] for row in report["calendar_receipts"]} == {
        "SSE",
    }
    assert all(
        len(row["request_semantics_sha256"]) == 64 and len(row["receipt_raw_sha256"]) == 64
        for row in report["calendar_receipts"]
    )

    resumed_transport = CalendarOnlyTransport()
    resumed = _collector(
        api,
        resumed_transport,
        store,
        FakeTrustedClock(),
        source_profile="jiaoch",
        temporal_role="development",
        temporal_start_date="2016-01-01",
        temporal_end_date="2023-12-31",
        workers=2,
    ).collect_trade_calendars(
        start_date="2022-12-31",
        end_date="2023-01-02",
        resume=True,
    )

    assert resumed["attempt_count"] == 0
    assert resumed["stored_count"] == 0
    assert resumed["skipped_count"] == 1
    assert resumed["controlled_request_lineage_complete"] is True
    assert resumed_transport.calls == []


def test_collect_trade_calendars_fails_before_fetch_without_controlled_receipt_store():
    api = _api()
    collector = _collector(
        api,
        ScriptedTransport([]),
        object(),
        FakeTrustedClock(),
    )
    calls = []
    collector.fetch_partition = lambda spec, *, resume=False: calls.append(spec)

    with pytest.raises(api.PITCollectionError, match="controlled receipt"):
        collector.collect_trade_calendars(
            start_date="2024-01-01",
            end_date="2024-01-03",
            resume=True,
        )

    assert calls == []


def test_collect_trade_calendars_rejects_out_of_authority_range_before_network(tmp_path):
    api = _api()
    transport = ScriptedTransport([])
    collector = _collector(
        api,
        transport,
        PITReceiptStore(str(tmp_path / "store")),
        FakeTrustedClock(),
        temporal_start_date="2024-01-01",
        temporal_end_date="2024-01-03",
    )

    with pytest.raises(api.PITCollectionError, match="exceeds collector authority"):
        collector.collect_trade_calendars(
            start_date="2023-12-31",
            end_date="2024-01-03",
            resume=True,
        )

    assert transport.calls == []


def test_collect_runs_two_calendars_eight_master_shards_and_only_open_daily_sessions():
    api = _api()
    events = []

    class PlanningStore:
        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            events.append(("calendar_gate", start_date, end_date))
            return ["2024-01-02", "2024-01-03"]

        def begin_or_resume_stock_basic_generation(self, *args, **kwargs):
            raise AssertionError("generation orchestration should be stubbed as one unit")

        def stage_stock_basic_attempt(self, *args, **kwargs):
            raise AssertionError("generation orchestration should be stubbed as one unit")

        def publish_stock_basic_generation(self, *args, **kwargs):
            raise AssertionError("generation orchestration should be stubbed as one unit")

        def active_stock_basic_generation(self, *args, **kwargs):
            raise AssertionError("generation orchestration should be stubbed as one unit")

        def verify_stock_basic_generation(self, generation_id):
            assert generation_id == "planned-generation"
            return {
                "manifest": {},
                "manifest_sha256": "a" * 64,
                "rows_sha256": "b" * 64,
                "audit_identity_sha256": "c" * 64,
            }

    collector = _collector(
        api,
        ScriptedTransport([]),
        PlanningStore(),
        FakeTrustedClock(),
    )
    seen = []

    def fake_fetch(spec, *, resume=False):
        assert resume is True
        seen.append(spec)
        events.append(("fetch", spec.partition_key))
        return {"status": "stored", "attempts": [{}], "receipt": {"status": "stored"}}

    collector.fetch_partition = fake_fetch

    def fake_generation(**kwargs):
        assert kwargs == {"resume": True, "reuse_published": False}
        events.append(("generation", "planned-generation"))
        return {
            "generation_id": "planned-generation",
            "fetched_partition_count": 8,
            "attempt_count": 8,
        }

    collector.collect_stock_basic_generation = fake_generation

    market_sessions_seen = []

    def fake_market_generation(trade_date, **kwargs):
        # WHY: collect now drives one historical_backfill market session
        # generation per common open session; this stub keeps the test focused
        # on the legacy planning invariants without exercising real fetches.
        assert kwargs == {"vintage": "historical_backfill", "resume": True}
        market_sessions_seen.append(trade_date)
        return {
            "status": "published",
            "trade_date": trade_date,
            "attempt_count": 0,
            "fetched_dataset_count": 0,
        }

    collector.collect_market_session_generation = fake_market_generation
    report = collector.collect(start_date="2024-01-01", end_date="2024-01-03")

    assert [spec.partition_key for spec in seen[:2]] == [
        "SSE:2024-01-01:2024-01-03",
        "SZSE:2024-01-01:2024-01-03",
    ]
    assert [spec.partition_key for spec in seen[2:]] == ["2024-01-02", "2024-01-03"]
    assert events[2][0] == "calendar_gate"
    assert events[3] == ("generation", "planned-generation")
    assert market_sessions_seen == ["2024-01-02", "2024-01-03"]
    assert report["planned_receipt_count"] == 12
    assert report["attempt_count"] == 12
    assert report["stored_count"] == 12
    assert report["market_session_generation_count"] == 2
    assert report["temporal_role"] == "contaminated_diagnostic"
    assert report["final_oos_eligible"] is False
    assert report["promotion_eligible"] is False


def test_official_collect_rejects_a_store_without_generation_capabilities():
    api = _api()

    class LegacyOnlyStore:
        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            return ["2024-01-02"]

    collector = _collector(
        api,
        ScriptedTransport([]),
        LegacyOnlyStore(),
        FakeTrustedClock(),
    )
    collector.fetch_partition = lambda spec, *, resume=False: {
        "status": "stored",
        "attempts": [{}],
        "receipt": {"status": "stored"},
    }

    with pytest.raises(api.PITCollectionError, match="generation.*capabilit"):
        collector.collect(start_date="2024-01-01", end_date="2024-01-02")


def test_calendar_exchange_scope_follows_source_profile():
    api = _api()
    store = object()
    clock = FakeTrustedClock()

    official = _collector(
        api,
        ScriptedTransport([]),
        store,
        clock,
        source_profile="official",
    )
    jiaoch = _collector(
        api,
        ScriptedTransport([]),
        store,
        clock,
        source_profile="jiaoch",
    )

    assert official._calendar_exchanges() == ("SSE", "SZSE")
    assert jiaoch._calendar_exchanges() == ("SSE",)


def test_jiaoch_parallel_collect_supports_single_calendar_source():
    api = _api()

    class Store:
        begin_or_resume_stock_basic_generation = object()
        stage_stock_basic_attempt = object()
        publish_stock_basic_generation = object()
        active_stock_basic_generation = object()

        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            assert exchanges == ("SSE",)
            return []

        def verify_stock_basic_generation(self, generation_id):
            assert generation_id == "stock-generation"
            return {
                "manifest": {},
                "manifest_sha256": "a" * 64,
                "rows_sha256": "b" * 64,
                "audit_identity_sha256": "c" * 64,
            }

    collector = _collector(
        api,
        ScriptedTransport([]),
        Store(),
        FakeTrustedClock(),
        source_profile="jiaoch",
        api_url="http://jiaoch.site",
        allowed_hosts=("jiaoch.site",),
        request_protocol="tushare-path-per-interface/v1",
        workers=2,
    )
    calendar_partitions = []

    def fetch_partition(spec, *, resume=False):
        calendar_partitions.append(spec.partition_key)
        return {
            "status": "stored",
            "attempts": [{}],
            "receipt": {"status": "stored"},
        }

    collector.fetch_partition = fetch_partition
    collector.collect_stock_basic_generation = lambda **kwargs: {
        "generation_id": "stock-generation",
        "fetched_partition_count": 8,
        "attempt_count": 8,
    }

    report = collector.collect(
        start_date="2024-01-01",
        end_date="2024-01-01",
        workers=2,
    )

    assert calendar_partitions == ["SSE:2024-01-01:2024-01-01"]
    assert report["open_session_count"] == 0
    assert report["planned_receipt_count"] == 9
    assert report["generation_id"] == "stock-generation"


def test_controlled_collect_end_to_end_passes_coverage_and_then_resumes_without_network(
    tmp_path, monkeypatch
):
    api = _api()
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)

    # WHY: the official collect path now drives the four-shard market session
    # generation for every common open session in addition to the legacy trade
    # calendar / stock master / bak_basic receipts. The fixture must therefore
    # answer daily / adj_factor / stk_limit / suspend_d with field-ordered rows
    # so the market generations can be staged and published without network
    # falling back to legacy per-dataset receipts.
    market_rows = {
        "daily": lambda trade_date: [
            ["600001.SH", trade_date, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]
        ],
        "adj_factor": lambda trade_date: [["600001.SH", trade_date, 1.5]],
        "stk_limit": lambda trade_date: [[trade_date, "600001.SH", 10.0, 11.0, 9.0]],
        "suspend_d": lambda trade_date: (
            [["000002.SZ", trade_date, "09:30:00", "S"]]
            if trade_date == "20240102"
            else []
        ),
    }

    class CompleteFixtureTransport:
        def __init__(self):
            self.events = []
            self.calls = []

        def post(self, *, url, headers, body, timeout_s, max_body_bytes):
            request = json.loads(body)
            self.calls.append(request)
            api_name = request["api_name"]
            params = request["params"]
            fields = request["fields"].split(",")
            if api_name == "trade_cal":
                exchange = params["exchange"]
                rows = [
                    [exchange, "20240101", 0, "20231229"],
                    [exchange, "20240102", 1, "20231229"],
                    [exchange, "20240103", 1, "20240102"],
                ]
            elif api_name == "stock_basic":
                shard = (params["exchange"], params["list_status"])
                rows = []
                if shard == ("SSE", "L"):
                    rows = [
                        ["600001.SH", "600001", "A", "SSE", "主板", "L", "20100101", None]
                    ]
                elif shard == ("SZSE", "D"):
                    rows = [
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
            elif api_name in market_rows:
                rows = market_rows[api_name](params["trade_date"])
            else:
                trade_date = params["trade_date"]
                rows = [[trade_date, "600001.SH", "A", "银行", "20100101"]]
                if trade_date == "20240102":
                    rows.append([trade_date, "000002.SZ", "B", "地产", "20100101"])
            payload = _canonical_json_bytes(
                {
                    "request_id": f"{api_name}-{len(self.calls)}",
                    "code": 0,
                    "msg": "",
                    "data": {"fields": fields, "items": rows},
                }
            )
            self.events.append("body_complete")
            return api.HttpEntityResponse(status=200, headers={}, body=payload)

    store = PITReceiptStore(str(tmp_path / "store"))
    transport = CompleteFixtureTransport()
    collector = _collector(api, transport, store, FakeTrustedClock())

    report = collector.collect(start_date="2024-01-01", end_date="2024-01-03")

    # WHY: planned/stored receipts cover only the legacy trade_cal (2) +
    # stock_basic generation (8) + bak_basic (2) = 12 partitions; the four-
    # shard market generations are staged into the generation store, not as
    # legacy receipts, so they add network calls without adding receipts.
    assert report["planned_receipt_count"] == 12
    assert report["stored_count"] == 12
    assert report["market_session_generation_count"] == 2
    # 12 legacy calls + 2 sessions * 4 market shards = 20 total transport calls.
    assert len(transport.calls) == 20
    assert store.audit_coverage(
        start_date="2024-01-01", end_date="2024-01-03"
    )["status"] == "passed"

    resumed = _collector(api, transport, store, FakeTrustedClock())
    resumed_report = resumed.collect(
        start_date="2024-01-01", end_date="2024-01-03", resume=True
    )
    assert len(transport.calls) == 20
    assert resumed_report["attempt_count"] == 0

    old_stock_generation_id = store.active_stock_basic_generation()["generation_id"]
    old_market_generation_ids = {
        session: store.active_market_session_generation(session)["generation_id"]
        for session in ("2024-01-02", "2024-01-03")
    }
    route_transport = CompleteFixtureTransport()
    route_changed = _collector(
        api,
        route_transport,
        store,
        FakeTrustedClock(),
        network_route="loopback_http_proxy",
        proxy_endpoint="http://127.0.0.1:7897",
    )

    route_report = route_changed.collect(
        start_date="2024-01-01", end_date="2024-01-03", resume=True
    )

    assert route_report["attempt_count"] == 12
    assert route_report["stock_basic_generation"]["fetched_partition_count"] == 8
    assert [
        generation["fetched_dataset_count"]
        for generation in route_report["market_session_generations"]
    ] == [4, 4]
    assert len(route_transport.calls) == 20
    assert store.active_stock_basic_generation()["generation_id"] != old_stock_generation_id
    assert store.verify_stock_basic_generation(old_stock_generation_id)[
        "verification_status"
    ] == "passed"
    for session, old_generation_id in old_market_generation_ids.items():
        assert (
            store.active_market_session_generation(session)["generation_id"]
            != old_generation_id
        )
        assert store.verify_market_session_generation(old_generation_id)[
            "verification_status"
        ] == "passed"

    route_stock_generation_id = store.active_stock_basic_generation()["generation_id"]
    source_transport = CompleteFixtureTransport()
    source_changed = _collector(
        api,
        source_transport,
        store,
        FakeTrustedClock(),
        network_route="loopback_http_proxy",
        proxy_endpoint="http://127.0.0.1:7897",
        source_profile="alternate",
    )

    source_report = source_changed.collect(
        start_date="2024-01-01", end_date="2024-01-03", resume=True
    )

    assert source_report["attempt_count"] == 12
    assert source_report["stock_basic_generation"]["fetched_partition_count"] == 8
    assert [
        generation["fetched_dataset_count"]
        for generation in source_report["market_session_generations"]
    ] == [4, 4]
    assert len(source_transport.calls) == 20
    assert (
        store.active_stock_basic_generation()["generation_id"]
        != route_stock_generation_id
    )
    assert store.verify_stock_basic_generation(route_stock_generation_id)[
        "verification_status"
    ] == "passed"

    # WHY: the market datasets must leave no legacy per-dataset receipts;
    # their evidence lives entirely inside the published generations.
    legacy_index = store.controlled_receipt_index()
    market_datasets = {"daily", "adj_factor", "stk_limit", "suspend_d"}
    assert [
        key for key in legacy_index if key[0] in market_datasets
    ] == []
    # WHY: backfilled sessions are pinned to historical_backfill so they can
    # never be mistaken for live_forward heads.
    for session in ("2024-01-02", "2024-01-03"):
        active = store.active_market_session_generation(session)
        assert active is not None
        assert active["vintage"] == "historical_backfill"

    # WHY: a second collect with the same source and route must reuse every
    # receipt and published generation, leaving the new transport at 20 calls.
    resumed = _collector(
        api,
        source_transport,
        store,
        FakeTrustedClock(),
        network_route="loopback_http_proxy",
        proxy_endpoint="http://127.0.0.1:7897",
        source_profile="alternate",
    )
    resumed_report = resumed.collect(
        start_date="2024-01-01", end_date="2024-01-03", resume=True
    )
    assert len(source_transport.calls) == 20
    assert resumed_report["attempt_count"] == 0
    assert resumed_report["market_session_generation_count"] == 2
    assert store.audit_coverage(
        start_date="2024-01-01", end_date="2024-01-03"
    )["status"] == "passed"


def test_urllib_transport_posts_exact_body_and_marks_oversized_entity_incomplete():
    api = _api()

    class Response:
        headers = {"Content-Length": "6", "Content-Type": "application/json"}

        def __init__(self):
            self.closed = False

        def read(self, limit):
            assert limit == 5
            return b"abcdef"

        def getcode(self):
            return 200

        def close(self):
            self.closed = True

    response = Response()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://example.invalid/tushare"
            assert request.method == "POST"
            assert request.data == b'{"wire":true}'
            assert timeout == 3.0
            return response

    transport = api.UrllibTushareTransport(opener=Opener())
    result = transport.post(
        url="https://example.invalid/tushare",
        headers={"Content-Type": "application/json"},
        body=b'{"wire":true}',
        timeout_s=3.0,
        max_body_bytes=4,
    )

    assert result.body == b"abcd"
    assert result.body_complete is False
    assert result.status == 200
    assert response.closed is True


def test_system_clock_probe_fails_closed_and_returns_aware_utc_time():
    api = _api()
    rejected = api.SystemTrustedClock(
        probe=lambda: {"source": "test", "synchronized": False}
    )
    with pytest.raises(api.PITCollectionError, match="synchronization"):
        rejected.assert_synchronized()

    accepted = api.SystemTrustedClock(
        probe=lambda: {"source": "test", "synchronized": True}
    )
    assert accepted.assert_synchronized()["synchronized"] is True
    assert accepted.now_utc().tzinfo is not None
    assert accepted.monotonic_ns() > 0


def test_system_clock_probe_timeout_is_reported_as_stable_fail_closed_error():
    api = _api()

    def timed_out():
        raise subprocess.TimeoutExpired(cmd=["sntp"], timeout=8)

    clock = api.SystemTrustedClock(probe=timed_out)
    with pytest.raises(api.PITCollectionError, match="could not be proven") as exc_info:
        clock.assert_synchronized()

    assert "sntp" not in str(exc_info.value)


def test_system_clock_retries_transient_probe_timeouts_before_accepting_evidence():
    api = _api()
    calls = 0

    def transient_probe():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise subprocess.TimeoutExpired(cmd=["sntp"], timeout=8)
        return {"source": "sntp", "synchronized": True, "offset_seconds": 0.1}

    evidence = api.SystemTrustedClock(probe=transient_probe).assert_synchronized()

    assert calls == 3
    assert evidence["synchronized"] is True


def test_collector_refuses_to_send_token_to_an_unpinned_host():
    api = _api()
    with pytest.raises(api.PITCollectionError, match="host"):
        api.ControlledTushareCollector(
            store=RecordingStore(),
            token=TOKEN,
            api_url="https://example.invalid/tushare",
            transport=ScriptedTransport([]),
                clock=FakeTrustedClock(),
                temporal_contract=TEMPORAL_CONTRACT,
                temporal_role="contaminated_diagnostic",
                temporal_contract_sha256=TEMPORAL_CONTRACT["contract_sha256"],
                temporal_start_date="2024-01-01",
                temporal_end_date="2026-07-03",
        )


def test_fetch_cli_reads_jiaoch_token_only_from_environment_and_forwards_options(
    tmp_path, monkeypatch, capsys
):
    captured = {}
    monkeypatch.setenv("JIAOCH_TOKEN", TOKEN)

    class Collector:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def collect(self, **kwargs):
            captured.update(kwargs)
            return {"status": "complete", "attempt_count": 12}

    monkeypatch.setattr(jobs, "ControlledTushareCollector", Collector, raising=False)
    monkeypatch.setattr(
        jobs,
        "UrllibTushareTransport",
        lambda **kwargs: captured.setdefault("transport_options", kwargs) or "transport",
        raising=False,
    )
    monkeypatch.setattr(jobs, "SystemTrustedClock", lambda: "clock", raising=False)

    assert (
        jobs.main(
            [
                "research-pit-fetch-tushare",
                "--source-profile",
                "jiaoch",
                "--store-dir",
                str(tmp_path / "store"),
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-03",
                "--max-attempts",
                "2",
                "--timeout-seconds",
                "7",
                "--temporal-role",
                "contaminated_diagnostic",
            ]
        )
        == 0
    )
    assert captured["token"] == TOKEN
    assert captured["api_url"] == "http://jiaoch.site"
    assert captured["source_profile"] == "jiaoch"
    assert captured["request_protocol"] == "tushare-path-per-interface/v1"
    assert captured["max_attempts"] == 2
    assert captured["timeout_s"] == 7.0
    assert captured["transport_options"] == {"proxy_url": None}
    assert captured["network_route"] == "direct"
    assert captured["proxy_endpoint"] is None
    assert captured["resume"] is True
    assert captured["temporal_role"] == "contaminated_diagnostic"
    assert captured["temporal_contract_sha256"] == captured["temporal_contract"][
        "contract_sha256"
    ]
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize(
    "command",
    ["research-pit-fetch-tushare", "research-pit-fetch-calendars"],
)
@pytest.mark.parametrize(
    "blocked_arguments",
    [
        ["--source-profile", "official"],
        ["--api-url", "https://api.tushare.pro"],
        ["--allow-insecure-official-http"],
    ],
)
def test_generic_research_cli_rejects_non_jiaoch_controls_before_side_effects(
    tmp_path, monkeypatch, command, blocked_arguments
):
    touched = []

    def forbidden(*_args, **_kwargs):
        touched.append(True)
        raise AssertionError("non-Jiaoch control crossed the CLI boundary")

    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    monkeypatch.setenv("DISABLE_ENV_FILE", "1")
    monkeypatch.setattr(jobs, "load_temporal_partition_contract", forbidden)
    monkeypatch.setattr(jobs, "resolve_tushare_source", forbidden)
    monkeypatch.setattr(jobs, "PITReceiptStore", forbidden)
    monkeypatch.setattr(jobs, "ControlledTushareCollector", forbidden, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        jobs.main(
            [
                command,
                "--store-dir",
                str(tmp_path / "store"),
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-03",
                *blocked_arguments,
            ]
        )

    assert exc_info.value.code == 2
    assert touched == []


@pytest.mark.parametrize(
    "command",
    ["research-pit-fetch-tushare", "research-pit-fetch-calendars"],
)
def test_generic_research_cli_help_exposes_only_jiaoch_source(command, capsys):
    with pytest.raises(SystemExit) as exc_info:
        jobs.main([command, "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--source-profile {jiaoch}" in output
    assert "official" not in output
    assert "--api-url" not in output
    assert "--allow-insecure-official-http" not in output


def test_fetch_cli_rejects_temporal_range_before_source_store_clock_or_transport(
    tmp_path, monkeypatch
):
    events = []
    monkeypatch.setattr(
        jobs,
        "load_temporal_partition_contract",
        lambda path: events.append(("load", str(path)))
        or load_temporal_partition_contract(path),
        raising=False,
    )
    monkeypatch.setattr(
        jobs,
        "resolve_tushare_source",
        lambda *args, **kwargs: events.append("source"),
    )
    monkeypatch.setattr(
        jobs, "PITReceiptStore", lambda *args, **kwargs: events.append("store")
    )
    monkeypatch.setattr(
        jobs, "SystemTrustedClock", lambda: events.append("clock"), raising=False
    )
    monkeypatch.setattr(
        jobs, "UrllibTushareTransport", lambda **kwargs: events.append("transport")
    )

    with pytest.raises(Exception, match="range crosses|forbidden|exceeds collector authority"):
        jobs.main(
            [
                "research-pit-fetch-tushare",
                "--store-dir",
                str(tmp_path / "store"),
                "--start-date",
                "2023-12-31",
                "--end-date",
                "2024-01-01",
            ]
        )

    assert events == [("load", "data/research_partitions/frozen-v1.json")]


def test_collect_rechecks_temporal_guard_before_store_clock_or_transport():
    api = _api()
    events = []
    store = RecordingStore(events)
    clock = FakeTrustedClock(events)
    transport = ScriptedTransport([], events=events)
    collector = _collector(
        api,
        transport,
        store,
        clock,
        temporal_role="development",
        temporal_start_date="2016-01-01",
        temporal_end_date="2023-12-31",
    )

    with pytest.raises(Exception, match="exceeds collector authority"):
        collector.collect(start_date="2024-01-01", end_date="2024-01-03")

    assert events == []


def test_public_temporal_fields_are_read_only_and_contract_copy_cannot_expand_authority():
    api = _api()
    events = []

    class HeadStore(RecordingStore):
        def active_market_session_generation(self, session):
            events.append(("active_head", session))
            return {"status": "published", "generation_id": "existing"}

        def begin_or_resume_market_session_generation(self, *args, **kwargs):
            events.append("begin")

        def stage_market_session_attempt(self, *args, **kwargs):
            events.append("stage")

        def publish_market_session_generation(self, *args, **kwargs):
            events.append("publish")

        def verify_market_session_generation(self, *args, **kwargs):
            events.append("verify")

    collector = _collector(
        api,
        ScriptedTransport([], events=events),
        HeadStore(events),
        FakeTrustedClock(events),
    )
    for name, value in (
        ("temporal_role", "final_oos"),
        ("temporal_start_date", "1900-01-01"),
        ("temporal_end_date", "2099-12-31"),
        ("temporal_contract_sha256", "0" * 64),
        ("temporal_contract", {}),
    ):
        with pytest.raises(AttributeError):
            setattr(collector, name, value)
    exposed = collector.temporal_contract
    exposed["roles"][-1]["permitted_operations"].append("collect")
    assert collector.temporal_contract["roles"][-1]["permitted_operations"] == []
    assert isinstance(collector._temporal_authority.contract_json, str)
    with pytest.raises(AttributeError):
        collector._temporal_authority = collector._temporal_authority

    with pytest.raises(api.PITCollectionError, match="date exceeds collector authority"):
        collector.collect_market_session_generation("2026-07-04", resume=True)
    assert events == []


def test_fetch_cli_selects_jiaoch_profile_without_exposing_token(
    tmp_path, monkeypatch, capsys
):
    captured = {}
    monkeypatch.setenv("JIAOCH_TOKEN", TOKEN)
    monkeypatch.setenv("JIAOCH_PROXY_URL", "http://127.0.0.1:7897")

    class Collector:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def collect(self, **kwargs):
            captured.update(kwargs)
            return {"status": "complete", "attempt_count": 12}

    monkeypatch.setattr(jobs, "ControlledTushareCollector", Collector, raising=False)
    monkeypatch.setattr(
        jobs,
        "UrllibTushareTransport",
        lambda **kwargs: captured.setdefault("transport_options", kwargs) or "transport",
        raising=False,
    )
    monkeypatch.setattr(jobs, "SystemTrustedClock", lambda: "clock", raising=False)

    assert jobs.main(
        [
            "research-pit-fetch-tushare",
            "--source-profile",
            "jiaoch",
            "--store-dir",
            str(tmp_path / "store"),
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-01-03",
            "--temporal-role",
            "contaminated_diagnostic",
        ]
    ) == 0
    assert captured["token"] == TOKEN
    assert captured["api_url"] == "http://jiaoch.site"
    assert captured["allowed_hosts"] == ("jiaoch.site",)
    assert captured["source_profile"] == "jiaoch"
    assert captured["request_protocol"] == "tushare-path-per-interface/v1"
    assert captured["transport_options"] == {
        "proxy_url": "http://127.0.0.1:7897"
    }
    assert captured["network_route"] == "loopback_http_proxy"
    assert captured["proxy_endpoint"] == "http://127.0.0.1:7897"
    assert TOKEN not in capsys.readouterr().out


def test_fetch_cli_defaults_to_jiaoch_source_profile(tmp_path, monkeypatch, capsys):
    captured = {}
    monkeypatch.setenv("JIAOCH_TOKEN", TOKEN)
    monkeypatch.setenv("TUSHARE_TOKEN", "official-token-must-not-be-used")
    monkeypatch.delenv("JIAOCH_PROXY_URL", raising=False)

    class Collector:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def collect(self, **kwargs):
            captured.update(kwargs)
            return {"status": "complete", "attempt_count": 12}

    monkeypatch.setattr(jobs, "ControlledTushareCollector", Collector, raising=False)
    monkeypatch.setattr(
        jobs,
        "UrllibTushareTransport",
        lambda **kwargs: captured.setdefault("transport_options", kwargs) or "transport",
        raising=False,
    )
    monkeypatch.setattr(jobs, "SystemTrustedClock", lambda: "clock", raising=False)

    assert jobs.main(
        [
            "research-pit-fetch-tushare",
            "--store-dir",
            str(tmp_path / "store"),
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-01-03",
            "--temporal-role",
            "contaminated_diagnostic",
        ]
    ) == 0

    assert captured["source_profile"] == "jiaoch"
    assert captured["token"] == TOKEN
    assert captured["api_url"] == "http://jiaoch.site"
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert "official-token-must-not-be-used" not in output


def test_calendar_only_cli_defaults_to_jiaoch_and_calls_no_full_collection(
    tmp_path, monkeypatch, capsys
):
    captured = {}
    monkeypatch.setenv("JIAOCH_TOKEN", TOKEN)

    class Collector:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def collect(self, **_kwargs):
            raise AssertionError("calendar-only CLI invoked full collection")

        def collect_trade_calendars(self, **kwargs):
            captured["calendar_args"] = kwargs
            return {
                "status": "complete",
                "mode": "trade_calendars_only",
                "controlled_request_lineage_complete": True,
            }

    monkeypatch.setattr(jobs, "ControlledTushareCollector", Collector, raising=False)
    monkeypatch.setattr(
        jobs,
        "UrllibTushareTransport",
        lambda **kwargs: captured.setdefault("transport_options", kwargs) or "transport",
        raising=False,
    )
    monkeypatch.setattr(jobs, "SystemTrustedClock", lambda: "clock", raising=False)

    assert jobs.main(
        [
            "research-pit-fetch-calendars",
            "--store-dir",
            str(tmp_path / "store"),
            "--start-date",
            "2022-12-31",
            "--end-date",
            "2023-01-02",
            "--temporal-role",
            "development",
            "--workers",
            "2",
        ]
    ) == 0
    assert captured["source_profile"] == "jiaoch"
    assert captured["workers"] == 2
    assert captured["calendar_args"] == {
        "start_date": "2022-12-31",
        "end_date": "2023-01-02",
        "resume": True,
    }
    output = capsys.readouterr().out
    assert json.loads(output)["mode"] == "trade_calendars_only"
    assert TOKEN not in output


def test_current_pool_market_only_collects_calendars_and_market_generations_only(
    tmp_path,
):
    api = _api()
    events = []

    class MarketPlanningStore:
        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            assert (start_date, end_date) == ("2024-01-01", "2024-01-03")
            events.append("common_open_sessions")
            return ["2024-01-02", "2024-01-03"]

        def bind_current_pool_market_collection(self, **kwargs):
            assert kwargs["sessions"] == ["2024-01-02", "2024-01-03"]
            events.append("temporal_binding")
            return {"binding_sha256": "a" * 64}

    collector = _collector(
        api,
        ScriptedTransport([]),
        MarketPlanningStore(),
        FakeTrustedClock(),
        source_profile="jiaoch",
        workers=2,
        row_cap_overrides={"stk_limit": 10_000},
    )

    def calendars_only(**kwargs):
        assert kwargs == {
            "start_date": "2024-01-01",
            "end_date": "2024-01-03",
            "resume": True,
        }
        events.append("trade_cal:SSE+SZSE")
        return {
            "status": "complete",
            "mode": "trade_calendars_only",
            "controlled_receipt_count": 2,
            "attempt_count": 0,
        }

    def market_generation(session, **kwargs):
        assert kwargs == {"vintage": "historical_backfill", "resume": True}
        events.append(f"market:{session}")
        return {
            "status": "published",
            "trade_date": session,
            "fetched_dataset_count": 4,
            "attempt_count": 4,
            "reused_published": False,
        }

    collector._collect_trade_calendars_once = calendars_only
    collector.collect_market_session_generation = market_generation
    collector.collect_stock_basic_generation = lambda **_kwargs: pytest.fail(
        "market-only collection must not call stock_basic"
    )
    collector.fetch_membership_snapshot = lambda *_args, **_kwargs: pytest.fail(
        "market-only collection must not call bak_basic or membership"
    )

    report = collector.collect_current_pool_market(
        start_date="2024-01-01",
        end_date="2024-01-03",
        resume=True,
        batch_size=1,
        progress_path=tmp_path / "progress.json",
    )

    assert events[0:2] == ["trade_cal:SSE+SZSE", "common_open_sessions"]
    assert set(events[2:]) == {
        "market:2024-01-02",
        "market:2024-01-03",
        "temporal_binding",
    }
    assert report == {
        "status": "complete",
        "mode": "current_pool_market_only",
        "source_profile": "jiaoch",
        "start_date": "2024-01-01",
        "end_date": "2024-01-03",
        "resume_requested": True,
        "workers": 2,
        "batch_size": 1,
        "calendar_receipt_count": 2,
        "planned": 2,
        "completed": 2,
        "remaining": 0,
        "last_session": "2024-01-03",
        "market_session_generation_count": 2,
        "fetched_dataset_count": 8,
        "attempt_count": 8,
        "reused_session_count": 0,
        "temporal_role": "contaminated_diagnostic",
        "temporal_contract_sha256": TEMPORAL_CONTRACT["contract_sha256"],
        "temporal_binding_sha256": "a" * 64,
        "current_universe_bias": True,
        "development_only": True,
        "live_proof": False,
    }


def test_current_pool_market_only_uses_published_generation_resume(tmp_path):
    api = _api()

    class MarketPlanningStore:
        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            return ["2024-01-02", "2024-01-03"]

        def bind_current_pool_market_collection(self, **kwargs):
            assert kwargs["sessions"] == ["2024-01-02", "2024-01-03"]
            return {"binding_sha256": "b" * 64}

    collector = _collector(
        api,
        ScriptedTransport([]),
        MarketPlanningStore(),
        FakeTrustedClock(),
        source_profile="jiaoch",
        workers=1,
    )
    collector._collect_trade_calendars_once = lambda **_kwargs: {
        "controlled_receipt_count": 2,
        "attempt_count": 0,
    }
    calls = []

    def reuse_generation(session, **kwargs):
        calls.append((session, kwargs))
        return {
            "status": "published",
            "trade_date": session,
            "fetched_dataset_count": 0,
            "attempt_count": 0,
            "reused_published": True,
        }

    collector.collect_market_session_generation = reuse_generation

    report = collector.collect_current_pool_market(
        start_date="2024-01-01",
        end_date="2024-01-03",
        resume=True,
        batch_size=2,
        progress_path=tmp_path / "progress.json",
    )

    assert calls == [
        (
            "2024-01-02",
            {"vintage": "historical_backfill", "resume": True},
        ),
        (
            "2024-01-03",
            {"vintage": "historical_backfill", "resume": True},
        ),
    ]
    assert report["reused_session_count"] == 2
    assert report["fetched_dataset_count"] == 0
    assert report["attempt_count"] == 0


@pytest.mark.parametrize("prior_state", ["collecting", "published"])
def test_current_pool_market_no_resume_forces_fresh_generation_without_erasing_evidence(
    tmp_path, prior_state
):
    api = _api()
    store = PITReceiptStore(str(tmp_path / prior_state))
    transport = MarketShardTransport(api)
    collector = _collector(
        api,
        transport,
        store,
        FakeTrustedClock(),
        source_profile="jiaoch",
        row_cap_overrides={"stk_limit": 10_000},
    )
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)

    if prior_state == "collecting":
        specs = api.build_market_session_fetch_specs("2024-01-02")
        manifest, manifest_sha256 = collector._expected_request_semantics_manifest(
            specs, key="dataset"
        )
        previous = store.begin_or_resume_market_session_generation(
            now,
            "2024-01-02",
            expected_request_semantics=manifest,
            expected_request_semantics_sha256=manifest_sha256,
        )
        collector.fetch_partition(
            specs[0], market_generation_id=previous["generation_id"]
        )
        previous_attempt_id = store.fetch_attempts()[0]["attempt_id"]
    else:
        previous = collector.collect_market_session_generation(
            "2024-01-02",
            now=now,
            resume=True,
            vintage="historical_backfill",
        )
        previous_attempt_id = store.fetch_attempts()[0]["attempt_id"]

    transport.calls.clear()
    replacement = collector.collect_market_session_generation(
        "2024-01-02",
        now=now,
        resume=False,
        vintage="historical_backfill",
    )

    assert replacement["generation_id"] != previous["generation_id"]
    assert transport.calls == ["daily", "adj_factor", "stk_limit", "suspend_d"]
    assert replacement["fetched_dataset_count"] == 4
    assert store.fetch_attempts(attempt_id=previous_attempt_id)[0]["attempt_id"] == (
        previous_attempt_id
    )
    if prior_state == "published":
        assert (
            store.verify_market_session_generation(previous["generation_id"])[
                "verification_status"
            ]
            == "passed"
        )
    else:
        with store._connect() as connection:
            abandoned = connection.execute(
                "SELECT status, terminal_reason FROM market_session_generations "
                "WHERE generation_id = ?",
                (previous["generation_id"],),
            ).fetchone()
        assert dict(abandoned) == {
            "status": "abandoned",
            "terminal_reason": "force_new",
        }


def test_current_pool_market_checkpoint_replaces_stale_state_before_calendar_and_on_failure(
    tmp_path,
):
    api = _api()
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(
        json.dumps({"status": "complete", "start_date": "stale"}),
        encoding="utf-8",
    )

    class MarketPlanningStore:
        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            pytest.fail("calendar failure must stop before session planning")

    collector = _collector(
        api,
        ScriptedTransport([]),
        MarketPlanningStore(),
        FakeTrustedClock(),
        source_profile="jiaoch",
    )
    observed_running = []

    def fail_calendars(**_kwargs):
        observed_running.append(json.loads(progress_path.read_text(encoding="utf-8")))
        raise RuntimeError(f"calendar provider failed {TOKEN}")

    collector._collect_trade_calendars_once = fail_calendars

    with pytest.raises(RuntimeError):
        collector.collect_current_pool_market(
            start_date="2024-01-01",
            end_date="2024-01-03",
            resume=True,
            batch_size=2,
            progress_path=progress_path,
        )

    expected_common = {
        "schema_version": "current-pool-market-progress/v1",
        "mode": "current_pool_market_only",
        "phase": "calendar",
        "source_profile": "jiaoch",
        "start_date": "2024-01-01",
        "end_date": "2024-01-03",
        "resume_requested": True,
        "workers": 1,
        "batch_size": 2,
        "planned": None,
        "completed": 0,
        "remaining": None,
        "last_session": None,
        "current_universe_bias": True,
        "development_only": True,
        "live_proof": False,
    }
    assert observed_running == [{**expected_common, "status": "running"}]
    failed = json.loads(progress_path.read_text(encoding="utf-8"))
    assert failed == {**expected_common, "status": "failed"}
    assert TOKEN not in progress_path.read_text(encoding="utf-8")


def test_current_pool_market_only_real_store_calls_only_calendar_and_four_market_apis(
    tmp_path,
):
    api = _api()

    class MarketOnlyTransport:
        def __init__(self):
            self.calls = []
            self.events = []

        def post(self, *, url, headers, body, timeout_s, max_body_bytes):
            request = json.loads(body)
            self.calls.append(request)
            api_name = request["api_name"]
            params = request["params"]
            fields = request["fields"].split(",")
            if api_name == "trade_cal":
                exchange = params["exchange"]
                rows = [
                    [exchange, "20240101", 0, "20231229"],
                    [exchange, "20240102", 1, "20231229"],
                    [exchange, "20240103", 1, "20240102"],
                ]
            elif api_name == "daily":
                trade_date = params["trade_date"]
                rows = [
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
            elif api_name == "adj_factor":
                rows = [["600001.SH", params["trade_date"], 1.5]]
            elif api_name == "stk_limit":
                rows = [[params["trade_date"], "600001.SH", 9.9, 10.89, 8.91]]
            elif api_name == "suspend_d":
                rows = []
            else:
                pytest.fail(f"forbidden API called by market-only path: {api_name}")
            payload = _canonical_json_bytes(
                {
                    "request_id": f"{api_name}-{len(self.calls)}",
                    "code": 0,
                    "msg": "",
                    "data": {"fields": fields, "items": rows},
                }
            )
            self.events.append("body_complete")
            return api.HttpEntityResponse(status=200, headers={}, body=payload)

    store = PITReceiptStore(str(tmp_path / "store"))
    transport = MarketOnlyTransport()
    collector = _collector(
        api,
        transport,
        store,
        FakeTrustedClock(),
        source_profile="jiaoch",
        api_url="http://jiaoch.site",
        allowed_hosts=("jiaoch.site",),
        request_protocol="tushare-path-per-interface/v1",
        workers=2,
        row_cap_overrides={"stk_limit": 10_000},
    )

    first = collector.collect_current_pool_market(
        start_date="2024-01-01",
        end_date="2024-01-03",
        resume=True,
        batch_size=2,
        progress_path=tmp_path / "progress.json",
    )
    called_apis = [call["api_name"] for call in transport.calls]
    assert called_apis.count("trade_cal") == 1  # jiaoch 只抓 SSE
    for api_name in ("daily", "adj_factor", "stk_limit", "suspend_d"):
        assert called_apis.count(api_name) == 2
    assert set(called_apis) == {
        "trade_cal",
        "daily",
        "adj_factor",
        "stk_limit",
        "suspend_d",
    }
    assert first["planned"] == 2
    assert first["fetched_dataset_count"] == 8

    calls_before_resume = len(transport.calls)
    second = collector.collect_current_pool_market(
        start_date="2024-01-01",
        end_date="2024-01-03",
        resume=True,
        batch_size=2,
        progress_path=tmp_path / "progress.json",
    )
    assert len(transport.calls) == calls_before_resume
    assert second["reused_session_count"] == 2
    assert second["fetched_dataset_count"] == 0


def test_current_pool_market_only_persists_last_completed_batch_on_failure(tmp_path):
    api = _api()
    progress_path = tmp_path / "nested" / "progress.json"

    class MarketPlanningStore:
        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            return [
                "2024-01-02",
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
            ]

    collector = _collector(
        api,
        ScriptedTransport([]),
        MarketPlanningStore(),
        FakeTrustedClock(),
        source_profile="jiaoch",
        workers=1,
    )
    collector._collect_trade_calendars_once = lambda **_kwargs: {
        "controlled_receipt_count": 2,
        "attempt_count": 0,
    }

    def fail_second_batch(session, **_kwargs):
        if session == "2024-01-04":
            raise RuntimeError(f"provider failure {TOKEN}")
        return {
            "status": "published",
            "trade_date": session,
            "fetched_dataset_count": 4,
            "attempt_count": 4,
            "reused_published": False,
        }

    collector.collect_market_session_generation = fail_second_batch

    with pytest.raises(api.PITCollectionError) as exc_info:
        collector.collect_current_pool_market(
            start_date="2024-01-01",
            end_date="2024-01-05",
            resume=True,
            batch_size=2,
            progress_path=progress_path,
        )

    assert TOKEN not in str(exc_info.value)
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress == {
        "schema_version": "current-pool-market-progress/v1",
        "status": "failed",
        "mode": "current_pool_market_only",
        "phase": "market",
        "source_profile": "jiaoch",
        "start_date": "2024-01-01",
        "end_date": "2024-01-05",
        "resume_requested": True,
        "workers": 1,
        "batch_size": 2,
        "planned": 4,
        "completed": 2,
        "remaining": 2,
        "last_session": "2024-01-03",
        "current_universe_bias": True,
        "development_only": True,
        "live_proof": False,
    }
    assert TOKEN not in progress_path.read_text(encoding="utf-8")
    assert not list(progress_path.parent.glob("*.tmp"))


def test_current_pool_market_only_records_successful_partial_batch_on_failure(tmp_path):
    api = _api()
    progress_path = tmp_path / "progress.json"
    partial_success = threading.Event()

    class MarketPlanningStore:
        def common_open_sessions(self, *, start_date, end_date, exchanges=("SSE",)):
            return [
                "2024-01-02",
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
            ]

    collector = _collector(
        api,
        ScriptedTransport([]),
        MarketPlanningStore(),
        FakeTrustedClock(),
        source_profile="jiaoch",
        workers=2,
    )
    collector._collect_trade_calendars_once = lambda **_kwargs: {
        "controlled_receipt_count": 2,
        "attempt_count": 0,
    }

    def partial_batch(session, **_kwargs):
        if session == "2024-01-04":
            partial_success.set()
        elif session == "2024-01-05":
            assert partial_success.wait(timeout=2)
            raise RuntimeError("provider failure")
        return {
            "status": "published",
            "trade_date": session,
            "fetched_dataset_count": 4,
            "attempt_count": 4,
            "reused_published": False,
        }

    collector.collect_market_session_generation = partial_batch

    with pytest.raises(api.PITCollectionError):
        collector.collect_current_pool_market(
            start_date="2024-01-01",
            end_date="2024-01-05",
            resume=True,
            batch_size=2,
            progress_path=progress_path,
        )

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["status"] == "failed"
    assert progress["planned"] == 4
    assert progress["completed"] == 3
    assert progress["remaining"] == 1
    assert progress["last_session"] == "2024-01-04"


def test_current_pool_market_cli_defaults_to_jiaoch_and_emits_compact_safe_report(
    tmp_path, monkeypatch, capsys
):
    captured = {}
    monkeypatch.setenv("JIAOCH_TOKEN", TOKEN)
    monkeypatch.setenv("TUSHARE_TOKEN", "official-token-must-not-be-used")
    monkeypatch.delenv("JIAOCH_PROXY_URL", raising=False)
    monkeypatch.setattr(
        jobs,
        "get_settings",
        lambda: pytest.fail("market-only research CLI must not load app settings"),
    )

    class Collector:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def collect(self, **_kwargs):
            raise AssertionError("market-only CLI invoked full PIT collection")

        def collect_current_pool_market(self, **kwargs):
            captured["market_args"] = kwargs
            return {
                "status": "complete",
                "mode": "current_pool_market_only",
                "source_profile": "jiaoch",
                "planned": 2,
                "completed": 2,
                "remaining": 0,
                "current_universe_bias": True,
                "development_only": True,
                "live_proof": False,
            }

    monkeypatch.setattr(jobs, "ControlledTushareCollector", Collector, raising=False)
    monkeypatch.setattr(
        jobs,
        "UrllibTushareTransport",
        lambda **kwargs: captured.setdefault("transport_options", kwargs) or "transport",
        raising=False,
    )
    monkeypatch.setattr(jobs, "SystemTrustedClock", lambda: "clock", raising=False)

    progress_path = tmp_path / "progress.json"
    assert jobs.main(
        [
            "research-current-pool-fetch-market-jiaoch",
            "--store-dir",
            str(tmp_path / "store"),
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-01-03",
            "--max-attempts",
            "2",
            "--timeout",
            "7",
            "--progress-path",
            str(progress_path),
            "--temporal-role",
            "contaminated_diagnostic",
        ]
    ) == 0

    assert captured["source_profile"] == "jiaoch"
    assert captured["token"] == TOKEN
    assert captured["api_url"] == "http://jiaoch.site"
    assert captured["allowed_hosts"] == ("jiaoch.site",)
    assert captured["workers"] == 2
    assert captured["max_attempts"] == 2
    assert captured["timeout_s"] == 7.0
    assert captured["market_args"] == {
        "start_date": "2024-01-01",
        "end_date": "2024-01-03",
        "resume": True,
        "batch_size": 10,
        "progress_path": progress_path,
    }
    output = capsys.readouterr().out
    parsed_output = json.loads(output)
    assert parsed_output["mode"] == "current_pool_market_only"
    assert parsed_output["source_profile"] == "jiaoch"
    assert TOKEN not in output
    assert "official-token-must-not-be-used" not in output


@pytest.mark.parametrize(
    "invalid_args",
    [
        ["--workers", "0"],
        ["--workers", "3"],
        ["--batch-size", "0"],
        ["--batch-size", "51"],
        ["--max-attempts", "0"],
        ["--timeout", "0"],
        ["--timeout", "nan"],
        ["--source-profile", "official"],
    ],
)
def test_current_pool_market_cli_rejects_invalid_worker_and_batch_ranges_before_source(
    tmp_path, monkeypatch, invalid_args
):
    monkeypatch.setattr(
        jobs,
        "resolve_tushare_source",
        lambda *_args, **_kwargs: pytest.fail("invalid CLI args reached source resolution"),
    )

    with pytest.raises(SystemExit):
        jobs.main(
            [
                "research-current-pool-fetch-market-jiaoch",
                "--store-dir",
                str(tmp_path / "store"),
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-03",
                "--temporal-role",
                "contaminated_diagnostic",
                *invalid_args,
            ]
        )


def _pythonpath_with_blocked_pypdf(tmp_path, source: str) -> tuple[dict[str, str], Path]:
    project_root = Path(__file__).resolve().parents[1]
    blocked = tmp_path / "blocked-pypdf"
    blocked.mkdir()
    (blocked / "pypdf.py").write_text(source, encoding="utf-8")
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    entries = [str(blocked), str(project_root)]
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env, project_root


def test_market_only_import_and_help_do_not_load_pdf_parser_without_pypdf(tmp_path):
    env, project_root = _pythonpath_with_blocked_pypdf(
        tmp_path,
        "raise AssertionError('market-only path imported pypdf')\n",
    )
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.jobs; import app.research_pit_collector; "
                "import app.research_pit_store; "
                "assert 'app.research_suspension_evidence' not in sys.modules"
            ),
        ],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr

    help_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.jobs",
            "research-current-pool-fetch-market-jiaoch",
            "--help",
        ],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "research-current-pool-fetch-market-jiaoch" in help_result.stdout
    assert "--source-profile" not in help_result.stdout
    assert "--api-url" not in help_result.stdout
    assert "--allow-insecure-official-http" not in help_result.stdout


def test_cninfo_pdf_ingest_reports_missing_pypdf_only_when_command_is_used(tmp_path):
    env, project_root = _pythonpath_with_blocked_pypdf(
        tmp_path,
        (
            "raise ModuleNotFoundError("
            '"No module named \'pypdf\'", name="pypdf")\n'
        ),
    )
    start_pdf = tmp_path / "start.pdf"
    resume_pdf = tmp_path / "resume.pdf"
    start_pdf.write_bytes(b"%PDF-1.4\n")
    resume_pdf.write_bytes(b"%PDF-1.4\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.jobs",
            "research-pit-ingest-cninfo-suspension",
            "--store-dir",
            str(tmp_path / "store"),
            "--ts-code",
            "300114.SZ",
            "--start-pdf-path",
            str(start_pdf),
            "--start-source-url",
            "https://static.cninfo.com.cn/finalpage/2023-01-12/1.PDF",
            "--start-published-at",
            "2023-01-12T00:00:00+08:00",
            "--start-retrieved-at",
            "2026-07-13T10:00:00+08:00",
            "--resume-pdf-path",
            str(resume_pdf),
            "--resume-source-url",
            "https://static.cninfo.com.cn/finalpage/2023-02-02/2.PDF",
            "--resume-published-at",
            "2023-02-02T00:00:00+08:00",
            "--resume-retrieved-at",
            "2026-07-13T10:00:01+08:00",
        ],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert (
        "CNINFO suspension PDF support requires optional dependency 'pypdf'"
        in result.stderr
    )
