import hashlib
import json
import sqlite3

import pytest

from app.research_pit_store import PITReceiptError, PITReceiptStore, _normalize_rows


def _response(fields, rows):
    return json.dumps(
        {
            "request_id": "market-store-test",
            "code": 0,
            "msg": "",
            "data": {"fields": fields, "items": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _ingest(store, dataset, fields, rows, *, row_cap):
    raw = _response(fields, rows)
    receipt = store.ingest_tushare_response(
        dataset=dataset,
        partition_key="2024-01-02",
        endpoint=dataset,
        params={"trade_date": "20240102"},
        raw_bytes=raw,
        http_status=200,
        retrieved_at="2024-01-02T16:30:00+08:00",
        row_cap=row_cap,
    )
    return receipt, raw


def test_raw_daily_receipt_is_immutable_content_addressed_execution_evidence(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    fields = [
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
    ]
    rows = [
        ["600001.SH", "20240102", 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]
    ]

    receipt, raw = _ingest(store, "daily", fields, rows, row_cap=6000)

    assert receipt["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["row_count"] == 1
    assert store.verify_receipts()["verified_receipt_count"] == 1
    with sqlite3.connect(store.database_path) as connection:
        stored = connection.execute(
            """
            SELECT trade_date, ts_code, open, high, low, close, pre_close,
                   change, pct_chg, vol, amount
            FROM raw_daily_bars
            """
        ).fetchone()
    assert stored == (
        "2024-01-02",
        "600001.SH",
        10.0,
        10.5,
        9.8,
        10.2,
        9.9,
        0.3,
        3.03,
        1000.0,
        10100.0,
    )

    changed = [[*rows[0][:-1], 99999]]
    with pytest.raises(PITReceiptError, match="immutable partition conflict"):
        _ingest(store, "daily", fields, changed, row_cap=6000)


def test_market_receipts_require_full_session_params_and_matching_trade_date(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    fields = ["ts_code", "trade_date", "adj_factor"]
    raw = _response(fields, [["600001.SH", "20240102", 1.5]])

    with pytest.raises(PITReceiptError, match="full-snapshot parameters"):
        store.ingest_tushare_response(
            dataset="adj_factor",
            partition_key="2024-01-02",
            endpoint="adj_factor",
            params={"trade_date": "20240102", "ts_code": "600001.SH"},
            raw_bytes=raw,
            http_status=200,
            retrieved_at="2024-01-02T09:20:00+08:00",
            row_cap=6000,
        )

    wrong_day = _response(fields, [["600001.SH", "20240103", 1.5]])
    with pytest.raises(PITReceiptError, match="trade_date does not match"):
        store.ingest_tushare_response(
            dataset="adj_factor",
            partition_key="2024-01-02",
            endpoint="adj_factor",
            params={"trade_date": "20240102"},
            raw_bytes=wrong_day,
            http_status=200,
            retrieved_at="2024-01-02T09:20:00+08:00",
            row_cap=6000,
        )


@pytest.mark.parametrize(
    "dataset,fields,rows,row_cap,table,expected",
    [
        (
            "adj_factor",
            ["ts_code", "trade_date", "adj_factor"],
            [["600001.SH", "20240102", 1.5]],
            6000,
            "adjustment_factors",
            ("2024-01-02", "600001.SH", 1.5),
        ),
        (
            "stk_limit",
            ["trade_date", "ts_code", "pre_close", "up_limit", "down_limit"],
            [["20240102", "600001.SH", 10.0, 11.0, 9.0]],
            5800,
            "daily_price_limits",
            ("2024-01-02", "600001.SH", 10.0, 11.0, 9.0),
        ),
        (
            "suspend_d",
            ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
            [["600001.SH", "20240102", None, "S"]],
            5000,
            "suspension_events",
            ("2024-01-02", "600001.SH", "", "S"),
        ),
    ],
)
def test_market_event_receipts_normalize_into_lineage_tables(
    tmp_path, dataset, fields, rows, row_cap, table, expected
):
    store = PITReceiptStore(str(tmp_path / dataset))

    _ingest(store, dataset, fields, rows, row_cap=row_cap)

    with sqlite3.connect(store.database_path) as connection:
        values = connection.execute(f"SELECT * FROM {table}").fetchone()
    assert values[: len(expected)] == expected
    assert store.verify_receipts()["verified_receipt_count"] == 1


@pytest.mark.parametrize(
    "dataset,fields,rows,row_cap,message",
    [
        (
            "daily",
            [
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
            [["600001.SH", "20240102", 10, 9, 9.5, 10, 9.8, 0.2, 2, 1, 10]],
            6000,
            "high",
        ),
        (
            "adj_factor",
            ["ts_code", "trade_date", "adj_factor"],
            [["600001.SH", "20240102", 0]],
            6000,
            "positive",
        ),
        (
            "stk_limit",
            ["trade_date", "ts_code", "pre_close", "up_limit", "down_limit"],
            [["20240102", "600001.SH", 10, 9, 11]],
            5800,
            "limit interval",
        ),
        (
            "suspend_d",
            ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
            [["600001.SH", "20240102", None, "X"]],
            5000,
            "suspend_type",
        ),
    ],
)
def test_invalid_market_rows_are_rejected_before_receipt_publication(
    tmp_path, dataset, fields, rows, row_cap, message
):
    store = PITReceiptStore(str(tmp_path / dataset))

    with pytest.raises(PITReceiptError, match=message):
        _ingest(store, dataset, fields, rows, row_cap=row_cap)

    assert store.receipt_count() == 0


@pytest.mark.parametrize(
    "dataset,fields,row_cap",
    [
        (
            "daily",
            [
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
            6000,
        ),
        ("adj_factor", ["ts_code", "trade_date", "adj_factor"], 6000),
        (
            "stk_limit",
            ["trade_date", "ts_code", "pre_close", "up_limit", "down_limit"],
            5800,
        ),
    ],
)
def test_open_session_market_receipts_cannot_publish_empty_sets(
    tmp_path, dataset, fields, row_cap
):
    store = PITReceiptStore(str(tmp_path / dataset))

    with pytest.raises(PITReceiptError, match="empty"):
        _ingest(store, dataset, fields, [], row_cap=row_cap)


def test_empty_suspend_response_is_preserved_as_explicit_no_event_evidence(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))

    receipt, raw = _ingest(
        store,
        "suspend_d",
        ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
        [],
        row_cap=5000,
    )

    assert receipt["row_count"] == 0
    assert receipt["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert store.verify_receipts()["verified_receipt_count"] == 1


def test_unknown_datasets_fail_closed_in_normalize_insert_and_read_layers(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="unsupported normalized dataset"):
        _normalize_rows("unknown", "2024-01-02", {}, [], [])
    with store._connect() as connection:
        with pytest.raises(PITReceiptError, match="unsupported normalized dataset"):
            store._insert_normalized(connection, "unknown", "2024-01-02", [])
        with pytest.raises(PITReceiptError, match="unsupported normalized dataset"):
            store._normalized_rows_from_db(connection, "unknown", "2024-01-02")
