"""Connection-internal market generation coverage helper.

WHY: coverage proves that every required open-market session in a sorted
trade-date list has a published, active, deep-verified generation frozen at its
latest head. It must reuse the per-day active-head + manifest + stored-lineage
deep verification, so a missing / collecting / rolled-back / tampered day fails
closed. It is a pure read over a single connection: it must never open a second
connection, never write legacy receipts, and never touch final_oos eligibility.

These tests are intentionally self-contained (own fixtures) so the existing
market-session fixtures are not modified.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.research_pit_store import (
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_ROW_CAPS,
    PITReceiptError,
    PITReceiptStore,
    _sha256,
)

BASE = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)

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


def _dataset_body(dataset, trade_date, rows, *, label="same"):
    import json

    return (
        json.dumps(
            {
                "request_id": f"market-{dataset}-{trade_date}-{label}",
                "code": 0,
                "msg": "",
                "data": {"fields": DATASET_FIELDS[dataset], "items": rows},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _default_rows(dataset, trade_date):
    wire = trade_date.replace("-", "")
    if dataset == "daily":
        return [["600001.SH", wire, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]]
    if dataset == "adj_factor":
        return [["600001.SH", wire, 1.5]]
    if dataset == "stk_limit":
        return [[wire, "600001.SH", 10.0, 11.0, 9.0]]
    return []  # suspend_d legitimately empty


def _record_market_attempt(store, dataset, trade_date, retrieved_at):
    import hashlib
    import json

    semantics = {
        "schema_version": "tushare-wire-request/v1",
        "dataset": dataset,
        "partition_key": trade_date,
        "api_name": dataset,
        "method": "POST",
        "url": "https://api.tushare.pro",
        "wire_params": {"trade_date": trade_date.replace("-", "")},
        "receipt_params": {"trade_date": trade_date},
        "fields": DATASET_FIELDS[dataset],
        "row_cap": MARKET_SESSION_ROW_CAPS[dataset],
    }
    raw = _dataset_body(dataset, trade_date, _default_rows(dataset, trade_date))
    wire_sha256 = hashlib.sha256(
        f"{dataset}:{trade_date}:{retrieved_at}".encode("utf-8")
    ).hexdigest()
    semantics_sha256 = hashlib.sha256(
        json.dumps(semantics, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return store.record_fetch_attempt(
        dataset=dataset,
        partition_key=trade_date,
        endpoint=dataset,
        params={"trade_date": trade_date},
        fields=DATASET_FIELDS[dataset],
        wire_request_sha256=wire_sha256,
        request_body_sha256=wire_sha256,
        request_semantics=semantics,
        request_semantics_sha256=semantics_sha256,
        raw_bytes=raw,
        http_status=200,
        started_at=(datetime.fromisoformat(retrieved_at) - timedelta(seconds=1)).isoformat(),
        retrieved_at=retrieved_at,
        elapsed_ns=1_000_000_000,
        row_cap=MARKET_SESSION_ROW_CAPS[dataset],
        body_complete=True,
    )


def _stage_four(store, generation_id, trade_date, started):
    for index, dataset in enumerate(MARKET_SESSION_DATASETS, 1):
        attempt = _record_market_attempt(
            store, dataset, trade_date, (started + timedelta(seconds=index)).isoformat()
        )
        store.stage_market_session_attempt(generation_id, dataset, attempt["attempt_id"])


def _publish_session(store, trade_date, started):
    """Build one fully-published four-shard generation bound to ``trade_date``."""

    generation = store.begin_or_resume_market_session_generation(started, trade_date)
    _stage_four(store, generation["generation_id"], trade_date, started)
    return store.publish_market_session_generation(generation["generation_id"])


def _coverage(store, sessions):
    """Call the helper inside one connection + transaction (read-only snapshot)."""

    with store._connect() as connection:
        connection.execute("BEGIN")
        return store._market_generation_coverage_on_connection(connection, list(sessions))


def test_coverage_helper_fails_closed_without_an_active_transaction(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _publish_session(store, "2024-01-02", BASE)

    with store._connect() as connection:
        # WHY: coverage proves a frozen snapshot, so it must refuse to run
        # outside an explicit transaction — a bare read could observe a
        # half-applied write.
        assert connection.in_transaction is False
        with pytest.raises(PITReceiptError, match="transaction"):
            store._market_generation_coverage_on_connection(
                connection, ["2024-01-02"]
            )


def test_two_day_coverage_stable_order_and_root(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = _publish_session(store, "2024-01-02", BASE)
    second = _publish_session(store, "2024-01-03", BASE + timedelta(days=1))

    result = _coverage(store, ["2024-01-02", "2024-01-03"])

    refs = result["refs"]
    assert [ref["trade_date"] for ref in refs] == ["2024-01-02", "2024-01-03"]
    # Each ref strictly carries exactly the five contract fields.
    assert all(set(ref) == {
        "trade_date",
        "generation_id",
        "manifest_sha256",
        "lineage_sha256",
        "vintage",
    } for ref in refs)
    assert refs[0]["generation_id"] == first["generation_id"]
    assert refs[1]["generation_id"] == second["generation_id"]
    assert refs[0]["manifest_sha256"] == first["manifest_sha256"]
    assert refs[0]["lineage_sha256"] == first["lineage_sha256"]
    assert refs[0]["vintage"] == "live_forward"
    assert result["count"] == 2
    assert result["root"] == _sha256(refs)

    # Deterministic: reversed input must yield the identical, sorted root.
    reversed_result = _coverage(store, ["2024-01-03", "2024-01-02"])
    assert reversed_result["root"] == result["root"]
    assert [ref["trade_date"] for ref in reversed_result["refs"]] == [
        "2024-01-02",
        "2024-01-03",
    ]

    # Read-only: final_oos eligibility is never flipped.
    with sqlite3.connect(store.database_path) as connection:
        eligible = connection.execute(
            "SELECT COUNT(*) FROM market_session_generations WHERE final_oos_eligible != 0"
        ).fetchone()[0]
    assert eligible == 0


def test_coverage_streams_one_verified_row_capsule_per_session(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = _publish_session(store, "2024-01-02", BASE)
    second = _publish_session(store, "2024-01-03", BASE + timedelta(days=1))
    statements = []
    consumed = []

    def consume(session, generation, rows_by_dataset):
        assert generation["trade_date"] == session
        assert set(rows_by_dataset) == set(MARKET_SESSION_DATASETS)
        assert all(
            row["generation_id"] == generation["generation_id"]
            for rows in rows_by_dataset.values()
            for row in rows
        )
        consumed.append(
            (
                session,
                generation["generation_id"],
                {dataset: len(rows_by_dataset[dataset]) for dataset in MARKET_SESSION_DATASETS},
            )
        )

    with store._connect() as connection:
        connection.execute("BEGIN")
        connection.set_trace_callback(statements.append)
        result = store._market_generation_coverage_on_connection(
            connection,
            ["2024-01-02", "2024-01-03"],
            on_verified_session=consume,
        )

    assert consumed == [
        (
            "2024-01-02",
            first["generation_id"],
            {"daily": 1, "adj_factor": 1, "stk_limit": 1, "suspend_d": 0},
        ),
        (
            "2024-01-03",
            second["generation_id"],
            {"daily": 1, "adj_factor": 1, "stk_limit": 1, "suspend_d": 0},
        ),
    ]
    assert set(result) == {"refs", "count", "root"}
    assert [ref["lineage_sha256"] for ref in result["refs"]] == [
        first["lineage_sha256"],
        second["lineage_sha256"],
    ]
    for dataset in MARKET_SESSION_DATASETS:
        table = f"market_session_generation_rows_{dataset}"
        materializations = [
            statement
            for statement in statements
            if table in statement and "GROUP BY" not in statement
        ]
        assert len(materializations) == 2


def test_missing_day_fails_closed(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _publish_session(store, "2024-01-02", BASE)
    # 2024-01-03 has no published generation (missing head).

    with pytest.raises(PITReceiptError, match="missing"):
        _coverage(store, ["2024-01-02", "2024-01-03"])


def test_stored_lineage_tamper_fails_closed(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = _publish_session(store, "2024-01-02", BASE)
    _publish_session(store, "2024-01-03", BASE + timedelta(days=1))

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generations SET lineage_sha256 = ? WHERE generation_id = ?",
            ("0" * 64, first["generation_id"]),
        )

    with pytest.raises(PITReceiptError, match="lineage"):
        _coverage(store, ["2024-01-02", "2024-01-03"])


def test_head_rollback_fails_closed(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    older = _publish_session(store, "2024-01-02", BASE)
    newer = _publish_session(store, "2024-01-02", BASE + timedelta(days=2))
    # Head now points at the newest generation; coherently rewind it (id AND
    # manifest) to the older published generation so the rollback detector,
    # not the manifest check, is what must fire.
    assert newer["generation_id"] != older["generation_id"]
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generation_head "
            "SET generation_id = ?, manifest_sha256 = ? WHERE trade_date = ?",
            (older["generation_id"], older["manifest_sha256"], "2024-01-02"),
        )

    with pytest.raises(PITReceiptError, match="rollback"):
        _coverage(store, ["2024-01-02"])


def test_underlying_row_tamper_fails_closed(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = _publish_session(store, "2024-01-02", BASE)
    _publish_session(store, "2024-01-03", BASE + timedelta(days=1))

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generation_rows_daily SET close = 999.0 "
            "WHERE generation_id = ?",
            (first["generation_id"],),
        )

    with pytest.raises(PITReceiptError):
        _coverage(store, ["2024-01-02", "2024-01-03"])
