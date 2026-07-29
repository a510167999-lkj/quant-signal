from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import uuid

import pytest

from app import (
    jiaoch_credential_slots,
    jiaoch_trade_cal_authority,
    research_provider_pit_tail_v2,
)
from app.jiaoch_credential_slots import (
    HISTORICAL_MINUTE_ENV,
    POINTS_PRIMARY_ENV,
    collect_jiaoch_trade_cal_authority,
    create_jiaoch_credential_generation,
)
from app.jiaoch_trade_cal_authority import verify_jiaoch_trade_cal_authority
from app.research_pit_transport import HttpEntityResponse


POINTS_TOKEN = "points-secret-value"
MINUTE_TOKEN = "minute-secret-value"
START_DATE = date(2026, 7, 20)
END_DATE = date(2026, 7, 26)
UTC_NOW = datetime(2026, 7, 29, 11, 30, 0, 123456, tzinfo=timezone.utc)
RETRIEVED_AT = UTC_NOW.isoformat()
FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
FIELDS_TEXT = ",".join(FIELDS)
CALENDAR_ROWS = [
    ["SSE", "20260720", 1, "20260717"],
    ["SSE", "20260721", 1, "20260720"],
    ["SSE", "20260722", 1, "20260721"],
    ["SSE", "20260723", 1, "20260722"],
    ["SSE", "20260724", 1, "20260723"],
    ["SSE", "20260725", 0, "20260724"],
    ["SSE", "20260726", 0, "20260724"],
]
OPEN_SESSIONS = [
    "20260720",
    "20260721",
    "20260722",
    "20260723",
    "20260724",
]
LEGACY_POLICY_SHA256 = "de38737b3d44bc9730b5504a622ef88eb7fd517b6a7fb8c42255702d02445694"
LEGACY_POLICY_DOCUMENT = {
    "routes": [
        {
            "api_name": "daily_basic",
            "credential_slot_id": "points-primary",
            "purpose": "points-interface",
            "route_id": "points-primary:daily_basic",
        },
        {
            "api_name": "moneyflow",
            "credential_slot_id": "points-primary",
            "purpose": "points-interface",
            "route_id": "points-primary:moneyflow",
        },
        {
            "api_name": "stk_mins",
            "credential_slot_id": "historical-minute",
            "purpose": "historical-minute",
            "route_id": "historical-minute:stk_mins",
        },
        {
            "api_name": "daily",
            "credential_slot_id": "historical-minute",
            "purpose": "minute-calibration",
            "route_id": "historical-minute:calibration-daily",
        },
    ],
    "schema": "jiaoch-credential-routing-policy/v1",
}
AUXILIARY_POLICY_DOCUMENT = {
    "routes": [
        {
            "api_name": "trade_cal",
            "credential_slot_id": "points-primary",
            "purpose": "exchange-calendar",
            "route_id": "auxiliary:points-primary:trade_cal",
        }
    ],
    "schema": "jiaoch-credential-auxiliary-routing-policy/v1",
}


class RecordingTransport:
    def __init__(self, outcomes: list[HttpEntityResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("unexpected fallback or retry")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _fixed_private_utc_clock(monkeypatch) -> None:
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_utc_now",
        lambda: UTC_NOW,
        raising=False,
    )


def _generation():
    return create_jiaoch_credential_generation(
        environment_snapshot={
            POINTS_PRIMARY_ENV: POINTS_TOKEN,
            HISTORICAL_MINUTE_ENV: MINUTE_TOKEN,
        }
    )


def _response_body(
    *,
    rows: list[list] | None = None,
    fields: list[str] | None = None,
    code: int = 0,
    msg: str = "success",
) -> bytes:
    payload = {
        "code": code,
        "data": {
            "fields": FIELDS if fields is None else fields,
            "items": list(reversed(CALENDAR_ROWS)) if rows is None else rows,
        },
        "msg": msg,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _entity(body: bytes, *, status: int = 200, complete: bool = True) -> HttpEntityResponse:
    return HttpEntityResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=body,
        body_complete=complete,
    )


def _collect(
    tmp_path: Path,
    monkeypatch,
    *,
    outcome: HttpEntityResponse | Exception | None = None,
    start_date: date = START_DATE,
    end_date: date = END_DATE,
):
    transport = RecordingTransport([_entity(_response_body()) if outcome is None else outcome])
    constructions: list[None] = []

    def factory():
        constructions.append(None)
        return transport

    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", factory)
    publication = collect_jiaoch_trade_cal_authority(
        generation=_generation(),
        output_root=tmp_path,
        start_date=start_date,
        end_date=end_date,
        timeout_seconds=7.5,
    )
    return publication, transport, constructions


def _manifest(root: Path, publication: dict) -> dict:
    return json.loads((root / publication["authority_manifest_relative_path"]).read_bytes())


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _write_forged_manifest(root: Path, payload: dict) -> tuple[str, str]:
    raw = _canonical_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"trade_cal_manifests/sha256/{digest[:2]}/{digest}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return relative, digest


def _open_sessions_root() -> str:
    descriptor = {
        "end_date": "20260726",
        "exchange": "SSE",
        "open_sessions": OPEN_SESSIONS,
        "schema": "jiaoch-trade-cal-open-sessions/v1",
        "start_date": "20260720",
    }
    return hashlib.sha256(_canonical_bytes(descriptor)).hexdigest()


def _calendar_rows_root() -> str:
    descriptor = {
        "rows": [
            {
                "cal_date": row[1],
                "exchange": row[0],
                "is_open": row[2],
                "pretrade_date": row[3],
            }
            for row in CALENDAR_ROWS
        ],
        "schema": "jiaoch-trade-cal-normalized-rows/v1",
    }
    return hashlib.sha256(_canonical_bytes(descriptor)).hexdigest()


def _normalization_evidence(rows: list[list]) -> dict:
    entries = []
    for row in sorted(rows, key=lambda item: item[1]):
        wire_value = row[2]
        entries.append(
            {
                "cal_date": row[1],
                "canonical_value": int(wire_value),
                "wire_type": "integer" if type(wire_value) is int else "string",
                "wire_value": wire_value,
            }
        )
    return {
        "contract_canonical_sha256": (
            research_provider_pit_tail_v2.IS_OPEN_NORMALIZATION_CONTRACT_SHA256
        ),
        "endpoint": "trade_cal",
        "entries": entries,
        "field": "is_open",
        "raw_wire_preserved": True,
        "schema": "provider-trade-cal-is-open-normalization/v1",
    }


def _normalization_root(rows: list[list]) -> str:
    return hashlib.sha256(_canonical_bytes(_normalization_evidence(rows))).hexdigest()


def test_legacy_policy_document_digest_routes_and_descriptions_remain_exact() -> None:
    generation = _generation()

    assert jiaoch_credential_slots._policy_document(jiaoch_credential_slots._POLICY) == (
        LEGACY_POLICY_DOCUMENT
    )
    assert jiaoch_credential_slots._POLICY_SHA256 == LEGACY_POLICY_SHA256
    assert set(jiaoch_credential_slots._ROUTES_BY_ID) == {
        "points-primary:daily_basic",
        "points-primary:moneyflow",
        "historical-minute:stk_mins",
        "historical-minute:calibration-daily",
    }
    assert "auxiliary:points-primary:trade_cal" not in jiaoch_credential_slots._ROUTES_BY_ID
    assert {description.policy_sha256 for description in generation.describe_slots()} == {
        LEGACY_POLICY_SHA256
    }


def test_trade_cal_uses_separate_versioned_auxiliary_policy_descriptor() -> None:
    descriptor = jiaoch_credential_slots._auxiliary_policy_descriptor()
    expected_digest = hashlib.sha256(_canonical_bytes(AUXILIARY_POLICY_DOCUMENT)).hexdigest()

    assert descriptor == {
        "document": AUXILIARY_POLICY_DOCUMENT,
        "schema": "jiaoch-credential-auxiliary-policy-descriptor/v1",
        "sha256": expected_digest,
    }
    assert descriptor["sha256"] != LEGACY_POLICY_SHA256


def test_public_entrypoint_and_offline_verifier_have_no_secret_transport_or_clock_inputs() -> None:
    assert set(inspect.signature(collect_jiaoch_trade_cal_authority).parameters) == {
        "generation",
        "output_root",
        "start_date",
        "end_date",
        "timeout_seconds",
    }
    for forbidden in (
        "token",
        "credential",
        "transport",
        "callback",
        "fallback",
        "retrieved_at",
        "clock",
    ):
        assert forbidden not in inspect.signature(collect_jiaoch_trade_cal_authority).parameters
    assert set(inspect.signature(verify_jiaoch_trade_cal_authority).parameters) == {
        "output_root",
        "authority_manifest_relative_path",
        "expected_authority_manifest_sha256",
        "publication_capability",
    }


def test_only_returned_unpersisted_capability_promotes_candidate_to_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _ = _collect(tmp_path, monkeypatch)
    capability = publication["publication_capability"]
    manifest = _manifest(tmp_path, publication)

    assert publication["schema"] == "jiaoch-trade-cal-authority-publication/v1"
    assert publication["publication_status"] == "DURABLE_POSTVERIFIED_AND_RETURNED"
    assert publication["authority_manifest_relative_path"].startswith(
        "trade_cal_manifest_candidates/sha256/"
    )
    assert manifest["calendar_authority_status"] == (
        "NOT_GRANTED_WITHOUT_RETURNED_PUBLICATION_CAPABILITY"
    )
    assert (
        manifest["publication_capability_sha256"] == hashlib.sha256(capability.encode()).hexdigest()
    )
    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert capability.encode() not in persisted
    assert (
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=publication["authority_manifest_relative_path"],
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
            publication_capability=capability,
        )["calendar_authority_status"]
        == "VERIFIED_SINGLE_SEALED_CALL"
    )
    with pytest.raises(ValueError, match="capability"):
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=publication["authority_manifest_relative_path"],
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
            publication_capability=str(uuid.uuid4()),
        )


def test_internal_utc_clock_runs_once_before_transport_and_is_shared_by_all_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport([_entity(_response_body())])
    events: list[str] = []

    def clock() -> datetime:
        events.append("clock")
        return UTC_NOW

    def factory() -> RecordingTransport:
        events.append("transport")
        return transport

    monkeypatch.setattr(jiaoch_trade_cal_authority, "_utc_now", clock)
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", factory)
    publication = collect_jiaoch_trade_cal_authority(
        generation=_generation(),
        output_root=tmp_path,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    manifest = _manifest(tmp_path, publication)
    attempt = json.loads((tmp_path / manifest["attempt"]["attempt_relative_path"]).read_bytes())

    assert events == ["clock", "transport"]
    assert manifest["retrieved_at"] == RETRIEVED_AT
    assert attempt["retrieved_at"] == RETRIEVED_AT
    assert RETRIEVED_AT.endswith("+00:00")


class _DerivedDate(date):
    pass


class _DerivedDatetime(datetime):
    pass


@pytest.mark.parametrize(
    "invalid_now",
    [
        date(2026, 7, 29),
        datetime(2026, 7, 29, 11, 30),
        datetime(2026, 7, 29, 19, 30, tzinfo=timezone(timedelta(hours=8))),
        _DerivedDatetime(2026, 7, 29, 11, 30, tzinfo=timezone.utc),
    ],
)
def test_invalid_private_clock_fails_once_before_transport(
    tmp_path: Path,
    monkeypatch,
    invalid_now: object,
) -> None:
    clock_calls = 0
    constructions = []

    def clock():
        nonlocal clock_calls
        clock_calls += 1
        return invalid_now

    monkeypatch.setattr(jiaoch_trade_cal_authority, "_utc_now", clock)
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: constructions.append(True),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed") as caught:
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert caught.value.__context__ is None
    assert clock_calls == 1
    assert constructions == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        ("20260720", END_DATE),
        (START_DATE, "20260726"),
        (datetime(2026, 7, 20), END_DATE),
        (START_DATE, datetime(2026, 7, 26)),
        (_DerivedDate(2026, 7, 20), END_DATE),
        (START_DATE, _DerivedDate(2026, 7, 26)),
        (END_DATE, START_DATE),
    ],
)
def test_date_window_requires_exact_dates_and_order_before_clock_or_transport(
    tmp_path: Path,
    monkeypatch,
    start_date: object,
    end_date: object,
) -> None:
    events = []
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_utc_now",
        lambda: events.append("clock"),
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: events.append("transport"),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=start_date,
            end_date=end_date,
        )

    assert events == []
    assert list(tmp_path.iterdir()) == []


def test_closed_collection_uses_one_points_token_and_exactly_one_direct_post(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, transport, constructions = _collect(tmp_path, monkeypatch)

    assert constructions == [None]
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://jiaoch.site/trade_cal"
    assert call["timeout_s"] == 7.5
    assert call["max_body_bytes"] == 32 * 1024 * 1024
    assert call["headers"] == {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "quant-jiaoch-trade-cal-authority/1",
    }
    assert json.loads(call["body"]) == {
        "api_name": "trade_cal",
        "fields": FIELDS_TEXT,
        "params": {
            "end_date": "20260726",
            "exchange": "SSE",
            "start_date": "20260720",
        },
        "token": POINTS_TOKEN,
    }
    assert MINUTE_TOKEN.encode() not in call["body"]
    assert publication["authority_manifest_created"] is True


def test_manifest_binds_attempt_auxiliary_policy_calendar_roots_and_all_safety_false(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _ = _collect(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path, publication)
    attempt = json.loads((tmp_path / manifest["attempt"]["attempt_relative_path"]).read_bytes())
    verified = verify_jiaoch_trade_cal_authority(
        output_root=tmp_path,
        authority_manifest_relative_path=publication["authority_manifest_relative_path"],
        expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
    )

    assert manifest["schema"] == "jiaoch-trade-cal-authority/v1"
    assert manifest["source_id"] == "jiaoch"
    assert manifest["credential_slot_id"] == "points-primary"
    assert manifest["calendar_authority_status"] == "VERIFIED_SINGLE_SEALED_CALL"
    assert manifest["exchange"] == "SSE"
    assert manifest["start_date"] == "2026-07-20"
    assert manifest["end_date"] == "2026-07-26"
    assert manifest["calendar_day_count"] == 7
    assert manifest["calendar_rows_root_sha256"] == _calendar_rows_root()
    assert manifest["open_sessions"] == OPEN_SESSIONS
    assert manifest["open_session_count"] == 5
    assert manifest["open_sessions_root_sha256"] == _open_sessions_root()
    assert manifest["pretrade_anchor_date"] == "20260717"
    assert manifest["is_open_normalization_contract"] == {
        "document": research_provider_pit_tail_v2.IS_OPEN_NORMALIZATION_CONTRACT,
        "schema": "jiaoch-trade-cal-is-open-normalization-contract-descriptor/v1",
        "sha256": research_provider_pit_tail_v2.IS_OPEN_NORMALIZATION_CONTRACT_SHA256,
    }
    assert manifest["is_open_normalization_evidence"] == _normalization_evidence(CALENDAR_ROWS)
    assert manifest["is_open_normalization_root_sha256"] == _normalization_root(CALENDAR_ROWS)
    assert manifest["natural_day_coverage_verified"] is True
    assert manifest["pretrade_chain_verified"] is True
    assert manifest["open_sessions_sorted"] is True
    assert manifest["auxiliary_policy_descriptor"] == (
        jiaoch_credential_slots._auxiliary_policy_descriptor()
    )
    runtime = manifest["runtime_mapping_descriptor"]
    assert runtime["generation_id"]
    assert runtime["auxiliary_policy_sha256"] == manifest["auxiliary_policy_descriptor"]["sha256"]
    assert runtime["credential_proof_status"] == "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF"
    assert manifest["credential_binding"] == {
        "basis": "ONE_SEALED_ENTRYPOINT_INVOCATION",
        "credential_proof_claimed": False,
        "same_runtime_credential_used": True,
    }
    assert manifest["producer_binding"]["schema"] == "jiaoch-trade-cal-producer/v1"
    assert "app/research_provider_pit_tail_v2.py" in {
        entry["path"] for entry in manifest["producer_binding"]["entries"]
    }
    assert attempt["schema"] == "jiaoch-trade-cal-raw-attempt/v1"
    assert attempt["authority_status"] == "UNBOUND"
    assert attempt["calendar_authority_status"] == "NOT_GRANTED"
    assert attempt["row_authority_status"] == "NOT_GRANTED"
    assert attempt["collection_binding"] == {
        "auxiliary_policy_sha256": runtime["auxiliary_policy_sha256"],
        "collection_call_id": manifest["collection_call_id"],
        "generation_id": runtime["generation_id"],
        "producer_root_sha256": manifest["producer_binding"]["root_sha256"],
        "schema": "jiaoch-trade-cal-collection-binding/v1",
    }
    assert verified == {
        "authority_manifest_sha256": publication["authority_manifest_sha256"],
        "calendar_authority_status": "VERIFIED_SINGLE_SEALED_CALL",
        "development_session_alignment_verified": False,
        "embargo_consumed": False,
        "end_date": "2026-07-26",
        "exchange": "SSE",
        "final_oos_consumed": False,
        "is_open_normalization_root_sha256": _normalization_root(CALENDAR_ROWS),
        "open_session_count": 5,
        "open_sessions_root_sha256": _open_sessions_root(),
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "start_date": "2026-07-20",
        "verified": True,
    }
    for field in (
        "development_session_alignment_verified",
        "development_session_count_claimed",
        "embargo_consumed",
        "experiment_launch_eligible",
        "final_oos_consumed",
        "formal_materialization_eligible",
        "production_profile_registered",
        "production_recommendation_eligible",
        "rows_published",
    ):
        assert manifest[field] is False
        assert attempt[field] is False

    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    for secret in (POINTS_TOKEN, MINUTE_TOKEN):
        assert secret.encode() not in persisted
        assert hashlib.sha256(secret.encode()).hexdigest().encode() not in persisted
    assert b"483" not in persisted


def test_exact_integer_and_string_is_open_wire_values_share_canonical_calendar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rows = [
        ["SSE", "20260720", "1", "20260717"],
        ["SSE", "20260721", 1, "20260720"],
        ["SSE", "20260722", "1", "20260721"],
        ["SSE", "20260723", 1, "20260722"],
        ["SSE", "20260724", "1", "20260723"],
        ["SSE", "20260725", "0", "20260724"],
        ["SSE", "20260726", 0, "20260724"],
    ]
    body = _response_body(rows=list(reversed(rows)))
    transport = RecordingTransport([_entity(body)])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    publication = collect_jiaoch_trade_cal_authority(
        generation=_generation(),
        output_root=tmp_path,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    manifest = _manifest(tmp_path, publication)
    raw_path = tmp_path / manifest["attempt"]["raw_relative_path"]

    assert raw_path.read_bytes() == body
    assert manifest["calendar_rows_root_sha256"] == _calendar_rows_root()
    assert manifest["open_sessions"] == OPEN_SESSIONS
    assert manifest["is_open_normalization_evidence"] == _normalization_evidence(rows)
    assert manifest["is_open_normalization_root_sha256"] == _normalization_root(rows)
    assert verify_jiaoch_trade_cal_authority(
        output_root=tmp_path,
        authority_manifest_relative_path=publication["authority_manifest_relative_path"],
        expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
    )["is_open_normalization_root_sha256"] == _normalization_root(rows)


def test_target_calendar_window_full_manifest_fits_the_sealed_size_bound(
    tmp_path: Path,
    monkeypatch,
) -> None:
    start = date(2023, 6, 1)
    end = date(2026, 7, 3)
    cursor = start
    last_open = "20230531"
    rows = []
    while cursor <= end:
        cal_date = cursor.strftime("%Y%m%d")
        wire_is_open = "1" if cursor.weekday() < 5 else "0"
        rows.append(["SSE", cal_date, wire_is_open, last_open])
        if wire_is_open == "1":
            last_open = cal_date
        cursor += timedelta(days=1)
    transport = RecordingTransport([_entity(_response_body(rows=list(reversed(rows))))])
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: transport,
    )

    publication = collect_jiaoch_trade_cal_authority(
        generation=_generation(),
        output_root=tmp_path,
        start_date=start,
        end_date=end,
    )
    manifest_path = tmp_path / publication["authority_manifest_relative_path"]
    manifest = json.loads(manifest_path.read_bytes())

    assert manifest["calendar_day_count"] == 1129
    assert len(manifest["is_open_normalization_evidence"]["entries"]) == 1129
    assert manifest_path.stat().st_size <= 128 * 1024
    assert (
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=publication["authority_manifest_relative_path"],
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
        )["verified"]
        is True
    )


def test_calendar_window_with_no_open_sessions_is_valid_but_claims_no_development_alignment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rows = [
        ["SSE", "20260725", 0, "20260724"],
        ["SSE", "20260726", 0, "20260724"],
    ]
    transport = RecordingTransport([_entity(_response_body(rows=list(reversed(rows))))])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)
    publication = collect_jiaoch_trade_cal_authority(
        generation=_generation(),
        output_root=tmp_path,
        start_date=date(2026, 7, 25),
        end_date=date(2026, 7, 26),
    )
    manifest = _manifest(tmp_path, publication)

    assert manifest["open_sessions"] == []
    assert manifest["open_session_count"] == 0
    assert manifest["pretrade_anchor_date"] == "20260724"
    assert manifest["development_session_alignment_verified"] is False


@pytest.mark.parametrize(
    "bad_rows",
    [
        CALENDAR_ROWS[:-1],
        [*CALENDAR_ROWS, CALENDAR_ROWS[-1]],
        [["SSE", "20260719", 0, "20260717"], *CALENDAR_ROWS],
        [["SZSE", *row[1:]] for row in CALENDAR_ROWS],
        [[*row[:2], True, row[3]] for row in CALENDAR_ROWS],
        [[*row[:2], 2, row[3]] for row in CALENDAR_ROWS],
        [[*row[:2], 1.0, row[3]] for row in CALENDAR_ROWS],
        [[*row[:2], None, row[3]] for row in CALENDAR_ROWS],
        [[*row[:2], "01", row[3]] for row in CALENDAR_ROWS],
        [[*row[:2], " 1", row[3]] for row in CALENDAR_ROWS],
        [[*row[:2], "2", row[3]] for row in CALENDAR_ROWS],
        [[*CALENDAR_ROWS[0][:3], "20260720"], *CALENDAR_ROWS[1:]],
        [CALENDAR_ROWS[0], [*CALENDAR_ROWS[1][:3], "20260717"], *CALENDAR_ROWS[2:]],
        [[*CALENDAR_ROWS[0][:3], "2026-07-17"], *CALENDAR_ROWS[1:]],
        [],
    ],
)
def test_natural_day_identity_exchange_open_flag_and_pretrade_chain_fail_closed(
    tmp_path: Path,
    monkeypatch,
    bad_rows: list[list],
) -> None:
    transport = RecordingTransport([_entity(_response_body(rows=bad_rows))])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert len(transport.calls) == 1
    assert len(list((tmp_path / "trade_cal_attempts").rglob("*.json"))) == 1
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))


@pytest.mark.parametrize(
    "bad_body",
    [
        _response_body(code=-1, msg="permission denied"),
        _response_body(fields=list(reversed(FIELDS))),
        _response_body(rows=[CALENDAR_ROWS[0][:-1], *CALENDAR_ROWS[1:]]),
        b'{"code":0,"code":0,"data":{"fields":[],"items":[]},"msg":"success"}',
    ],
)
def test_provider_envelope_fields_width_or_duplicate_json_retains_attempt_without_manifest(
    tmp_path: Path,
    monkeypatch,
    bad_body: bytes,
) -> None:
    transport = RecordingTransport([_entity(bad_body)])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert len(list((tmp_path / "trade_cal_raw").rglob("*.body"))) == 1
    assert len(list((tmp_path / "trade_cal_attempts").rglob("*.json"))) == 1
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))


@pytest.mark.parametrize(
    ("status", "complete"),
    [(503, True), (200, False)],
)
def test_bad_http_entity_retains_partial_raw_attempt_but_no_terminal_manifest(
    tmp_path: Path,
    monkeypatch,
    status: int,
    complete: bool,
) -> None:
    transport = RecordingTransport([_entity(b"upstream failure", status=status, complete=complete)])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert len(transport.calls) == 1
    assert len(list((tmp_path / "trade_cal_raw").rglob("*.body"))) == 1
    assert len(list((tmp_path / "trade_cal_attempts").rglob("*.json"))) == 1
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))


def test_transport_failure_has_no_retry_fallback_or_secret_exception_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport([RuntimeError(f"upstream leaked {POINTS_TOKEN}")])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="trade calendar collection failed") as caught:
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert caught.value.__context__ is None
    assert POINTS_TOKEN not in str(caught.value)
    assert len(transport.calls) == 1
    assert not (tmp_path / "trade_cal_raw").exists()
    assert not (tmp_path / "trade_cal_attempts").exists()
    assert not (tmp_path / "trade_cal_manifests").exists()


def test_credential_echo_is_rejected_before_any_artifact_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    body = json.dumps({"code": 0, "echo": POINTS_TOKEN}).encode()
    transport = RecordingTransport([_entity(body)])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert list(tmp_path.iterdir()) == []


def test_response_above_32_mib_is_rejected_before_artifact_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport([_entity(b"x" * (32 * 1024 * 1024 + 1))])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert not (tmp_path / "trade_cal_raw").exists()
    assert not (tmp_path / "trade_cal_attempts").exists()
    assert not (tmp_path / "trade_cal_manifests").exists()


def test_offline_verifier_never_constructs_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _ = _collect(tmp_path, monkeypatch)
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: pytest.fail("offline verifier attempted network"),
    )

    assert (
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=publication["authority_manifest_relative_path"],
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
        )["verified"]
        is True
    )


def test_manifest_tamper_path_traversal_hardlink_and_reparse_are_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _ = _collect(tmp_path, monkeypatch)
    manifest_path = tmp_path / publication["authority_manifest_relative_path"]
    original = manifest_path.read_bytes()

    manifest_path.write_bytes(original + b"\n")
    with pytest.raises(ValueError):
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=publication["authority_manifest_relative_path"],
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
        )
    manifest_path.write_bytes(original)

    with pytest.raises(ValueError, match="path"):
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=f"../{publication['authority_manifest_relative_path']}",
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
        )

    hardlink_source = tmp_path / "manifest-hardlink-source"
    hardlink_source.write_bytes(original)
    manifest_path.unlink()
    try:
        os.link(hardlink_source, manifest_path)
    except OSError:
        pytest.skip("filesystem does not support hard links")
    with pytest.raises(ValueError, match="link|reparse|identity"):
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=publication["authority_manifest_relative_path"],
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
        )


@pytest.mark.parametrize("kind", ["raw", "attempt"])
def test_offline_verifier_rejects_hardlinked_raw_or_attempt(
    tmp_path: Path,
    monkeypatch,
    kind: str,
) -> None:
    publication, _, _ = _collect(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path, publication)
    key = "raw_relative_path" if kind == "raw" else "attempt_relative_path"
    target = tmp_path / manifest["attempt"][key]
    source = tmp_path / f"{kind}-hardlink-source"
    source.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.link(source, target)
    except OSError:
        pytest.skip("filesystem does not support hard links")

    with pytest.raises(ValueError, match="link|reparse|identity"):
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=publication["authority_manifest_relative_path"],
            expected_authority_manifest_sha256=publication["authority_manifest_sha256"],
        )


def test_attempt_from_another_sealed_call_cannot_be_spliced_into_rehashed_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first, _, _ = _collect(tmp_path, monkeypatch)
    second, _, _ = _collect(tmp_path, monkeypatch)
    forged = _manifest(tmp_path, first)
    forged["attempt"] = _manifest(tmp_path, second)["attempt"]
    relative, digest = _write_forged_manifest(tmp_path, forged)

    with pytest.raises(ValueError, match="attempt|binding"):
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=relative,
            expected_authority_manifest_sha256=digest,
        )


def test_rehashed_auxiliary_policy_or_open_sessions_drift_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _ = _collect(tmp_path, monkeypatch)
    for mutation in ("policy", "sessions"):
        forged = _manifest(tmp_path, publication)
        if mutation == "policy":
            forged["runtime_mapping_descriptor"]["auxiliary_policy_sha256"] = "0" * 64
        else:
            forged["open_sessions"] = list(reversed(forged["open_sessions"]))
        relative, digest = _write_forged_manifest(tmp_path, forged)

        with pytest.raises(ValueError, match="policy|session|attempt|root"):
            verify_jiaoch_trade_cal_authority(
                output_root=tmp_path,
                authority_manifest_relative_path=relative,
                expected_authority_manifest_sha256=digest,
            )


@pytest.mark.parametrize(
    "mutation",
    ["contract", "entry", "root"],
)
def test_rehashed_is_open_normalization_drift_is_rejected_offline(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
) -> None:
    publication, _, _ = _collect(tmp_path, monkeypatch)
    forged = _manifest(tmp_path, publication)
    if mutation == "contract":
        forged["is_open_normalization_contract"]["document"]["bool_allowed"] = True
        forged["is_open_normalization_contract"]["sha256"] = hashlib.sha256(
            _canonical_bytes(forged["is_open_normalization_contract"]["document"])
        ).hexdigest()
    elif mutation == "entry":
        forged["is_open_normalization_evidence"]["entries"][0]["wire_type"] = "string"
        forged["is_open_normalization_root_sha256"] = hashlib.sha256(
            _canonical_bytes(forged["is_open_normalization_evidence"])
        ).hexdigest()
    else:
        forged["is_open_normalization_root_sha256"] = "0" * 64
    relative, digest = _write_forged_manifest(tmp_path, forged)

    with pytest.raises(ValueError, match="normalization|evidence|root|producer"):
        verify_jiaoch_trade_cal_authority(
            output_root=tmp_path,
            authority_manifest_relative_path=relative,
            expected_authority_manifest_sha256=digest,
        )


def test_producer_drift_is_rejected_before_manifest_create(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stable_binding = jiaoch_trade_cal_authority._producer_binding()
    producer_calls = 0

    def drifting_binding():
        nonlocal producer_calls
        producer_calls += 1
        if producer_calls <= 2:
            return stable_binding
        return {**stable_binding, "root_sha256": "0" * 64}

    write_labels = []
    original_writer = jiaoch_trade_cal_authority.raw_authority._write_create_only

    def recording_writer(path, raw, *, label, reuse_identical):
        write_labels.append(label)
        return original_writer(
            path,
            raw,
            label=label,
            reuse_identical=reuse_identical,
        )

    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_producer_binding",
        drifting_binding,
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority.raw_authority,
        "_write_create_only",
        recording_writer,
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: RecordingTransport([_entity(_response_body())]),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert producer_calls == 3
    assert "Jiaoch trade calendar authority manifest" not in write_labels
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))


def test_manifest_content_address_conflict_does_not_delete_existing_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel = b"preexisting-content-address-conflict"
    conflicting_paths: list[Path] = []
    original_open = jiaoch_trade_cal_authority.os.open

    def conflicting_open(path, flags, *args, **kwargs):
        candidate = Path(path)
        if candidate.suffix == ".json" and "trade_cal_manifests" in candidate.parts:
            candidate.write_bytes(sentinel)
            conflicting_paths.append(candidate)
            raise FileExistsError("content-address conflict")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(
        jiaoch_trade_cal_authority.os,
        "open",
        conflicting_open,
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: RecordingTransport([_entity(_response_body())]),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert len(conflicting_paths) == 1
    assert conflicting_paths[0].read_bytes() == sentinel


def test_terminal_directory_fsync_failure_rolls_back_new_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_fsync = jiaoch_trade_cal_authority.raw_authority.fsync_directory
    injected = 0

    def failing_terminal_fsync(directory):
        nonlocal injected
        path = Path(directory)
        if (
            injected == 0
            and path.parent.name == "sha256"
            and path.parent.parent.name == "trade_cal_manifests"
            and list(path.glob("*.json"))
        ):
            injected += 1
            raise OSError("injected terminal directory fsync failure")
        return original_fsync(path)

    monkeypatch.setattr(
        jiaoch_trade_cal_authority.raw_authority,
        "fsync_directory",
        failing_terminal_fsync,
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: RecordingTransport([_entity(_response_body())]),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert injected == 1
    assert len(list((tmp_path / "trade_cal_raw").rglob("*.body"))) == 1
    assert len(list((tmp_path / "trade_cal_attempts").rglob("*.json"))) == 1
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))


def test_terminal_file_fsync_failure_rolls_back_new_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_fsync = jiaoch_trade_cal_authority.os.fsync
    injected = 0

    def failing_terminal_file_fsync(descriptor):
        nonlocal injected
        metadata = os.fstat(descriptor)
        if (
            injected == 0
            and jiaoch_trade_cal_authority.stat.S_ISREG(metadata.st_mode)
            and list((tmp_path / "trade_cal_manifests").rglob("*.json"))
        ):
            injected += 1
            raise OSError("injected terminal file fsync failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(
        jiaoch_trade_cal_authority.os,
        "fsync",
        failing_terminal_file_fsync,
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: RecordingTransport([_entity(_response_body())]),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert injected == 1
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))


def test_terminal_first_identity_read_failure_rolls_back_new_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_fstat = jiaoch_trade_cal_authority.os.fstat
    injected = 0

    def failing_first_terminal_fstat(descriptor):
        nonlocal injected
        if injected == 0 and list((tmp_path / "trade_cal_manifests").rglob("*.json")):
            injected += 1
            raise OSError("injected first terminal identity read failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(
        jiaoch_trade_cal_authority.os,
        "fstat",
        failing_first_terminal_fstat,
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: RecordingTransport([_entity(_response_body())]),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert injected == 1
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))


def test_terminal_fsync_cleanup_refuses_to_delete_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel = b"replacement-during-terminal-fsync-failure"
    original_fsync = jiaoch_trade_cal_authority.raw_authority.fsync_directory
    replaced_paths: list[Path] = []

    def replacing_terminal_fsync(directory):
        path = Path(directory)
        manifests = list(path.glob("*.json"))
        if (
            not replaced_paths
            and path.parent.name == "sha256"
            and path.parent.parent.name == "trade_cal_manifests"
            and manifests
        ):
            target = manifests[0]
            target.unlink()
            target.write_bytes(sentinel)
            replaced_paths.append(target)
            raise OSError("injected replacement before directory fsync failure")
        return original_fsync(path)

    monkeypatch.setattr(
        jiaoch_trade_cal_authority.raw_authority,
        "fsync_directory",
        replacing_terminal_fsync,
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: RecordingTransport([_entity(_response_body())]),
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == sentinel


def test_rollback_refuses_to_delete_identity_drifted_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel = b"identity-drifted-after-create"
    replaced_paths: list[Path] = []
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: RecordingTransport([_entity(_response_body())]),
    )

    def replacing_verifier(**kwargs):
        path = Path(kwargs["output_root"]) / kwargs["authority_manifest_relative_path"]
        path.unlink()
        path.write_bytes(sentinel)
        replaced_paths.append(path)
        raise ValueError("verification failed after identity drift")

    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "verify_jiaoch_trade_cal_authority",
        replacing_verifier,
    )

    with pytest.raises(ValueError, match="trade calendar collection failed"):
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == sentinel


def test_final_verifier_failure_rolls_back_only_new_terminal_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport([_entity(_response_body())])
    monkeypatch.setattr(jiaoch_trade_cal_authority, "_transport_factory", lambda: transport)

    def failing_verifier(**_kwargs):
        raise ValueError(f"verification failed {POINTS_TOKEN}")

    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "verify_jiaoch_trade_cal_authority",
        failing_verifier,
    )

    with pytest.raises(ValueError, match="trade calendar collection failed") as caught:
        collect_jiaoch_trade_cal_authority(
            generation=_generation(),
            output_root=tmp_path,
            start_date=START_DATE,
            end_date=END_DATE,
        )

    assert caught.value.__context__ is None
    assert POINTS_TOKEN not in str(caught.value)
    assert len(list((tmp_path / "trade_cal_raw").rglob("*.body"))) == 1
    assert len(list((tmp_path / "trade_cal_attempts").rglob("*.json"))) == 1
    assert not list((tmp_path / "trade_cal_manifests").rglob("*.json"))
