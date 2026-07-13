import hashlib
import json
import sqlite3

from app.research_pit_store import PITReceiptStore


DAILY_FIELDS = ("trade_date", "ts_code", "name", "industry", "list_date")
PARAMS = {"trade_date": "20240102"}
PARTITION_KEY = "2024-01-02"
RETRIEVED_AT = "2024-01-02T16:00:00+08:00"


def _canonical_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _daily_response(symbol="600001.SH", name="A"):
    return json.dumps(
        {
            "request_id": "promotion-recovery-test",
            "code": 0,
            "msg": "",
            "data": {
                "fields": list(DAILY_FIELDS),
                "items": [["20240102", symbol, name, "银行", "20100101"]],
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _request_semantics():
    return {
        "schema_version": "tushare-wire-request/v1",
        "dataset": "bak_basic",
        "partition_key": PARTITION_KEY,
        "api_name": "bak_basic",
        "method": "POST",
        "url": "https://api.tushare.pro",
        "wire_params": dict(PARAMS),
        "receipt_params": dict(PARAMS),
        "fields": list(DAILY_FIELDS),
        "row_cap": 7000,
    }


def _record_attempt(store, raw_bytes, *, retrieved_at=RETRIEVED_AT):
    semantics = _request_semantics()
    wire_sha256 = hashlib.sha256(b"exact-wire-request-with-token-redacted-by-hash").hexdigest()
    return store.record_fetch_attempt(
        dataset="bak_basic",
        partition_key=PARTITION_KEY,
        endpoint="bak_basic",
        params=PARAMS,
        fields=DAILY_FIELDS,
        wire_request_sha256=wire_sha256,
        request_body_sha256=wire_sha256,
        request_semantics=semantics,
        request_semantics_sha256=_canonical_sha256(semantics),
        raw_bytes=raw_bytes,
        http_status=200,
        started_at="2024-01-02T15:59:59+08:00",
        retrieved_at=retrieved_at,
        elapsed_ns=1_000_000_000,
        row_cap=7000,
        body_complete=True,
    )


def _commit_receipt_without_promotion_event(
    store, raw_bytes, *, retrieved_at=RETRIEVED_AT
):
    return store.ingest_tushare_response(
        dataset="bak_basic",
        partition_key=PARTITION_KEY,
        endpoint="bak_basic",
        params=PARAMS,
        raw_bytes=raw_bytes,
        http_status=200,
        retrieved_at=retrieved_at,
        row_cap=7000,
    )


def test_reopen_repairs_matching_receipt_without_event_before_resume_or_network(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    raw = _daily_response()
    attempt = _record_attempt(store, raw)
    receipt = _commit_receipt_without_promotion_event(store, raw)
    assert store.fetch_attempts(attempt_id=attempt["attempt_id"])[0]["promotion_events"] == []

    reopened = PITReceiptStore(str(root))
    index = reopened.controlled_receipt_index()

    key = ("bak_basic", PARTITION_KEY, attempt["request_semantics_sha256"])
    assert index[key]["promotion_status"] in {"reused", "recovered"}
    assert index[key]["receipt_raw_sha256"] == receipt["raw_sha256"]
    repaired = reopened.fetch_attempts(attempt_id=attempt["attempt_id"])[0]
    assert repaired["outcome"] == "captured"
    assert repaired["terminal_status"] in {"reused", "recovered"}
    assert len(repaired["promotion_events"]) == 1
    assert repaired["promotion_events"][0]["details"]["receipt_raw_sha256"] == receipt[
        "raw_sha256"
    ]


def test_matching_recovery_is_append_only_and_idempotent_across_repeated_reopens(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    raw = _daily_response()
    attempt = _record_attempt(store, raw)
    _commit_receipt_without_promotion_event(store, raw)

    first_reopen = PITReceiptStore(str(root))
    first_reopen.controlled_receipt_index()
    first = first_reopen.fetch_attempts(attempt_id=attempt["attempt_id"])[0]
    second_reopen = PITReceiptStore(str(root))
    second_reopen.controlled_receipt_index()
    second = second_reopen.fetch_attempts(attempt_id=attempt["attempt_id"])[0]

    assert first["outcome"] == second["outcome"] == "captured"
    assert first["promotion_events"] == second["promotion_events"]
    assert len(second["promotion_events"]) == 1
    with sqlite3.connect(second_reopen.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM fetch_promotion_events WHERE attempt_id = ?",
            (attempt["attempt_id"],),
        ).fetchone()[0] == 1


def test_reused_receipt_with_later_attempt_time_recovers_without_network(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    raw = _daily_response()
    _commit_receipt_without_promotion_event(store, raw)
    later = "2024-01-02T17:00:00+08:00"
    attempt = _record_attempt(store, raw, retrieved_at=later)
    reused = _commit_receipt_without_promotion_event(
        store, raw, retrieved_at=later
    )

    assert reused["status"] == "reused"
    index = PITReceiptStore(str(tmp_path / "store")).controlled_receipt_index()
    key = ("bak_basic", PARTITION_KEY, attempt["request_semantics_sha256"])
    assert index[key]["promotion_status"] in {"reused", "recovered"}


def test_reopen_never_recovers_conflicting_attempt_raw_as_the_existing_receipt(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    existing_raw = _daily_response("600001.SH", "A")
    candidate_raw = _daily_response("000002.SZ", "B")
    candidate = _record_attempt(store, candidate_raw)
    receipt = _commit_receipt_without_promotion_event(store, existing_raw)

    reopened = PITReceiptStore(str(root))
    index = reopened.controlled_receipt_index()

    key = ("bak_basic", PARTITION_KEY, candidate["request_semantics_sha256"])
    assert key not in index
    persisted = reopened.fetch_attempts(attempt_id=candidate["attempt_id"])[0]
    assert persisted["outcome"] == "captured"
    assert persisted["terminal_status"] not in {"stored", "reused", "recovered"}
    with sqlite3.connect(reopened.database_path) as connection:
        assert connection.execute(
            "SELECT raw_sha256 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
            (PARTITION_KEY,),
        ).fetchone()[0] == receipt["raw_sha256"]
