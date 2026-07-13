import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.research_pit_store import (
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_ROW_CAPS,
    NORMALIZED_FIELDS,
    OFFICIAL_ROW_CAPS,
    PITReceiptError,
    PITReceiptStore,
)


TARGET = "2016-10-10"
ANCHOR = "2016-09-30"
FUTURE = "2016-10-11"
ROLE = "development"
CONTRACT = "a" * 64
BASE = datetime(2016, 10, 10, 8, tzinfo=timezone.utc)


def _sha(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _body(dataset, rows):
    return json.dumps(
        {
            "code": 0,
            "msg": "",
            "data": {"fields": list(NORMALIZED_FIELDS[dataset]), "items": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _semantics(
    dataset,
    trade_date,
    *,
    role=ROLE,
    contract=CONTRACT,
    source_profile="fixture-source",
    url=None,
):
    return {
        "schema_version": "tushare-wire-request/v1",
        "source_profile": source_profile,
        "request_protocol": "tushare-path-per-interface/v1",
        "network_route": "direct_https",
        "proxy_endpoint": None,
        "dataset": dataset,
        "partition_key": trade_date,
        "api_name": dataset,
        "method": "POST",
        "url": url or f"https://data.example.test/{dataset}",
        "wire_params": {"trade_date": trade_date.replace("-", "")},
        "receipt_params": {"trade_date": trade_date},
        "fields": list(NORMALIZED_FIELDS[dataset]),
        "row_cap": (
            MARKET_SESSION_ROW_CAPS[dataset]
            if dataset in MARKET_SESSION_ROW_CAPS
            else OFFICIAL_ROW_CAPS[dataset]
        ),
        "temporal_role": role,
        "temporal_contract_sha256": contract,
    }


def _record(
    store,
    dataset,
    trade_date,
    rows,
    *,
    sequence=0,
    role=ROLE,
    contract=CONTRACT,
    source_profile="fixture-source",
    raw=None,
    error_kind=None,
    promote=True,
    url=None,
    error_message=None,
):
    semantics = _semantics(
        dataset,
        trade_date,
        role=role,
        contract=contract,
        source_profile=source_profile,
        url=url,
    )
    retrieved = BASE + timedelta(seconds=sequence)
    body = _body(dataset, rows) if raw is None else raw
    wire = hashlib.sha256(
        f"{dataset}:{trade_date}:{sequence}:{source_profile}".encode()
    ).hexdigest()
    attempt = store.record_fetch_attempt(
        dataset=dataset,
        partition_key=trade_date,
        endpoint=dataset,
        params={"trade_date": trade_date},
        fields=NORMALIZED_FIELDS[dataset],
        wire_request_sha256=wire,
        request_body_sha256=wire,
        request_semantics=semantics,
        request_semantics_sha256=_sha(semantics),
        raw_bytes=body,
        http_status=200,
        started_at=(retrieved - timedelta(seconds=1)).isoformat(),
        retrieved_at=retrieved.isoformat(),
        elapsed_ns=1_000_000_000,
        row_cap=semantics["row_cap"],
        body_complete=True,
        error_kind=error_kind,
        error_message=error_message,
    )
    event = store.promote_fetch_attempt(attempt["attempt_id"]) if promote else None
    return attempt, event


def _snapshot_rows(trade_date, codes=("600001.SH",)):
    return [
        [
            trade_date.replace("-", ""),
            code,
            f"Name-{code[:6]}",
            "Industry",
            "20000101",
        ]
        for code in codes
    ]


def _market_rows(dataset, trade_date):
    compact = trade_date.replace("-", "")
    codes = ("600001.SH", "600002.SH")
    if dataset == "daily":
        return [
            [code, compact, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100] for code in codes
        ]
    if dataset == "adj_factor":
        return [[code, compact, 1.5] for code in codes]
    if dataset == "stk_limit":
        return [[compact, code, 10.0, 11.0, 9.0] for code in codes]
    return []


def _publish_market(store, trade_date=TARGET, *, sequence=10, force_new=False):
    generation = store.begin_or_resume_market_session_generation(
        BASE + timedelta(seconds=sequence - 1),
        trade_date,
        vintage="historical_backfill",
        force_new=force_new,
    )
    for offset, dataset in enumerate(MARKET_SESSION_DATASETS):
        attempt, _event = _record(
            store,
            dataset,
            trade_date,
            _market_rows(dataset, trade_date),
            sequence=sequence + offset,
            promote=False,
        )
        store.stage_market_session_attempt(
            generation["generation_id"], dataset, attempt["attempt_id"]
        )
    return store.publish_market_session_generation(generation["generation_id"])


def _fixture(tmp_path, *, with_anchor=True):
    store = PITReceiptStore(str(tmp_path / "store"))
    if with_anchor:
        _attempt, event = _record(
            store,
            "bak_basic",
            ANCHOR,
            _snapshot_rows(ANCHOR),
            sequence=1,
        )
        assert event["status"] == "stored"
    empty_attempt, empty_event = _record(store, "bak_basic", TARGET, [], sequence=2)
    assert empty_event["status"] == "invalid_json"
    market = _publish_market(store)
    return store, empty_attempt, market


def _begin(store, *, now=BASE + timedelta(minutes=5), force_new=False):
    return store.begin_or_resume_membership_generation(
        now,
        TARGET,
        temporal_role=ROLE,
        temporal_contract_sha256=CONTRACT,
        force_new=force_new,
    )


def _publish_membership_fixture(tmp_path, *, with_anchor=True):
    store, empty, market = _fixture(tmp_path, with_anchor=with_anchor)
    generation = _begin(store)
    staged = store.stage_membership_generation(generation["generation_id"])
    published = store.publish_membership_generation(generation["generation_id"])
    return store, empty, market, staged, published


def test_membership_schema_has_four_foreign_keyed_tables(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))

    with sqlite3.connect(store.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "membership_session_generations",
            "membership_generation_rows",
            "membership_generation_evidence",
            "membership_session_head",
        } <= tables
        assert connection.execute("PRAGMA foreign_key_list(membership_generation_rows)").fetchall()
        assert connection.execute(
            "PRAGMA foreign_key_list(membership_generation_evidence)"
        ).fetchall()
        assert connection.execute("PRAGMA foreign_key_list(membership_session_head)").fetchall()


def test_membership_schema_rejects_invalid_boolean_and_evidence_status(tmp_path):
    store, _empty, _market = _fixture(tmp_path)
    generation = _begin(store)
    store.stage_membership_generation(generation["generation_id"])

    with sqlite3.connect(store.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE membership_generation_rows SET signal_eligible=1 WHERE generation_id=?",
                (generation["generation_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE membership_generation_evidence SET terminal_status='stored' "
                "WHERE generation_id=? AND evidence_kind='target_semantic_empty'",
                (generation["generation_id"],),
            )


def test_begin_membership_generation_requires_published_market_authority(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))

    with pytest.raises(PITReceiptError, match="active market generation is missing"):
        store.begin_or_resume_membership_generation(
            datetime(2016, 10, 10, tzinfo=timezone.utc),
            "2016-10-10",
            temporal_role="development",
            temporal_contract_sha256="a" * 64,
        )


def test_begin_resume_force_and_concurrent_uniqueness(tmp_path):
    store, _empty, market = _fixture(tmp_path)

    first = _begin(store)
    resumed = _begin(store, now=BASE + timedelta(minutes=6))
    assert resumed["generation_id"] == first["generation_id"]
    assert first["market_generation_id"] == market["generation_id"]
    assert first["source_kind"] is None
    assert first["signal_session_eligible"] is False

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: _begin(store)["generation_id"], range(2)))
    assert ids == [first["generation_id"], first["generation_id"]]

    replacement = _begin(store, now=BASE + timedelta(minutes=7), force_new=True)
    assert replacement["generation_id"] != first["generation_id"]
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT status FROM membership_session_generations WHERE generation_id=?",
                (first["generation_id"],),
            ).fetchone()[0]
            == "abandoned"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM membership_session_generations "
                "WHERE trade_date=? AND status='collecting'",
                (TARGET,),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("tamper", ["policy", "sequence", "generation_id"])
def test_stage_rejects_membership_generation_identity_tampering(tmp_path, tamper):
    store, _empty, _market = _fixture(tmp_path / tamper)
    generation = _begin(store)
    with sqlite3.connect(store.database_path) as connection:
        if tamper == "policy":
            connection.execute(
                "UPDATE membership_session_generations SET policy_version=? WHERE generation_id=?",
                ("tampered-policy/v9", generation["generation_id"]),
            )
        elif tamper == "sequence":
            connection.execute(
                "UPDATE membership_session_generations SET generation_sequence=? "
                "WHERE generation_id=?",
                (99, generation["generation_id"]),
            )
        else:
            connection.execute(
                "UPDATE membership_session_generations SET generation_id=? WHERE generation_id=?",
                ("membership-session-00000000000000000099", generation["generation_id"]),
            )

    with pytest.raises(PITReceiptError, match="identity|policy|sequence|does not exist"):
        store.stage_membership_generation(generation["generation_id"])

    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT status, manifest_json FROM membership_session_generations WHERE trade_date=?",
            (TARGET,),
        ).fetchone()
        assert row is not None
        assert row[0] == "collecting"
        assert row[1] is None


@pytest.mark.parametrize("field", ["terminal_at", "manifest_sha256"])
def test_stage_rejects_prepopulated_unstaged_membership_fields(tmp_path, field):
    store, _empty, _market = _fixture(tmp_path / field)
    generation = _begin(store)
    value = BASE.isoformat() if field == "terminal_at" else "f" * 64
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            f"UPDATE membership_session_generations SET {field}=? WHERE generation_id=?",
            (value, generation["generation_id"]),
        )

    with pytest.raises(PITReceiptError, match="pristine|identity"):
        store.stage_membership_generation(generation["generation_id"])


def test_begin_resume_rejects_collecting_identity_tampering(tmp_path):
    store, _empty, _market = _fixture(tmp_path)
    generation = _begin(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE membership_session_generations SET policy_version=? WHERE generation_id=?",
            ("tampered-policy/v9", generation["generation_id"]),
        )

    with pytest.raises(PITReceiptError, match="policy|identity"):
        _begin(store, now=BASE + timedelta(minutes=6))


def test_stage_rejects_tampered_published_head_before_reuse(tmp_path):
    store, _empty, _market, _staged, published = _publish_membership_fixture(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE membership_session_head SET manifest_sha256=? WHERE trade_date=?",
            ("f" * 64, TARGET),
        )

    with pytest.raises(PITReceiptError, match="head|manifest|lineage"):
        store.stage_membership_generation(published["generation_id"])


@pytest.mark.parametrize("field", ["terminal_at", "terminal_reason"])
def test_publish_rejects_prepopulated_staged_lifecycle_fields(tmp_path, field):
    store, _empty, _market = _fixture(tmp_path)
    staged = store.stage_membership_generation(_begin(store)["generation_id"])
    with sqlite3.connect(store.database_path) as connection:
        value = BASE.isoformat() if field == "terminal_at" else "poison"
        connection.execute(
            f"UPDATE membership_session_generations SET {field}=? WHERE generation_id=?",
            (value, staged["generation_id"]),
        )

    with pytest.raises(PITReceiptError, match="lifecycle|terminal|identity"):
        store.publish_membership_generation(staged["generation_id"])


@pytest.mark.parametrize("action", ["stage", "publish", "verify"])
def test_published_terminal_reason_tampering_rejected_by_all_entries(tmp_path, action):
    store, _empty, _market, _staged, published = _publish_membership_fixture(tmp_path / action)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE membership_session_generations SET terminal_reason='poison' "
            "WHERE generation_id=?",
            (published["generation_id"],),
        )

    with pytest.raises(PITReceiptError, match="lifecycle|terminal|identity"):
        if action == "stage":
            store.stage_membership_generation(published["generation_id"])
        elif action == "publish":
            store.publish_membership_generation(published["generation_id"])
        else:
            store.verify_membership_generation(TARGET)


@pytest.mark.parametrize("tamper", ["row", "manifest", "evidence"])
def test_begin_resume_rejects_staged_membership_tampering(tmp_path, tamper):
    store, empty, _market = _fixture(tmp_path / tamper)
    generation = _begin(store)
    staged = store.stage_membership_generation(generation["generation_id"])
    with sqlite3.connect(store.database_path) as connection:
        if tamper == "row":
            connection.execute(
                "UPDATE membership_generation_rows SET name='Poison' WHERE generation_id=?",
                (staged["generation_id"],),
            )
        elif tamper == "manifest":
            connection.execute(
                "UPDATE membership_session_generations SET manifest_sha256=? WHERE generation_id=?",
                ("f" * 64, staged["generation_id"]),
            )
        else:
            connection.execute(
                "UPDATE fetch_attempts SET raw_sha256=? WHERE attempt_id=?",
                ("f" * 64, empty["attempt_id"]),
            )

    with pytest.raises(PITReceiptError, match="immutable|lineage|manifest|raw"):
        _begin(store, now=BASE + timedelta(minutes=6))


@pytest.mark.parametrize("force_new", [False, True])
def test_begin_replaces_staged_generation_after_market_head_change(tmp_path, force_new):
    store, _empty, _market = _fixture(tmp_path)
    original = _begin(store)
    store.stage_membership_generation(original["generation_id"])
    replacement_market = _publish_market(store, sequence=40, force_new=True)

    replacement = _begin(
        store,
        now=BASE + timedelta(minutes=10),
        force_new=force_new,
    )
    assert replacement["generation_id"] != original["generation_id"]
    assert replacement["market_generation_id"] == replacement_market["generation_id"]
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT status FROM membership_session_generations WHERE generation_id=?",
                (original["generation_id"],),
            ).fetchone()[0]
            == "abandoned"
        )


def test_begin_rejects_temporal_authority_mismatch_and_exact_target_receipt(tmp_path):
    store, _empty, _market = _fixture(tmp_path)

    with pytest.raises(PITReceiptError, match="temporal"):
        store.begin_or_resume_membership_generation(
            BASE + timedelta(minutes=5),
            TARGET,
            temporal_role=ROLE,
            temporal_contract_sha256="b" * 64,
        )

    _attempt, event = _record(
        store,
        "bak_basic",
        TARGET,
        _snapshot_rows(TARGET),
        sequence=90,
    )
    assert event["status"] == "stored"
    with pytest.raises(PITReceiptError, match="same-day.*receipt"):
        _begin(store)


def test_stage_publish_verify_causal_projection_and_no_fake_receipt(tmp_path):
    store, empty, _market = _fixture(tmp_path)
    generation = _begin(store)

    staged = store.stage_membership_generation(generation["generation_id"])
    assert staged["source_kind"] == "causal_carry_forward"
    assert staged["anchor"]["trade_date"] == ANCHOR
    assert staged["summary"] == {
        "row_count": 2,
        "membership_present_count": 1,
        "observed_in_daily_count": 2,
        "unknown_metadata_count": 1,
        "metadata_stale_possible_count": 1,
        "signal_eligible_count": 0,
        "signal_session_eligible": False,
    }
    assert [row["ts_code"] for row in staged["rows"]] == [
        "600001.SH",
        "600002.SH",
    ]
    assert all(row["signal_eligible"] is False for row in staged["rows"])
    assert staged["rows"][1]["unknown_metadata"] is True
    assert store.stage_membership_generation(generation["generation_id"])["reused"] is True

    published = store.publish_membership_generation(generation["generation_id"])
    verified = store.verify_membership_generation(TARGET)
    assert published["manifest_sha256"] == verified["manifest_sha256"]
    assert published["lineage_sha256"] == verified["lineage_sha256"]
    assert verified["source_kind"] == "causal_carry_forward"
    assert verified["signal_session_eligible"] is False
    assert (
        store.active_membership_generation(TARGET)["generation_id"] == generation["generation_id"]
    )
    assert (
        store.publish_membership_generation(generation["generation_id"])["manifest_sha256"]
        == published["manifest_sha256"]
    )

    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (TARGET,),
            ).fetchone()
            is None
        )
        evidence = connection.execute(
            "SELECT evidence_kind, attempt_id FROM membership_generation_evidence "
            "WHERE generation_id=? ORDER BY evidence_kind",
            (generation["generation_id"],),
        ).fetchall()
    assert ("target_semantic_empty", empty["attempt_id"]) in evidence
    assert any(kind == "anchor_snapshot_attempt" for kind, _attempt_id in evidence)


def test_publish_terminal_time_is_causal_max_and_head_matches(tmp_path):
    store, _empty, market, _staged, published = _publish_membership_fixture(tmp_path)
    generation_id = published["generation_id"]
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        generation = connection.execute(
            "SELECT started_at, terminal_at FROM membership_session_generations "
            "WHERE generation_id=?",
            (generation_id,),
        ).fetchone()
        market_times = connection.execute(
            "SELECT generation.terminal_at, head.published_at "
            "FROM market_session_generations AS generation "
            "JOIN market_session_generation_head AS head "
            "ON head.generation_id=generation.generation_id "
            "WHERE generation.generation_id=?",
            (market["generation_id"],),
        ).fetchone()
        evidence_times = connection.execute(
            "SELECT attempt.retrieved_at, event.recorded_at "
            "FROM membership_generation_evidence AS evidence "
            "JOIN fetch_attempts AS attempt ON attempt.attempt_id=evidence.attempt_id "
            "JOIN fetch_promotion_events AS event ON event.attempt_id=evidence.attempt_id "
            "WHERE evidence.generation_id=?",
            (generation_id,),
        ).fetchall()
        head_published_at = connection.execute(
            "SELECT published_at FROM membership_session_head WHERE trade_date=?",
            (TARGET,),
        ).fetchone()[0]
    values = [generation["started_at"], *market_times]
    values.extend(value for row in evidence_times for value in row)
    expected = max(datetime.fromisoformat(value) for value in values).astimezone(timezone.utc)
    assert generation["terminal_at"] == expected.isoformat()
    assert head_published_at == expected.isoformat()


@pytest.mark.parametrize("tamper", ["terminal_null", "terminal_early", "head_time"])
def test_verify_rejects_membership_terminal_chronology_tampering(tmp_path, tamper):
    store, _empty, _market, _staged, published = _publish_membership_fixture(tmp_path / tamper)
    with sqlite3.connect(store.database_path) as connection:
        if tamper == "terminal_null":
            connection.execute(
                "UPDATE membership_session_generations SET terminal_at=NULL WHERE generation_id=?",
                (published["generation_id"],),
            )
        elif tamper == "terminal_early":
            connection.execute(
                "UPDATE membership_session_generations SET terminal_at=started_at "
                "WHERE generation_id=?",
                (published["generation_id"],),
            )
        else:
            connection.execute(
                "UPDATE membership_session_head SET published_at=? WHERE trade_date=?",
                (BASE.isoformat(), TARGET),
            )

    with pytest.raises(PITReceiptError, match="chronology"):
        store.verify_membership_generation(TARGET)


def test_verify_rejects_bound_market_terminal_time_tampering(tmp_path):
    store, _empty, market, _staged, _published = _publish_membership_fixture(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        started_at = connection.execute(
            "SELECT started_at FROM market_session_generations WHERE generation_id=?",
            (market["generation_id"],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_session_generations SET terminal_at=? WHERE generation_id=?",
            (started_at, market["generation_id"]),
        )

    with pytest.raises(PITReceiptError, match="market.*chronology"):
        store.verify_membership_generation(TARGET)


def test_verify_rejects_coordinated_bound_market_lifecycle_time_tampering(tmp_path):
    store, _empty, market, _staged, _published = _publish_membership_fixture(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        started_at = connection.execute(
            "SELECT started_at FROM market_session_generations WHERE generation_id=?",
            (market["generation_id"],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE market_session_generations SET terminal_at=? WHERE generation_id=?",
            (started_at, market["generation_id"]),
        )
        connection.execute(
            "UPDATE market_session_generation_head SET published_at=? WHERE generation_id=?",
            (started_at, market["generation_id"]),
        )

    with pytest.raises(PITReceiptError, match="lineage|market.*chronology"):
        store.verify_membership_generation(TARGET)


def test_preanchor_projection_contains_only_unknown_buy_ineligible_rows(tmp_path):
    store, _empty, _market = _fixture(tmp_path, with_anchor=False)
    generation = _begin(store)
    staged = store.stage_membership_generation(generation["generation_id"])

    assert staged["source_kind"] == "pre_anchor_quarantine"
    assert staged["anchor"] is None
    assert {row["ts_code"] for row in staged["rows"]} == {
        "600001.SH",
        "600002.SH",
    }
    assert all(row["unknown_metadata"] is True for row in staged["rows"])
    assert all(row["membership_present"] is False for row in staged["rows"])
    assert all(row["signal_eligible"] is False for row in staged["rows"])


def test_latest_strictly_past_anchor_selected_and_future_poison_ignored(tmp_path):
    store, _empty, _market = _fixture(tmp_path)
    _record(
        store,
        "bak_basic",
        "2016-10-09",
        _snapshot_rows("2016-10-09", ("600003.SH",)),
        sequence=70,
    )
    _record(
        store,
        "bak_basic",
        FUTURE,
        _snapshot_rows(FUTURE, ("600004.SH",)),
        sequence=71,
    )
    generation = _begin(store)
    staged = store.stage_membership_generation(generation["generation_id"])

    assert staged["anchor"]["trade_date"] == "2016-10-09"
    assert {row["ts_code"] for row in staged["rows"]} == {
        "600001.SH",
        "600002.SH",
        "600003.SH",
    }
    assert "600004.SH" not in {row["ts_code"] for row in staged["rows"]}
    root = staged["manifest_sha256"]
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE daily_universe SET name='Future Poison' WHERE trade_date=?",
            (FUTURE,),
        )
    assert store.stage_membership_generation(generation["generation_id"])["manifest_sha256"] == root


def test_anchor_search_skips_newer_receipt_with_different_authority(tmp_path):
    store, _empty, _market = _fixture(tmp_path)
    _record(
        store,
        "bak_basic",
        "2016-10-09",
        _snapshot_rows("2016-10-09", ("600003.SH",)),
        sequence=70,
        source_profile="different-source",
    )

    staged = store.stage_membership_generation(_begin(store)["generation_id"])
    assert staged["anchor"]["trade_date"] == ANCHOR
    assert "600003.SH" not in {row["ts_code"] for row in staged["rows"]}


def test_anchor_receipt_uses_matching_reused_attempt_not_first_foreign_attempt(
    tmp_path,
):
    store = PITReceiptStore(str(tmp_path / "anchor-reused"))
    rows = _snapshot_rows(ANCHOR)
    _record(
        store,
        "bak_basic",
        ANCHOR,
        rows,
        sequence=1,
        source_profile="different-source",
    )
    _attempt, event = _record(
        store,
        "bak_basic",
        ANCHOR,
        rows,
        sequence=2,
    )
    assert event["status"] == "reused"
    _record(store, "bak_basic", TARGET, [], sequence=3)
    _publish_market(store, sequence=10)

    staged = store.stage_membership_generation(_begin(store)["generation_id"])
    assert staged["anchor"]["trade_date"] == ANCHOR
    assert staged["source_kind"] == "causal_carry_forward"


def test_anchor_search_fails_closed_on_corrupt_latest_matching_receipt(tmp_path):
    store, _empty, _market = _fixture(tmp_path)
    _record(
        store,
        "bak_basic",
        "2016-10-09",
        _snapshot_rows("2016-10-09", ("600003.SH",)),
        sequence=70,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE receipts SET normalized_sha256=? "
            "WHERE dataset='bak_basic' AND partition_key='2016-10-09'",
            ("f" * 64,),
        )

    with pytest.raises(PITReceiptError, match="anchor receipt lineage"):
        store.stage_membership_generation(_begin(store)["generation_id"])


def test_anchor_search_does_not_hide_latest_matching_zero_row_receipt(tmp_path):
    store, _empty, _market = _fixture(tmp_path)
    _record(
        store,
        "bak_basic",
        "2016-10-09",
        _snapshot_rows("2016-10-09", ("600003.SH",)),
        sequence=70,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE receipts SET row_count=0 "
            "WHERE dataset='bak_basic' AND partition_key='2016-10-09'"
        )

    with pytest.raises(PITReceiptError, match="anchor receipt metadata"):
        store.stage_membership_generation(_begin(store)["generation_id"])


def test_anchor_search_does_not_hide_broken_matching_attempt_behind_foreign_success(
    tmp_path,
):
    store, _empty, _market = _fixture(tmp_path)
    rows = _snapshot_rows("2016-10-09", ("600003.SH",))
    _record(
        store,
        "bak_basic",
        "2016-10-09",
        rows,
        sequence=70,
        source_profile="different-source",
    )
    matching_attempt, event = _record(
        store,
        "bak_basic",
        "2016-10-09",
        rows,
        sequence=71,
    )
    assert event["status"] == "reused"
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE fetch_promotion_events SET status='api_error' WHERE attempt_id=?",
            (matching_attempt["attempt_id"],),
        )

    with pytest.raises(PITReceiptError, match="anchor attempt"):
        store.stage_membership_generation(_begin(store)["generation_id"])


@pytest.mark.parametrize("bad_list_date", [None, "20161010"])
def test_anchor_rejects_supported_a_share_with_noncausal_list_date(tmp_path, bad_list_date):
    store, _empty, _market = _fixture(tmp_path / str(bad_list_date))
    rows = _snapshot_rows("2016-10-09", ("600003.SH", "600004.SH"))
    rows[1][-1] = bad_list_date
    _record(store, "bak_basic", "2016-10-09", rows, sequence=70)

    with pytest.raises(PITReceiptError, match="list_date"):
        store.stage_membership_generation(_begin(store)["generation_id"])


@pytest.mark.parametrize(
    ("case", "raw", "error_kind", "promote"),
    [
        ("malformed", b"not-json", None, True),
        ("api", b'{"code":-1,"data":{"fields":[],"items":[]}}', None, True),
        ("transport", _body("bak_basic", []), "transport_error", True),
        ("missing_terminal", _body("bak_basic", []), None, False),
    ],
)
def test_stage_requires_raw_reparsed_semantic_empty_terminal_evidence(
    tmp_path, case, raw, error_kind, promote
):
    store = PITReceiptStore(str(tmp_path / case))
    _record(
        store,
        "bak_basic",
        ANCHOR,
        _snapshot_rows(ANCHOR),
        sequence=1,
    )
    _record(
        store,
        "bak_basic",
        TARGET,
        [],
        sequence=2,
        raw=raw,
        error_kind=error_kind,
        promote=promote,
    )
    _publish_market(store)
    generation = _begin(store)

    with pytest.raises(PITReceiptError, match="semantic-empty"):
        store.stage_membership_generation(generation["generation_id"])


def test_stage_accepts_controlled_invalid_json_label_only_after_raw_reparse(tmp_path):
    store = PITReceiptStore(str(tmp_path / "controlled-empty"))
    _record(
        store,
        "bak_basic",
        ANCHOR,
        _snapshot_rows(ANCHOR),
        sequence=1,
    )
    _record(
        store,
        "bak_basic",
        TARGET,
        [],
        sequence=2,
        error_kind="invalid_json",
        error_message="planned response is empty",
    )
    _publish_market(store)

    staged = store.stage_membership_generation(_begin(store)["generation_id"])
    assert staged["source_kind"] == "causal_carry_forward"


@pytest.mark.parametrize("bad_terminal", ["api_error", "transport_error", "missing"])
def test_stage_rejects_any_nonempty_terminal_shape_in_matching_target_cohort(
    tmp_path, bad_terminal
):
    store, _empty, _market = _fixture(tmp_path)
    if bad_terminal == "api_error":
        _record(
            store,
            "bak_basic",
            TARGET,
            [],
            sequence=80,
            raw=b'{"code":-1,"data":{"fields":[],"items":[]}}',
        )
    elif bad_terminal == "transport_error":
        _record(
            store,
            "bak_basic",
            TARGET,
            [],
            sequence=80,
            error_kind="transport_error",
        )
    else:
        _record(
            store,
            "bak_basic",
            TARGET,
            [],
            sequence=80,
            promote=False,
        )
    generation = _begin(store)

    with pytest.raises(PITReceiptError, match="target cohort"):
        store.stage_membership_generation(generation["generation_id"])


def test_target_cohort_persists_all_matching_empty_attempts_and_ignores_other_temporal(
    tmp_path,
):
    store, first_empty, _market = _fixture(tmp_path)
    second_empty, _event = _record(store, "bak_basic", TARGET, [], sequence=80)
    _record(
        store,
        "bak_basic",
        TARGET,
        [],
        sequence=81,
        contract="b" * 64,
        raw=b'{"code":-1,"data":{"fields":[],"items":[]}}',
    )
    staged = store.stage_membership_generation(_begin(store)["generation_id"])
    assert {item["attempt_id"] for item in staged["empty_evidence"]} == {
        first_empty["attempt_id"],
        second_empty["attempt_id"],
    }


@pytest.mark.parametrize("mismatch", ["empty_source", "anchor_temporal"])
def test_target_source_mismatch_rejected_and_foreign_anchor_not_selected(tmp_path, mismatch):
    store = PITReceiptStore(str(tmp_path / mismatch))
    _record(
        store,
        "bak_basic",
        ANCHOR,
        _snapshot_rows(ANCHOR),
        sequence=1,
        contract="b" * 64 if mismatch == "anchor_temporal" else CONTRACT,
    )
    _record(
        store,
        "bak_basic",
        TARGET,
        [],
        sequence=2,
        source_profile=("different-source" if mismatch == "empty_source" else "fixture-source"),
    )
    _publish_market(store)
    generation = _begin(store)

    if mismatch == "empty_source":
        with pytest.raises(PITReceiptError, match="authority"):
            store.stage_membership_generation(generation["generation_id"])
    else:
        staged = store.stage_membership_generation(generation["generation_id"])
        assert staged["source_kind"] == "pre_anchor_quarantine"
        assert staged["anchor"] is None


def test_stage_rejects_same_origin_wrong_endpoint_path(tmp_path):
    store = PITReceiptStore(str(tmp_path / "wrong-path"))
    _record(
        store,
        "bak_basic",
        ANCHOR,
        _snapshot_rows(ANCHOR),
        sequence=1,
    )
    _record(
        store,
        "bak_basic",
        TARGET,
        [],
        sequence=2,
        url="https://data.example.test/not-bak-basic",
    )
    _publish_market(store)
    generation = _begin(store)

    with pytest.raises(PITReceiptError, match="request URL path"):
        store.stage_membership_generation(generation["generation_id"])


def test_market_head_change_and_late_same_day_receipt_make_generation_stale(tmp_path):
    store, _empty, _market = _fixture(tmp_path)
    first = _begin(store)
    _publish_market(store, sequence=100, force_new=True)
    with pytest.raises(PITReceiptError, match="market.*stale"):
        store.stage_membership_generation(first["generation_id"])

    replacement = _begin(store, now=BASE + timedelta(minutes=10), force_new=True)
    store.stage_membership_generation(replacement["generation_id"])
    _record(
        store,
        "bak_basic",
        TARGET,
        _snapshot_rows(TARGET),
        sequence=120,
    )
    with pytest.raises(PITReceiptError, match="same-day.*receipt"):
        store.publish_membership_generation(replacement["generation_id"])


@pytest.mark.parametrize("tamper", ["dangling_generation", "manifest", "lineage"])
def test_new_publish_cannot_replace_a_tampered_prior_membership_head(tmp_path, tamper):
    store, _empty, _market, _staged, _published = _publish_membership_fixture(tmp_path / tamper)
    replacement = _begin(store, now=BASE + timedelta(minutes=20), force_new=True)
    store.stage_membership_generation(replacement["generation_id"])
    with sqlite3.connect(store.database_path) as connection:
        if tamper == "dangling_generation":
            connection.execute(
                "UPDATE membership_session_head SET generation_id='missing-generation' "
                "WHERE trade_date=?",
                (TARGET,),
            )
        elif tamper == "manifest":
            connection.execute(
                "UPDATE membership_session_head SET manifest_sha256=? WHERE trade_date=?",
                ("f" * 64, TARGET),
            )
        else:
            connection.execute(
                "UPDATE membership_session_head SET lineage_sha256=? WHERE trade_date=?",
                ("f" * 64, TARGET),
            )
        before = connection.execute(
            "SELECT * FROM membership_session_head WHERE trade_date=?",
            (TARGET,),
        ).fetchone()

    with pytest.raises(PITReceiptError):
        store.publish_membership_generation(replacement["generation_id"])

    with sqlite3.connect(store.database_path) as connection:
        after = connection.execute(
            "SELECT * FROM membership_session_head WHERE trade_date=?",
            (TARGET,),
        ).fetchone()
    assert after == before


def test_new_publish_rejects_missing_head_for_prior_published_generation(tmp_path):
    store, _empty, _market, _staged, published = _publish_membership_fixture(tmp_path)
    replacement = _begin(store, now=BASE + timedelta(minutes=20), force_new=True)
    store.stage_membership_generation(replacement["generation_id"])
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM membership_session_head WHERE trade_date=?",
            (TARGET,),
        )

    with pytest.raises(PITReceiptError, match="published membership.*head"):
        store.publish_membership_generation(replacement["generation_id"])

    with sqlite3.connect(store.database_path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT generation_id, status FROM membership_session_generations "
                "WHERE generation_id IN (?, ?)",
                (published["generation_id"], replacement["generation_id"]),
            )
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM membership_session_head WHERE trade_date=?",
                (TARGET,),
            ).fetchone()[0]
            == 0
        )
    assert statuses == {
        published["generation_id"]: "published",
        replacement["generation_id"]: "collecting",
    }


@pytest.mark.parametrize(
    "tamper",
    [
        "anchor_receipt",
        "empty_attempt",
        "terminal_event",
        "market_row",
        "derived_row",
        "manifest",
        "head",
    ],
)
def test_verify_rejects_lineage_tampering(tmp_path, tamper):
    store, empty, market, _staged, published = _publish_membership_fixture(tmp_path / tamper)
    generation_id = published["generation_id"]
    with sqlite3.connect(store.database_path) as connection:
        if tamper == "anchor_receipt":
            connection.execute(
                "UPDATE receipts SET normalized_sha256=? "
                "WHERE dataset='bak_basic' AND partition_key=?",
                ("f" * 64, ANCHOR),
            )
        elif tamper == "empty_attempt":
            connection.execute(
                "UPDATE fetch_attempts SET raw_sha256=? WHERE attempt_id=?",
                ("f" * 64, empty["attempt_id"]),
            )
        elif tamper == "terminal_event":
            connection.execute(
                "UPDATE fetch_promotion_events SET status='api_error' WHERE attempt_id=?",
                (empty["attempt_id"],),
            )
        elif tamper == "market_row":
            connection.execute(
                "UPDATE market_session_generation_rows_daily SET close=close+1 "
                "WHERE generation_id=? AND ts_code='600001.SH'",
                (market["generation_id"],),
            )
        elif tamper == "derived_row":
            connection.execute(
                "UPDATE membership_generation_rows SET name='Poison' "
                "WHERE generation_id=? AND ts_code='600001.SH'",
                (generation_id,),
            )
        elif tamper == "manifest":
            connection.execute(
                "UPDATE membership_session_generations SET manifest_sha256=? WHERE generation_id=?",
                ("f" * 64, generation_id),
            )
        else:
            connection.execute(
                "UPDATE membership_session_head SET manifest_sha256=? WHERE trade_date=?",
                ("f" * 64, TARGET),
            )

    with pytest.raises(PITReceiptError):
        store.verify_membership_generation(TARGET)


def test_new_membership_tables_and_results_do_not_persist_secrets(tmp_path):
    store, _empty, _market, _staged, published = _publish_membership_fixture(tmp_path)
    encoded_result = json.dumps(published, sort_keys=True).lower()
    assert "token" not in encoded_result
    assert "api_key" not in encoded_result
    with sqlite3.connect(store.database_path) as connection:
        values = []
        for table in (
            "membership_session_generations",
            "membership_generation_rows",
            "membership_generation_evidence",
            "membership_session_head",
        ):
            values.extend(connection.execute(f"SELECT * FROM {table}").fetchall())
    encoded_db = repr(values).lower()
    assert "token" not in encoded_db
    assert "api_key" not in encoded_db
