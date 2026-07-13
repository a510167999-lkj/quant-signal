import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app import research_pit_store
from app.research_pit_store import PITReceiptError, PITReceiptStore


@pytest.mark.parametrize("tamper_old", [False, True])
def test_legacy_migration_upgrades_published_history_without_moving_head(
    tmp_path, tamper_old
):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    generation_ids = []
    for generation_index, variant in enumerate(("base", "new")):
        generation = store.begin_or_resume_stock_basic_generation(
            BASE + timedelta(minutes=generation_index * 10)
        )
        generation_ids.append(generation["generation_id"])
        for index, partition in enumerate(PARTITIONS, 1):
            attempt = _record_attempt(
                store,
                partition,
                _at(minutes=generation_index * 10, seconds=index),
                variant=variant,
            )
            store.stage_stock_basic_attempt(
                generation["generation_id"], partition, attempt["attempt_id"]
            )
        store.publish_stock_basic_generation(generation["generation_id"])
    assert store.active_stock_basic_generation()["generation_id"] == generation_ids[1]

    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        for generation_id in generation_ids:
            verified = store._stock_generation_manifest_on_connection(
                connection, generation_id
            )
            payload = dict(verified["manifest"])
            payload.pop("expected_request_semantics", None)
            payload.pop("expected_request_semantics_sha256", None)
            legacy_hash = research_pit_store._sha256(payload)
            connection.execute(
                "UPDATE stock_basic_generations SET manifest_sha256 = ?, "
                "expected_request_semantics_json = NULL, "
                "expected_request_semantics_sha256 = NULL WHERE generation_id = ?",
                (legacy_hash, generation_id),
            )
            if generation_id == generation_ids[1]:
                connection.execute(
                    "UPDATE stock_basic_generation_head SET manifest_sha256 = ?",
                    (legacy_hash,),
                )
        if tamper_old:
            connection.execute(
                "UPDATE stock_basic_generations SET manifest_sha256 = ? "
                "WHERE generation_id = ?",
                ("0" * 64, generation_ids[0]),
            )

    if tamper_old:
        with pytest.raises(PITReceiptError, match="legacy.*manifest mismatch"):
            PITReceiptStore(str(root))
        return

    reopened = PITReceiptStore(str(root))
    assert reopened.active_stock_basic_generation()["generation_id"] == generation_ids[1]
    assert all(
        reopened.verify_stock_basic_generation(generation_id)["verification_status"]
        == "passed"
        for generation_id in generation_ids
    )
    with sqlite3.connect(reopened.database_path) as connection:
        records = json.loads(
            connection.execute(
                "SELECT value FROM store_metadata WHERE key = ?",
                ("generation_pin_migration/v1",),
            ).fetchone()[0]
        )
    assert {record["generation_id"] for record in records} == set(generation_ids)


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


def _at(**delta):
    return (BASE + timedelta(**delta)).isoformat()


def _canonical_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stock_body(exchange, status, *, variant="base"):
    items = []
    if status == "L":
        suffix = "SH" if exchange == "SSE" else "SZ"
        symbol = "600001" if exchange == "SSE" else "000001"
        name = "A" if variant == "base" else "B"
        items = [
            [
                f"{symbol}.{suffix}",
                symbol,
                name,
                exchange,
                "主板",
                status,
                "20100101",
                None,
            ]
        ]
    return json.dumps(
        {
            "request_id": f"generation-{exchange}-{status}-{variant}",
            "code": 0,
            "msg": "",
            "data": {"fields": list(STOCK_FIELDS), "items": items},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_attempt(store, partition, retrieved_at, *, variant="base"):
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
    raw = _stock_body(exchange, status, variant=variant)
    wire_sha256 = hashlib.sha256(
        f"{partition}:{retrieved_at}:{variant}".encode("utf-8")
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


def _new_generation(store, now=None):
    generation = store.begin_or_resume_stock_basic_generation(now or _at())
    assert generation["status"] == "collecting"
    return generation


def test_eight_distinct_shards_publish_atomically_and_become_the_active_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _new_generation(store)
    assert generation["started_at"] == _at()
    assert generation["staged_partitions"] == []
    assert store.active_stock_basic_generation() is None

    for index, partition in enumerate(PARTITIONS, 1):
        attempt = _record_attempt(store, partition, _at(seconds=index))
        staged = store.stage_stock_basic_attempt(
            generation["generation_id"], partition, attempt["attempt_id"]
        )
        assert staged["status"] == "staged"

    resumed = store.begin_or_resume_stock_basic_generation(_at(minutes=30))
    assert resumed["generation_id"] == generation["generation_id"]
    assert resumed["staged_partitions"] == list(PARTITIONS)
    published = store.publish_stock_basic_generation(generation["generation_id"])

    assert published["generation_id"] == generation["generation_id"]
    assert published["status"] == "published"
    assert published["staged_partitions"] == list(PARTITIONS)
    active = store.active_stock_basic_generation()
    assert active["generation_id"] == generation["generation_id"]
    assert active["status"] == "published"
    assert active["staged_partitions"] == list(PARTITIONS)


def test_publish_rejects_a_generation_with_even_one_missing_shard(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _new_generation(store)
    for index, partition in enumerate(PARTITIONS[:-1], 1):
        attempt = _record_attempt(store, partition, _at(seconds=index))
        store.stage_stock_basic_attempt(
            generation["generation_id"], partition, attempt["attempt_id"]
        )

    with pytest.raises(PITReceiptError, match="complete|missing|eight|8"):
        store.publish_stock_basic_generation(generation["generation_id"])

    resumed = store.begin_or_resume_stock_basic_generation(_at(minutes=10))
    assert resumed["generation_id"] == generation["generation_id"]
    assert resumed["status"] == "collecting"
    assert store.active_stock_basic_generation() is None


def test_staging_is_idempotent_for_same_attempt_and_conflicts_for_a_different_attempt(
    tmp_path,
):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _new_generation(store)
    first = _record_attempt(store, "SSE:L", _at(minutes=1), variant="base")
    second = _record_attempt(store, "SSE:L", _at(minutes=2), variant="changed")

    staged = store.stage_stock_basic_attempt(
        generation["generation_id"], "SSE:L", first["attempt_id"]
    )
    reused = store.stage_stock_basic_attempt(
        generation["generation_id"], "SSE:L", first["attempt_id"]
    )

    assert staged["status"] == "staged"
    assert reused["status"] == "reused"
    with pytest.raises(PITReceiptError, match="conflict|immutable|already staged"):
        store.stage_stock_basic_attempt(
            generation["generation_id"], "SSE:L", second["attempt_id"]
        )
    assert store.fetch_attempts(attempt_id=first["attempt_id"])[0]["raw_sha256"] == first[
        "raw_sha256"
    ]
    assert store.fetch_attempts(attempt_id=second["attempt_id"])[0]["raw_sha256"] == second[
        "raw_sha256"
    ]


def test_generation_and_stage_windows_include_exactly_one_hour_but_not_one_microsecond_more(
    tmp_path,
):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _new_generation(store)
    at_limit = _record_attempt(store, "SSE:L", _at(hours=1))
    after_limit = _record_attempt(store, "SSE:D", _at(hours=1, microseconds=1))

    assert store.stage_stock_basic_attempt(
        generation["generation_id"], "SSE:L", at_limit["attempt_id"]
    )["status"] == "staged"
    with pytest.raises(PITReceiptError, match="window|hour|expired|span"):
        store.stage_stock_basic_attempt(
            generation["generation_id"], "SSE:D", after_limit["attempt_id"]
        )
    assert store.begin_or_resume_stock_basic_generation(_at(seconds=3599, microseconds=999999))[
        "generation_id"
    ] == generation["generation_id"]
    assert store.begin_or_resume_stock_basic_generation(_at(hours=1))["generation_id"] == generation[
        "generation_id"
    ]

    replacement = store.begin_or_resume_stock_basic_generation(
        _at(hours=1, microseconds=1)
    )
    assert replacement["generation_id"] != generation["generation_id"]
    assert replacement["status"] == "collecting"
    assert replacement["staged_partitions"] == []


def test_expired_incomplete_generation_is_abandoned_without_mutating_old_attempt_or_raw(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _new_generation(store)
    attempt = _record_attempt(store, "SSE:L", _at(minutes=10))
    store.stage_stock_basic_attempt(
        generation["generation_id"], "SSE:L", attempt["attempt_id"]
    )
    before = store.fetch_attempts(attempt_id=attempt["attempt_id"])[0]
    raw_path = store.root / before["raw_path"]
    raw_before = raw_path.read_bytes()

    replacement = store.begin_or_resume_stock_basic_generation(
        _at(hours=1, microseconds=1)
    )

    assert replacement["generation_id"] != generation["generation_id"]
    with pytest.raises(PITReceiptError, match="abandoned|collecting|inactive|expired"):
        store.publish_stock_basic_generation(generation["generation_id"])
    with pytest.raises(PITReceiptError, match="abandoned|collecting|inactive|expired"):
        store.stage_stock_basic_attempt(
            generation["generation_id"], "SSE:L", attempt["attempt_id"]
        )
    assert store.fetch_attempts(attempt_id=attempt["attempt_id"])[0] == before
    assert raw_path.read_bytes() == raw_before


def test_concurrent_begin_or_resume_creates_only_one_collecting_generation(tmp_path):
    root = tmp_path / "store"
    PITReceiptStore(str(root))

    def begin(_index):
        return PITReceiptStore(str(root)).begin_or_resume_stock_basic_generation(_at())

    with ThreadPoolExecutor(max_workers=8) as executor:
        generations = list(executor.map(begin, range(16)))

    assert {row["generation_id"] for row in generations} == {
        generations[0]["generation_id"]
    }
    assert {row["status"] for row in generations} == {"collecting"}
    assert all(row["staged_partitions"] == [] for row in generations)
