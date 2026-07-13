"""ETF proxy generation manifest / lineage / publish / head / verify.

These tests pin the publish-time freeze of a two-symbol ``fund_daily`` ETF proxy
generation: the deterministic manifest + immutable lineage, the collecting ->
published transition, the scope head, idempotent re-publish, active-head +
rollback detection, and a fail-closed tamper matrix. They deliberately do NOT
exercise coverage, the collector, artifact copy, read frames, or backtest --
only the publish/verify surface.

The publish path re-derives every canonical invariant from the frozen contract
and the persisted attempts (no trust, no drift). Foreign keys stay ON; we never
``PRAGMA foreign_keys=OFF`` and never monkeypatch the store -- corruption is
simulated by direct SQL mutation of already-stored rows, the same way a torn
write would look on disk.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app import research_pit_store
from app.research_pit_store import PITReceiptError, PITReceiptStore
from app.research_proxy_data import (
    ETF_PROXY_REQUIRED_SYMBOLS,
    FUND_DAILY_FIELDS,
    FUND_DAILY_ROW_CAP,
)

# WHY a fixed +08:00 base: the freeze window reasons about elapsed wall-clock
# seconds; a pinned base keeps both shards well inside the 3600s boundary.
BASE_NOW = datetime(2024, 1, 3, 16, 0, 0, tzinfo=timezone(timedelta(hours=8)))
START = "2024-01-02"
END = "2024-03-31"


def _later(seconds: int) -> datetime:
    return BASE_NOW + timedelta(seconds=seconds)


def _row(symbol: str, date: str, close: float = 10.0, pre_close: float = 9.9) -> dict:
    return {
        "ts_code": symbol,
        "trade_date": date,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "pre_close": pre_close,
        "change": close - pre_close,
        "pct_chg": (close - pre_close) / pre_close * 100.0,
        "vol": 1000.0,
        "amount": 10000.0,
    }


def _canonical_semantics(partition_key: str, params: dict) -> dict:
    return {
        "schema_version": "tushare-wire-request/v1",
        "dataset": "fund_daily",
        "partition_key": partition_key,
        "api_name": "fund_daily",
        "method": "POST",
        "receipt_params": dict(params),
        "wire_params": research_pit_store._canonical_wire_params("fund_daily", params),
        "fields": list(FUND_DAILY_FIELDS),
        "row_cap": FUND_DAILY_ROW_CAP,
    }


def _envelope(rows: list[dict]) -> bytes:
    fields = list(FUND_DAILY_FIELDS)
    items = [[row[field] for field in fields] for row in rows]
    return json.dumps(
        {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}
    ).encode("utf-8")


def _record_attempt(
    store: PITReceiptStore,
    *,
    symbol: str,
    start: str = START,
    end: str = END,
    retrieved_at: datetime,
    rows: list[dict],
) -> str:
    params = {"ts_code": symbol, "start_date": start, "end_date": end}
    partition_key = research_pit_store._canonical_partition_key("fund_daily", params)
    attempt = store.record_fetch_attempt(
        dataset="fund_daily",
        partition_key=partition_key,
        endpoint="fund_daily",
        params=params,
        fields=list(FUND_DAILY_FIELDS),
        wire_request_sha256=hashlib.sha256(b"wire").hexdigest(),
        raw_bytes=_envelope(rows),
        http_status=200,
        started_at=retrieved_at,
        retrieved_at=retrieved_at,
        row_cap=FUND_DAILY_ROW_CAP,
        body_complete=True,
        request_semantics=_canonical_semantics(partition_key, params),
    )
    return attempt["attempt_id"]


def _begin(store: PITReceiptStore, *, start: str = START, end: str = END) -> dict:
    return store.begin_or_resume_etf_proxy_generation(BASE_NOW, start, end)


def _stage_symbol(
    store: PITReceiptStore, generation: dict, symbol: str, rows: list[dict] | None = None
) -> str:
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=rows or [_row(symbol, START)]
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], symbol, attempt_id)
    return attempt_id


def _stage_both(
    store: PITReceiptStore, generation: dict
) -> tuple[str, str]:
    """Stage both required symbols (in REVERSE order to prove canonical ordering)."""

    sym_a, sym_b = ETF_PROXY_REQUIRED_SYMBOLS
    attempt_b = _stage_symbol(store, generation, sym_b, [_row(sym_b, START), _row(sym_b, "2024-01-03", close=10.5)])
    attempt_a = _stage_symbol(store, generation, sym_a, [_row(sym_a, START)])
    return attempt_a, attempt_b


def _fresh_store(tmp_path) -> PITReceiptStore:
    return PITReceiptStore(str(tmp_path / "store"))


def _publish_complete(store: PITReceiptStore, *, start: str = START, end: str = END) -> dict:
    generation = _begin(store, start=start, end=end)
    _stage_both(store, generation)
    return store.publish_etf_proxy_generation(generation["generation_id"])


# ---------------------------------------------------------------------------
# Deterministic manifest + lineage
# ---------------------------------------------------------------------------


def test_manifest_and_lineage_are_deterministic_and_distinct(tmp_path):
    store = _fresh_store(tmp_path)
    published = _publish_complete(store)
    generation_id = published["generation_id"]

    with store._connect() as connection:
        first = store._etf_proxy_generation_manifest_on_connection(
            connection, generation_id
        )
        second = store._etf_proxy_generation_manifest_on_connection(
            connection, generation_id
        )

    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["lineage_sha256"] == second["lineage_sha256"]
    assert first["manifest"] == second["manifest"]
    # WHY distinct: lineage binds attempts/events/rows, not just the manifest
    # payload -- a lineage that equals the manifest hash would prove nothing.
    assert first["manifest_sha256"] != first["lineage_sha256"]
    assert published["manifest_sha256"] == first["manifest_sha256"]
    assert published["lineage_sha256"] == first["lineage_sha256"]


def test_manifest_shards_are_in_required_symbol_order(tmp_path):
    store = _fresh_store(tmp_path)
    published = _publish_complete(store)
    # _stage_both stages in reverse order; the manifest must still emit the
    # canonical ETF_PROXY_REQUIRED_SYMBOLS order.
    shards = published["manifest"]["shards"]
    assert [shard["symbol"] for shard in shards] == list(ETF_PROXY_REQUIRED_SYMBOLS)
    assert "symbol_roots" in published["manifest"]
    assert list(published["manifest"]["symbol_roots"].keys()) == list(
        ETF_PROXY_REQUIRED_SYMBOLS
    )


def test_manifest_payload_binds_frozen_scope_and_final_false(tmp_path):
    store = _fresh_store(tmp_path)
    published = _publish_complete(store)
    payload = published["manifest"]
    expected_scope = research_pit_store._etf_proxy_generation_scope_key(START, END)
    expected_contract = research_pit_store._etf_proxy_generation_contract_sha256(START, END)
    assert payload["schema_version"] == research_pit_store.ETF_PROXY_GENERATION_SCHEMA_VERSION
    assert payload["scope_key"] == expected_scope
    assert payload["start_date"] == START
    assert payload["end_date"] == END
    assert payload["contract_sha256"] == expected_contract
    assert payload["final_oos_eligible"] is False
    assert payload["vintage"] == "historical_backfill"
    assert "started_at" in payload
    assert "retrieved_at_min" in payload and "retrieved_at_max" in payload
    assert "rows_root" in payload


# ---------------------------------------------------------------------------
# Publish transition, head, final_oos, no legacy receipt
# ---------------------------------------------------------------------------


def test_publish_transitions_collecting_to_published_with_head(tmp_path):
    store = _fresh_store(tmp_path)
    generation = _begin(store)
    sym_a, sym_b = ETF_PROXY_REQUIRED_SYMBOLS
    _stage_both(store, generation)
    generation_id = generation["generation_id"]

    published = store.publish_etf_proxy_generation(generation_id)

    assert published["status"] == "published"
    assert published["final_oos_eligible"] is False
    assert published["manifest_sha256"] is not None
    assert published["lineage_sha256"] is not None

    with store._connect() as connection:
        row = dict(
            connection.execute(
                "SELECT status, terminal_at, manifest_sha256, lineage_sha256, "
                "final_oos_eligible FROM etf_proxy_generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        )
        max_retrieved = max(
            datetime.fromisoformat(r[0])
            for r in connection.execute(
                "SELECT retrieved_at FROM etf_proxy_generation_shards WHERE generation_id = ?",
                (generation_id,),
            )
        )
        terminal_at = datetime.fromisoformat(row["terminal_at"])
        head = dict(
            connection.execute(
                "SELECT generation_id, manifest_sha256, lineage_sha256 "
                "FROM etf_proxy_generation_head WHERE scope_key = ?",
                (published["scope_key"],),
            ).fetchone()
        )
        legacy_count = connection.execute(
            "SELECT COUNT(*) FROM receipts"
        ).fetchone()[0]

    assert row["status"] == "published"
    # WHY terminal_at == later retrieved_at: publish stamps the freeze instant at
    # the later of the two shards (normalized to UTC), never at wall-clock now.
    assert terminal_at == max_retrieved
    assert row["manifest_sha256"] == published["manifest_sha256"]
    assert row["lineage_sha256"] == published["lineage_sha256"]
    assert row["final_oos_eligible"] == 0
    assert head["generation_id"] == generation_id
    assert head["manifest_sha256"] == published["manifest_sha256"]
    assert head["lineage_sha256"] == published["lineage_sha256"]
    # Publish never writes a legacy fund_daily receipt.
    assert legacy_count == 0


def test_publish_rejects_incomplete_generation(tmp_path):
    store = _fresh_store(tmp_path)
    generation = _begin(store)
    _stage_symbol(store, generation, ETF_PROXY_REQUIRED_SYMBOLS[0])
    with pytest.raises(PITReceiptError, match="incomplete"):
        store.publish_etf_proxy_generation(generation["generation_id"])


def test_publish_rejects_abandoned_generation(tmp_path):
    store = _fresh_store(tmp_path)
    generation = _begin(store)
    _stage_both(store, generation)
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generations SET status = 'abandoned' WHERE generation_id = ?",
            (generation["generation_id"],),
        )
    with pytest.raises(PITReceiptError, match="abandoned"):
        store.publish_etf_proxy_generation(generation["generation_id"])


def test_repeat_publish_is_idempotent_and_deep_verifies(tmp_path):
    store = _fresh_store(tmp_path)
    published = _publish_complete(store)
    generation_id = published["generation_id"]

    republished = store.publish_etf_proxy_generation(generation_id)
    assert republished["manifest_sha256"] == published["manifest_sha256"]
    assert republished["lineage_sha256"] == published["lineage_sha256"]
    # Only one head row, still pointing at this generation.
    with store._connect() as connection:
        heads = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_head WHERE scope_key = ?",
            (published["scope_key"],),
        ).fetchone()[0]
    assert heads == 1


# ---------------------------------------------------------------------------
# Reopen: new generation advances head; old stays verifiable; no rollback
# ---------------------------------------------------------------------------


def test_new_generation_advances_head_and_old_stays_verifiable(tmp_path):
    store = _fresh_store(tmp_path)
    first = _publish_complete(store)
    first_id = first["generation_id"]

    # Reopen a fresh collecting generation for the SAME scope and publish it.
    second_generation = _begin(store)
    assert second_generation["generation_id"] != first_id
    _stage_both(store, second_generation)
    second = store.publish_etf_proxy_generation(second_generation["generation_id"])

    # Head advanced to the newer generation.
    with store._connect() as connection:
        head = dict(
            connection.execute(
                "SELECT generation_id FROM etf_proxy_generation_head WHERE scope_key = ?",
                (first["scope_key"],),
            ).fetchone()
        )
    assert head["generation_id"] == second["generation_id"]

    # The OLD generation is still verifiable by explicit id (head moved on).
    old_verify = store.verify_etf_proxy_generation(first_id)
    assert old_verify["verification_status"] == "passed"
    assert old_verify["generation_id"] == first_id

    # The active range path resolves to the NEW generation.
    active = store.active_etf_proxy_generation(START, END)
    assert active["generation_id"] == second["generation_id"]


def test_republish_old_generation_does_not_roll_back_head(tmp_path):
    store = _fresh_store(tmp_path)
    first = _publish_complete(store)
    second_generation = _begin(store)
    _stage_both(store, second_generation)
    second = store.publish_etf_proxy_generation(second_generation["generation_id"])

    # Re-publish the OLD (already-published) generation -- head must stay on #2.
    store.publish_etf_proxy_generation(first["generation_id"])
    with store._connect() as connection:
        head_id = connection.execute(
            "SELECT generation_id FROM etf_proxy_generation_head WHERE scope_key = ?",
            (first["scope_key"],),
        ).fetchone()[0]
    assert head_id == second["generation_id"]


# ---------------------------------------------------------------------------
# active + verify identifier rules
# ---------------------------------------------------------------------------


def test_active_returns_none_when_no_head(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.active_etf_proxy_generation(START, END) is None


def test_active_and_range_verify_resolve_published_head(tmp_path):
    store = _fresh_store(tmp_path)
    published = _publish_complete(store)

    active = store.active_etf_proxy_generation(START, END)
    assert active["generation_id"] == published["generation_id"]

    verified = store.verify_etf_proxy_generation(start_date=START, end_date=END)
    assert verified["verification_status"] == "passed"
    assert verified["manifest_sha256"] == published["manifest_sha256"]


def test_verify_explicit_id_passes_on_published(tmp_path):
    store = _fresh_store(tmp_path)
    published = _publish_complete(store)
    verified = store.verify_etf_proxy_generation(published["generation_id"])
    assert verified["verification_status"] == "passed"


def test_verify_rejects_non_published_generation(tmp_path):
    store = _fresh_store(tmp_path)
    generation = _begin(store)
    _stage_both(store, generation)
    with pytest.raises(PITReceiptError, match="not published"):
        store.verify_etf_proxy_generation(generation["generation_id"])


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (dict(generation_id="x", start_date=START), "not both"),
        (dict(generation_id="x", end_date=END), "not both"),
        (dict(start_date=START), "both start_date and end_date"),
        (dict(end_date=END), "both start_date and end_date"),
        (dict(), "both start_date and end_date"),
    ],
)
def test_verify_identifier_rule_rejects_bad_args(tmp_path, kwargs, match):
    store = _fresh_store(tmp_path)
    with pytest.raises(PITReceiptError, match=match):
        store.verify_etf_proxy_generation(**kwargs)


def test_range_verify_rejects_when_no_active_head(tmp_path):
    store = _fresh_store(tmp_path)
    with pytest.raises(PITReceiptError, match="missing"):
        store.verify_etf_proxy_generation(start_date=START, end_date=END)


# ---------------------------------------------------------------------------
# Concurrency: same-generation publish is safe + idempotent, one head
# ---------------------------------------------------------------------------


def test_concurrent_same_generation_publish_is_safe_and_idempotent(tmp_path):
    store = _fresh_store(tmp_path)
    generation = _begin(store)
    _stage_both(store, generation)
    generation_id = generation["generation_id"]

    def publish(_index):
        return store.publish_etf_proxy_generation(generation_id)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(publish, range(4)))

    manifest_hashes = {result["manifest_sha256"] for result in results}
    lineage_hashes = {result["lineage_sha256"] for result in results}
    assert len(manifest_hashes) == 1
    assert len(lineage_hashes) == 1
    with store._connect() as connection:
        heads = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_head WHERE scope_key = ?",
            (generation["scope_key"],),
        ).fetchone()[0]
        statuses = [
            r[0]
            for r in connection.execute(
                "SELECT status FROM etf_proxy_generations WHERE generation_id = ?",
                (generation_id,),
            )
        ]
    assert heads == 1
    assert statuses == ["published"]


# ---------------------------------------------------------------------------
# Tamper matrix: every mutation fails closed on publish (idempotent re-publish)
# AND/OR verify. Helpers below mutate the already-published generation.
# ---------------------------------------------------------------------------


def _published_store(tmp_path) -> tuple[PITReceiptStore, dict, dict[str, str]]:
    store = _fresh_store(tmp_path)
    generation = _begin(store)
    attempt_a, attempt_b = _stage_both(store, generation)
    published = store.publish_etf_proxy_generation(generation["generation_id"])
    return store, published, {
        ETF_PROXY_REQUIRED_SYMBOLS[0]: attempt_a,
        ETF_PROXY_REQUIRED_SYMBOLS[1]: attempt_b,
    }


def _shard_attempt_ids(store, generation_id) -> dict[str, str]:
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT symbol, attempt_id FROM etf_proxy_generation_shards WHERE generation_id = ?",
            (generation_id,),
        ).fetchall()
    return {row["symbol"]: row["attempt_id"] for row in rows}


@pytest.mark.parametrize(
    "field",
    ["contract_sha256", "scope_key"],
)
def test_tamper_generation_contract_field_fails_closed(tmp_path, field):
    store, published, _ = _published_store(tmp_path)
    with store._connect() as connection:
        connection.execute(
            f"UPDATE etf_proxy_generations SET {field} = ? WHERE generation_id = ?",
            ("0" * 64, published["generation_id"]),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])
    with pytest.raises(PITReceiptError):
        store.verify_etf_proxy_generation(published["generation_id"])


def test_tamper_generation_vintage_fails_closed(tmp_path):
    store, published, _ = _published_store(tmp_path)
    # Vintage has a CHECK constraint; 'live_forward' is the only other allowed
    # value, so the mutation succeeds but changes the recomputed manifest+lineage.
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generations SET vintage = 'live_forward' WHERE generation_id = ?",
            (published["generation_id"],),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])
    with pytest.raises(PITReceiptError):
        store.verify_etf_proxy_generation(published["generation_id"])


def test_final_oos_is_schema_enforced_fails_closed(tmp_path):
    """final_oos_eligible is pinned to 0 by a CHECK constraint; any attempt to
    flip it fails at the DB layer -- it can never become true."""

    store, published, _ = _published_store(tmp_path)
    with store._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE etf_proxy_generations SET final_oos_eligible = 1 "
                "WHERE generation_id = ?",
                (published["generation_id"],),
            )


def test_tamper_stored_manifest_fails_closed(tmp_path):
    store, published, _ = _published_store(tmp_path)
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generations SET manifest_sha256 = ? WHERE generation_id = ?",
            ("0" * 64, published["generation_id"]),
        )
    with pytest.raises(PITReceiptError, match="manifest"):
        store.publish_etf_proxy_generation(published["generation_id"])
    with pytest.raises(PITReceiptError, match="manifest"):
        store.verify_etf_proxy_generation(published["generation_id"])


def test_tamper_stored_lineage_fails_closed(tmp_path):
    store, published, _ = _published_store(tmp_path)
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generations SET lineage_sha256 = ? WHERE generation_id = ?",
            ("0" * 64, published["generation_id"]),
        )
    with pytest.raises(PITReceiptError, match="lineage"):
        store.publish_etf_proxy_generation(published["generation_id"])
    with pytest.raises(PITReceiptError, match="lineage"):
        store.verify_etf_proxy_generation(published["generation_id"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_sha256", "1" * 64),
        ("lineage_sha256", "1" * 64),
    ],
)
def test_tamper_head_hash_fails_closed_range_verify(tmp_path, field, value):
    store, published, _ = _published_store(tmp_path)
    with store._connect() as connection:
        connection.execute(
            f"UPDATE etf_proxy_generation_head SET {field} = ? WHERE scope_key = ?",
            (value, published["scope_key"]),
        )
    with pytest.raises(PITReceiptError):
        store.verify_etf_proxy_generation(start_date=START, end_date=END)


def test_tamper_head_generation_id_rollback_fails_closed(tmp_path):
    """Rewriting head.generation_id to an older published generation (or a
    non-latest sequence) must surface as a head/rollback mismatch on the range
    path, never a silent restore."""

    store, published, _ = _published_store(tmp_path)
    # Publish a second generation so a real "older" sibling exists.
    second_generation = _begin(store)
    _stage_both(store, second_generation)
    store.publish_etf_proxy_generation(second_generation["generation_id"])
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generation_head SET generation_id = ? WHERE scope_key = ?",
            (published["generation_id"], published["scope_key"]),
        )
    with pytest.raises(PITReceiptError):
        store.verify_etf_proxy_generation(start_date=START, end_date=END)


@pytest.mark.parametrize(
    "field",
    [
        "request_semantics_sha256",
        "retrieved_at",
        "raw_sha256",
        "normalized_sha256",
        "row_count",
    ],
)
def test_tamper_shard_lineage_field_fails_closed(tmp_path, field):
    store, published, _ = _published_store(tmp_path)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    bogus = "2099-01-01T00:00:00+00:00" if field == "retrieved_at" else ("9" * 64)
    value = 999999 if field == "row_count" else bogus
    with store._connect() as connection:
        connection.execute(
            f"UPDATE etf_proxy_generation_shards SET {field} = ? "
            "WHERE generation_id = ? AND symbol = ?",
            (value, published["generation_id"], symbol),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])
    with pytest.raises(PITReceiptError):
        store.verify_etf_proxy_generation(published["generation_id"])


def test_tamper_shard_raw_bytes_fails_closed(tmp_path):
    """A torn write drifting ONLY the shard's raw_bytes (to a fractional value
    that bare int() would truncate) must fail closed -- lossless int validation."""

    store, published, _ = _published_store(tmp_path)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generation_shards SET raw_bytes = ? "
            "WHERE generation_id = ? AND symbol = ?",
            (12345.9, published["generation_id"], symbol),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE fetch_attempts SET http_status = 500 WHERE attempt_id = ?",
        "UPDATE fetch_attempts SET body_complete = 0 WHERE attempt_id = ?",
        "UPDATE fetch_attempts SET error_kind = 'api_error' WHERE attempt_id = ?",
    ],
)
def test_tamper_attempt_promotability_fails_closed(tmp_path, sql):
    store, published, attempts = _published_store(tmp_path)
    with store._connect() as connection:
        connection.execute(sql, (attempts[ETF_PROXY_REQUIRED_SYMBOLS[0]],))
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_attempt_semantics_fails_closed(tmp_path):
    store, published, attempts = _published_store(tmp_path)
    attempt_id = attempts[ETF_PROXY_REQUIRED_SYMBOLS[0]]
    with store._connect() as connection:
        row = dict(
            connection.execute(
                "SELECT request_semantics_json FROM fetch_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        )
        semantics = json.loads(row["request_semantics_json"])
        semantics["method"] = "GET"
        digest = research_pit_store._sha256(semantics)
        connection.execute(
            "UPDATE fetch_attempts SET request_semantics_json = ?, "
            "request_semantics_sha256 = ? WHERE attempt_id = ?",
            (json.dumps(semantics), digest, attempt_id),
        )
        connection.execute(
            "UPDATE etf_proxy_generation_shards SET request_semantics_sha256 = ? "
            "WHERE attempt_id = ?",
            (digest, attempt_id),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_event_status_fails_closed(tmp_path):
    store, published, attempts = _published_store(tmp_path)
    attempt_id = attempts[ETF_PROXY_REQUIRED_SYMBOLS[0]]
    with store._connect() as connection:
        connection.execute(
            "UPDATE fetch_promotion_events SET status = 'transport_error' "
            "WHERE attempt_id = ?",
            (attempt_id,),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_event_details_fails_closed(tmp_path):
    store, published, attempts = _published_store(tmp_path)
    attempt_id = attempts[ETF_PROXY_REQUIRED_SYMBOLS[0]]
    with store._connect() as connection:
        row = dict(
            connection.execute(
                "SELECT details_json FROM fetch_promotion_events WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        )
        details = json.loads(row["details_json"])
        details["extra"] = "tamper"
        connection.execute(
            "UPDATE fetch_promotion_events SET details_json = ? WHERE attempt_id = ?",
            (json.dumps(details), attempt_id),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_event_recorded_at_fails_closed(tmp_path):
    """recorded_at is a lifecycle field excluded from the manifest but bound by
    the lineage -- mutating it changes the recomputed lineage and rejects."""

    store, published, attempts = _published_store(tmp_path)
    attempt_id = attempts[ETF_PROXY_REQUIRED_SYMBOLS[0]]
    with store._connect() as connection:
        connection.execute(
            "UPDATE fetch_promotion_events SET recorded_at = ? WHERE attempt_id = ?",
            ("2099-01-01T00:00:00+00:00", attempt_id),
        )
    with pytest.raises(PITReceiptError, match="lineage"):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_row_value_fails_closed(tmp_path):
    store, published, _ = _published_store(tmp_path)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generation_rows SET close = 999.0 "
            "WHERE generation_id = ? AND ts_code = ?",
            (published["generation_id"], symbol),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_row_deleted_fails_closed(tmp_path):
    store, published, _ = _published_store(tmp_path)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM etf_proxy_generation_rows "
            "WHERE generation_id = ? AND ts_code = ? AND trade_date = ?",
            (published["generation_id"], symbol, START),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_extra_row_fails_closed(tmp_path):
    store, published, _ = _published_store(tmp_path)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    shard = _shard_attempt_ids(store, published["generation_id"])
    del shard
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM etf_proxy_generation_rows "
            "WHERE generation_id = ? AND ts_code = ? AND trade_date = ?",
            (published["generation_id"], symbol, START),
        )
        connection.execute(
            "INSERT INTO etf_proxy_generation_rows ("
            "generation_id, ts_code, trade_date, open, high, low, close, "
            "pre_close, change, pct_chg, vol, amount"
            ") VALUES (?, ?, '2024-02-02', 1.0, 1.1, 0.9, 1.0, 0.9, 0.1, 10.0, 1.0, 10.0)",
            (published["generation_id"], symbol),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_tamper_raw_file_fails_closed(tmp_path):
    store, published, attempts = _published_store(tmp_path)
    attempt_id = attempts[ETF_PROXY_REQUIRED_SYMBOLS[0]]
    with store._connect() as connection:
        raw_path = connection.execute(
            "SELECT raw_path, raw_bytes FROM fetch_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    path = store._safe_raw_path(str(raw_path["raw_path"]))
    # Append one byte: size no longer matches raw_bytes -> CAS mismatch.
    with path.open("ab") as handle:
        handle.write(b"\x00")
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


# ---------------------------------------------------------------------------
# Publish-time head consistency: the head-update path must NOT silently repair a
# torn / rolled-back / spurious head by overwriting it. Publish validates the
# scope head read-only, inside the same BEGIN IMMEDIATE transaction, before any
# head write -- failure rolls back and leaves the corruption exactly as found.
# These are distinct from the range-verify tamper matrix above: they target the
# PUBLISH write path, proving it fails closed rather than masking corruption.
# ---------------------------------------------------------------------------


def _generation_status(store, generation_id) -> str:
    with store._connect() as connection:
        return connection.execute(
            "SELECT status FROM etf_proxy_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()[0]


def _head_row(store, scope_key) -> dict | None:
    with store._connect() as connection:
        row = connection.execute(
            "SELECT generation_id, manifest_sha256, lineage_sha256 "
            "FROM etf_proxy_generation_head WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()
    return None if row is None else dict(row)


def test_publish_rejects_spurious_head_pointing_at_collecting_first_generation(tmp_path):
    """WHY: before any generation is published, the scope head must NOT exist.
    A torn write that drops a head row pointing at the still-collecting first
    generation (with bogus hashes) is inconsistent: publish must fail closed and
    roll back, NOT silently overwrite the bogus head with the frozen hashes.
    Silent repair here would certify a corrupt state as clean."""

    store = _fresh_store(tmp_path)
    generation = _begin(store)
    _stage_both(store, generation)
    # Torn write: head exists before anything published, pointing at the
    # collecting generation with bogus hashes.
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO etf_proxy_generation_head ("
            "scope_key, generation_id, manifest_sha256, lineage_sha256, published_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                generation["scope_key"],
                generation["generation_id"],
                "f" * 64,
                "e" * 64,
                "2099-01-01T00:00:00+00:00",
            ),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(generation["generation_id"])

    # Rollback: the generation stayed collecting, and the bogus head was NOT
    # repaired into a correct one (no silent overwrite).
    assert _generation_status(store, generation["generation_id"]) == "collecting"
    head = _head_row(store, generation["scope_key"])
    assert head is not None
    assert head["manifest_sha256"] == "f" * 64
    assert head["lineage_sha256"] == "e" * 64


def test_publish_rejects_when_head_missing_after_prior_publish(tmp_path):
    """WHY: once a generation has been published, the scope head MUST exist.
    Deleting it (torn write / disk loss) leaves the scope inconsistent:
    publishing a new collecting generation must fail closed rather than silently
    re-inserting the missing head to mask the loss."""

    store = _fresh_store(tmp_path)
    first = _publish_complete(store)  # gen1 published, head present
    second_generation = _begin(store)
    _stage_both(store, second_generation)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM etf_proxy_generation_head WHERE scope_key = ?",
            (first["scope_key"],),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(second_generation["generation_id"])

    assert _generation_status(store, second_generation["generation_id"]) == "collecting"
    assert _head_row(store, first["scope_key"]) is None


@pytest.mark.parametrize("field", ["manifest_sha256", "lineage_sha256"])
def test_publish_rejects_when_head_hash_corrupted_before_new_publish(tmp_path, field):
    """WHY: a head whose manifest/lineage no longer match the latest published
    generation is torn. Publishing a newer collecting generation must NOT
    overwrite the head (which would silently repair the corruption); it must
    fail closed so the torn state stays visible."""

    store, first, _ = _published_store(tmp_path)  # gen1 published, head valid
    second_generation = _begin(store)
    _stage_both(store, second_generation)
    with store._connect() as connection:
        connection.execute(
            f"UPDATE etf_proxy_generation_head SET {field} = ? WHERE scope_key = ?",
            ("d" * 64, first["scope_key"]),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(second_generation["generation_id"])

    assert _generation_status(store, second_generation["generation_id"]) == "collecting"
    head = _head_row(store, first["scope_key"])
    # Head did NOT advance to gen2 (no silent overwrite/repair).
    assert head["generation_id"] == first["generation_id"]


def test_publish_rejects_rolled_back_head_even_with_matching_hashes(tmp_path):
    """WHY: a head rolled back to an older published generation is inconsistent
    EVEN WHEN its manifest/lineage are that older generation's legitimate
    hashes. Publishing a newer collecting generation must surface the rollback,
    not advance the head forward and bury it."""

    store = _fresh_store(tmp_path)
    first = _publish_complete(store)  # gen1 published
    second_generation = _begin(store)
    _stage_both(store, second_generation)
    store.publish_etf_proxy_generation(second_generation["generation_id"])  # head=gen2
    third_generation = _begin(store)
    _stage_both(store, third_generation)
    # Roll head back to gen1 AND copy gen1's legitimate hashes onto it, so the
    # only defect is "points at a non-latest published sequence".
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generation_head SET generation_id = ?, "
            "manifest_sha256 = ?, lineage_sha256 = ? WHERE scope_key = ?",
            (
                first["generation_id"],
                first["manifest_sha256"],
                first["lineage_sha256"],
                first["scope_key"],
            ),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(third_generation["generation_id"])

    assert _generation_status(store, third_generation["generation_id"]) == "collecting"
    head = _head_row(store, first["scope_key"])
    # Head was NOT advanced to gen3; still points at gen1 (rollback not buried).
    assert head["generation_id"] == first["generation_id"]


@pytest.mark.parametrize("field", ["manifest_sha256", "lineage_sha256"])
def test_republish_rejects_when_latest_head_hash_corrupted(tmp_path, field):
    """WHY: republishing the latest published generation is read-only
    idempotent ONLY when the scope head is fully consistent. A tampered head
    hash must reject even on republish -- the published branch must validate
    the head, not just the generation's own hashes."""

    store = _fresh_store(tmp_path)
    published = _publish_complete(store)  # single published gen == latest
    with store._connect() as connection:
        connection.execute(
            f"UPDATE etf_proxy_generation_head SET {field} = ? WHERE scope_key = ?",
            ("c" * 64, published["scope_key"]),
        )
    with pytest.raises(PITReceiptError):
        store.publish_etf_proxy_generation(published["generation_id"])


def test_republish_old_generation_allowed_when_head_points_at_latest(tmp_path):
    """WHY counterpart to the reject case: an explicit OLDER published generation
    may be re-published read-only and idempotently as long as the scope head is
    fully valid and points at the LATEST published generation. The republish
    must not touch or roll back the head."""

    store = _fresh_store(tmp_path)
    first = _publish_complete(store)
    second_generation = _begin(store)
    _stage_both(store, second_generation)
    second = store.publish_etf_proxy_generation(second_generation["generation_id"])
    # head validly points at gen2 (latest); republish the OLD gen1.
    republished = store.publish_etf_proxy_generation(first["generation_id"])
    assert republished["manifest_sha256"] == first["manifest_sha256"]
    assert republished["lineage_sha256"] == first["lineage_sha256"]
    head = _head_row(store, first["scope_key"])
    # Head untouched: still a single row, still on gen2 (no rollback).
    assert head is not None
    assert head["generation_id"] == second["generation_id"]
    assert head["manifest_sha256"] == second["manifest_sha256"]
    assert head["lineage_sha256"] == second["lineage_sha256"]
