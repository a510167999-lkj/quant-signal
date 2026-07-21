import hashlib
import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import jobs
from app import research_pit_store
from app.research_partitions import load_temporal_partition_contract
from app.research_pit_store import (
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_ROW_CAPS,
    MARKET_SESSION_VINTAGES,
    NORMALIZED_FIELDS,
    PITReceiptError,
    PITReceiptStore,
)


def test_concurrent_generation_begin_is_single_for_same_pin_and_rotates_for_different_pin(
    tmp_path,
):
    store = PITReceiptStore(str(tmp_path / "store"))
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    manifest_a = {
        key: "a" * 64 for key in research_pit_store.REQUIRED_STOCK_PARTITIONS
    }
    manifest_b = {
        key: "b" * 64 for key in research_pit_store.REQUIRED_STOCK_PARTITIONS
    }
    digest_a = research_pit_store._sha256(manifest_a)
    digest_b = research_pit_store._sha256(manifest_b)

    def begin(manifest, digest):
        return store.begin_or_resume_stock_basic_generation(
            now,
            expected_request_semantics=manifest,
            expected_request_semantics_sha256=digest,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        same = list(executor.map(lambda _: begin(manifest_a, digest_a), range(2)))
    assert len({row["generation_id"] for row in same}) == 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        different = list(
            executor.map(
                lambda pair: begin(*pair),
                ((manifest_a, digest_a), (manifest_b, digest_b)),
            )
        )
    assert len({row["generation_id"] for row in different}) == 2
    with sqlite3.connect(store.database_path) as connection:
        statuses = connection.execute(
            "SELECT status, COUNT(*) FROM stock_basic_generations GROUP BY status"
        ).fetchall()
    status_counts = dict(statuses)
    assert status_counts["collecting"] == 1
    assert status_counts["abandoned"] >= 1


def test_generation_begin_rejects_incomplete_or_extra_semantics_pin(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    for manifest in (
        {"SSE:L": "a" * 64},
        {
            **{key: "a" * 64 for key in research_pit_store.REQUIRED_STOCK_PARTITIONS},
            "extra": "a" * 64,
        },
    ):
        with pytest.raises(PITReceiptError, match="keyset"):
            store.begin_or_resume_stock_basic_generation(
                now,
                expected_request_semantics=manifest,
                expected_request_semantics_sha256=research_pit_store._sha256(manifest),
            )


@pytest.mark.parametrize("column", ["expected_request_semantics_json", "expected_request_semantics_sha256"])
def test_published_generation_verify_rejects_tampered_semantics_pin(tmp_path, column):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    generation_id = store.active_stock_basic_generation()["generation_id"]
    replacement = "{}" if column.endswith("_json") else "0" * 64
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            f"UPDATE stock_basic_generations SET {column} = ? WHERE generation_id = ?",
            (replacement, generation_id),
        )
    with pytest.raises(PITReceiptError, match="semantics|pin|keyset|hash"):
        store.verify_stock_basic_generation(generation_id)


@pytest.mark.parametrize(
    "tamper",
    [None, "stock_manifest", "stock_head", "market_lineage"],
)
def test_reopen_migrates_legacy_published_generation_pins_idempotently(
    tmp_path, tamper
):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    _ingest_complete_two_day_fixture(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        for generation in connection.execute(
            "SELECT generation_id FROM stock_basic_generations WHERE status = 'published'"
        ):
            verified = store._stock_generation_manifest_on_connection(
                connection, generation[0]
            )
            payload = dict(verified["manifest"])
            payload.pop("expected_request_semantics", None)
            payload.pop("expected_request_semantics_sha256", None)
            legacy_hash = research_pit_store._sha256(payload)
            connection.execute(
                "UPDATE stock_basic_generations SET manifest_sha256 = ? "
                "WHERE generation_id = ?",
                (legacy_hash, generation[0]),
            )
            connection.execute(
                "UPDATE stock_basic_generation_head SET manifest_sha256 = ? "
                "WHERE generation_id = ?",
                (legacy_hash, generation[0]),
            )
        for generation in connection.execute(
            "SELECT generation_id FROM market_session_generations "
            "WHERE status = 'published'"
        ):
            verified = store._market_session_manifest_on_connection(
                connection, generation[0]
            )
            payload = dict(verified["manifest"])
            payload.pop("expected_request_semantics", None)
            payload.pop("expected_request_semantics_sha256", None)
            legacy_hash = research_pit_store._sha256(payload)
            legacy_lineage = store._market_session_lineage_sha256_on_connection(
                connection, generation[0], legacy=True
            )
            connection.execute(
                "UPDATE market_session_generations SET manifest_sha256 = ?, "
                "lineage_sha256 = ? WHERE generation_id = ?",
                (legacy_hash, legacy_lineage, generation[0]),
            )
            connection.execute(
                "UPDATE market_session_generation_head SET manifest_sha256 = ? "
                "WHERE generation_id = ?",
                (legacy_hash, generation[0]),
            )
        connection.execute(
            "UPDATE stock_basic_generations SET "
            "expected_request_semantics_json = NULL, "
            "expected_request_semantics_sha256 = NULL"
        )
        connection.execute(
            "UPDATE market_session_generations SET "
            "expected_request_semantics_json = NULL, "
            "expected_request_semantics_sha256 = NULL"
        )
        if tamper == "stock_manifest":
            connection.execute(
                "UPDATE stock_basic_generations SET manifest_sha256 = ?",
                ("0" * 64,),
            )
        elif tamper == "stock_head":
            connection.execute(
                "UPDATE stock_basic_generation_head SET manifest_sha256 = ?",
                ("0" * 64,),
            )
        elif tamper == "market_lineage":
            connection.execute(
                "UPDATE market_session_generations SET lineage_sha256 = ?",
                ("0" * 64,),
            )

    if tamper is not None:
        with pytest.raises(PITReceiptError, match="legacy.*mismatch"):
            PITReceiptStore(str(root))
        return

    reopened = PITReceiptStore(str(root))
    assert reopened.active_stock_basic_generation() is not None
    assert reopened.active_market_session_generation("2024-01-02") is not None
    assert reopened.audit_coverage(
        start_date="2024-01-02", end_date="2024-01-03"
    )["status"] == "passed"
    with sqlite3.connect(reopened.database_path) as connection:
        first_record = connection.execute(
            "SELECT value FROM store_metadata WHERE key = ?",
            ("generation_pin_migration/v1",),
        ).fetchone()[0]
    PITReceiptStore(str(root))
    with sqlite3.connect(reopened.database_path) as connection:
        second_record = connection.execute(
            "SELECT value FROM store_metadata WHERE key = ?",
            ("generation_pin_migration/v1",),
        ).fetchone()[0]
    assert first_record == second_record


def test_legacy_published_pin_migration_fails_closed_on_tampered_shard(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    _ingest_complete_two_day_fixture(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE stock_basic_generations SET "
            "expected_request_semantics_json = NULL, "
            "expected_request_semantics_sha256 = NULL"
        )
        connection.execute(
            "UPDATE stock_basic_generation_shards SET "
            "request_semantics_sha256 = ? WHERE logical_partition_key = 'SSE:L'",
            ("0" * 64,),
        )
    with pytest.raises(PITReceiptError, match="lineage|semantics|mismatch"):
        PITReceiptStore(str(root))


def _response(fields, items, *, code=0, msg=""):
    return json.dumps(
        {"request_id": "test", "code": code, "msg": msg, "data": {"fields": fields, "items": items}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _audited_universe_loader():
    loader = getattr(research_pit_store, "AuditedPointInTimeUniverse", None)
    assert loader is not None, "AuditedPointInTimeUniverse loader is required"
    class ExplicitLegacyLoader(loader):
        from_file = loader.from_legacy_unbound_file

    return ExplicitLegacyLoader


def test_stock_basic_l_status_may_carry_a_frozen_delist_date():
    fields = list(NORMALIZED_FIELDS["stock_basic"])
    rows = research_pit_store._normalize_rows(
        "stock_basic",
        "SSE:L",
        {"exchange": "SSE", "list_status": "L"},
        fields,
        [
            [
                "600001.SH",
                "600001",
                "示例股份",
                "SSE",
                "主板",
                "L",
                "20000101",
                "20260706",
            ]
        ],
    )

    assert rows[0]["list_status"] == "L"
    assert rows[0]["delist_date"] == "2026-07-06"


def test_bak_basic_zero_list_date_is_preserved_as_not_yet_listed():
    rows = research_pit_store._normalize_rows(
        "bak_basic",
        "2024-01-02",
        {"trade_date": "2024-01-02"},
        list(NORMALIZED_FIELDS["bak_basic"]),
        [["20240102", "603361.SH", "浙江国祥", "专用设备", "0"]],
    )

    assert rows[0]["list_date"] is None


def test_daily_skips_nonfinite_out_of_scope_bj_rows():
    rows = research_pit_store._normalize_rows(
        "daily",
        "2016-09-30",
        {"trade_date": "2016-09-30"},
        list(NORMALIZED_FIELDS["daily"]),
        [
            [
                "920726.BJ",
                "20160930",
                1.0,
                1.0,
                1.0,
                1.0,
                float("nan"),
                float("nan"),
                float("nan"),
                11000.0,
                1100.0,
            ],
            [
                "600001.SH",
                "20160930",
                10.0,
                11.0,
                9.5,
                10.5,
                10.0,
                0.5,
                5.0,
                1000.0,
                10000.0,
            ],
        ],
    )

    assert [row["ts_code"] for row in rows] == ["600001.SH"]


def test_stock_basic_accepts_pre_2016_delisted_legacy_temporary_code_as_evidence():
    rows = research_pit_store._normalize_rows(
        "stock_basic",
        "SSE:D",
        {"exchange": "SSE", "list_status": "D"},
        list(NORMALIZED_FIELDS["stock_basic"]),
        [["T600018.SH", "T600018", "上柴退市", "SSE", "", "D", "20000719", "20061020"]],
    )

    assert rows[0]["ts_code"] == "T600018.SH"
    assert rows[0]["market"] == "历史遗留"
    assert rows[0]["delist_date"] == "2006-10-20"


def test_stk_limit_allows_missing_optional_pre_close_but_keeps_exact_limits():
    rows = research_pit_store._normalize_rows(
        "stk_limit",
        "2024-01-02",
        {"trade_date": "2024-01-02"},
        list(NORMALIZED_FIELDS["stk_limit"]),
        [["20240102", "600001.SH", None, 11.0, 9.0]],
    )

    assert rows == [
        {
            "trade_date": "2024-01-02",
            "ts_code": "600001.SH",
            "pre_close": None,
            "up_limit": 11.0,
            "down_limit": 9.0,
        }
    ]


def test_stk_limit_preserves_zero_limit_for_scope_reconciliation():
    rows = research_pit_store._normalize_rows(
        "stk_limit",
        "2024-01-05",
        {"trade_date": "2024-01-05"},
        list(NORMALIZED_FIELDS["stk_limit"]),
        [["20240105", "920690.BJ", None, 100000.0, 0.0]],
    )

    assert rows[0]["up_limit"] == 100000.0
    assert rows[0]["down_limit"] == 0.0


@pytest.mark.parametrize("field_index", [3, 4])
def test_stk_limit_rejects_negative_limits(field_index):
    row = ["20240105", "920690.BJ", None, 100000.0, 0.0]
    row[field_index] = -0.01

    with pytest.raises(PITReceiptError, match="must be nonnegative"):
        research_pit_store._normalize_rows(
            "stk_limit",
            "2024-01-05",
            {"trade_date": "2024-01-05"},
            list(NORMALIZED_FIELDS["stk_limit"]),
            [row],
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field_index", [3, 4])
def test_stk_limit_rejects_nonfinite_limits(value, field_index):
    row = ["20240105", "920690.BJ", None, 100000.0, 0.0]
    row[field_index] = value

    with pytest.raises(PITReceiptError, match="is not finite"):
        research_pit_store._normalize_rows(
            "stk_limit",
            "2024-01-05",
            {"trade_date": "2024-01-05"},
            list(NORMALIZED_FIELDS["stk_limit"]),
            [row],
        )


def test_new_store_price_limit_tables_encode_nonnegative_checks(tmp_path):
    store = PITReceiptStore(str(tmp_path))

    with sqlite3.connect(store.database_path) as connection:
        schemas = {
            name: sql
            for name, sql in connection.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'table' AND name IN (
                    'daily_price_limits',
                    'market_session_generation_rows_stk_limit'
                )
                """
            )
        }

    for sql in schemas.values():
        compact = " ".join(sql.split())
        assert "CHECK (up_limit >= 0)" in compact
        assert "CHECK (down_limit >= 0)" in compact


@pytest.mark.parametrize(
    ("up_limit", "down_limit", "reference_close"),
    [
        (float("inf"), 9.0, 10.0),
        (11.0, float("nan"), 10.0),
        (11.0, 9.0, float("inf")),
        (11.0, 9.0, None),
    ],
)
def test_expected_limit_interval_rejects_nonfinite_or_missing_database_values(
    up_limit, down_limit, reference_close
):
    assert not research_pit_store._has_valid_expected_limit_interval(
        up_limit, down_limit, reference_close
    )


def test_legacy_nonnullable_limit_pre_close_schema_requires_rebuild(tmp_path):
    database_path = tmp_path / "metadata.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE store_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO store_metadata VALUES ('schema_version', 'pit-receipt-store/v2');
            CREATE TABLE market_session_generation_rows_stk_limit (
                generation_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                pre_close REAL NOT NULL,
                up_limit REAL NOT NULL,
                down_limit REAL NOT NULL,
                PRIMARY KEY (generation_id, trade_date, ts_code)
            );
            """
        )

    with pytest.raises(
        PITReceiptError, match="incompatible.*pre_close.*rebuild store"
    ):
        PITReceiptStore(str(tmp_path))


def _stock_response(rows):
    fields = [
        "ts_code",
        "symbol",
        "name",
        "exchange",
        "market",
        "list_status",
        "list_date",
        "delist_date",
    ]
    return _response(fields, rows)


def _calendar_response(rows):
    return _response(["exchange", "cal_date", "is_open", "pretrade_date"], rows)


def _daily_response(trade_date, rows):
    return _response(
        ["trade_date", "ts_code", "name", "industry", "list_date"],
        [
            [trade_date, *row, "20100101"] if len(row) == 3 else [trade_date, *row]
            for row in rows
        ],
    )


def test_daily_receipt_is_content_addressed_and_reverified_from_raw_bytes(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    raw = _daily_response(
        "20240102",
        [
            ["600001.SH", "历史名称A", "银行"],
            ["000002.SZ", "历史名称B", "地产"],
        ],
    )

    receipt = store.ingest_tushare_response(
        dataset="bak_basic",
        partition_key="2024-01-02",
        endpoint="bak_basic",
        params={"trade_date": "20240102"},
        raw_bytes=raw,
        http_status=200,
        retrieved_at="2024-01-02T16:00:00+08:00",
        row_cap=7000,
    )

    assert receipt["status"] == "stored"
    assert receipt["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["row_count"] == 2
    assert store.daily_symbols("2024-01-02") == {"000002.SZ", "600001.SH"}
    assert store.verify_receipts()["verified_receipt_count"] == 1


def test_ingest_is_idempotent_but_partition_cannot_be_rewritten(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    raw = _daily_response("20240102", [["600001.SH", "历史名称", "银行"]])
    kwargs = {
        "dataset": "bak_basic",
        "partition_key": "2024-01-02",
        "endpoint": "bak_basic",
        "params": {"trade_date": "20240102"},
        "raw_bytes": raw,
        "http_status": 200,
        "retrieved_at": "2024-01-02T16:00:00+08:00",
        "row_cap": 7000,
    }

    assert store.ingest_tushare_response(**kwargs)["status"] == "stored"
    semantic_retry = {**kwargs, "params": {"trade_date": "2024-01-02"}}
    assert store.ingest_tushare_response(**semantic_retry)["status"] == "reused"

    changed = {**kwargs, "raw_bytes": _daily_response("20240102", [["000002.SZ", "B", "地产"]])}
    with pytest.raises(PITReceiptError, match="immutable partition conflict"):
        store.ingest_tushare_response(**changed)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (_response(["trade_date", "ts_code", "name"], [], code=2002, msg="permission denied"), "response code"),
        (_response(["trade_date", "ts_code", "ts_code"], [["20240102", "A", "A"]]), "duplicate fields"),
        (_response(["trade_date", "ts_code", "name"], [["20240102", "600001.SH"]]), "field width"),
    ],
)
def test_native_response_errors_are_rejected_before_publication(tmp_path, raw, message):
    store = PITReceiptStore(str(tmp_path))
    with pytest.raises(PITReceiptError, match=message):
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-02",
            endpoint="bak_basic",
            params={"trade_date": "20240102"},
            raw_bytes=raw,
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=7000,
        )
    assert store.receipt_count() == 0


def test_cap_hit_and_duplicate_normalized_keys_are_transactionally_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    cap_hit = _daily_response(
        "20240102",
        [[f"{index:06d}.SZ", f"股票{index}", "测试"] for index in range(7000)],
    )
    with pytest.raises(PITReceiptError, match="row cap"):
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-02",
            endpoint="bak_basic",
            params={"trade_date": "20240102"},
            raw_bytes=cap_hit,
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=7000,
        )

    duplicate = _daily_response(
        "20240102",
        [
            ["600001.SH", "A", "银行"],
            ["600001.SH", "A2", "银行"],
        ],
    )
    with pytest.raises(PITReceiptError, match="duplicate normalized key"):
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-02",
            endpoint="bak_basic",
            params={"trade_date": "20240102"},
            raw_bytes=duplicate,
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=7000,
        )
    assert store.receipt_count() == 0
    assert store.daily_symbols("2024-01-02") == set()

    with pytest.raises(PITReceiptError, match="official row cap"):
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-02",
            endpoint="bak_basic",
            params={"trade_date": "20240102"},
            raw_bytes=_daily_response("20240102", [["600001.SH", "A", "银行"]]),
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=7001,
        )


_MARKET_DATASET_FIELDS = {
    "daily": list(NORMALIZED_FIELDS["daily"]),
    "adj_factor": list(NORMALIZED_FIELDS["adj_factor"]),
    "stk_limit": list(NORMALIZED_FIELDS["stk_limit"]),
    "suspend_d": list(NORMALIZED_FIELDS["suspend_d"]),
}


def _market_default_rows(dataset, trade_date):
    wire = trade_date.replace("-", "")
    if dataset == "daily":
        rows = [["600001.SH", wire, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]]
        if trade_date == "2024-01-02":
            rows.append(["000002.SZ", wire, 20.0, 20.5, 19.8, 20.2, 19.9, 0.3, 1.51, 800, 16000])
        return rows
    if dataset == "adj_factor":
        rows = [["600001.SH", wire, 1.5]]
        if trade_date == "2024-01-02":
            rows.append(["000002.SZ", wire, 1.2])
        return rows
    if dataset == "stk_limit":
        rows = [[wire, "600001.SH", 10.0, 11.0, 9.0]]
        if trade_date == "2024-01-02":
            rows.append([wire, "000002.SZ", 20.0, 22.0, 18.0])
        return rows
    return []  # suspend_d legitimately empty


def _publish_market_session_for_audit(
    store, trade_date, started, *, stk_limit_rows=None
):
    """Publish one four-shard market session generation for ``trade_date``.

    WHY: coverage audit fail-closed requires every open session to carry a
    published, active, deep-verified market generation. The fixture builds
    it explicitly rather than relying on any fallback synthesis.
    """

    generation = store.begin_or_resume_market_session_generation(started, trade_date)
    for index, dataset in enumerate(MARKET_SESSION_DATASETS, 1):
        retrieved_at = (started + timedelta(seconds=index)).isoformat()
        fields = _MARKET_DATASET_FIELDS[dataset]
        semantics = {
            "schema_version": "tushare-wire-request/v1",
            "dataset": dataset,
            "partition_key": trade_date,
            "api_name": dataset,
            "method": "POST",
            "url": "https://api.tushare.pro",
            "wire_params": {"trade_date": trade_date.replace("-", "")},
            "receipt_params": {"trade_date": trade_date},
            "fields": fields,
            "row_cap": MARKET_SESSION_ROW_CAPS[dataset],
        }
        raw = json.dumps(
            {
                "request_id": f"audit-fixture-{dataset}-{trade_date}",
                "code": 0,
                "msg": "",
                "data": {
                    "fields": fields,
                    "items": (
                        stk_limit_rows
                        if dataset == "stk_limit" and stk_limit_rows is not None
                        else _market_default_rows(dataset, trade_date)
                    ),
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        wire_sha256 = hashlib.sha256(
            f"{dataset}:{trade_date}:{retrieved_at}".encode("utf-8")
        ).hexdigest()
        semantics_sha256 = hashlib.sha256(
            json.dumps(
                semantics, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        attempt = store.record_fetch_attempt(
            dataset=dataset,
            partition_key=trade_date,
            endpoint=dataset,
            params={"trade_date": trade_date},
            fields=fields,
            wire_request_sha256=wire_sha256,
            request_body_sha256=wire_sha256,
            request_semantics=semantics,
            request_semantics_sha256=semantics_sha256,
            raw_bytes=raw,
            http_status=200,
            started_at=(started + timedelta(seconds=index - 1)).isoformat(),
            retrieved_at=retrieved_at,
            elapsed_ns=1_000_000_000,
            row_cap=MARKET_SESSION_ROW_CAPS[dataset],
            body_complete=True,
        )
        store.stage_market_session_attempt(
            generation["generation_id"], dataset, attempt["attempt_id"]
        )
    return store.publish_market_session_generation(generation["generation_id"])


def _ingest_complete_two_day_fixture(
    store,
    *,
    omit_day_one_symbol=False,
    omit_day_two=False,
    include_bse=False,
    calendar_padding=False,
    include_future_p_and_g=False,
    include_current_code_migration=False,
):
    generation = store.begin_or_resume_stock_basic_generation(
        "2024-01-01T16:00:00+08:00"
    )
    for exchange in ("SSE", "SZSE"):
        for status in ("L", "D", "P", "G"):
            rows = []
            if (exchange, status) == ("SSE", "L"):
                rows = [
                    ["600001.SH", "600001", "A", "SSE", "主板", "L", "20100101", None]
                ]
            elif (exchange, status) == ("SZSE", "D"):
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
            elif include_future_p_and_g and (exchange, status) == ("SSE", "P"):
                rows = [
                    ["600010.SH", "600010", "未来待上市", "SSE", "主板", "P", "20250101", None]
                ]
            elif include_future_p_and_g and (exchange, status) == ("SZSE", "G"):
                rows = [["000010.SZ", "000010", "过会未交易", "SZSE", "主板", "G", None, None]]
            if include_current_code_migration and (exchange, status) == ("SZSE", "L"):
                rows.append(
                    [
                        "302132.SZ",
                        "302132",
                        "当前迁移代码",
                        "SZSE",
                        "创业板",
                        "L",
                        "20100827",
                        None,
                    ]
                )
            if include_current_code_migration and (exchange, status) == ("SSE", "L"):
                rows.append(
                    [
                        "603999.SH",
                        "603999",
                        "待上市预备记录",
                        "SSE",
                        "主板",
                        "L",
                        "20240110",
                        None,
                    ]
                )
            partition = f"{exchange}:{status}"
            params = {"exchange": exchange, "list_status": status}
            semantics = {
                "schema_version": "tushare-wire-request/v1",
                "dataset": "stock_basic",
                "partition_key": partition,
                "api_name": "stock_basic",
                "method": "POST",
                "url": "https://api.tushare.pro",
                "wire_params": dict(params),
                "receipt_params": dict(params),
                "fields": list(NORMALIZED_FIELDS["stock_basic"]),
                "row_cap": 6000,
            }
            attempt = store.record_fetch_attempt(
                dataset="stock_basic",
                partition_key=partition,
                endpoint="stock_basic",
                params=params,
                fields=NORMALIZED_FIELDS["stock_basic"],
                wire_request_sha256=hashlib.sha256(
                    f"stock-generation:{partition}".encode()
                ).hexdigest(),
                raw_bytes=_stock_response(rows),
                http_status=200,
                started_at="2024-01-01T15:59:59+08:00",
                retrieved_at="2024-01-01T16:00:00+08:00",
                elapsed_ns=1_000_000,
                row_cap=6000,
                body_complete=True,
                request_semantics=semantics,
            )
            assert store.stage_stock_basic_attempt(
                generation["generation_id"], partition, attempt["attempt_id"]
            )["status"] == "staged"
        
        calendar_start = "2024-01-01" if calendar_padding else "2024-01-02"
        calendar_end = "2024-01-04" if calendar_padding else "2024-01-03"
        calendar_rows = [
            [exchange, "20240102", 1, "20231229"],
            [exchange, "20240103", 1, "20240102"],
        ]
        if calendar_padding:
            calendar_rows = [
                [exchange, "20240101", 0, "20231229"],
                *calendar_rows,
                [exchange, "20240104", 1, "20240103"],
            ]
        store.ingest_tushare_response(
            dataset="trade_cal",
            partition_key=f"{exchange}:{calendar_start}:{calendar_end}",
            endpoint="trade_cal",
            params={
                "exchange": exchange,
                "start_date": calendar_start.replace("-", ""),
                "end_date": calendar_end.replace("-", ""),
            },
            raw_bytes=_calendar_response(calendar_rows),
            http_status=200,
            retrieved_at="2024-01-01T16:00:00+08:00",
            row_cap=10000,
        )
    store.publish_stock_basic_generation(generation["generation_id"])
    first_rows = [["600001.SH", "A", "银行"]]
    if not omit_day_one_symbol:
        first_rows.append(["000002.SZ", "B", "地产"])
    if include_bse:
        first_rows.append(["920001.BJ", "北交所样本", "测试"])
    if include_current_code_migration:
        first_rows.append(["603999.SH", "待上市预备记录", "测试", "0"])
    store.ingest_tushare_response(
        dataset="bak_basic",
        partition_key="2024-01-02",
        endpoint="bak_basic",
        params={"trade_date": "20240102"},
        raw_bytes=_daily_response("20240102", first_rows),
        http_status=200,
        retrieved_at="2024-01-02T16:00:00+08:00",
        row_cap=7000,
    )
    if not omit_day_two:
        second_rows = [["600001.SH", "A", "银行"]]
        if include_bse:
            second_rows.append(["920001.BJ", "北交所样本", "测试"])
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-03",
            endpoint="bak_basic",
            params={"trade_date": "20240103"},
            raw_bytes=_daily_response("20240103", second_rows),
            http_status=200,
            retrieved_at="2024-01-03T16:00:00+08:00",
            row_cap=7000,
        )
    # WHY: each complete open session must carry a published market generation
    # so coverage audit's per-session fail-closed gate has evidence to bind.
    _publish_market_session_for_audit(
        store, "2024-01-02", datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)
    )
    if not omit_day_two:
        _publish_market_session_for_audit(
            store, "2024-01-03", datetime(2024, 1, 3, 16, 0, tzinfo=timezone.utc)
        )


def _attach_controlled_attempts_to_all_receipts(store):
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        receipts = [dict(row) for row in connection.execute("SELECT * FROM receipts")]
    for index, receipt in enumerate(receipts, 1):
        params = json.loads(receipt["params_json"])
        wire_params = {
            key: value.replace("-", "") if key.endswith("date") else value
            for key, value in params.items()
        }
        fields = tuple(NORMALIZED_FIELDS[receipt["dataset"]])
        semantics = {
            "schema_version": "tushare-wire-request/v1",
            "dataset": receipt["dataset"],
            "partition_key": receipt["partition_key"],
            "api_name": receipt["endpoint"],
            "method": "POST",
            "url": "https://example.invalid/tushare",
            "wire_params": wire_params,
            "receipt_params": params,
            "fields": list(fields),
            "row_cap": receipt["row_cap"],
        }
        attempt = store.record_fetch_attempt(
            dataset=receipt["dataset"],
            partition_key=receipt["partition_key"],
            endpoint=receipt["endpoint"],
            params=params,
            fields=fields,
            wire_request_sha256=hashlib.sha256(f"wire-{index}".encode()).hexdigest(),
            raw_bytes=(store.root / receipt["raw_path"]).read_bytes(),
            http_status=receipt["http_status"],
            started_at="2024-01-01T15:59:59+08:00",
            retrieved_at=receipt["retrieved_at"],
            elapsed_ns=1_000_000,
            row_cap=receipt["row_cap"],
            body_complete=True,
            request_semantics=semantics,
        )
        assert store.promote_fetch_attempt(attempt["attempt_id"])["status"] == "reused"


def test_streaming_coverage_audit_matches_daily_set_to_security_lifecycles(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)

    audit = store.audit_coverage(
        start_date="2024-01-02",
        end_date="2024-01-03",
    )

    assert audit["status"] == "passed"
    assert audit["receipt_verification_scope"] == (
        "whole_store_plus_active_stock_per_session_market_and_official_suspension_fail_closed"
    )
    assert audit["stock_generation_id"] == store.active_stock_basic_generation()[
        "generation_id"
    ]


def test_coverage_audit_allows_zero_limit_only_on_out_of_scope_extra(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    baseline = store.audit_coverage(
        start_date="2024-01-02", end_date="2024-01-03"
    )
    # Rebuild a later active generation with a provider-returned BSE extra.
    published = _publish_market_session_for_audit(
        store,
        "2024-01-02",
        datetime(2024, 1, 4, 16, 0, tzinfo=timezone.utc),
        stk_limit_rows=[
            *_market_default_rows("stk_limit", "2024-01-02"),
            ["20240102", "920690.BJ", None, 100000.0, 0.0],
        ],
    )

    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    with sqlite3.connect(store.database_path) as connection:
        persisted = connection.execute(
            """
            SELECT up_limit, down_limit
            FROM market_session_generation_rows_stk_limit
            WHERE generation_id = ? AND ts_code = ?
            """,
            (published["generation_id"], "920690.BJ"),
        ).fetchone()
    assert persisted == (100000.0, 0.0)
    assert audit["status"] == "passed"
    assert published["generation_id"] == audit["market_generation_refs"][0][
        "generation_id"
    ]
    assert audit["excluded_out_of_scope_market_rows"] == (
        baseline["excluded_out_of_scope_market_rows"] + 1
    )


@pytest.mark.parametrize(
    ("up_limit", "down_limit"),
    [(0.0, 9.0), (11.0, 0.0)],
)
def test_coverage_audit_rejects_zero_limit_for_expected_traded_stock(
    tmp_path, up_limit, down_limit
):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    _publish_market_session_for_audit(
        store,
        "2024-01-02",
        datetime(2024, 1, 4, 16, 0, tzinfo=timezone.utc),
        stk_limit_rows=[
            ["20240102", "600001.SH", None, up_limit, down_limit],
            ["20240102", "000002.SZ", 20.0, 22.0, 18.0],
        ],
    )

    with pytest.raises(PITReceiptError, match="market evidence mismatch.*stk_limit"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_coverage_audit_uses_daily_pre_close_when_limit_pre_close_is_null(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    _publish_market_session_for_audit(
        store,
        "2024-01-02",
        datetime(2024, 1, 4, 16, 0, tzinfo=timezone.utc),
        stk_limit_rows=[
            ["20240102", "600001.SH", None, 11.0, 9.0],
            ["20240102", "000002.SZ", None, 22.0, 18.0],
        ],
    )

    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert audit["status"] == "passed"


@pytest.mark.parametrize(
    ("up_limit", "down_limit"),
    [(11.0, 10.0), (9.9, 9.0)],
)
def test_coverage_audit_rejects_limit_interval_against_daily_pre_close(
    tmp_path, up_limit, down_limit
):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    _publish_market_session_for_audit(
        store,
        "2024-01-02",
        datetime(2024, 1, 4, 16, 0, tzinfo=timezone.utc),
        stk_limit_rows=[
            ["20240102", "600001.SH", None, up_limit, down_limit],
            ["20240102", "000002.SZ", 20.0, 22.0, 18.0],
        ],
    )

    with pytest.raises(PITReceiptError, match="market evidence mismatch.*stk_limit"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_coverage_audit_rejects_nonfinite_limit_tampered_in_database(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    generation_id = store.active_market_session_generation("2024-01-02")[
        "generation_id"
    ]
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE market_session_generation_rows_stk_limit
            SET up_limit = ?
            WHERE generation_id = ? AND ts_code = '600001.SH'
            """,
            (float("inf"), generation_id),
        )

    with pytest.raises(PITReceiptError, match="market generation normalized rows mismatch"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_database_rejects_nan_limit_even_if_check_constraints_are_ignored(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    generation_id = store.active_market_session_generation("2024-01-02")[
        "generation_id"
    ]

    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            # Python's sqlite adapter maps NaN to SQL NULL; NOT NULL remains a
            # fail-closed boundary even when CHECK constraints are bypassed.
            connection.execute(
                """
                UPDATE market_session_generation_rows_stk_limit
                SET down_limit = ?
                WHERE generation_id = ? AND ts_code = '600001.SH'
                """,
                (float("nan"), generation_id),
            )


def test_coverage_uses_historical_bak_snapshot_not_current_code_migration(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store, include_current_code_migration=True)

    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert audit["status"] == "passed"
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifact"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    universe = _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    try:
        loaded_codes = {
            item["ts_code"] for item in universe.items_as_of("2024-01-02")
        }
        assert "302132.SZ" not in loaded_codes
        assert "603999.SH" not in loaded_codes
    finally:
        universe.close()
    assert audit["stock_generation_manifest_sha256"] == (
        store.verify_stock_basic_generation()["manifest_sha256"]
    )
    assert audit["session_count"] == 2
    assert audit["maximum_session_rows_loaded"] == 3
    assert len(audit["receipt_manifest_sha256"]) == 64
    assert len(audit["coverage_audit_sha256"]) == 64


def test_coverage_audit_hash_is_bound_to_the_active_stock_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    first = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    _ingest_complete_two_day_fixture(store)
    second = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert second["stock_generation_id"] != first["stock_generation_id"]
    assert second["stock_generation_manifest_sha256"] != first[
        "stock_generation_manifest_sha256"
    ]
    assert second["coverage_audit_sha256"] != first["coverage_audit_sha256"]


def test_coverage_audit_binds_exact_generation_attempt_and_event_lineage(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    before = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    assert len(before["stock_generation_lineage_sha256"]) == 64

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            UPDATE fetch_attempts SET response_headers_json = ?
            WHERE attempt_id = (
                SELECT attempt_id FROM stock_basic_generation_shards
                ORDER BY logical_partition_key LIMIT 1
            )
            """,
            ('{"x-lineage-test":"changed"}',),
        )
    after_attempt = store.audit_coverage(
        start_date="2024-01-02", end_date="2024-01-03"
    )
    assert after_attempt["stock_generation_lineage_sha256"] != before[
        "stock_generation_lineage_sha256"
    ]
    assert after_attempt["coverage_audit_sha256"] != before[
        "coverage_audit_sha256"
    ]

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            UPDATE fetch_promotion_events SET recorded_at = ?
            WHERE attempt_id = (
                SELECT attempt_id FROM stock_basic_generation_shards
                ORDER BY logical_partition_key LIMIT 1
            )
            """,
            ("2024-01-01T00:00:00+00:00",),
        )
    after_event = store.audit_coverage(
        start_date="2024-01-02", end_date="2024-01-03"
    )
    assert after_event["stock_generation_lineage_sha256"] != after_attempt[
        "stock_generation_lineage_sha256"
    ]
    with pytest.raises(PITReceiptError, match="coverage audit hash mismatch"):
        store.publish_universe_artifact(
            str(tmp_path / "artifacts"),
            start_date="2024-01-02",
            end_date="2024-01-03",
            expected_coverage_audit_sha256=before["coverage_audit_sha256"],
        )


def test_coverage_audit_rejects_legacy_stock_receipts_without_active_generation(
    tmp_path,
):
    store = PITReceiptStore(str(tmp_path))
    for exchange in ("SSE", "SZSE"):
        for status in ("L", "D", "P", "G"):
            store.ingest_tushare_response(
                dataset="stock_basic",
                partition_key=f"{exchange}:{status}",
                endpoint="stock_basic",
                params={"exchange": exchange, "list_status": status},
                raw_bytes=_stock_response([]),
                http_status=200,
                retrieved_at="2024-01-01T16:00:00+08:00",
                row_cap=6000,
            )

    with pytest.raises(PITReceiptError, match="active stock_basic generation is missing"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_external_coverage_connection_requires_a_preexisting_snapshot(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    with store._connect() as connection:
        with pytest.raises(PITReceiptError, match="already be in a transaction"):
            store.audit_coverage(
                start_date="2024-01-02",
                end_date="2024-01-03",
                _connection=connection,
            )


def test_store_connection_context_closes_handle_after_exit(tmp_path):
    store = PITReceiptStore(str(tmp_path))

    with store._connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_external_coverage_connection_remains_borrowed(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)

    with store._connect() as connection:
        connection.execute("BEGIN")
        audit = store.audit_coverage(
            start_date="2024-01-02",
            end_date="2024-01-03",
            _connection=connection,
        )

        assert audit["status"] == "passed"
        assert connection.in_transaction is True
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_common_open_sessions_verifies_two_exchange_calendar_gate(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)

    assert store.common_open_sessions(
        start_date="2024-01-02", end_date="2024-01-03"
    ) == ["2024-01-02", "2024-01-03"]


def test_coverage_audit_identity_does_not_depend_on_unrelated_module_bytes(
    tmp_path, monkeypatch
):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    first = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    original_read_bytes = Path.read_bytes
    module_path = Path(research_pit_store.__file__).resolve()

    def altered_read_bytes(path):
        if path.resolve() == module_path:
            return b"unrelated source edit"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", altered_read_bytes)
    second = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert second["verifier_code_sha256"] == first["verifier_code_sha256"]
    assert second["coverage_audit_sha256"] == first["coverage_audit_sha256"]


def test_coverage_hash_is_stable_when_unrelated_future_receipt_is_appended(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)
    before = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    store.ingest_tushare_response(
        dataset="bak_basic",
        partition_key="2024-01-04",
        endpoint="bak_basic",
        params={"trade_date": "20240104"},
        raw_bytes=_daily_response("20240104", [["600001.SH", "A", "银行"]]),
        http_status=200,
        retrieved_at="2024-01-04T16:00:00+08:00",
        row_cap=7000,
    )
    after = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert after["receipt_manifest_sha256"] == before["receipt_manifest_sha256"]
    assert after["coverage_audit_sha256"] == before["coverage_audit_sha256"]


def test_coverage_audit_rejects_missing_session_and_trusts_hashed_bak_snapshot(tmp_path):
    missing_store = PITReceiptStore(str(tmp_path / "missing"))
    _ingest_complete_two_day_fixture(missing_store, omit_day_two=True)
    with pytest.raises(PITReceiptError, match="missing complete daily receipt"):
        missing_store.audit_coverage(
            start_date="2024-01-02", end_date="2024-01-03"
        )

    partial_store = PITReceiptStore(str(tmp_path / "partial"))
    _ingest_complete_two_day_fixture(partial_store, omit_day_one_symbol=True)
    audit = partial_store.audit_coverage(
        start_date="2024-01-02", end_date="2024-01-03"
    )
    assert audit["status"] == "passed"
    assert audit["excluded_out_of_scope_market_rows"] == 3


def test_default_scope_explicitly_excludes_bse_rows_without_marking_them_extra(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store, include_bse=True)

    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert audit["status"] == "passed"
    assert audit["excluded_out_of_scope_daily_rows"] == 2


def test_bse_cannot_be_claimed_in_scope_without_a_supported_market_policy(tmp_path):
    store = PITReceiptStore(str(tmp_path))

    with pytest.raises(PITReceiptError, match="BSE scope is not supported"):
        store.audit_coverage(
            start_date="2024-01-02",
            end_date="2024-01-03",
            calendar_exchanges=("BSE",),
        )


def test_full_market_audit_cannot_be_downgraded_to_one_exchange(tmp_path):
    store = PITReceiptStore(str(tmp_path))

    with pytest.raises(PITReceiptError, match="full-market exchange policy"):
        store.audit_coverage(
            start_date="2024-01-02",
            end_date="2024-01-03",
            calendar_exchanges=("SSE",),
        )


def test_market_scope_cannot_be_changed_to_exclude_every_security(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    _ingest_complete_two_day_fixture(store)

    with pytest.raises(PITReceiptError, match="market scope policy is not supported"):
        store.audit_coverage(
            start_date="2024-01-02",
            end_date="2024-01-03",
            allowed_markets=("主版",),
        )


def test_raw_or_normalized_tampering_is_detected(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    raw = _daily_response("20240102", [["600001.SH", "A", "银行"]])
    receipt = store.ingest_tushare_response(
        dataset="bak_basic",
        partition_key="2024-01-02",
        endpoint="bak_basic",
        params={"trade_date": "20240102"},
        raw_bytes=raw,
        http_status=200,
        retrieved_at="2024-01-02T16:00:00+08:00",
        row_cap=7000,
    )
    (tmp_path / receipt["raw_path"]).write_bytes(b"{}")
    with pytest.raises(PITReceiptError, match="raw receipt (byte count|hash) mismatch"):
        store.verify_receipts()

    clean = PITReceiptStore(str(tmp_path / "normalized"))
    clean.ingest_tushare_response(
        dataset="bak_basic",
        partition_key="2024-01-02",
        endpoint="bak_basic",
        params={"trade_date": "20240102"},
        raw_bytes=raw,
        http_status=200,
        retrieved_at="2024-01-02T16:00:00+08:00",
        row_cap=7000,
    )
    clean.execute_for_test(
        "UPDATE daily_universe SET name = ? WHERE trade_date = ? AND ts_code = ?",
        ("篡改", "2024-01-02", "600001.SH"),
    )
    with pytest.raises(PITReceiptError, match="normalized rows hash mismatch"):
        clean.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-02",
            endpoint="bak_basic",
            params={"trade_date": "20240102"},
            raw_bytes=raw,
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=7000,
        )
    with pytest.raises(PITReceiptError, match="normalized rows hash mismatch"):
        clean.verify_receipts()


def test_orphan_normalized_rows_are_rejected_even_if_foreign_keys_were_bypassed(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    raw = _daily_response("20240102", [["600001.SH", "A", "银行"]])
    store.ingest_tushare_response(
        dataset="bak_basic",
        partition_key="2024-01-02",
        endpoint="bak_basic",
        params={"trade_date": "20240102"},
        raw_bytes=raw,
        http_status=200,
        retrieved_at="2024-01-02T16:00:00+08:00",
        row_cap=7000,
    )
    with sqlite3.connect(str(store.database_path)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO daily_universe (
                trade_date, ts_code, exchange, name, industry, list_date,
                receipt_dataset, receipt_partition
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2024-01-03",
                "600999.SH",
                "SSE",
                "孤儿行",
                "测试",
                "2010-01-01",
                "bak_basic",
                "ghost",
            ),
        )

    with pytest.raises(PITReceiptError, match="broken receipt foreign keys"):
        store.verify_receipts()


def test_non_success_http_status_is_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path))

    with pytest.raises(PITReceiptError, match="HTTP status"):
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-02",
            endpoint="bak_basic",
            params={"trade_date": "20240102"},
            raw_bytes=_daily_response("20240102", [["600001.SH", "A", "银行"]]),
            http_status=503,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=7000,
        )


@pytest.mark.parametrize(
    ("dataset", "partition_key", "endpoint", "params", "raw", "row_cap"),
    [
        (
            "bak_basic",
            "2024-01-02",
            "bak_basic",
            {"trade_date": "20240102", "ts_code": "600001.SH"},
            _daily_response("20240102", [["600001.SH", "A", "银行"]]),
            7000,
        ),
        (
            "stock_basic",
            "SSE:L",
            "stock_basic",
            {"exchange": "SSE", "list_status": "L", "limit": 1},
            _stock_response(
                [["600001.SH", "600001", "A", "SSE", "主板", "L", "20100101", None]]
            ),
            6000,
        ),
    ],
)
def test_narrowing_query_parameters_are_rejected(
    tmp_path, dataset, partition_key, endpoint, params, raw, row_cap
):
    store = PITReceiptStore(str(tmp_path))

    with pytest.raises(PITReceiptError, match="full-snapshot parameters"):
        store.ingest_tushare_response(
            dataset=dataset,
            partition_key=partition_key,
            endpoint=endpoint,
            params=params,
            raw_bytes=raw,
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=row_cap,
        )


def test_unknown_or_exchange_incompatible_market_label_is_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path))

    for market in ("未知板", "创业板"):
        with pytest.raises(PITReceiptError, match="market/exchange combination"):
            store.ingest_tushare_response(
                dataset="stock_basic",
                partition_key="SSE:L",
                endpoint="stock_basic",
                params={"exchange": "SSE", "list_status": "L"},
                raw_bytes=_stock_response(
                    [
                        [
                            "600001.SH",
                            "600001",
                            "A",
                            "SSE",
                            market,
                            "L",
                            "20100101",
                            None,
                        ]
                    ]
                ),
                http_status=200,
                retrieved_at="2024-01-02T16:00:00+08:00",
                row_cap=6000,
            )


def test_cdr_is_archived_on_either_main_exchange_but_remains_out_of_scope(tmp_path):
    for exchange, ts_code in (("SSE", "689001.SH"), ("SZSE", "009001.SZ")):
        store = PITReceiptStore(str(tmp_path / exchange))
        receipt = store.ingest_tushare_response(
            dataset="stock_basic",
            partition_key=f"{exchange}:L",
            endpoint="stock_basic",
            params={"exchange": exchange, "list_status": "L"},
            raw_bytes=_stock_response(
                [[ts_code, ts_code[:6], "CDR样本", exchange, "CDR", "L", "20200101", None]]
            ),
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=6000,
        )
        assert receipt["row_count"] == 1


def test_receipt_ingest_cli_preserves_native_response_bytes(tmp_path, capsys):
    raw_path = tmp_path / "response.json"
    raw = _daily_response("20240102", [["600001.SH", "A", "银行"]])
    raw_path.write_bytes(raw)
    store_dir = tmp_path / "store"

    assert (
        jobs.main(
            [
                "research-pit-ingest-response",
                "--store-dir",
                str(store_dir),
                "--dataset",
                "bak_basic",
                "--partition-key",
                "2024-01-02",
                "--endpoint",
                "bak_basic",
                "--params-json",
                '{"trade_date":"20240102"}',
                "--raw-response-path",
                str(raw_path),
                "--http-status",
                "200",
                "--retrieved-at",
                "2024-01-02T16:00:00+08:00",
                "--row-cap",
                "7000",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert PITReceiptStore(str(store_dir)).verify_receipts()["verified_receipt_count"] == 1


def test_concurrent_same_partition_same_bytes_is_idempotent(tmp_path):
    store = PITReceiptStore(str(tmp_path))
    raw = _daily_response("20240102", [["600001.SH", "A", "银行"]])

    def ingest(_index):
        return store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key="2024-01-02",
            endpoint="bak_basic",
            params={"trade_date": "20240102"},
            raw_bytes=raw,
            http_status=200,
            retrieved_at="2024-01-02T16:00:00+08:00",
            row_cap=7000,
        )["status"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(ingest, range(16)))

    assert statuses.count("stored") == 1
    assert statuses.count("reused") == 15
    assert store.receipt_count() == 1


def test_audited_store_streams_to_self_contained_universe_artifact(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store, include_bse=True)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    loader = _audited_universe_loader()
    with pytest.raises(TypeError, match="expected_coverage_audit_sha256"):
        loader.from_file(artifact["path"])

    universe = loader.from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )

    assert artifact["coverage_audit_sha256"] == audit["coverage_audit_sha256"]
    assert artifact["stock_generation"]["generation_id"] == audit[
        "stock_generation_id"
    ]
    assert artifact["stock_generation"]["manifest_sha256"] == audit[
        "stock_generation_manifest_sha256"
    ]
    assert artifact["maximum_rows_buffered"] <= 1000
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["bundle_sha256"] == Path(artifact["path"]).parent.name
    assert manifest["artifact_root_sha256"] == artifact["artifact_root_sha256"]
    assert universe.stock_generation_id == audit["stock_generation_id"]
    assert universe.stock_generation_manifest_sha256 == audit[
        "stock_generation_manifest_sha256"
    ]
    assert universe.item_as_of("000002", "2024-01-02")["name"] == "B"
    assert universe.item_as_of("000002", "2024-01-03") is None
    assert universe.item_as_of("600001", "2024-01-03")["name"] == "A"
    assert universe.item_as_of("920001", "2024-01-02") is None
    assert {item["symbol"] for item in universe.seed_items("2024-01-02", "2024-01-03")} == {
        "000002",
        "600001",
    }
    assert universe.final_oos_eligible is False
    universe.close()


def test_audited_universe_items_as_of_is_sorted_batched_and_reopen_safe(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store, include_bse=True)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    loader = _audited_universe_loader()
    universe = loader.from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    statements = []
    universe._connection.set_trace_callback(statements.append)

    rows = universe.items_as_of("2024-01-02")

    membership_queries = [
        sql for sql in statements if "FROM daily_universe AS daily" in sql
    ]
    assert len(membership_queries) == 1
    assert [row["symbol"] for row in rows] == ["000002", "600001"]
    statements.clear()
    assert rows == [
        universe.item_as_of("000002", "2024-01-02"),
        universe.item_as_of("600001", "2024-01-02"),
    ]
    assert all("security_master" not in sql for sql in statements)
    rows[0]["name"] = "caller mutation"
    assert universe.items_as_of("2024-01-02")[0]["name"] == "B"
    universe.close()

    reopened = loader.from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert [row["symbol"] for row in reopened.items_as_of("2024-01-03")] == [
        "600001"
    ]
    with pytest.raises(ValueError, match="outside PIT universe coverage"):
        reopened.items_as_of("2024-01-01")
    reopened.close()


def test_audited_universe_open_sessions_is_one_query_and_reopen_safe(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    loader = _audited_universe_loader()
    universe = loader.from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    statements = []
    universe._connection.set_trace_callback(statements.append)

    assert universe.open_sessions("2024-01-02", "2024-01-03") == [
        "2024-01-02",
        "2024-01-03",
    ]
    session_queries = [
        sql for sql in statements if "FROM trade_sessions" in sql
    ]
    assert len(session_queries) == 1
    universe.close()

    reopened = loader.from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert reopened.open_sessions("2024-01-03", "2024-01-03") == ["2024-01-03"]
    reopened.close()


def test_universe_artifact_replays_market_generation_audit_offline(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )

    bundle_root = Path(artifact["path"]).parent

    def _recompute_market_audit():
        # WHY: bypass PITReceiptStore.__init__ so a missing market table in the
        # snapshot surfaces as ``no such table`` instead of being silently
        # re-created by _initialize. Read-only so the bundle is not mutated.
        snapshot_store = PITReceiptStore.__new__(PITReceiptStore)
        snapshot_store.root = bundle_root
        snapshot_store.raw_root = bundle_root / "raw"
        snapshot_store.database_path = Path(artifact["path"])
        connection = sqlite3.connect(
            f"file:{Path(artifact['path']).as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        try:
            return snapshot_store.audit_coverage(
                start_date="2024-01-02",
                end_date="2024-01-03",
                _connection=connection,
            )
        finally:
            connection.close()

    recomputed = _recompute_market_audit()
    assert recomputed["market_generation_refs"] == audit["market_generation_refs"]
    assert recomputed["market_generation_root_sha256"] == audit[
        "market_generation_root_sha256"
    ]
    assert recomputed["coverage_audit_sha256"] == audit["coverage_audit_sha256"]
    assert recomputed["receipt_manifest_sha256"] == audit["receipt_manifest_sha256"]

    # WHY: deleting the source store proves the bundle is a self-contained
    # proof — every market row, attempt, event and raw byte it needs lives
    # inside bundle_root, with no live link back to the source database/raw tree.
    shutil.rmtree(tmp_path / "store")
    recomputed_after = _recompute_market_audit()
    assert recomputed_after["market_generation_refs"] == audit["market_generation_refs"]
    assert recomputed_after["market_generation_root_sha256"] == audit[
        "market_generation_root_sha256"
    ]
    assert recomputed_after["coverage_audit_sha256"] == audit["coverage_audit_sha256"]


def test_universe_export_rejects_stale_audit_root_and_tampered_artifact(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)

    with pytest.raises(PITReceiptError, match="coverage audit hash mismatch"):
        store.publish_universe_artifact(
            str(tmp_path / "artifacts"),
            start_date="2024-01-02",
            end_date="2024-01-03",
            expected_coverage_audit_sha256="0" * 64,
        )

    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    with open(artifact["path"], "ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(PITReceiptError, match="artifact file hash mismatch"):
        _audited_universe_loader().from_file(
            artifact["path"],
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )


def test_audited_universe_loader_rejects_legacy_json_even_with_an_audit_hash(tmp_path):
    legacy = tmp_path / "legacy-universe.json"
    legacy.write_text(
        json.dumps({"schema_version": "pit-universe/v1", "quality": {}}),
        encoding="utf-8",
    )

    with pytest.raises(PITReceiptError, match="audited.*SQLite|SQLite.*artifact|sqlite3"):
        _audited_universe_loader().from_file(
            str(legacy),
            expected_coverage_audit_sha256="a" * 64,
        )


def test_audited_bundle_survives_source_store_deletion(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store, include_bse=True)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )

    shutil.rmtree(store.root)
    universe = _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert universe.item_as_of("600001", "2024-01-03")["name"] == "A"
    universe.close()


def test_audited_bundle_replays_full_selected_receipt_lineage(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store, include_bse=True)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )

    with sqlite3.connect(artifact["path"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 4
        assert connection.execute(
            "SELECT COUNT(*) FROM receipts WHERE dataset='stock_basic'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM stock_basic_generations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM stock_basic_generation_head"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM stock_basic_generation_shards"
        ).fetchone()[0] == 8
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_universe WHERE ts_code='920001.BJ'"
        ).fetchone()[0] == 2
    raw_file_count = len(list((Path(artifact["path"]).parent / "raw").rglob("*.json")))
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))
    assert raw_file_count == manifest["raw"]["file_count"]
    with sqlite3.connect(artifact["path"]) as connection:
        # WHY: raw files are content-addressed CAS objects, so the on-disk count
        # must equal the set of distinct raw_sha256 referenced by the exported
        # receipts and attempts. Derive the exact count from the bundle's own
        # tables instead of a stale hard cap, so the two-day market generation's
        # shard raws survive export rather than being deduped to fit <12.
        distinct_raw_count = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT raw_sha256 FROM receipts
                UNION
                SELECT raw_sha256 FROM fetch_attempts WHERE raw_sha256 IS NOT NULL
            )
            """
        ).fetchone()[0]
        receipt_raw_count = connection.execute(
            "SELECT COUNT(DISTINCT raw_sha256) FROM receipts"
        ).fetchone()[0]
        market_shard_count = connection.execute(
            "SELECT COUNT(*) FROM market_session_generation_shards"
        ).fetchone()[0]
    assert raw_file_count == distinct_raw_count
    # two open sessions × four market datasets contribute eight shard raws that
    # must be preserved (no dedup against the retired <12 threshold).
    assert market_shard_count == 8
    assert raw_file_count >= receipt_raw_count + market_shard_count

    universe = _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert universe.item_as_of("920001", "2024-01-02") is None
    universe.close()


def test_loader_rejects_security_master_divergence_from_generation_projection(
    tmp_path,
):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))
    with sqlite3.connect(artifact["path"]) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "UPDATE security_master SET name = 'tampered-consumer-name' "
            "WHERE ts_code = '600001.SH'"
        )
        with pytest.raises(PITReceiptError, match="materialization mismatch"):
            _audited_universe_loader()._verify_stock_generation_materialization(
                connection, manifest["stock_generation"]
            )


def test_audited_bundle_preserves_controlled_attempt_and_promotion_lineage(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    _attach_controlled_attempts_to_all_receipts(store)
    failed_raw = _response([], [], code=2002, msg="permission denied")
    failed = store.record_fetch_attempt(
        dataset="stock_basic",
        partition_key="SSE:L",
        endpoint="stock_basic",
        params={"exchange": "SSE", "list_status": "L"},
        fields=NORMALIZED_FIELDS["stock_basic"],
        wire_request_sha256="f" * 64,
        raw_bytes=failed_raw,
        http_status=200,
        started_at="2024-01-01T15:59:59+08:00",
        retrieved_at="2024-01-01T16:00:00+08:00",
        elapsed_ns=1_000_000,
        row_cap=6000,
        body_complete=True,
    )
    assert store.promote_fetch_attempt(failed["attempt_id"])["status"] == "api_error"
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))

    with sqlite3.connect(artifact["path"]) as connection:
        stock_shard_count = connection.execute(
            "SELECT COUNT(*) FROM stock_basic_generation_shards"
        ).fetchone()[0]
        market_shard_count = connection.execute(
            "SELECT COUNT(*) FROM market_session_generation_shards"
        ).fetchone()[0]
        # WHY: controlled (legacy receipt) attempts are exactly those backed by a
        # committed receipt — JOIN receipts selects the trade_cal / bak_basic
        # attempts and excludes every generation shard (stock + market).
        controlled_count = connection.execute(
            """
            SELECT COUNT(*) FROM fetch_attempts AS attempt
            JOIN receipts
              ON receipts.dataset = attempt.dataset
             AND receipts.partition_key = attempt.partition_key
            """
        ).fetchone()[0]
        # every exported market shard attempt must be present AND carry its
        # market_session_staged terminal event.
        market_shard_attempt_gaps = connection.execute(
            """
            SELECT COUNT(*) FROM market_session_generation_shards AS shard
            WHERE NOT EXISTS (
                SELECT 1 FROM fetch_attempts AS attempt
                WHERE attempt.attempt_id = shard.attempt_id
            )
            """
        ).fetchone()[0]
        unstaged_market = connection.execute(
            """
            SELECT COUNT(*) FROM market_session_generation_shards AS shard
            WHERE NOT EXISTS (
                SELECT 1 FROM fetch_promotion_events AS event
                WHERE event.attempt_id = shard.attempt_id
                  AND event.status = 'market_session_staged'
            )
            """
        ).fetchone()[0]
    expected_attempts = stock_shard_count + market_shard_count + controlled_count
    assert expected_attempts == 20  # 8 stock + 8 market + 4 controlled receipt attempts
    assert manifest["tables"]["fetch_attempts"]["rows"] == expected_attempts
    assert manifest["tables"]["fetch_promotion_events"]["rows"] == expected_attempts
    assert market_shard_attempt_gaps == 0
    assert unstaged_market == 0
    assert manifest["quality"]["controlled_request_lineage_complete"] is True
    shutil.rmtree(store.root)
    universe = _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert universe.item_as_of("600001", "2024-01-02")["name"] == "A"
    universe.close()


def test_publish_recovers_matching_orphan_promotion_before_freezing_bundle(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    _attach_controlled_attempts_to_all_receipts(store)
    with sqlite3.connect(store.database_path) as connection:
        # WHY: limit orphan-promotion rehearsal to legacy selected receipts
        # (trade_cal / bak_basic). A bare `dataset != 'stock_basic'` would now
        # also match market_session generation shards, and deleting their
        # market_session_staged event would break the per-session fail-closed
        # generation. JOINing receipts selects only attempts backed by an
        # artifact-selected receipt and excludes every generation shard.
        orphan_id = connection.execute(
            """
            SELECT attempt.attempt_id
            FROM fetch_attempts AS attempt
            JOIN receipts
              ON receipts.dataset = attempt.dataset
             AND receipts.partition_key = attempt.partition_key
            JOIN fetch_promotion_events AS event
              ON event.attempt_id = attempt.attempt_id
            WHERE attempt.dataset IN ('trade_cal', 'bak_basic')
            ORDER BY attempt.attempt_sequence LIMIT 1
            """
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM fetch_promotion_events WHERE attempt_id = ?", (orphan_id,)
        )

    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))

    assert manifest["quality"]["controlled_request_lineage_complete"] is True
    assert manifest["tables"]["fetch_attempts"]["rows"] == manifest["tables"][
        "fetch_promotion_events"
    ]["rows"]
    repaired = store.fetch_attempts(attempt_id=orphan_id)[0]
    assert repaired["terminal_status"] in {"reused", "recovered"}


def test_reopen_rejects_crash_gap_and_artifact_keeps_related_failed_attempt(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    _ingest_complete_two_day_fixture(store)
    _attach_controlled_attempts_to_all_receipts(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        staged = connection.execute(
            """
            SELECT attempt.* FROM fetch_attempts AS attempt
            JOIN market_session_generation_shards AS shard
              ON shard.attempt_id = attempt.attempt_id
            WHERE shard.dataset = 'stk_limit'
              AND attempt.partition_key = '2024-01-03'
            """
        ).fetchone()
        columns = [row[1] for row in connection.execute("PRAGMA table_info(fetch_attempts)")]
        values = dict(staged)
        values["attempt_sequence"] = connection.execute(
            "SELECT MAX(attempt_sequence) + 1 FROM fetch_attempts"
        ).fetchone()[0]
        values["attempt_id"] = "crash-gap-stk-limit-2024-01-03"
        connection.execute(
            f"INSERT INTO fetch_attempts ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(values[column] for column in columns),
        )

    reopened = PITReceiptStore(str(root))
    repaired = reopened.fetch_attempts(
        attempt_id="crash-gap-stk-limit-2024-01-03"
    )[0]
    assert repaired["terminal_status"] == "rejected"
    details = repaired["promotion_events"][0]["details"]
    assert details["recovery"] == "recorded_attempt_without_terminal_event"
    assert details["reason"] == "uncommitted_before_crash"
    assert len(details["evidence_sha256"]) == 64
    assert details["attempt_recorded_at"] == repaired["recorded_at"]

    audit = reopened.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = reopened.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    with sqlite3.connect(artifact["path"]) as connection:
        assert connection.execute(
            "SELECT status FROM fetch_promotion_events WHERE attempt_id = ?",
            ("crash-gap-stk-limit-2024-01-03",),
        ).fetchone() == ("rejected",)
        assert connection.execute(
            "SELECT raw_sha256 FROM fetch_attempts WHERE attempt_id = ?",
            ("crash-gap-stk-limit-2024-01-03",),
        ).fetchone() == (repaired["raw_sha256"],)


def test_reopen_recovers_committed_market_shard_terminal_event(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    _ingest_complete_two_day_fixture(store)
    with sqlite3.connect(store.database_path) as connection:
        attempt_id = connection.execute(
            """
            SELECT attempt_id FROM market_session_generation_shards
            WHERE dataset = 'stk_limit' ORDER BY generation_id LIMIT 1
            """
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM fetch_promotion_events WHERE attempt_id = ?", (attempt_id,)
        )

    reopened = PITReceiptStore(str(root))
    repaired = reopened.fetch_attempts(attempt_id=attempt_id)[0]
    assert repaired["terminal_status"] == "market_session_staged"
    assert repaired["promotion_events"][0]["details"]["recovery"] == (
        "generation_shard_committed_before_event"
    )


def test_artifact_excludes_terminal_attempt_outside_selected_coverage(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    _attach_controlled_attempts_to_all_receipts(store)
    unrelated = store.record_fetch_attempt(
        dataset="stk_limit",
        partition_key="2024-01-04",
        endpoint="stk_limit",
        params={"trade_date": "20240104"},
        fields=NORMALIZED_FIELDS["stk_limit"],
        wire_request_sha256="e" * 64,
        raw_bytes=None,
        http_status=None,
        elapsed_ns=1,
        row_cap=MARKET_SESSION_ROW_CAPS["stk_limit"],
        body_complete=False,
        error_kind="transport_error",
        error_message="connection reset",
    )
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    with sqlite3.connect(artifact["path"]) as connection:
        assert connection.execute(
            "SELECT 1 FROM fetch_attempts WHERE attempt_id = ?",
            (unrelated["attempt_id"],),
        ).fetchone() is None


def test_offline_lineage_verifier_rejects_attempt_without_terminal_event(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    _attach_controlled_attempts_to_all_receipts(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))
    with sqlite3.connect(artifact["path"]) as connection:
        connection.row_factory = sqlite3.Row
        attempt_id = connection.execute(
            "SELECT attempt_id FROM fetch_attempts ORDER BY attempt_sequence LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM fetch_promotion_events WHERE attempt_id = ?", (attempt_id,)
        )
        with pytest.raises(PITReceiptError, match="terminal event cardinality"):
            _audited_universe_loader()._verify_controlled_request_lineage(
                connection, manifest["quality"]
            )


def test_manifest_distinguishes_consumer_coverage_from_wide_calendar_receipts(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store, calendar_padding=True)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))

    assert manifest["coverage"] == {
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
    }
    assert manifest["materialized_ranges"]["trade_sessions"] == {
        "start_date": "2024-01-01",
        "end_date": "2024-01-04",
        "rows": 8,
    }


def test_future_p_and_null_date_g_are_preserved_as_proof_but_not_consumed(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store, include_future_p_and_g=True)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )

    with sqlite3.connect(artifact["path"]) as connection:
        assert connection.execute(
            "SELECT list_status, list_date FROM security_master WHERE ts_code='600010.SH'"
        ).fetchone() == ("P", "2025-01-01")
        assert connection.execute(
            "SELECT list_status, list_date FROM security_master WHERE ts_code='000010.SZ'"
        ).fetchone() == ("G", None)
    universe = _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert universe.item_as_of("600010", "2024-01-02") is None
    assert universe.item_as_of("000010", "2024-01-02") is None
    universe.close()


def test_raw_tamper_and_poisoned_existing_bundle_are_never_resigned(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    manifest_path = Path(artifact["manifest_path"])
    original_manifest = manifest_path.read_bytes()
    raw_path = next((Path(artifact["path"]).parent / "raw").rglob("*.json"))
    raw_path.write_bytes(raw_path.read_bytes() + b"poison")

    with pytest.raises(PITReceiptError, match="raw receipt"):
        _audited_universe_loader().from_file(
            artifact["path"],
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )
    with pytest.raises(PITReceiptError, match="raw receipt"):
        store.publish_universe_artifact(
            str(tmp_path / "artifacts"),
            start_date="2024-01-02",
            end_date="2024-01-03",
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )
    assert manifest_path.read_bytes() == original_manifest


def test_identical_audited_bundle_publish_is_verified_and_reused(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    first = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    second = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )

    assert first["status"] == "stored"
    assert second["status"] == "reused"
    assert second["path"] == first["path"]
    universe = _audited_universe_loader().from_file(
        first["path"], expected_coverage_audit_sha256=audit["coverage_audit_sha256"]
    )
    try:
        assert universe.temporal_role == "legacy_development_unbound"
        assert universe.temporal_contract_sha256 is None
        assert universe.final_oos_eligible is False
    finally:
        universe.close()


def test_bound_loader_requires_all_external_authority_anchors(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    with pytest.raises(TypeError):
        research_pit_store.AuditedPointInTimeUniverse.from_file(
            artifact["path"],
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )
    universe = research_pit_store.AuditedPointInTimeUniverse.from_legacy_unbound_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    universe.close()


def test_audited_bundle_publish_cli_rejects_legacy_unbound_attempts(tmp_path):
    store_dir = tmp_path / "store"
    store = PITReceiptStore(str(store_dir))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    with pytest.raises(PITReceiptError, match="selected attempts"):
        jobs.main(
            [
                "research-pit-publish-universe",
                "--store-dir",
                str(store_dir),
                "--start-date",
                "2024-01-02",
                "--end-date",
                "2024-01-03",
                "--expected-coverage-audit-sha256",
                audit["coverage_audit_sha256"],
                "--temporal-role",
                "contaminated_diagnostic",
                "--output-dir",
                str(tmp_path / "artifacts"),
            ]
        )


def test_publish_cli_rejects_sealed_role_before_store_io(monkeypatch):
    opened = False

    class ForbiddenStore:
        def __init__(self, _path):
            nonlocal opened
            opened = True

    monkeypatch.setattr(jobs, "PITReceiptStore", ForbiddenStore)
    with pytest.raises(ValueError, match="forbidden"):
        jobs.main(
            [
                "research-pit-publish-universe",
                "--store-dir", "must-not-open",
                "--start-date", "2026-07-13",
                "--end-date", "2026-07-13",
                "--temporal-role", "final_oos",
                "--expected-coverage-audit-sha256", "0" * 64,
            ]
        )
    assert opened is False


def test_publish_cli_passes_verified_temporal_binding(monkeypatch, capsys):
    captured = {}

    class RecordingStore:
        def __init__(self, path):
            captured["store_dir"] = path

        def publish_universe_artifact(self, directory, **kwargs):
            captured.update(kwargs)
            return {"path": str(Path(directory) / "metadata.sqlite3")}

    monkeypatch.setattr(jobs, "PITReceiptStore", RecordingStore)
    assert jobs.main(
        [
            "research-pit-publish-universe",
            "--store-dir", "offline-store",
            "--start-date", "2024-01-02",
            "--end-date", "2024-01-03",
            "--temporal-role", "contaminated_diagnostic",
            "--expected-coverage-audit-sha256", "0" * 64,
        ]
    ) == 0
    contract = load_temporal_partition_contract(
        "data/research_partitions/frozen-v1.json"
    )
    assert captured["temporal_role"] == "contaminated_diagnostic"
    assert captured["temporal_contract_sha256"] == contract["contract_sha256"]
    assert captured["permitted_operation"] == "publish"
    assert captured["promotion_eligible"] is False
    assert json.loads(capsys.readouterr().out)["path"].endswith("metadata.sqlite3")


def test_store_publish_temporal_binding_parameters_are_atomic(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="temporal binding"):
        store.publish_universe_artifact(
            str(tmp_path / "artifacts"),
            start_date="2024-01-02",
            end_date="2024-01-03",
            expected_coverage_audit_sha256="0" * 64,
            temporal_role="contaminated_diagnostic",
        )


def test_concurrent_identical_bundle_publish_has_one_atomic_final_directory(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    output_dir = tmp_path / "artifacts"

    def publish(_index):
        return store.publish_universe_artifact(
            str(output_dir),
            start_date="2024-01-02",
            end_date="2024-01-03",
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(publish, range(4)))

    assert [result["status"] for result in results].count("stored") == 1
    assert [result["status"] for result in results].count("reused") == 3
    assert len({result["path"] for result in results}) == 1
    final_directories = [path for path in output_dir.iterdir() if not path.name.startswith(".")]
    assert len(final_directories) == 1
    assert not list(output_dir.glob(".audited-universe.*"))


def test_independent_generations_publish_distinct_lineage_anchored_bundles(tmp_path):
    stores = [PITReceiptStore(str(tmp_path / f"store-{index}")) for index in range(2)]
    for store in stores:
        _ingest_complete_two_day_fixture(store)
    audits = [
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
        for store in stores
    ]
    assert audits[0]["stock_generation_lineage_sha256"] != audits[1][
        "stock_generation_lineage_sha256"
    ]
    assert audits[0]["coverage_audit_sha256"] != audits[1][
        "coverage_audit_sha256"
    ]
    output_dir = tmp_path / "artifacts"

    def publish(index):
        return stores[index].publish_universe_artifact(
            str(output_dir),
            start_date="2024-01-02",
            end_date="2024-01-03",
            expected_coverage_audit_sha256=audits[index]["coverage_audit_sha256"],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, range(2)))

    assert [result["status"] for result in results] == ["stored", "stored"]
    assert len({result["path"] for result in results}) == 2


# WHY: the artifact manifest must explicitly bind the market generations it
# freezes, so a tampered / rolled-back / extra market generation cannot hide
# behind a valid outer signature. These tests pin the v4 market_generations
# object and the fail-closed semantic re-proof.


def _publish_two_day_bundle(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "artifacts"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    return store, audit, artifact


def _resign_manifest(manifest_path):
    """Re-derive artifact_root / bundle / manifest hashes after a tamper.

    Mirrors publish_universe_artifact's signing scheme so a mutated manifest is
    not trivially caught by an outer hash mismatch — only the semantic re-proof
    can reject it. Reads the on-disk manifest, re-signs it in place.
    """

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    non_logical = {
        "artifact_root_sha256",
        "sqlite",
        "maximum_rows_buffered",
        "bundle_sha256",
        "manifest_sha256",
    }
    logical = {key: value for key, value in manifest.items() if key not in non_logical}
    manifest["artifact_root_sha256"] = research_pit_store._sha256(logical)
    bundle_payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"bundle_sha256", "manifest_sha256"}
    }
    manifest["bundle_sha256"] = research_pit_store._sha256(bundle_payload)
    signed_payload = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    manifest["manifest_sha256"] = research_pit_store._sha256(signed_payload)
    manifest_path.write_text(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"),
        encoding="utf-8",
    )


def _relocate_bundle(manifest_path):
    """Rename the bundle directory to the re-signed bundle hash so the loader's
    directory-name check does not short-circuit the semantic re-proof."""

    bundle_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relocated = bundle_root.parent / manifest["bundle_sha256"]
    bundle_root.rename(relocated)
    new_manifest = relocated / "manifest.json"
    new_manifest.write_text(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"),
        encoding="utf-8",
    )
    return relocated, new_manifest


def _fully_resign_temporal_binding(manifest_path, binding):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["temporal_binding"] = binding
    non_logical = {
        "artifact_root_sha256", "sqlite", "maximum_rows_buffered",
        "bundle_sha256", "manifest_sha256",
    }
    logical = {key: value for key, value in manifest.items() if key not in non_logical}
    root = research_pit_store._sha256(logical)
    database_path = manifest_path.parent / manifest["sqlite"]["path"]
    connection = sqlite3.connect(database_path)
    try:
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO artifact_metadata (key, value_json) VALUES (?, ?)",
                ("temporal_binding", research_pit_store._canonical_json(binding)),
            )
            connection.execute(
                "UPDATE artifact_metadata SET value_json = ? WHERE key = 'artifact_root_sha256'",
                (research_pit_store._canonical_json(root),),
            )
    finally:
        connection.close()
    manifest["artifact_root_sha256"] = root
    manifest["sqlite"] = {
        **manifest["sqlite"],
        "sha256": research_pit_store._file_sha256(database_path),
        "bytes": database_path.stat().st_size,
    }
    bundle_payload = {
        key: value for key, value in manifest.items()
        if key not in {"bundle_sha256", "manifest_sha256"}
    }
    manifest["bundle_sha256"] = research_pit_store._sha256(bundle_payload)
    manifest["manifest_sha256"] = research_pit_store._sha256(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    relocated = manifest_path.parent.parent / manifest["bundle_sha256"]
    manifest_path.parent.rename(relocated)
    return relocated / "metadata.sqlite3", manifest


def test_bound_loader_external_authority_defeats_complete_local_resign(tmp_path):
    _store, audit, artifact = _publish_two_day_bundle(tmp_path)
    binding = {
        "schema_version": "research-artifact-temporal-binding/v1",
        "contract_sha256": "a" * 64,
        "role": "contaminated_diagnostic",
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
        "permitted_operation": "publish",
        "promotion_eligible": False,
    }
    database_path, manifest = _fully_resign_temporal_binding(
        Path(artifact["manifest_path"]), binding
    )
    anchors = {
        "expected_coverage_audit_sha256": audit["coverage_audit_sha256"],
        "expected_artifact_root_sha256": manifest["artifact_root_sha256"],
        "expected_temporal_contract_sha256": binding["contract_sha256"],
        "expected_temporal_role": binding["role"],
    }
    universe = research_pit_store.AuditedPointInTimeUniverse.from_file(
        str(database_path), **anchors
    )
    universe.close()

    tampered = {**binding, "role": "development"}
    database_path, _resigned = _fully_resign_temporal_binding(
        database_path.parent / "manifest.json", tampered
    )
    with pytest.raises(PITReceiptError, match="external anchor"):
        research_pit_store.AuditedPointInTimeUniverse.from_file(
            str(database_path), **anchors
        )


def test_universe_artifact_manifest_binds_market_generations(tmp_path):
    _store, audit, artifact = _publish_two_day_bundle(tmp_path)
    manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))

    market_generations = manifest["market_generations"]
    assert set(market_generations) == {"count", "root_sha256", "refs"}
    assert market_generations["count"] == len(market_generations["refs"])
    assert market_generations["count"] == audit["market_generation_count"]
    assert (
        market_generations["root_sha256"] == audit["market_generation_root_sha256"]
    )
    assert market_generations["refs"] == audit["market_generation_refs"]

    # WHY: every ref is a fixed five-field shape so a missing/extra binding
    # field cannot slip through as a silent schema drift.
    for ref in market_generations["refs"]:
        assert set(ref) == {
            "trade_date",
            "generation_id",
            "manifest_sha256",
            "lineage_sha256",
            "vintage",
        }
        assert ref["vintage"] in MARKET_SESSION_VINTAGES
        assert len(ref["manifest_sha256"]) == 64
        assert len(ref["lineage_sha256"]) == 64

    dates = [ref["trade_date"] for ref in market_generations["refs"]]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)

    assert research_pit_store._sha256(market_generations["refs"]) == (
        market_generations["root_sha256"]
    )
    # WHY: the descriptor is the publish API surface, so it must surface the
    # same binding a caller uses to anchor the artifact.
    assert artifact["market_generations"] == market_generations

    # WHY: coverage open-day set must equal the bound ref dates — no hidden
    # session can exist outside the market generation proof.
    with sqlite3.connect(artifact["path"]) as connection:
        open_days = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT cal_date FROM trade_sessions WHERE is_open = 1"
            )
        }
    assert open_days == set(dates)

    # WHY: deleting the source store proves the bundle is self-contained; the
    # market_generations binding reloads purely from the snapshot.
    shutil.rmtree(tmp_path / "store")
    universe = _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert universe.manifest["market_generations"] == market_generations
    assert universe.item_as_of("600001", "2024-01-03")["name"] == "A"
    universe.close()


def test_universe_artifact_rejects_resigned_manifest_with_tampered_market_root(
    tmp_path,
):
    _store, audit, artifact = _publish_two_day_bundle(tmp_path)
    manifest_path = Path(artifact["manifest_path"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # WHY: claim a different root than the refs actually hash to. The manifest
    # is re-signed and relocated so only the semantic structural check catches
    # the lie (not an outer hash / directory mismatch).
    manifest["market_generations"]["root_sha256"] = "e" * 64
    manifest_path.write_text(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"),
        encoding="utf-8",
    )
    _resign_manifest(manifest_path)
    relocated, _new_manifest = _relocate_bundle(manifest_path)

    with pytest.raises(PITReceiptError, match="market generations root mismatch"):
        _audited_universe_loader().from_file(
            str(relocated / "metadata.sqlite3"),
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )


def test_universe_artifact_rejects_tampered_manifest_and_metadata_via_rerun_audit(
    tmp_path,
):
    _store, audit, artifact = _publish_two_day_bundle(tmp_path)
    manifest_path = Path(artifact["manifest_path"])
    database_path = Path(artifact["path"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # WHY: keep the tamper structurally self-consistent (root follows the
    # forged refs, vintage/hashes still valid, dates unchanged) so every
    # structural + manifest==metadata + table-root + sqlite-hash check passes.
    # Only the rerun audit — which re-derives refs from the untampered table
    # evidence — can detect the forgery.
    forged_refs = [
        {**ref, "lineage_sha256": "e" * 64}
        for ref in manifest["market_generations"]["refs"]
    ]
    manifest["market_generations"]["refs"] = forged_refs
    manifest["market_generations"]["root_sha256"] = (
        research_pit_store._sha256(forged_refs)
    )
    non_logical = {
        "artifact_root_sha256",
        "sqlite",
        "maximum_rows_buffered",
        "bundle_sha256",
        "manifest_sha256",
    }
    forged_logical = {k: v for k, v in manifest.items() if k not in non_logical}
    forged_root = research_pit_store._sha256(forged_logical)

    # Mirror the forgery into artifact_metadata so manifest==metadata agrees
    # and artifact_root recomputes consistently. This mutates the DB file, so
    # the sqlite hash must be re-derived afterwards.
    connection = sqlite3.connect(database_path)
    try:
        with connection:
            connection.execute(
                "UPDATE artifact_metadata SET value_json = ? WHERE key = 'market_generations'",
                (research_pit_store._canonical_json(manifest["market_generations"]),),
            )
            connection.execute(
                "UPDATE artifact_metadata SET value_json = ? WHERE key = 'artifact_root_sha256'",
                (research_pit_store._canonical_json(forged_root),),
            )
    finally:
        connection.close()

    # WHY: re-derive the sqlite hash/bytes after the metadata edit, then re-sign
    # so the loader is not stopped by the file-hash check and actually reaches
    # the semantic rerun audit.
    manifest["sqlite"] = {
        "path": database_path.name,
        "sha256": research_pit_store._file_sha256(database_path),
        "bytes": database_path.stat().st_size,
    }
    manifest_path.write_text(
        (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"),
        encoding="utf-8",
    )
    _resign_manifest(manifest_path)
    relocated, _new_manifest = _relocate_bundle(manifest_path)

    with pytest.raises(PITReceiptError, match="market generation proof mismatch"):
        _audited_universe_loader().from_file(
            str(relocated / "metadata.sqlite3"),
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )
