import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.research_pit_store import PITReceiptError, PITReceiptStore


BASE = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
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
PARTITIONS = tuple(
    f"{exchange}:{status}"
    for exchange in ("SSE", "SZSE")
    for status in ("L", "D", "P", "G")
)


def _canonical_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stock_body(exchange, status, *, label="same", duplicate_across_status=False):
    items = []
    if status == "L":
        suffix = "SH" if exchange == "SSE" else "SZ"
        symbol = "600001" if exchange == "SSE" else "000001"
        items = [
            [
                f"{symbol}.{suffix}",
                symbol,
                f"{label}-{exchange}",
                exchange,
                "主板",
                status,
                "20100101",
                None,
            ]
        ]
    elif duplicate_across_status and (exchange, status) == ("SSE", "P"):
        items = [
            [
                "600001.SH",
                "600001",
                f"{label}-duplicate",
                "SSE",
                "主板",
                "P",
                "20100101",
                None,
            ]
        ]
    return json.dumps(
        {
            "request_id": f"fixed-{exchange}-{status}-{label}",
            "code": 0,
            "msg": "",
            "data": {"fields": list(STOCK_FIELDS), "items": items},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_attempt(
    store,
    partition,
    retrieved_at,
    *,
    label="same",
    duplicate_across_status=False,
):
    exchange, status = partition.split(":")
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
        "fields": list(STOCK_FIELDS),
        "row_cap": 6000,
    }
    raw = _stock_body(
        exchange,
        status,
        label=label,
        duplicate_across_status=duplicate_across_status,
    )
    wire_sha256 = hashlib.sha256(
        f"{partition}:{retrieved_at}:{label}".encode("utf-8")
    ).hexdigest()
    return store.record_fetch_attempt(
        dataset="stock_basic",
        partition_key=partition,
        endpoint="stock_basic",
        params=params,
        fields=STOCK_FIELDS,
        wire_request_sha256=wire_sha256,
        request_body_sha256=wire_sha256,
        request_semantics=semantics,
        request_semantics_sha256=_canonical_sha256(semantics),
        raw_bytes=raw,
        http_status=200,
        started_at=(datetime.fromisoformat(retrieved_at) - timedelta(seconds=1)).isoformat(),
        retrieved_at=retrieved_at,
        elapsed_ns=1_000_000_000,
        row_cap=6000,
        body_complete=True,
    )


def _begin_and_stage(
    store,
    started_at,
    *,
    label="same",
    partitions=PARTITIONS,
    duplicate_across_status=False,
    force_new=False,
):
    generation = store.begin_or_resume_stock_basic_generation(
        started_at,
        force_new=force_new,
    )
    started = datetime.fromisoformat(started_at)
    attempts = {}
    for index, partition in enumerate(partitions, 1):
        attempt = _record_attempt(
            store,
            partition,
            (started + timedelta(seconds=index)).isoformat(),
            label=label,
            duplicate_across_status=duplicate_across_status,
        )
        store.stage_stock_basic_attempt(
            generation["generation_id"],
            partition,
            attempt["attempt_id"],
        )
        attempts[partition] = attempt
    return generation, attempts


def _publish_generation(store, started_at, *, label, force_new=False):
    generation, attempts = _begin_and_stage(
        store,
        started_at,
        label=label,
        force_new=force_new,
    )
    published = store.publish_stock_basic_generation(generation["generation_id"])
    assert published["status"] == "published"
    return generation, attempts


def _assert_sha256(value):
    assert isinstance(value, str)
    assert len(value) == 64
    assert set(value) <= set("0123456789abcdef")


def _table_with_columns(database_path: Path, required_columns, *, expected_table=None):
    with sqlite3.connect(database_path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        matches = []
        for name in names:
            assert name.replace("_", "").isalnum()
            columns = {
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{name}")')
            }
            if set(required_columns) <= columns:
                matches.append(name)
    if expected_table is not None:
        assert expected_table in matches, (
            f"expected {expected_table} to contain columns {required_columns}, "
            f"found {matches}"
        )
        return expected_table
    assert len(matches) == 1, (
        f"expected exactly one generation table with columns {required_columns}, "
        f"found {matches}"
    )
    return matches[0]


def _tamper_generation_manifest(store, generation_id):
    table = _table_with_columns(
        store.database_path,
        {"generation_id", "manifest_sha256", "status", "scope_key"},
        expected_table="stock_basic_generations",
    )
    with sqlite3.connect(store.database_path) as connection:
        cursor = connection.execute(
            f'UPDATE "{table}" SET manifest_sha256 = ? WHERE generation_id = ?',
            ("0" * 64, generation_id),
        )
        assert cursor.rowcount == 1


def _tamper_generation_row(store, generation_id):
    table = _table_with_columns(
        store.database_path,
        {"generation_id", "ts_code", "name"},
        expected_table="stock_basic_generation_rows",
    )
    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            f'SELECT ts_code FROM "{table}" WHERE generation_id = ? ORDER BY ts_code LIMIT 1',
            (generation_id,),
        ).fetchone()
        assert row is not None
        cursor = connection.execute(
            f'UPDATE "{table}" SET name = name || ? '
            "WHERE generation_id = ? AND ts_code = ?",
            ("-tampered", generation_id, row[0]),
        )
        assert cursor.rowcount == 1


def test_same_raw_in_distinct_generations_has_distinct_manifest_and_audit_identity(
    tmp_path,
):
    store = PITReceiptStore(str(tmp_path / "store"))
    first, first_attempts = _publish_generation(
        store,
        BASE.isoformat(),
        label="same",
    )
    second, second_attempts = _publish_generation(
        store,
        (BASE + timedelta(hours=2)).isoformat(),
        label="same",
        force_new=True,
    )

    assert {
        partition: attempt["raw_sha256"]
        for partition, attempt in first_attempts.items()
    } == {
        partition: attempt["raw_sha256"]
        for partition, attempt in second_attempts.items()
    }
    first_verified = store.verify_stock_basic_generation(first["generation_id"])
    second_verified = store.verify_stock_basic_generation(second["generation_id"])

    for generation, verified in (
        (first, first_verified),
        (second, second_verified),
    ):
        assert verified["generation_id"] == generation["generation_id"]
        assert verified["status"] == "published"
        assert {
            "manifest_sha256",
            "rows_sha256",
            "audit_identity_sha256",
        } <= verified.keys()
        _assert_sha256(verified["manifest_sha256"])
        _assert_sha256(verified["rows_sha256"])
        _assert_sha256(verified["audit_identity_sha256"])
    assert first_verified["manifest_sha256"] != second_verified["manifest_sha256"]
    assert first_verified["rows_sha256"] == second_verified["rows_sha256"]
    assert (
        first_verified["audit_identity_sha256"]
        != second_verified["audit_identity_sha256"]
    )


def test_publish_rejects_a_generation_with_a_missing_shard(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation, _attempts = _begin_and_stage(
        store,
        BASE.isoformat(),
        partitions=PARTITIONS[:-1],
    )

    with pytest.raises(PITReceiptError, match="(?i)missing|complete|eight|8"):
        store.publish_stock_basic_generation(generation["generation_id"])

    assert store.active_stock_basic_generation() is None


def test_cross_hour_attempt_cannot_be_staged_or_published(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation, _attempts = _begin_and_stage(
        store,
        BASE.isoformat(),
        partitions=PARTITIONS[:-1],
    )
    late_partition = PARTITIONS[-1]
    late_attempt = _record_attempt(
        store,
        late_partition,
        (BASE + timedelta(hours=1, microseconds=1)).isoformat(),
    )

    with pytest.raises(PITReceiptError, match="(?i)window|hour|expired|span"):
        store.stage_stock_basic_attempt(
            generation["generation_id"],
            late_partition,
            late_attempt["attempt_id"],
        )
    with pytest.raises(PITReceiptError, match="(?i)missing|complete|window|hour|span"):
        store.publish_stock_basic_generation(generation["generation_id"])

    assert store.active_stock_basic_generation() is None


def test_publish_rejects_duplicate_ts_code_across_status_shards(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation, _attempts = _begin_and_stage(
        store,
        BASE.isoformat(),
        duplicate_across_status=True,
    )

    with pytest.raises(PITReceiptError, match="(?i)duplicate|ts_code|security"):
        store.publish_stock_basic_generation(generation["generation_id"])

    assert store.active_stock_basic_generation() is None


def test_old_generation_remains_explicitly_verifiable_after_head_switch(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first, _first_attempts = _publish_generation(
        store,
        BASE.isoformat(),
        label="old",
    )
    before_switch = store.verify_stock_basic_generation(first["generation_id"])
    second, _second_attempts = _publish_generation(
        store,
        (BASE + timedelta(hours=2)).isoformat(),
        label="active",
        force_new=True,
    )

    active = store.active_stock_basic_generation()
    old_after_switch = store.verify_stock_basic_generation(first["generation_id"])
    active_verified = store.verify_stock_basic_generation(second["generation_id"])

    assert active["generation_id"] == second["generation_id"]
    assert old_after_switch == before_switch
    assert old_after_switch["generation_id"] == first["generation_id"]
    assert active_verified["generation_id"] == second["generation_id"]
    assert old_after_switch["manifest_sha256"] != active_verified["manifest_sha256"]


def test_default_verify_and_active_reject_tampered_head_manifest(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation, _attempts = _publish_generation(
        store, BASE.isoformat(), label="head-manifest"
    )
    explicit_before = store.verify_stock_basic_generation(generation["generation_id"])
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE stock_basic_generation_head SET manifest_sha256 = ?",
            ("0" * 64,),
        )

    with pytest.raises(PITReceiptError, match="(?i)head|manifest|hash|mismatch"):
        store.active_stock_basic_generation()
    with pytest.raises(PITReceiptError, match="(?i)head|manifest|hash|mismatch"):
        store.verify_stock_basic_generation()
    assert store.verify_stock_basic_generation(generation["generation_id"]) == explicit_before


def test_active_head_cannot_silently_roll_back_to_older_published_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first, _first_attempts = _publish_generation(
        store, BASE.isoformat(), label="old-head"
    )
    second, _second_attempts = _publish_generation(
        store,
        (BASE + timedelta(hours=2)).isoformat(),
        label="new-head",
        force_new=True,
    )
    first_verified = store.verify_stock_basic_generation(first["generation_id"])
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            UPDATE stock_basic_generation_head
            SET generation_id = ?, manifest_sha256 = ?
            """,
            (first["generation_id"], first_verified["manifest_sha256"]),
        )

    with pytest.raises(PITReceiptError, match="(?i)head|rollback|latest|sequence"):
        store.active_stock_basic_generation()
    with pytest.raises(PITReceiptError, match="(?i)head|rollback|latest|sequence"):
        store.verify_stock_basic_generation()
    assert store.verify_stock_basic_generation(first["generation_id"])[
        "generation_id"
    ] == first["generation_id"]
    assert store.verify_stock_basic_generation(second["generation_id"])[
        "generation_id"
    ] == second["generation_id"]


@pytest.mark.parametrize("tamper_kind", ["manifest", "row"])
def test_verify_deep_checks_persisted_generation_manifest_and_rows(
    tmp_path,
    tamper_kind,
):
    store = PITReceiptStore(str(tmp_path / tamper_kind))
    generation, _attempts = _publish_generation(
        store,
        BASE.isoformat(),
        label="deep-verify",
    )
    generation_id = generation["generation_id"]
    verified = store.verify_stock_basic_generation(generation_id)
    _assert_sha256(verified["manifest_sha256"])

    if tamper_kind == "manifest":
        _tamper_generation_manifest(store, generation_id)
    else:
        _tamper_generation_row(store, generation_id)

    with pytest.raises(PITReceiptError, match="(?i)hash|manifest|row|mismatch|integrity"):
        store.verify_stock_basic_generation(generation_id)


@pytest.mark.parametrize("target_index", [0, 1], ids=["historical", "active"])
def test_verify_detects_raw_tamper_in_active_and_historical_generations(
    tmp_path,
    target_index,
):
    store = PITReceiptStore(str(tmp_path / str(target_index)))
    first, first_attempts = _publish_generation(
        store,
        BASE.isoformat(),
        label="old",
    )
    second, second_attempts = _publish_generation(
        store,
        (BASE + timedelta(hours=2)).isoformat(),
        label="active",
        force_new=True,
    )
    generations = (first, second)
    attempts = (first_attempts, second_attempts)
    verified_before = [
        store.verify_stock_basic_generation(generation["generation_id"])
        for generation in generations
    ]
    target = generations[target_index]
    target_attempt = attempts[target_index]["SSE:L"]
    target_raw = store.root / target_attempt["raw_path"]

    target_raw.write_bytes(target_raw.read_bytes() + b"\n")

    with pytest.raises(PITReceiptError, match="(?i)raw|hash|bytes|mismatch|integrity"):
        store.verify_stock_basic_generation(target["generation_id"])
    other_index = 1 - target_index
    other = store.verify_stock_basic_generation(
        generations[other_index]["generation_id"]
    )
    assert other == verified_before[other_index]
