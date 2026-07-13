"""Begin/resume state machine for ETF proxy generations.

These tests pin the deterministic scope/contract binding and the collecting
window transitions. They deliberately do NOT exercise staging, publishing, the
collector, or artifact copy -- only the begin/resume entry point. The final OOS
eligibility flag must stay False forever and the store must never touch the
network here.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app import research_pit_store
from app.research_pit_store import PITReceiptError, PITReceiptStore
from app.research_proxy_data import ETF_PROXY_REQUIRED_SYMBOLS


# WHY a fixed +08:00 base: the state machine reasons about elapsed wall-clock
# seconds between started_at and the caller-supplied ``now``. A pinned base lets
# us hit the < / == / > 3600 second boundaries exactly without floating-point
# drift.
BASE_NOW = datetime(2024, 1, 3, 16, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def _later(seconds: int) -> datetime:
    return BASE_NOW + timedelta(seconds=seconds)


def _insert_fetch_attempt(connection, attempt_id: str) -> None:
    """Minimal legal fetch_attempts row so a shard FK resolves.

    Foreign keys are ON in _connect, so we cannot stage a shard without a real
    parent attempt row. We insert the smallest row the NOT NULL contract accepts
    rather than going through the full attempt-recording API.
    """

    connection.execute(
        """
        INSERT INTO fetch_attempts (
            attempt_id, dataset, partition_key, endpoint, params_json,
            fields_json, request_semantics_json, request_semantics_sha256,
            wire_request_sha256, attempt_no, elapsed_ns, row_cap,
            response_headers_json, clock_attestation_json, body_complete,
            raw_bytes, outcome, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            "fund_daily",
            "etf-proxy",
            "fund_daily",
            "{}",
            "[]",
            "{}",
            "e" * 64,
            "f" * 64,
            1,
            0,
            5000,
            "{}",
            "{}",
            1,
            0,
            "captured",
            "2024-01-03T16:00:00+08:00",
        ),
    )


def _stage_shard(connection, generation_id: str, symbol: str, attempt_id: str) -> None:
    _insert_fetch_attempt(connection, attempt_id)
    connection.execute(
        """
        INSERT INTO etf_proxy_generation_shards (
            generation_id, symbol, attempt_id, request_semantics_sha256,
            retrieved_at, raw_sha256, raw_bytes, normalized_sha256, row_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generation_id,
            symbol,
            attempt_id,
            "e" * 64,
            "2024-01-03T16:00:00+08:00",
            "f" * 64,
            0,
            "g" * 64,
            1,
        ),
    )


def test_creates_first_generation_with_frozen_shape(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    result = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )

    assert result["generation_id"] == "etf-proxy-00000000000000000001"
    assert result["status"] == "collecting"
    assert result["vintage"] == "historical_backfill"
    assert result["start_date"] == "2024-01-02"
    assert result["end_date"] == "2024-03-31"
    # final OOS eligibility must never flip true from begin/resume.
    assert result["final_oos_eligible"] is False
    assert result["staged_symbols"] == []
    assert result["terminal_at"] is None
    assert result["terminal_reason"] is None
    assert isinstance(result["scope_key"], str) and result["scope_key"]
    assert isinstance(result["contract_sha256"], str) and len(result["contract_sha256"]) == 64


def test_resume_same_range_reuses_generation_and_persists(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )

    # Same range, later but still inside the window -> identical generation.
    second = store.begin_or_resume_etf_proxy_generation(
        _later(60), "2024-01-02", "2024-03-31"
    )
    assert second["generation_id"] == first["generation_id"]
    assert second["status"] == "collecting"

    # Reopen from disk: still the one collecting generation, no second row.
    reopened = PITReceiptStore(str(root))
    third = reopened.begin_or_resume_etf_proxy_generation(
        _later(120), "2024-01-02", "2024-03-31"
    )
    assert third["generation_id"] == first["generation_id"]
    with reopened._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generations"
        ).fetchone()[0] == 1


def test_different_date_ranges_are_independent_scopes(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    a = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    b = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-04-01", "2024-06-30"
    )

    assert a["scope_key"] != b["scope_key"]
    assert a["contract_sha256"] != b["contract_sha256"]
    assert a["generation_id"] != b["generation_id"]
    # Both scopes may collect concurrently (partial unique index is per-scope).
    with store._connect() as connection:
        collecting = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generations WHERE status = 'collecting'"
        ).fetchone()[0]
    assert collecting == 2


def test_same_range_contract_is_stable(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    store.begin_or_resume_etf_proxy_generation(_later(10), "2024-01-02", "2024-03-31")

    # Recompute the contract for the same range independently and confirm the
    # persisted value matches the deterministic binding.
    assert (
        first["contract_sha256"]
        == research_pit_store._etf_proxy_generation_contract_sha256("2024-01-02", "2024-03-31")
    )
    assert first["contract_sha256"] != research_pit_store._etf_proxy_generation_contract_sha256(
        "2024-01-02", "2024-03-30"
    )


def test_elapsed_under_window_keeps_original_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    # 3599s elapsed, incomplete, not forced -> resume the same generation.
    resumed = store.begin_or_resume_etf_proxy_generation(
        _later(3599), "2024-01-02", "2024-03-31"
    )
    assert resumed["generation_id"] == first["generation_id"]
    assert resumed["status"] == "collecting"


def test_elapsed_exactly_at_window_keeps_original_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    # Boundary: elapsed == window exactly must NOT abandon an incomplete
    # generation. Only strictly-greater elapsed does.
    resumed = store.begin_or_resume_etf_proxy_generation(
        _later(3600), "2024-01-02", "2024-03-31"
    )
    assert resumed["generation_id"] == first["generation_id"]
    assert resumed["status"] == "collecting"


def test_elapsed_over_window_abandons_stale_incomplete(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    resumed = store.begin_or_resume_etf_proxy_generation(
        _later(3601), "2024-01-02", "2024-03-31"
    )

    assert resumed["generation_id"] != first["generation_id"]
    assert resumed["status"] == "collecting"
    with store._connect() as connection:
        stale = connection.execute(
            "SELECT status, terminal_reason FROM etf_proxy_generations "
            "WHERE generation_id = ?",
            (first["generation_id"],),
        ).fetchone()
    assert dict(stale) == {"status": "abandoned", "terminal_reason": "stale_incomplete"}


def test_complete_generation_survives_past_window(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    with store._connect() as connection:
        _stage_shard(connection, first["generation_id"], ETF_PROXY_REQUIRED_SYMBOLS[0], "att-1")
        _stage_shard(connection, first["generation_id"], ETF_PROXY_REQUIRED_SYMBOLS[1], "att-2")

    # Both shards staged -> complete; even far past the window we must wait for
    # publish, never abandon a complete generation.
    resumed = store.begin_or_resume_etf_proxy_generation(
        _later(10_000), "2024-01-02", "2024-03-31"
    )
    assert resumed["generation_id"] == first["generation_id"]
    assert resumed["status"] == "collecting"
    assert resumed["terminal_reason"] is None


def test_force_new_abandons_even_when_incomplete(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    resumed = store.begin_or_resume_etf_proxy_generation(
        _later(10), "2024-01-02", "2024-03-31", force_new=True
    )

    assert resumed["generation_id"] != first["generation_id"]
    with store._connect() as connection:
        stale = connection.execute(
            "SELECT status, terminal_reason FROM etf_proxy_generations "
            "WHERE generation_id = ?",
            (first["generation_id"],),
        ).fetchone()
    assert dict(stale) == {"status": "abandoned", "terminal_reason": "force_new"}


def test_force_new_abandons_even_when_complete(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    with store._connect() as connection:
        _stage_shard(connection, first["generation_id"], ETF_PROXY_REQUIRED_SYMBOLS[0], "att-1")
        _stage_shard(connection, first["generation_id"], ETF_PROXY_REQUIRED_SYMBOLS[1], "att-2")

    resumed = store.begin_or_resume_etf_proxy_generation(
        _later(10), "2024-01-02", "2024-03-31", force_new=True
    )
    assert resumed["generation_id"] != first["generation_id"]
    with store._connect() as connection:
        stale = connection.execute(
            "SELECT terminal_reason FROM etf_proxy_generations WHERE generation_id = ?",
            (first["generation_id"],),
        ).fetchone()
    assert stale["terminal_reason"] == "force_new"


def test_clock_moved_backwards_is_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    store.begin_or_resume_etf_proxy_generation(BASE_NOW, "2024-01-02", "2024-03-31")
    with pytest.raises(PITReceiptError, match="clock moved backwards"):
        store.begin_or_resume_etf_proxy_generation(
            _later(-1), "2024-01-02", "2024-03-31"
        )


def test_naive_now_is_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    naive = datetime(2024, 1, 3, 16, 0, 0)
    with pytest.raises(PITReceiptError, match="timezone"):
        store.begin_or_resume_etf_proxy_generation(naive, "2024-01-02", "2024-03-31")


def test_invalid_vintage_is_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="vintage"):
        store.begin_or_resume_etf_proxy_generation(
            BASE_NOW, "2024-01-02", "2024-03-31", vintage="backfilled_after_seeing_results"
        )


def test_end_before_start_is_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="end_date"):
        store.begin_or_resume_etf_proxy_generation(BASE_NOW, "2024-03-31", "2024-01-02")


def test_invalid_date_is_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError):
        store.begin_or_resume_etf_proxy_generation(BASE_NOW, "not-a-date", "2024-03-31")


def test_staged_symbols_follow_frozen_symbol_order(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31"
    )
    # Insert shards in reverse order; the view must surface the frozen order.
    with store._connect() as connection:
        _stage_shard(connection, first["generation_id"], ETF_PROXY_REQUIRED_SYMBOLS[1], "att-2")
        _stage_shard(connection, first["generation_id"], ETF_PROXY_REQUIRED_SYMBOLS[0], "att-1")

    resumed = store.begin_or_resume_etf_proxy_generation(
        _later(60), "2024-01-02", "2024-03-31"
    )
    assert resumed["staged_symbols"] == list(ETF_PROXY_REQUIRED_SYMBOLS)


def test_live_forward_vintage_is_accepted(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    result = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-01-02", "2024-03-31", vintage="live_forward"
    )
    assert result["vintage"] == "live_forward"
    assert result["status"] == "collecting"


def test_concurrent_identical_scope_collapses_to_single_generation(tmp_path):
    """Real concurrent begin/resume under one scope must collapse to one row.

    WHY this exists: ``begin_or_resume_etf_proxy_generation`` is the entry point
    concurrent collector workers race through. The ONLY serialization it may lean
    on is SQLite's own ``BEGIN IMMEDIATE`` write lock plus the per-scope partial
    unique index -- there is no process-level lock in the store, and this test is
    forbidden from adding one. If that in-database serialization ever breaks
    (e.g. a read-then-write path that releases the write lock between the two, or
    a connection that never acquires it), N callers would insert N generations for
    a single scope and silently breach the one-collecting-generation invariant
    the rest of the suite assumes. Firing real threads -- not a serialized loop --
    is what catches that class of regression.
    """

    store = PITReceiptStore(str(tmp_path / "store"))

    workers = 8
    total_calls = 32

    def call():
        return store.begin_or_resume_etf_proxy_generation(
            BASE_NOW, "2024-01-02", "2024-03-31"
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(call) for _ in range(total_calls)]
        # Surface concurrency failures explicitly: a serialized-by-mistake path or
        # a busy-timeout that falls over under contention surfaces here as a real
        # exception rather than a misleading assertion later.
        raised = [future.exception() for future in futures]
        failures = [error for error in raised if error is not None]
        assert not failures, f"concurrent callers raised: {failures!r}"
        results = [future.result() for future in futures]

    # Every caller must observe the single collecting generation for this scope.
    generation_ids = {result["generation_id"] for result in results}
    assert len(generation_ids) == 1, (
        f"concurrent callers diverged into {len(generation_ids)} generations: "
        f"{sorted(generation_ids)}"
    )

    with store._connect() as connection:
        row_count = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generations"
        ).fetchone()[0]
        assert row_count == 1, f"expected exactly one generation row, got {row_count}"
        row = dict(
            connection.execute(
                "SELECT status, scope_key FROM etf_proxy_generations "
                "WHERE generation_id = ?",
                (results[0]["generation_id"],),
            ).fetchone()
        )

    # The lone row is still actively collecting, and its persisted scope_key is the
    # same one every caller saw -- proving the index bound the row, not just the
    # returned view.
    assert row["status"] == "collecting"
    assert row["scope_key"] == results[0]["scope_key"]
