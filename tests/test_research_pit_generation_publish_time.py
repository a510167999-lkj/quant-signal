import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.research_pit_store import PITReceiptStore


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
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _record_empty_stock_attempt(store, partition, retrieved_at):
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
    raw_bytes = json.dumps(
        {
            "request_id": f"publish-time-{partition}",
            "code": 0,
            "msg": "",
            "data": {"fields": list(STOCK_FIELDS), "items": []},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    wire_sha256 = hashlib.sha256(
        f"{partition}:{retrieved_at}".encode("utf-8")
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
        raw_bytes=raw_bytes,
        http_status=200,
        started_at=(
            datetime.fromisoformat(retrieved_at) - timedelta(seconds=1)
        ).isoformat(),
        retrieved_at=retrieved_at,
        elapsed_ns=1_000_000_000,
        row_cap=6000,
        body_complete=True,
    )


def test_publish_time_uses_latest_utc_instant_across_timezone_offsets(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_stock_basic_generation(
        "2024-01-02T02:00:00+00:00"
    )
    lexically_later_but_utc_earlier = "2024-01-02T10:00:00+08:00"
    utc_latest = "2024-01-02T03:00:00+00:00"
    assert datetime.fromisoformat(
        lexically_later_but_utc_earlier
    ).astimezone(timezone.utc) < datetime.fromisoformat(utc_latest).astimezone(
        timezone.utc
    )

    retrieved_times = [lexically_later_but_utc_earlier] * 7 + [utc_latest]
    for partition, retrieved_at in zip(PARTITIONS, retrieved_times, strict=True):
        attempt = _record_empty_stock_attempt(store, partition, retrieved_at)
        store.stage_stock_basic_attempt(
            generation["generation_id"], partition, attempt["attempt_id"]
        )

    store.publish_stock_basic_generation(generation["generation_id"])
    with sqlite3.connect(store.database_path) as connection:
        published_at = connection.execute(
            "SELECT published_at FROM stock_basic_generation_head "
            "WHERE generation_id = ?",
            (generation["generation_id"],),
        ).fetchone()[0]

    assert published_at == utc_latest
