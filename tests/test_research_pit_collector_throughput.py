"""Latency throughput proof and Python-heap scaling smoke for the PIT collector.

Run with ``pytest -s tests/test_research_pit_collector_throughput.py`` to print
the measured wall-time table.  The fixture deliberately uses the real receipt
store and every production collect/stage/publish/audit path; only upstream I/O
latency is synthetic.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import tracemalloc
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research_partitions import load_temporal_partition_contract
from app.research_pit_collector import ControlledTushareCollector, HttpEntityResponse
from app.research_pit_store import PITReceiptStore


TOKEN = "throughput-fixture-token"
SESSIONS = ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05")
NETWORK_DELAY_S = 0.060


class FrozenTrustedClock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._monotonic = 1_000_000

    def assert_synchronized(self):
        return {"source": "throughput-test", "synchronized": True}

    def now_utc(self):
        return datetime(2024, 1, 3, 8, 0, tzinfo=timezone.utc)

    def monotonic_ns(self):
        with self._lock:
            self._monotonic += 1
            return self._monotonic


def _session_dates(start_date, end_date):
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    return [
        (start.fromordinal(ordinal)).strftime("%Y%m%d")
        for ordinal in range(start.toordinal(), end.toordinal() + 1)
        if ordinal > start.toordinal()
    ]


def _rows(dataset, params, stock_count=None):
    if stock_count is not None:
        stocks = [f"{600000 + index:06d}.SH" for index in range(stock_count)]
        if dataset == "trade_cal":
            exchange = params["exchange"]
            sessions = _session_dates(params["start_date"], params["end_date"])
            return [[exchange, params["start_date"], 0, "20231229"], *[
                [exchange, session, 1, "20231229" if index == 0 else sessions[index - 1]]
                for index, session in enumerate(sessions)
            ]]
        if dataset == "stock_basic":
            if (params["exchange"], params["list_status"]) != ("SSE", "L"):
                return []
            return [
                [code, code.split(".")[0], f"S{index}", "SSE", "主板", "L", "20100101", None]
                for index, code in enumerate(stocks)
            ]
        trade_date = params["trade_date"]
        if dataset == "daily":
            return [[code, trade_date, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100] for code in stocks]
        if dataset == "adj_factor":
            return [[code, trade_date, 1.5] for code in stocks]
        if dataset == "stk_limit":
            return [[trade_date, code, 10.0, 11.0, 9.0] for code in stocks]
        if dataset == "suspend_d":
            return []
        return [[trade_date, code, f"S{index}", "工业", "20100101"] for index, code in enumerate(stocks)]
    if dataset == "trade_cal":
        exchange = params["exchange"]
        sessions = _session_dates(params["start_date"], params["end_date"])
        return [[exchange, params["start_date"], 0, "20231229"], *[
            [exchange, session, 1, "20231229" if index == 0 else sessions[index - 1]]
            for index, session in enumerate(sessions)
        ]]
    if dataset == "stock_basic":
        shard = (params["exchange"], params["list_status"])
        if shard == ("SSE", "L"):
            return [["600001.SH", "600001", "A", "SSE", "主板", "L", "20100101", None]]
        if shard == ("SZSE", "D"):
            return [["000002.SZ", "000002", "B", "SZSE", "主板", "D", "20100101", "20240103"]]
        return []
    trade_date = params["trade_date"]
    if dataset == "daily":
        return [["600001.SH", trade_date, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]]
    if dataset == "adj_factor":
        return [["600001.SH", trade_date, 1.5]]
    if dataset == "stk_limit":
        return [[trade_date, "600001.SH", 10.0, 11.0, 9.0]]
    if dataset == "suspend_d":
        return [["000002.SZ", trade_date, "09:30:00", "S"]] if trade_date == "20240102" else []
    rows = [[trade_date, "600001.SH", "A", "银行", "20100101"]]
    if trade_date == "20240102":
        rows.append([trade_date, "000002.SZ", "B", "地产", "20100101"])
    return rows


class LatencyControlledTransport:
    def __init__(self, delay_s=NETWORK_DELAY_S, stock_count=None):
        self.delay_s = delay_s
        self.stock_count = stock_count
        self.calls = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def post(self, *, url, headers, body, timeout_s, max_body_bytes):
        request = json.loads(body)
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            # A fixed, GIL-releasing delay models upstream latency.  The active
            # counter is the synchronization barrier proving requests overlap.
            time.sleep(self.delay_s)
            params = request["params"]
            dataset = request["api_name"]
            canonical = (dataset, json.dumps(params, sort_keys=True), request["fields"])
            with self._lock:
                self.calls.append(canonical)
            payload = json.dumps(
                {
                    "request_id": f"canonical-{dataset}-{canonical[1]}",
                    "code": 0,
                    "msg": "",
                    "data": {
                        "fields": request["fields"].split(","),
                        "items": _rows(dataset, params, self.stock_count),
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            return HttpEntityResponse(200, {}, payload, body_complete=True)
        finally:
            with self._lock:
                self.active -= 1


@dataclass
class Measurement:
    workers: int
    elapsed_s: float
    max_active: int
    peak_bytes: Optional[int]
    calls: list
    report: dict
    audit: dict
    store: PITReceiptStore
    rss_delta_bytes: Optional[int] = None
    round_index: Optional[int] = None
    position: Optional[int] = None
    pid: int = 0
    logical_cpu_count: int = 0
    affinity: Optional[tuple[int, ...]] = None
    elapsed_ns: int = 0
    timed_phase: str = "collector.collect_only"


def _collector(store, transport, workers):
    contract = load_temporal_partition_contract("data/research_partitions/frozen-v1.json")
    return ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=transport,
        clock=FrozenTrustedClock(),
        max_attempts=1,
        sleeper=lambda _seconds: None,
        temporal_contract=contract,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
        workers=workers,
    )


def _rss_bytes():
    """Best-effort process RSS; tracemalloc excludes SQLite/native allocations."""
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except (ImportError, OSError):
        return None


def _cpu_metadata():
    logical_cpu_count = os.cpu_count() or 1
    try:
        import psutil

        affinity = tuple(psutil.Process().cpu_affinity())
    except (ImportError, OSError):
        affinity = None
    return logical_cpu_count, affinity


def _fresh_e_root(label):
    root = Path("tmp") / f"throughput-{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def _measure(
    root,
    workers,
    *,
    label="default",
    start_date="2024-01-01",
    end_date="2024-01-03",
    delay_s=NETWORK_DELAY_S,
    stock_count=None,
    capture_python_heap=True,
    round_index=None,
    position=None,
):
    store_path = root / f"store-{label}-{workers}"
    assert not store_path.exists(), f"benchmark store root must be fresh: {store_path}"
    assert not tracemalloc.is_tracing(), "benchmark environment has active tracemalloc"
    assert not multiprocessing.active_children(), "benchmark environment has active child processes"
    assert not [
        thread for thread in threading.enumerate()
        if thread.name.startswith("ThreadPoolExecutor")
    ], "benchmark environment has active executor threads"
    logical_cpu_count, affinity = _cpu_metadata()
    store = PITReceiptStore(str(store_path))
    transport = LatencyControlledTransport(delay_s, stock_count)
    collector = _collector(store, transport, workers)
    before_threads = {thread.ident for thread in threading.enumerate()}
    rss_before = _rss_bytes()
    peak = None
    if capture_python_heap:
        tracemalloc.start()
    try:
        started_ns = time.perf_counter_ns()
        report = collector.collect(start_date=start_date, end_date=end_date, workers=workers)
        elapsed_ns = time.perf_counter_ns() - started_ns
        elapsed = elapsed_ns / 1_000_000_000
        if capture_python_heap:
            _, peak = tracemalloc.get_traced_memory()
    finally:
        if capture_python_heap:
            tracemalloc.stop()
    leaked = [
        thread
        for thread in threading.enumerate()
        if thread.ident not in before_threads and thread.name.startswith("ThreadPoolExecutor")
    ]
    assert leaked == []
    assert not multiprocessing.active_children(), "collector left active child processes"
    rss_after = _rss_bytes()
    audit = store.audit_coverage(start_date=start_date, end_date=end_date)
    rss_delta = None if rss_before is None or rss_after is None else max(0, rss_after - rss_before)
    return Measurement(
        workers, elapsed, transport.max_active, peak, transport.calls,
        report, audit, store, rss_delta, round_index, position, os.getpid(),
        logical_cpu_count, affinity, elapsed_ns, "collector.collect_only",
    )


def _canonical_market_refs(audit):
    return [
        # Manifest/lineage hashes intentionally bind random attempt/generation
        # identities; canonical business references exclude those identities.
        {key: ref[key] for key in ("trade_date", "vintage")}
        for ref in audit["market_generation_refs"]
    ]


def _normalized_table_counts(connection):
    tables = (
        "security_master", "trade_sessions", "daily_universe",
        "stock_basic_generation_rows", "market_session_generation_rows_daily",
        "market_session_generation_rows_adj_factor",
        "market_session_generation_rows_stk_limit",
        "market_session_generation_rows_suspend_d",
    )
    return {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }


def _assert_terminal_event_lineage(store, expected_attempts):
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        grouped = connection.execute(
            """
            SELECT attempt.attempt_id, attempt.dataset, attempt.raw_sha256,
                   attempt.request_semantics_sha256, COUNT(event.event_sequence) AS event_count,
                   MIN(event.status) AS status, MIN(event.details_json) AS details_json,
                   receipt.raw_sha256 AS receipt_raw_sha256,
                   stock.generation_id AS stock_generation_id,
                   stock.request_semantics_sha256 AS stock_semantics_sha256,
                   market.generation_id AS market_generation_id,
                   market.request_semantics_sha256 AS market_semantics_sha256
            FROM fetch_attempts AS attempt
            LEFT JOIN fetch_promotion_events AS event ON event.attempt_id = attempt.attempt_id
            LEFT JOIN receipts AS receipt
              ON receipt.dataset = attempt.dataset AND receipt.partition_key = attempt.partition_key
            LEFT JOIN stock_basic_generation_shards AS stock ON stock.attempt_id = attempt.attempt_id
            LEFT JOIN market_session_generation_shards AS market ON market.attempt_id = attempt.attempt_id
            GROUP BY attempt.attempt_id
            ORDER BY attempt.attempt_sequence
            """
        ).fetchall()
        orphan_events = connection.execute(
            """SELECT COUNT(*) FROM fetch_promotion_events AS event
               LEFT JOIN fetch_attempts AS attempt ON attempt.attempt_id = event.attempt_id
               WHERE attempt.attempt_id IS NULL"""
        ).fetchone()[0]
    assert len(grouped) == expected_attempts
    assert orphan_events == 0
    assert {row["event_count"] for row in grouped} == {1}
    for row in grouped:
        details = json.loads(row["details_json"])
        if row["dataset"] in {"trade_cal", "bak_basic"}:
            assert row["status"] in {"stored", "reused"}
            assert details["receipt_raw_sha256"] == row["raw_sha256"]
            assert row["receipt_raw_sha256"] == row["raw_sha256"]
        elif row["dataset"] == "stock_basic":
            assert row["status"] == "generation_staged"
            assert details["generation_id"] == row["stock_generation_id"]
            assert details["raw_sha256"] == row["raw_sha256"]
            assert row["stock_semantics_sha256"] == row["request_semantics_sha256"]
            assert details["logical_partition_key"]
        else:
            assert row["status"] == "market_session_staged"
            assert details["generation_id"] == row["market_generation_id"]
            assert details["dataset"] == row["dataset"]
            assert details["raw_sha256"] == row["raw_sha256"]
            assert row["market_semantics_sha256"] == row["request_semantics_sha256"]


def _throughput_sample_schedule():
    return (
        (1, 2, 8, 4),
        (2, 4, 1, 8),
        (4, 8, 2, 1),
        (8, 1, 4, 2),
    )


def _paired_measurements(samples, left_workers, right_workers):
    left = {item.round_index: item for item in samples[left_workers]}
    right = {item.round_index: item for item in samples[right_workers]}
    assert None not in left and None not in right
    assert set(left) == set(right)
    assert len(left) == len(samples[left_workers])
    assert len(right) == len(samples[right_workers])
    return [(left[index], right[index]) for index in sorted(left)]


def _subprocess_diagnostic(result):
    payload = result.stderr or result.stdout or b""
    return bytes(payload).decode("utf-8", errors="replace")


def _json_stdout(result):
    assert isinstance(result.stdout, (bytes, bytearray)), _subprocess_diagnostic(result)
    return json.loads(bytes(result.stdout).decode("utf-8", errors="strict"))


def test_throughput_child_protocol_uses_binary_capture_and_strict_json_utf8():
    result = subprocess.CompletedProcess(
        args=["synthetic-child"],
        returncode=0,
        stdout=b'{"workers":4,"elapsed_s":1.25}',
        stderr=b"\xd6",
    )

    assert _json_stdout(result) == {"workers": 4, "elapsed_s": 1.25}
    assert "\ufffd" in _subprocess_diagnostic(result)

    with pytest.raises(UnicodeDecodeError):
        _json_stdout(
            subprocess.CompletedProcess(
                args=["synthetic-child"], returncode=0, stdout=b"\xd6", stderr=b""
            )
        )


def test_throughput_child_commands_force_utf8_before_protocol_output(monkeypatch, tmp_path):
    captured = []

    def intercepted_run(args, **kwargs):
        captured.append((args, kwargs))
        raise RuntimeError("intercepted child launch")

    monkeypatch.setattr(subprocess, "run", intercepted_run)

    with pytest.raises(RuntimeError, match="intercepted child launch"):
        _measure_isolated(
            tmp_path,
            2,
            label="isolated",
            end_date="2024-01-05",
            round_index=0,
            position=0,
        )
    with pytest.raises(RuntimeError, match="intercepted child launch"):
        _measure_pair_isolated(tmp_path, 0, {1: 0, 4: 1})

    assert len(captured) == 2
    for args, kwargs in captured:
        assert args[0] == sys.executable
        assert args[1:3] == ["-X", "utf8"]
        assert kwargs == {"capture_output": True, "check": False}


def _measure_isolated(root, workers, *, label, end_date, round_index, position):
    assert not multiprocessing.active_children(), "benchmark parent has active child processes"
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(Path(__file__).resolve()),
            "--throughput-worker",
            str(root.resolve()),
            str(workers),
            label,
            end_date,
            str(round_index),
            str(position),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, _subprocess_diagnostic(result)
    payload = _json_stdout(result)
    assert not multiprocessing.active_children(), "benchmark child process did not exit"
    return _measurement_from_payload(root, payload, label)


def _measurement_from_payload(root, payload, label):
    workers = payload["workers"]
    store_path = root / f"store-{label}-{workers}"
    assert store_path.is_dir()
    return Measurement(
        payload["workers"], payload["elapsed_s"], payload["max_active"],
        payload["peak_bytes"], [tuple(call) for call in payload["calls"]], payload["report"],
        payload["audit"], PITReceiptStore(str(store_path)), payload["rss_delta_bytes"],
        payload["round_index"], payload["position"], payload["pid"],
        payload["logical_cpu_count"], tuple(payload["affinity"]) if payload["affinity"] else None,
        payload["elapsed_ns"], payload["timed_phase"],
    )


def _measure_pair_isolated(root, round_index, positions):
    assert not multiprocessing.active_children(), "benchmark parent has active child processes"
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(Path(__file__).resolve()),
            "--throughput-pair",
            str(root.resolve()),
            str(round_index),
            str(positions[1]),
            str(positions[4]),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, _subprocess_diagnostic(result)
    payloads = _json_stdout(result)
    assert len(payloads) == 2
    assert not multiprocessing.active_children(), "benchmark pair child did not exit"
    return sorted(
        (_measurement_from_payload(root, payload, payload["label"]) for payload in payloads),
        key=lambda measurement: measurement.workers,
    )


def test_full_collector_parallel_throughput_and_business_equivalence():
    tmp_path = _fresh_e_root("full")
    workers_set = (1, 2, 4, 8)
    samples = {workers: [] for workers in workers_set}
    for round_index, round_workers in enumerate(_throughput_sample_schedule()):
        positions = {workers: position for position, workers in enumerate(round_workers)}
        pair = _measure_pair_isolated(tmp_path, round_index, positions)
        samples[1].append(pair[0])
        samples[4].append(pair[1])
        for position, workers in enumerate(round_workers):
            if workers in {1, 4}:
                continue
            samples[workers].append(
                _measure_isolated(
                    tmp_path,
                    workers,
                    label=f"throughput-r{round_index}-p{position}-w{workers}",
                    end_date="2024-01-05",
                    round_index=round_index,
                    position=position,
                )
            )
    heap_samples = {
        workers: _measure(
            tmp_path,
            workers,
            label=f"heap-{workers}",
            end_date="2024-01-05",
            delay_s=0,
            capture_python_heap=True,
        )
        for workers in workers_set
    }
    measurements = [measurement for group in samples.values() for measurement in group]
    baseline = samples[1][0]
    expected_calls = 2 + 8 + len(SESSIONS) * 5
    with sqlite3.connect(baseline.store.database_path) as connection:
        baseline_table_counts = _normalized_table_counts(connection)
    business_keys = {
        "status", "start_date", "end_date", "open_session_count",
        "planned_receipt_count", "controlled_receipt_count",
        "controlled_request_lineage_complete", "attempt_count", "stored_count",
        "reused_count", "skipped_count", "error_count", "temporal_role",
        "temporal_contract_sha256", "market_session_generation_count", "rows_sha256",
    }

    for measured in measurements:
        assert measured.pid > 0
        assert measured.pid != os.getpid()
        assert measured.logical_cpu_count >= 1
        assert measured.affinity is None or measured.affinity
        assert measured.elapsed_ns > 0
        assert measured.timed_phase == "collector.collect_only"
        assert len(measured.calls) == expected_calls
        assert len(set(measured.calls)) == expected_calls
        assert sorted(measured.calls) == sorted(baseline.calls)
        assert measured.max_active <= measured.workers
        # The top-level report counts controlled receipts plus stock shards;
        # market-generation attempts are represented by their generation refs.
        assert measured.report["attempt_count"] == 2 + 8 + len(SESSIONS)
        assert measured.audit["status"] == "passed"
        assert measured.audit["session_count"] == len(SESSIONS)
        assert measured.audit["market_generation_count"] == len(SESSIONS)
        assert measured.audit["market_reconciliation_root_sha256"] == baseline.audit["market_reconciliation_root_sha256"]
        assert _canonical_market_refs(measured.audit) == _canonical_market_refs(baseline.audit)
        assert {key: measured.report[key] for key in business_keys} == {
            key: baseline.report[key] for key in business_keys
        }
        attempts = measured.store.fetch_attempts()
        assert len(attempts) == expected_calls
        assert {attempt["attempt_no"] for attempt in attempts} == {1}
        semantics = sorted(
            json.dumps(attempt["request_semantics"], sort_keys=True) for attempt in attempts
        )
        baseline_semantics = sorted(
            json.dumps(attempt["request_semantics"], sort_keys=True)
            for attempt in baseline.store.fetch_attempts()
        )
        assert semantics == baseline_semantics
        with sqlite3.connect(measured.store.database_path) as connection:
            attempts_count = connection.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0]
            events_count = connection.execute("SELECT COUNT(*) FROM fetch_promotion_events").fetchone()[0]
            table_counts = _normalized_table_counts(connection)
        assert attempts_count == events_count == expected_calls
        _assert_terminal_event_lineage(measured.store, expected_calls)
        assert table_counts == baseline_table_counts
        assert table_counts == {
            "security_master": 0,
            "trade_sessions": 10,
            "daily_universe": 5,
            "stock_basic_generation_rows": 2,
            "market_session_generation_rows_daily": 4,
            "market_session_generation_rows_adj_factor": 4,
            "market_session_generation_rows_stk_limit": 4,
            "market_session_generation_rows_suspend_d": 1,
        }

        calls_before_resume = len(measured.calls)
        resumed = _collector(measured.store, _ResumeForbiddenTransport(), measured.workers)
        resume_report = resumed.collect(
            start_date="2024-01-01", end_date="2024-01-05",
            workers=measured.workers,
        )
        assert resume_report["attempt_count"] == 0
        assert len(measured.calls) == calls_before_resume

    assert min(item.max_active for item in samples[2]) >= 2
    assert min(item.max_active for item in samples[4]) >= 4
    assert min(item.max_active for item in samples[8]) >= 6
    assert all(
        sample.peak_bytes is not None and sample.peak_bytes < 32 * 1024 * 1024
        for sample in heap_samples.values()
    )
    serial_median = statistics.median(item.elapsed_s for item in samples[1])
    assert len(samples[1]) == len(samples[4])
    paired = _paired_measurements(samples, 1, 4)
    paired_speedups = [serial.elapsed_s / parallel.elapsed_s for serial, parallel in paired]
    paired_speedup_median = statistics.median(paired_speedups)
    assert paired_speedup_median >= 2.2
    assert min(paired_speedups) >= 1.8

    print("\nworkers  median_s  speedup  worst_pair  max_active  peak_mib")
    for workers in workers_set:
        group = samples[workers]
        median_s = statistics.median(item.elapsed_s for item in group)
        assert len(samples[1]) == len(group)
        paired_group = _paired_measurements(samples, 1, workers)
        worst = min(serial.elapsed_s / parallel.elapsed_s for serial, parallel in paired_group)
        print(f"{workers:>7}  {median_s:>8.3f}  {serial_median / median_s:>7.2f}x  "
              f"{worst:>10.2f}x  {max(item.max_active for item in group):>10}  "
              f"{heap_samples[workers].peak_bytes / 1024 / 1024:>8.2f}")


class _ResumeForbiddenTransport:
    def post(self, **kwargs):
        raise AssertionError("resume of a complete collection must use zero network calls")


def test_full_collector_python_heap_scaling_smoke():
    tmp_path = _fresh_e_root("heap")
    # tracemalloc observes Python allocations only. RSS is optional diagnostic
    # output when psutil happens to be installed; it is deliberately not a gate.
    small = _measure(
        tmp_path, 4, label="memory-small", end_date="2024-01-03", delay_s=0,
        stock_count=100,
    )
    large = _measure(
        tmp_path, 4, label="memory-large", end_date="2024-01-13", delay_s=0,
        stock_count=100,
    )
    assert small.report["open_session_count"] == 2
    assert large.report["open_session_count"] == 12
    with sqlite3.connect(small.store.database_path) as connection:
        small_counts = _normalized_table_counts(connection)
    with sqlite3.connect(large.store.database_path) as connection:
        large_counts = _normalized_table_counts(connection)
    assert small_counts == {
        "security_master": 0, "trade_sessions": 6, "daily_universe": 200,
        "stock_basic_generation_rows": 100,
        "market_session_generation_rows_daily": 200,
        "market_session_generation_rows_adj_factor": 200,
        "market_session_generation_rows_stk_limit": 200,
        "market_session_generation_rows_suspend_d": 0,
    }
    assert large_counts == {
        "security_master": 0, "trade_sessions": 26, "daily_universe": 1200,
        "stock_basic_generation_rows": 100,
        "market_session_generation_rows_daily": 1200,
        "market_session_generation_rows_adj_factor": 1200,
        "market_session_generation_rows_stk_limit": 1200,
        "market_session_generation_rows_suspend_d": 0,
    }
    small_rows = sum(small_counts.values())
    large_rows = sum(large_counts.values())
    row_multiple = large_rows / small_rows
    assert large.peak_bytes < 32 * 1024 * 1024
    assert large.peak_bytes / small.peak_bytes < row_multiple * 0.8
    assert (large.peak_bytes - small.peak_bytes) / (large_rows - small_rows) < 16 * 1024
    _assert_terminal_event_lineage(small.store, 20)
    _assert_terminal_event_lineage(large.store, 70)
    print(
        "\nmemory small/large: "
        f"rows={small_rows}/{large_rows}, "
        f"py_peak_mib={small.peak_bytes / 1024 / 1024:.2f}/"
        f"{large.peak_bytes / 1024 / 1024:.2f}, "
        f"rss_delta_mib={None if large.rss_delta_bytes is None else round(large.rss_delta_bytes / 1024 / 1024, 2)}"
    )


def test_throughput_measurement_excludes_python_heap_tracing(monkeypatch):
    def unexpected_tracemalloc_call(*_args, **_kwargs):
        raise AssertionError("throughput timing must not enable Python heap tracing")

    monkeypatch.setattr(tracemalloc, "start", unexpected_tracemalloc_call)
    monkeypatch.setattr(tracemalloc, "stop", unexpected_tracemalloc_call)
    monkeypatch.setattr(tracemalloc, "get_traced_memory", unexpected_tracemalloc_call)
    tmp_path = _fresh_e_root("no-heap-tracing")
    measured = _measure(
        tmp_path,
        1,
        label="no-heap-tracing",
        end_date="2024-01-02",
        delay_s=0,
        capture_python_heap=False,
    )
    assert measured.peak_bytes is None


def test_throughput_measurement_rejects_preexisting_tracemalloc(monkeypatch):
    monkeypatch.setattr(tracemalloc, "is_tracing", lambda: True)
    tmp_path = _fresh_e_root("preexisting-tracemalloc")
    with pytest.raises(AssertionError, match="active tracemalloc"):
        _measure(tmp_path, 1, label="preexisting-tracemalloc", capture_python_heap=False)


def test_throughput_measurement_rejects_preexisting_child_process(monkeypatch):
    monkeypatch.setattr(multiprocessing, "active_children", lambda: [object()])
    tmp_path = _fresh_e_root("preexisting-child")
    with pytest.raises(AssertionError, match="active child processes"):
        _measure(tmp_path, 1, label="preexisting-child", capture_python_heap=False)


def test_throughput_measurement_requires_fresh_store_root():
    tmp_path = _fresh_e_root("existing")
    (tmp_path / "store-existing-1").mkdir()
    with pytest.raises(AssertionError, match="store root must be fresh"):
        _measure(tmp_path, 1, label="existing", capture_python_heap=False)


def test_throughput_sample_schedule_is_latin_balanced():
    rounds = _throughput_sample_schedule()
    workers = (1, 2, 4, 8)
    assert len(rounds) == len(workers)
    assert all(tuple(sorted(round_)) == workers for round_ in rounds)
    for worker in workers:
        assert sorted(round_.index(worker) for round_ in rounds) == list(range(len(workers)))


def test_throughput_sample_schedule_balances_directed_carryover():
    rounds = _throughput_sample_schedule()
    transitions = [
        (round_[position], round_[position + 1])
        for round_ in rounds
        for position in range(len(round_) - 1)
    ]
    assert len(transitions) == 12
    assert len(set(transitions)) == len(transitions)


def _measurement_payload(measurement, label):
    return {
        "label": label,
        "workers": measurement.workers,
        "elapsed_s": measurement.elapsed_s,
        "max_active": measurement.max_active,
        "peak_bytes": measurement.peak_bytes,
        "calls": measurement.calls,
        "report": measurement.report,
        "audit": measurement.audit,
        "rss_delta_bytes": measurement.rss_delta_bytes,
        "round_index": measurement.round_index,
        "position": measurement.position,
        "pid": measurement.pid,
        "logical_cpu_count": measurement.logical_cpu_count,
        "affinity": measurement.affinity,
        "elapsed_ns": measurement.elapsed_ns,
        "timed_phase": measurement.timed_phase,
    }


def _throughput_worker_main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--throughput-pair":
        root = Path(sys.argv[2])
        round_index = int(sys.argv[3])
        positions = {1: int(sys.argv[4]), 4: int(sys.argv[5])}
        payloads = []
        order = tuple(sorted((1, 4), key=lambda workers: positions[workers]))
        for workers in order:
            label = f"throughput-r{round_index}-p{positions[workers]}-w{workers}"
            measurement = _measure(
                root,
                workers,
                label=label,
                end_date="2024-01-05",
                capture_python_heap=False,
                round_index=round_index,
                position=positions[workers],
            )
            payloads.append(_measurement_payload(measurement, label))
        print(json.dumps(payloads, ensure_ascii=False, separators=(",", ":")))
        return
    if len(sys.argv) != 8 or sys.argv[1] != "--throughput-worker":
        return
    measurement = _measure(
        Path(sys.argv[2]),
        int(sys.argv[3]),
        label=sys.argv[4],
        end_date=sys.argv[5],
        capture_python_heap=False,
        round_index=int(sys.argv[6]),
        position=int(sys.argv[7]),
    )
    print(json.dumps(_measurement_payload(measurement, sys.argv[4]),
                     ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    _throughput_worker_main()
