import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.research_pit_store import PITReceiptError, PITReceiptStore


DAILY_FIELDS = ("trade_date", "ts_code", "name", "industry", "list_date")
STARTED_AT = "2024-01-02T15:59:59+08:00"
RETRIEVED_AT = "2024-01-02T16:00:00+08:00"


def _response(rows, *, code=0, msg=""):
    return json.dumps(
        {
            "request_id": "attempt-test",
            "code": code,
            "msg": msg,
            "data": {"fields": list(DAILY_FIELDS), "items": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _daily_response(symbol="600001.SH", name="A"):
    return _response([["20240102", symbol, name, "银行", "20100101"]])


def _wire_sha256(label="bak-basic-20240102"):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _record(
    store,
    *,
    raw_bytes,
    http_status=200,
    fields=DAILY_FIELDS,
    params=None,
    error_message=None,
):
    return store.record_fetch_attempt(
        dataset="bak_basic",
        partition_key="2024-01-02",
        endpoint="bak_basic",
        params=params or {"trade_date": "20240102"},
        fields=fields,
        wire_request_sha256=_wire_sha256(),
        raw_bytes=raw_bytes,
        http_status=http_status,
        started_at=STARTED_AT,
        retrieved_at=RETRIEVED_AT,
        elapsed_ns=1_000_000_000,
        row_cap=7000,
        body_complete=True,
        error_message=error_message,
    )


def _attempt(store, attempt_id):
    return next(row for row in store.fetch_attempts() if row["attempt_id"] == attempt_id)


def test_fetch_attempts_are_append_only_and_exact_response_bodies_use_one_cas_object(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    raw = _daily_response()

    first = _record(store, raw_bytes=raw)
    second = _record(store, raw_bytes=raw)

    assert first["attempt_id"] != second["attempt_id"]
    assert first["outcome"] == second["outcome"] == "captured"
    assert first["raw_sha256"] == second["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert first["raw_path"] == second["raw_path"]
    assert (store.root / first["raw_path"]).read_bytes() == raw
    assert len(list(store.raw_root.rglob("*.json"))) == 1
    assert first["wire_request_sha256"] == _wire_sha256()
    assert first["fields"] == list(DAILY_FIELDS)
    assert first["started_at"] == STARTED_AT
    assert first["retrieved_at"] == RETRIEVED_AT
    assert first["elapsed_ns"] == 1_000_000_000
    assert first["http_status"] == 200
    assert first["body_complete"] is True
    assert first["request_semantics"] == {
        "dataset": "bak_basic",
        "endpoint": "bak_basic",
        "params": {"trade_date": "2024-01-02"},
        "fields": list(DAILY_FIELDS),
    }
    attempts = store.fetch_attempts(partition_key="2024-01-02")
    assert [row["attempt_id"] for row in attempts] == sorted(
        [first["attempt_id"], second["attempt_id"]]
    )
    assert [row["outcome"] for row in attempts] == ["captured", "captured"]


def test_stored_promotion_is_durably_bound_to_its_attempt_and_survives_reopen(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    raw = _daily_response()
    attempt = _record(store, raw_bytes=raw)

    promoted = store.promote_fetch_attempt(attempt["attempt_id"])

    assert promoted["attempt_id"] == attempt["attempt_id"]
    assert promoted["status"] == "stored"
    assert promoted["receipt_raw_sha256"] == hashlib.sha256(raw).hexdigest()
    persisted = _attempt(store, attempt["attempt_id"])
    assert persisted["outcome"] == "captured"
    assert persisted["terminal_status"] == "stored"
    assert [event["status"] for event in persisted["promotion_events"]] == ["stored"]
    assert persisted["promotion_events"][0]["details"]["receipt_raw_sha256"] == promoted[
        "receipt_raw_sha256"
    ]

    reopened = PITReceiptStore(str(root))
    reopened_attempt = _attempt(reopened, attempt["attempt_id"])
    assert reopened_attempt["terminal_status"] == "stored"
    assert reopened_attempt["promotion_events"] == persisted["promotion_events"]
    assert reopened.verify_receipts()["verified_receipt_count"] == 1
    with sqlite3.connect(reopened.database_path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert list(connection.execute("PRAGMA foreign_key_check")) == []


def test_v2_receipt_store_migrates_attempt_tables_without_rewriting_receipts(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TABLE fetch_promotion_events")
        connection.execute("DROP TABLE fetch_attempts")
        connection.execute(
            "UPDATE store_metadata SET value='pit-receipt-store/v2' WHERE key='schema_version'"
        )
        connection.execute("PRAGMA user_version = 2")

    migrated = PITReceiptStore(str(root))
    attempt = _record(migrated, raw_bytes=_daily_response())

    assert attempt["outcome"] == "captured"
    with sqlite3.connect(migrated.database_path) as connection:
        assert connection.execute(
            "SELECT value FROM store_metadata WHERE key='schema_version'"
        ).fetchone()[0] == "pit-receipt-store/v4"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4


def test_same_body_on_a_later_attempt_reuses_receipt_but_preserves_both_attempts(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    raw = _daily_response()
    first = _record(store, raw_bytes=raw)
    assert store.promote_fetch_attempt(first["attempt_id"])["status"] == "stored"
    second = _record(store, raw_bytes=raw)

    reused = store.promote_fetch_attempt(second["attempt_id"])

    assert reused["status"] == "reused"
    assert reused["attempt_id"] == second["attempt_id"]
    assert reused["receipt_raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert store.receipt_count() == 1
    attempts = store.fetch_attempts(partition_key="2024-01-02")
    assert [row["terminal_status"] for row in attempts] == ["stored", "reused"]
    assert [row["outcome"] for row in attempts] == ["captured", "captured"]


def test_concurrent_same_body_attempt_promotions_have_one_store_and_one_reuse(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    raw = _daily_response()
    attempts = [_record(store, raw_bytes=raw) for _index in range(2)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                store.promote_fetch_attempt,
                [attempt["attempt_id"] for attempt in attempts],
            )
        )

    assert sorted(result["status"] for result in results) == ["reused", "stored"]
    assert store.receipt_count() == 1
    assert sorted(
        attempt["terminal_status"] for attempt in store.fetch_attempts()
    ) == ["reused", "stored"]


def test_different_body_records_immutable_conflict_without_overwriting_receipt(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    existing_raw = _daily_response("600001.SH", "A")
    candidate_raw = _daily_response("000002.SZ", "B")
    existing = _record(store, raw_bytes=existing_raw)
    assert store.promote_fetch_attempt(existing["attempt_id"])["status"] == "stored"
    candidate = _record(store, raw_bytes=candidate_raw)

    conflict = store.promote_fetch_attempt(candidate["attempt_id"])

    existing_digest = hashlib.sha256(existing_raw).hexdigest()
    candidate_digest = hashlib.sha256(candidate_raw).hexdigest()
    assert conflict == {
        "attempt_id": candidate["attempt_id"],
        "status": "immutable_conflict",
        "existing_raw_sha256": existing_digest,
        "candidate_raw_sha256": candidate_digest,
    }
    assert store.receipt_count() == 1
    assert store.daily_symbols("2024-01-02") == {"600001.SH"}
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT raw_sha256 FROM receipts WHERE dataset='bak_basic' AND partition_key='2024-01-02'"
        ).fetchone()[0] == existing_digest
    persisted = _attempt(store, candidate["attempt_id"])
    assert persisted["outcome"] == "captured"
    assert persisted["terminal_status"] == "immutable_conflict"
    assert persisted["promotion_events"][-1]["details"] == {
        "existing_raw_sha256": existing_digest,
        "candidate_raw_sha256": candidate_digest,
    }


@pytest.mark.parametrize(
    ("raw_bytes", "http_status", "expected_status"),
    [
        (b"gateway timeout", 503, "http_error"),
        (_response([], code=2002, msg="permission denied"), 200, "api_error"),
        (b'{"unterminated":', 200, "invalid_json"),
    ],
)
def test_unpromotable_http_api_and_json_outcomes_remain_auditable_without_receipts(
    tmp_path, raw_bytes, http_status, expected_status
):
    store = PITReceiptStore(str(tmp_path / expected_status))
    attempt = _record(store, raw_bytes=raw_bytes, http_status=http_status)

    result = store.promote_fetch_attempt(attempt["attempt_id"])

    assert result["attempt_id"] == attempt["attempt_id"]
    assert result["status"] == expected_status
    assert store.receipt_count() == 0
    persisted = _attempt(store, attempt["attempt_id"])
    assert persisted["outcome"] == "captured"
    assert persisted["terminal_status"] == expected_status
    assert [event["status"] for event in persisted["promotion_events"]] == [expected_status]
    assert (store.root / persisted["raw_path"]).read_bytes() == raw_bytes


@pytest.mark.parametrize("token_env", ["TUSHARE_TOKEN", "JIAOCH_TOKEN"])
@pytest.mark.parametrize("leak_location", ["field", "value"])
def test_request_semantics_reject_token_fields_and_configured_token_values(
    tmp_path, monkeypatch, leak_location, token_env
):
    token = "live-tushare-token-must-never-persist"
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("JIAOCH_TOKEN", raising=False)
    monkeypatch.setenv(token_env, token)
    store = PITReceiptStore(str(tmp_path / leak_location))
    fields = (*DAILY_FIELDS, "token") if leak_location == "field" else DAILY_FIELDS
    params = {"trade_date": "20240102"}
    error_message = None if leak_location == "field" else token

    with pytest.raises(PITReceiptError, match="credential|token"):
        _record(
            store,
            raw_bytes=_daily_response(),
            fields=fields,
            params=params,
            error_message=error_message,
        )

    assert store.fetch_attempts() == []
    assert list(Path(store.raw_root).rglob("*.json")) == []
