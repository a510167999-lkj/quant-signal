"""Atomic per-trade-date market session generations: four-shard evidence contract.

A market session generation binds exactly one ISO trade_date and the four
required datasets (daily, adj_factor, stk_limit, suspend_d). Partial
generations are never published; a fresh partial generation can resume, a
generation whose four shards never land inside the freeze window is abandoned
with evidence retained, and a complete generation may be crash-recovery
published as long as the four shards' retrieved_at span fits the window.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import research_pit_store
from app.research_pit_store import (
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_GENERATION_WINDOW_SECONDS,
    MARKET_SESSION_ROW_CAPS,
    NONEMPTY_MARKET_SESSION_DATASETS,
    PITReceiptError,
    PITReceiptStore,
    _market_session_contract_sha256,
)

TRADE_DATE = "2024-01-02"
BASE = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)

DAILY_FIELDS = [
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
ADJ_FIELDS = ["ts_code", "trade_date", "adj_factor"]
LIMIT_FIELDS = ["trade_date", "ts_code", "pre_close", "up_limit", "down_limit"]
SUSPEND_FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]

DATASET_FIELDS = {
    "daily": DAILY_FIELDS,
    "adj_factor": ADJ_FIELDS,
    "stk_limit": LIMIT_FIELDS,
    "suspend_d": SUSPEND_FIELDS,
}


def _canonical_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dataset_body(dataset, trade_date, rows, *, label="same"):
    return json.dumps(
        {
            "request_id": f"market-{dataset}-{trade_date}-{label}",
            "code": 0,
            "msg": "",
            "data": {"fields": DATASET_FIELDS[dataset], "items": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _default_rows(dataset, trade_date):
    if dataset == "daily":
        return [["600001.SH", trade_date.replace("-", ""), 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]]
    if dataset == "adj_factor":
        return [["600001.SH", trade_date.replace("-", ""), 1.5]]
    if dataset == "stk_limit":
        return [[trade_date.replace("-", ""), "600001.SH", 10.0, 11.0, 9.0]]
    return []  # suspend_d legitimately empty


def _record_market_attempt(
    store,
    dataset,
    trade_date,
    retrieved_at,
    *,
    rows=None,
    label="same",
    row_cap=None,
    trade_date_param=None,
):
    params_trade_date = trade_date_param or trade_date
    params = {"trade_date": params_trade_date}
    semantics = {
        "schema_version": "tushare-wire-request/v1",
        "dataset": dataset,
        "partition_key": trade_date,
        "api_name": dataset,
        "method": "POST",
        "url": "https://api.tushare.pro",
        "wire_params": {"trade_date": params_trade_date.replace("-", "")},
        "receipt_params": {"trade_date": params_trade_date},
        "fields": DATASET_FIELDS[dataset],
        "row_cap": row_cap or MARKET_SESSION_ROW_CAPS[dataset],
    }
    body_rows = _default_rows(dataset, trade_date) if rows is None else rows
    raw = _dataset_body(dataset, trade_date, body_rows, label=label)
    wire_sha256 = hashlib.sha256(
        f"{dataset}:{trade_date}:{retrieved_at}:{label}".encode("utf-8")
    ).hexdigest()
    return store.record_fetch_attempt(
        dataset=dataset,
        partition_key=trade_date,
        endpoint=dataset,
        params=params,
        fields=DATASET_FIELDS[dataset],
        wire_request_sha256=wire_sha256,
        request_body_sha256=wire_sha256,
        request_semantics=semantics,
        request_semantics_sha256=_canonical_sha256(semantics),
        raw_bytes=raw,
        http_status=200,
        started_at=(datetime.fromisoformat(retrieved_at) - timedelta(seconds=1)).isoformat(),
        retrieved_at=retrieved_at,
        elapsed_ns=1_000_000_000,
        row_cap=row_cap or MARKET_SESSION_ROW_CAPS[dataset],
        body_complete=True,
    )


def _stage_four(store, generation_id, trade_date, started, *, suspend_rows=None):
    staged = {}
    for index, dataset in enumerate(MARKET_SESSION_DATASETS, 1):
        rows = suspend_rows if dataset == "suspend_d" and suspend_rows is not None else None
        attempt = _record_market_attempt(
            store,
            dataset,
            trade_date,
            (started + timedelta(seconds=index)).isoformat(),
            rows=rows,
        )
        store.stage_market_session_attempt(generation_id, dataset, attempt["attempt_id"])
        staged[dataset] = attempt
    return staged


def test_begin_returns_fresh_generation_bound_to_one_trade_date(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)

    assert generation["status"] == "collecting"
    assert generation["trade_date"] == TRADE_DATE
    assert generation["contract_sha256"] == _market_session_contract_sha256()
    assert generation["staged_datasets"] == []
    assert generation["final_oos_eligible"] is False


def test_fresh_partial_generation_resumes_and_keeps_staged_evidence(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    attempt = _record_market_attempt(
        store, "daily", TRADE_DATE, (BASE + timedelta(seconds=60)).isoformat()
    )
    store.stage_market_session_attempt(first["generation_id"], "daily", attempt["attempt_id"])

    resumed = store.begin_or_resume_market_session_generation(
        BASE + timedelta(seconds=120), TRADE_DATE
    )
    assert resumed["generation_id"] == first["generation_id"]
    assert resumed["staged_datasets"] == ["daily"]


def test_stale_incomplete_generation_is_abandoned_with_evidence_retained(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    stale = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    attempt = _record_market_attempt(
        store, "daily", TRADE_DATE, (BASE + timedelta(seconds=60)).isoformat()
    )
    store.stage_market_session_attempt(stale["generation_id"], "daily", attempt["attempt_id"])

    beyond_window = BASE + timedelta(seconds=MARKET_SESSION_GENERATION_WINDOW_SECONDS + 1)
    fresh = store.begin_or_resume_market_session_generation(beyond_window, TRADE_DATE)
    assert fresh["generation_id"] != stale["generation_id"]

    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT status, terminal_reason FROM market_session_generations "
            "WHERE generation_id = ?",
            (stale["generation_id"],),
        ).fetchone()
    assert row[0] == "abandoned"
    assert row[1] == "stale_incomplete"


def test_staging_window_boundary_rejects_retrieved_beyond_freeze_window(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    boundary = (BASE + timedelta(seconds=MARKET_SESSION_GENERATION_WINDOW_SECONDS)).isoformat()
    ok = _record_market_attempt(store, "daily", TRADE_DATE, boundary)
    store.stage_market_session_attempt(generation["generation_id"], "daily", ok["attempt_id"])

    beyond = (BASE + timedelta(seconds=MARKET_SESSION_GENERATION_WINDOW_SECONDS + 1)).isoformat()
    late = _record_market_attempt(store, "adj_factor", TRADE_DATE, beyond)
    with pytest.raises(PITReceiptError, match="staging window"):
        store.stage_market_session_attempt(
            generation["generation_id"], "adj_factor", late["attempt_id"]
        )


def test_four_shards_publish_atomically_with_manifest_and_lineage(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    _stage_four(store, generation["generation_id"], TRADE_DATE, BASE)

    published = store.publish_market_session_generation(generation["generation_id"])
    assert published["status"] == "published"
    assert set(published["staged_datasets"]) == set(MARKET_SESSION_DATASETS)
    manifest = published["manifest"]
    assert manifest["schema_version"].startswith("market-session-generations/")
    assert manifest["trade_date"] == TRADE_DATE
    assert manifest["final_oos_eligible"] is False
    assert published["manifest_sha256"] == _canonical_sha256(manifest)
    assert {shard["dataset"] for shard in manifest["shards"]} == set(MARKET_SESSION_DATASETS)
    assert published["lineage_sha256"]
    active = store.active_market_session_generation(TRADE_DATE)
    assert active["generation_id"] == generation["generation_id"]


def test_empty_suspend_response_is_still_a_required_shard(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    _stage_four(store, generation["generation_id"], TRADE_DATE, BASE)
    published = store.publish_market_session_generation(generation["generation_id"])
    suspend_shard = next(
        shard for shard in published["manifest"]["shards"] if shard["dataset"] == "suspend_d"
    )
    assert suspend_shard["row_count"] == 0
    assert suspend_shard["raw_sha256"]


def test_publish_rejects_generation_missing_a_shard(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    for dataset in ("daily", "adj_factor", "stk_limit"):
        attempt = _record_market_attempt(
            store, dataset, TRADE_DATE, (BASE + timedelta(seconds=30)).isoformat()
        )
        store.stage_market_session_attempt(generation["generation_id"], dataset, attempt["attempt_id"])
    with pytest.raises(PITReceiptError, match="incomplete"):
        store.publish_market_session_generation(generation["generation_id"])


def test_cross_date_attempt_cannot_attach_to_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    wrong_date = "2024-01-03"
    attempt = _record_market_attempt(
        store, "daily", TRADE_DATE, (BASE + timedelta(seconds=30)).isoformat()
    )
    # Tamper partition_key so the attempt belongs to a different trade_date.
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE fetch_attempts SET partition_key = ? WHERE attempt_id = ?",
            (wrong_date, attempt["attempt_id"]),
        )
    with pytest.raises(PITReceiptError):
        store.stage_market_session_attempt(
            generation["generation_id"], "daily", attempt["attempt_id"]
        )


def test_same_trade_date_new_generation_preserves_old_published_evidence(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    _stage_four(store, first["generation_id"], TRADE_DATE, BASE)
    store.publish_market_session_generation(first["generation_id"])

    second = store.begin_or_resume_market_session_generation(
        BASE + timedelta(seconds=600), TRADE_DATE, force_new=True
    )
    assert second["generation_id"] != first["generation_id"]
    _stage_four(
        store,
        second["generation_id"],
        TRADE_DATE,
        BASE + timedelta(seconds=600),
        suspend_rows=[["600002.SH", "20240102", None, "S"]],
    )
    store.publish_market_session_generation(second["generation_id"])

    active = store.active_market_session_generation(TRADE_DATE)
    assert active["generation_id"] == second["generation_id"]
    # Old generation evidence is retained and still verifiable on its own.
    first_verified = store.verify_market_session_generation(first["generation_id"])
    assert first_verified["status"] == "published"
    assert first_verified["manifest_sha256"] != active["manifest_sha256"]


def test_active_verify_detects_head_manifest_tamper(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    _stage_four(store, generation["generation_id"], TRADE_DATE, BASE)
    store.publish_market_session_generation(generation["generation_id"])

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generation_head SET manifest_sha256 = ? WHERE trade_date = ?",
            ("0" * 64, TRADE_DATE),
        )
    with pytest.raises(PITReceiptError, match="manifest"):
        store.verify_market_session_generation(trade_date=TRADE_DATE)


def test_active_verify_detects_head_rollback_to_older_published_sequence(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    first = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    _stage_four(store, first["generation_id"], TRADE_DATE, BASE)
    store.publish_market_session_generation(first["generation_id"])
    second = store.begin_or_resume_market_session_generation(
        BASE + timedelta(seconds=600), TRADE_DATE, force_new=True
    )
    _stage_four(
        store, second["generation_id"], TRADE_DATE, BASE + timedelta(seconds=600)
    )
    store.publish_market_session_generation(second["generation_id"])

    # Roll the head back to the older published generation. Keep the head
    # manifest consistent with the rolled-back generation so the rollback
    # detector (sequence != latest published) is the failure that fires.
    with sqlite3.connect(store.database_path) as connection:
        first_manifest = connection.execute(
            "SELECT manifest_sha256 FROM market_session_generations WHERE generation_id = ?",
            (first["generation_id"],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_session_generation_head SET generation_id = ?, manifest_sha256 = ? "
            "WHERE trade_date = ?",
            (first["generation_id"], first_manifest, TRADE_DATE),
        )
    with pytest.raises(PITReceiptError, match="rollback"):
        store.verify_market_session_generation(trade_date=TRADE_DATE)


def test_post_fourth_shard_crash_recovery_publish_outside_window(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    # All four shards land inside the freeze window.
    _stage_four(store, generation["generation_id"], TRADE_DATE, BASE)

    # Process restarts long after the window; publish must still succeed because
    # the four shards' retrieved_at span fits the window.
    published = store.publish_market_session_generation(generation["generation_id"])
    assert published["status"] == "published"


def test_publish_rejects_shards_whose_retrieved_span_exceeds_window(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    _stage_four(store, generation["generation_id"], TRADE_DATE, BASE)
    # Stretch one shard's retrieved_at past the freeze window. Both the shard
    # row and its underlying attempt are rewritten consistently so the manifest
    # lineage stays intact and the freeze-window span check is what fires.
    late = (BASE + timedelta(seconds=MARKET_SESSION_GENERATION_WINDOW_SECONDS + 5)).isoformat()
    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT attempt_id FROM market_session_generation_shards "
            "WHERE generation_id = ? AND dataset = 'adj_factor'",
            (generation["generation_id"],),
        ).fetchone()
        attempt_id = row[0]
        connection.execute(
            "UPDATE market_session_generation_shards SET retrieved_at = ? "
            "WHERE generation_id = ? AND dataset = 'adj_factor'",
            (late, generation["generation_id"]),
        )
        connection.execute(
            "UPDATE fetch_attempts SET retrieved_at = ? WHERE attempt_id = ?",
            (late, attempt_id),
        )
    with pytest.raises(PITReceiptError, match="window"):
        store.publish_market_session_generation(generation["generation_id"])


def test_only_one_collecting_generation_per_trade_date(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    with sqlite3.connect(store.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO market_session_generations "
                "(generation_sequence, generation_id, trade_date, contract_sha256, "
                " status, started_at, final_oos_eligible) "
                "VALUES (?, ?, ?, ?, 'collecting', ?, 0)",
                (
                    999,
                    "market-session-rogue",
                    TRADE_DATE,
                    _market_session_contract_sha256(),
                    BASE.isoformat(),
                ),
            )


def test_unknown_dataset_dispatch_fails_closed_at_every_layer(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with store._connect() as connection:
        with pytest.raises(PITReceiptError, match="unsupported market dataset"):
            store._insert_market_session_rows(connection, "g1", "unknown", [])
        with pytest.raises(PITReceiptError, match="unsupported market dataset"):
            store._market_session_rows_from_db(connection, "g1", "unknown")


def test_empty_non_suspend_dataset_cannot_be_staged(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    attempt = _record_market_attempt(
        store,
        "daily",
        TRADE_DATE,
        (BASE + timedelta(seconds=30)).isoformat(),
        rows=[],
    )
    with pytest.raises(PITReceiptError, match="empty"):
        store.stage_market_session_attempt(
            generation["generation_id"], "daily", attempt["attempt_id"]
        )


def test_row_cap_reaching_cap_marks_attempt_non_promotable(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    attempt = _record_market_attempt(
        store,
        "suspend_d",
        TRADE_DATE,
        (BASE + timedelta(seconds=30)).isoformat(),
        rows=[["600001.SH", "20240102", None, "S"]],
        row_cap=1,  # exactly equal to row count -> at cap, not promotable
    )
    with pytest.raises(PITReceiptError, match="row cap"):
        store.stage_market_session_attempt(
            generation["generation_id"], "suspend_d", attempt["attempt_id"]
        )


def test_nonempty_dataset_list_is_canonical():
    assert set(NONEMPTY_MARKET_SESSION_DATASETS) == {"daily", "adj_factor", "stk_limit"}


# --- Lineage stability / tamper detection (P1) ---------------------------------
#
# WHY: the generation lineage hash must be a function of immutable generation
# semantics only. If it drifts between publish (computed while collecting) and
# verify (recomputed once published), the stored receipt no longer proves what
# was frozen, and tampering with the persisted lineage goes undetected.


def _published_four_shard_store(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    _stage_four(store, generation["generation_id"], TRADE_DATE, BASE)
    published = store.publish_market_session_generation(generation["generation_id"])
    return store, generation, published


@pytest.mark.parametrize("tamper_old", [False, True])
def test_legacy_migration_upgrades_market_history_without_moving_head(
    tmp_path, tamper_old
):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    generation_ids = []
    for generation_index in range(2):
        started = BASE + timedelta(minutes=generation_index * 10)
        generation = store.begin_or_resume_market_session_generation(
            started, TRADE_DATE
        )
        generation_ids.append(generation["generation_id"])
        _stage_four(store, generation["generation_id"], TRADE_DATE, started)
        store.publish_market_session_generation(generation["generation_id"])

    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        for generation_id in generation_ids:
            verified = store._market_session_manifest_on_connection(
                connection, generation_id
            )
            payload = dict(verified["manifest"])
            payload.pop("expected_request_semantics", None)
            payload.pop("expected_request_semantics_sha256", None)
            legacy_manifest = research_pit_store._sha256(payload)
            legacy_lineage = store._market_session_lineage_sha256_on_connection(
                connection, generation_id, legacy=True
            )
            connection.execute(
                "UPDATE market_session_generations SET manifest_sha256 = ?, "
                "lineage_sha256 = ?, expected_request_semantics_json = NULL, "
                "expected_request_semantics_sha256 = NULL WHERE generation_id = ?",
                (legacy_manifest, legacy_lineage, generation_id),
            )
            if generation_id == generation_ids[1]:
                connection.execute(
                    "UPDATE market_session_generation_head SET manifest_sha256 = ?",
                    (legacy_manifest,),
                )
        if tamper_old:
            connection.execute(
                "UPDATE market_session_generations SET lineage_sha256 = ? "
                "WHERE generation_id = ?",
                ("0" * 64, generation_ids[0]),
            )

    if tamper_old:
        with pytest.raises(PITReceiptError, match="legacy.*identity mismatch"):
            PITReceiptStore(str(root))
        return

    reopened = PITReceiptStore(str(root))
    assert reopened.active_market_session_generation(TRADE_DATE)[
        "generation_id"
    ] == generation_ids[1]
    assert all(
        reopened.verify_market_session_generation(generation_id)[
            "verification_status"
        ]
        == "passed"
        for generation_id in generation_ids
    )


def test_lineage_is_identical_across_persistence_publish_explicit_and_active_verify(tmp_path):
    store, generation, published = _published_four_shard_store(tmp_path)

    # The lineage persisted in the generation row at publish time.
    with sqlite3.connect(store.database_path) as connection:
        persisted_lineage = connection.execute(
            "SELECT lineage_sha256 FROM market_session_generations WHERE generation_id = ?",
            (generation["generation_id"],),
        ).fetchone()[0]

    publish_lineage = published["lineage_sha256"]
    explicit_lineage = store.verify_market_session_generation(
        generation["generation_id"]
    )["lineage_sha256"]
    active_lineage = store.verify_market_session_generation(trade_date=TRADE_DATE)[
        "lineage_sha256"
    ]

    # All four must be byte-identical: the receipt proves the same frozen state
    # regardless of which code path recomputes it.
    assert persisted_lineage == publish_lineage == explicit_lineage == active_lineage
    assert persisted_lineage  # never empty


def test_manifest_reuses_verified_market_rows_for_byte_identical_lineage(tmp_path):
    store, generation, published = _published_four_shard_store(tmp_path)
    statements = []

    with store._connect() as connection:
        connection.execute("BEGIN")
        connection.set_trace_callback(statements.append)
        verified = store._market_session_manifest_on_connection(
            connection, generation["generation_id"]
        )
        connection.set_trace_callback(None)
        reference_lineage = store._market_session_lineage_sha256_on_connection(
            connection, generation["generation_id"]
        )

    assert verified["manifest_sha256"] == published["manifest_sha256"]
    assert (
        verified["lineage_sha256"]
        == reference_lineage
        == published["lineage_sha256"]
    )
    for dataset in MARKET_SESSION_DATASETS:
        table = f"market_session_generation_rows_{dataset}"
        materializations = [
            statement
            for statement in statements
            if table in statement and "GROUP BY" not in statement
        ]
        assert len(materializations) == 1, (
            f"{dataset} verified rows were materialized more than once: "
            f"{materializations}"
        )


def test_explicit_verify_detects_persisted_lineage_tamper(tmp_path):
    store, generation, _published = _published_four_shard_store(tmp_path)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generations SET lineage_sha256 = ? WHERE generation_id = ?",
            ("0" * 64, generation["generation_id"]),
        )

    # Tampering the persisted lineage must fail closed on explicit verify.
    with pytest.raises(PITReceiptError, match="lineage"):
        store.verify_market_session_generation(generation["generation_id"])


def test_active_verify_detects_persisted_lineage_tamper(tmp_path):
    store, generation, _published = _published_four_shard_store(tmp_path)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generations SET lineage_sha256 = ? WHERE generation_id = ?",
            ("0" * 64, generation["generation_id"]),
        )

    # Tampering the persisted lineage must fail closed on active/default verify.
    with pytest.raises(PITReceiptError, match="lineage"):
        store.verify_market_session_generation(trade_date=TRADE_DATE)


# --- suspend_d identity key (P1) -----------------------------------------------
#
# WHY: two legitimate suspension events for the same security on the same
# trade_date with the same suspend_type but different suspend_timing (e.g. an
# AM and PM session suspension) are distinct events. They must both persist and
# both contribute to a stable lineage, instead of colliding on a too-narrow key.


def test_suspend_d_distinct_timings_same_type_coexist_and_verify(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    suspend_rows = [
        ["600001.SH", "20240102", "上午", "S"],
        ["600001.SH", "20240102", "下午", "S"],
    ]
    _stage_four(
        store, generation["generation_id"], TRADE_DATE, BASE, suspend_rows=suspend_rows
    )
    published = store.publish_market_session_generation(generation["generation_id"])
    suspend_shard = next(
        shard for shard in published["manifest"]["shards"] if shard["dataset"] == "suspend_d"
    )
    assert suspend_shard["row_count"] == 2

    # Both timings are retained and exactly verifiable, with a stable lineage.
    verified = store.verify_market_session_generation(generation["generation_id"])
    assert verified["lineage_sha256"] == published["lineage_sha256"]
    with sqlite3.connect(store.database_path) as connection:
        timings = sorted(
            row[0]
            for row in connection.execute(
                "SELECT suspend_timing FROM market_session_generation_rows_suspend_d "
                "WHERE generation_id = ?",
                (generation["generation_id"],),
            )
        )
    assert timings == ["上午", "下午"]


# --- vintage enum (P1) ---------------------------------------------------------
#
# WHY: vintage classifies how a generation was produced and feeds the lineage.
# An unbounded string would let any value become part of an immutable receipt.
# It must be a frozen enum with the begin API and collector sharing one default.


def test_vintage_defaults_to_live_forward_and_rejects_unknown_values(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))

    live = store.begin_or_resume_market_session_generation(BASE, TRADE_DATE)
    assert live["vintage"] == "live_forward"
    assert live["final_oos_eligible"] is False  # vintage must not flip OOS eligibility

    backfill = store.begin_or_resume_market_session_generation(
        BASE, "2024-01-03", vintage="historical_backfill"
    )
    assert backfill["vintage"] == "historical_backfill"
    assert backfill["final_oos_eligible"] is False

    # The legacy free-form value "live" is no longer accepted.
    with pytest.raises(PITReceiptError, match="vintage"):
        store.begin_or_resume_market_session_generation(BASE, "2024-01-04", vintage="live")
