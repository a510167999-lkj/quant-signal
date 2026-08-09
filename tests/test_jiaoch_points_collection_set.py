from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from app import jiaoch_points_collection_set
from app.jiaoch_credential_slots import (
    HISTORICAL_MINUTE_ENV,
    POINTS_PRIMARY_ENV,
    collect_jiaoch_points_collection_set,
    create_jiaoch_credential_generation,
)
from app.jiaoch_points_collection_set import verify_jiaoch_points_collection_set
from app.jiaoch_points_raw_authority import (
    publish_jiaoch_points_raw_attempt,
    verify_jiaoch_points_raw_attempt,
)
from app.research_pit_transport import HttpEntityResponse


POINTS_TOKEN = "points-secret-value"
MINUTE_TOKEN = "minute-secret-value"
TRADE_DATE = date(2026, 7, 28)
UTC_NOW = datetime(2026, 7, 29, 11, 30, 0, 123456, tzinfo=timezone.utc)
RETRIEVED_AT = UTC_NOW.isoformat()
DAILY_BASIC_FIELDS_TEXT = (
    "ts_code,trade_date,turnover_rate,turnover_rate_f,free_share,float_share,total_mv,circ_mv"
)
MONEYFLOW_FIELDS_TEXT = (
    "ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
    "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,"
    "buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,"
    "sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount"
)
DAILY_BASIC_FIELDS = DAILY_BASIC_FIELDS_TEXT.split(",")
MONEYFLOW_FIELDS = MONEYFLOW_FIELDS_TEXT.split(",")


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
        jiaoch_points_collection_set,
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
    api_name: str,
    code: int = 0,
    msg: str = "success",
    fields: list[str] | None = None,
    items: list[list] | None = None,
) -> bytes:
    if api_name == "daily_basic":
        expected_fields = DAILY_BASIC_FIELDS
        expected_items = [
            ["600000.SH", "20260728", 1.2, 1.4, 100.0, 120.0, 2000.0, 1800.0],
            ["000001.SZ", "20260728", 2.2, 2.4, 200.0, 220.0, 3000.0, 2800.0],
        ]
    else:
        expected_fields = MONEYFLOW_FIELDS
        expected_items = [
            [
                "600000.SH",
                "20260728",
                10,
                1.0,
                9,
                0.9,
                8,
                0.8,
                7,
                0.7,
                6,
                0.6,
                5,
                0.5,
                4,
                0.4,
                3,
                0.3,
                4,
                0.4,
            ],
            [
                "000001.SZ",
                "20260728",
                20,
                2.0,
                19,
                1.9,
                18,
                1.8,
                17,
                1.7,
                16,
                1.6,
                15,
                1.5,
                14,
                1.4,
                13,
                1.3,
                4,
                0.4,
            ],
        ]
    payload = {
        "code": code,
        "data": {
            "fields": expected_fields if fields is None else fields,
            "items": expected_items if items is None else items,
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


def _response_bodies() -> tuple[bytes, bytes]:
    return (
        _response_body(api_name="daily_basic"),
        _response_body(api_name="moneyflow"),
    )


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
    outcomes: list[HttpEntityResponse | Exception] | None = None,
):
    bodies = _response_bodies()
    transport = RecordingTransport(
        list(outcomes) if outcomes is not None else [_entity(body) for body in bodies]
    )
    constructions: list[None] = []

    def factory():
        constructions.append(None)
        return transport

    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", factory)
    publication = collect_jiaoch_points_collection_set(
        generation=_generation(),
        output_root=tmp_path,
        trade_date=TRADE_DATE,
        timeout_seconds=7.5,
    )
    return publication, transport, constructions, bodies


def _manifest(root: Path, publication: dict) -> dict:
    return json.loads((root / publication["collection_set_relative_path"]).read_bytes())


def _canonical_bytes(value: dict) -> bytes:
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
    relative = f"points_collection_sets/sha256/{digest[:2]}/{digest}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return relative, digest


def test_public_collection_surface_accepts_only_sealed_generation_and_closed_inputs() -> None:
    assert set(inspect.signature(collect_jiaoch_points_collection_set).parameters) == {
        "generation",
        "output_root",
        "trade_date",
        "timeout_seconds",
    }
    for forbidden in (
        "token",
        "credential",
        "transport",
        "callback",
        "fallback",
        "source",
        "retrieved_at",
        "clock",
    ):
        assert forbidden not in inspect.signature(collect_jiaoch_points_collection_set).parameters
    assert set(inspect.signature(verify_jiaoch_points_collection_set).parameters) == {
        "output_root",
        "collection_set_relative_path",
        "expected_collection_set_sha256",
    }
    private_parameters = inspect.signature(
        jiaoch_points_collection_set._collect_jiaoch_points_collection_set_with_route_credential
    ).parameters
    assert "retrieved_at" not in private_parameters
    assert "clock" not in private_parameters
    assert "callback" not in private_parameters


def test_private_utc_clock_is_called_once_before_transport_and_shared_by_all_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport([_entity(body) for body in _response_bodies()])
    events: list[str] = []

    def clock() -> datetime:
        events.append("clock")
        return UTC_NOW

    def factory() -> RecordingTransport:
        events.append("transport")
        return transport

    monkeypatch.setattr(jiaoch_points_collection_set, "_utc_now", clock)
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", factory)
    publication = collect_jiaoch_points_collection_set(
        generation=_generation(),
        output_root=tmp_path,
        trade_date=TRADE_DATE,
    )
    manifest = _manifest(tmp_path, publication)

    assert events == ["clock", "transport"]
    assert manifest["retrieved_at"] == RETRIEVED_AT
    assert RETRIEVED_AT.endswith("+00:00")
    for binding in manifest["attempts"]:
        attempt = json.loads((tmp_path / binding["attempt_relative_path"]).read_bytes())
        assert attempt["retrieved_at"] == RETRIEVED_AT


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
def test_invalid_private_clock_fails_once_before_transport_construction(
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

    monkeypatch.setattr(jiaoch_points_collection_set, "_utc_now", clock)
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_transport_factory",
        lambda: constructions.append(True),
    )

    with pytest.raises(ValueError, match="collection failed") as caught:
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert caught.value.__context__ is None
    assert clock_calls == 1
    assert constructions == []
    assert list(tmp_path.iterdir()) == []


def test_private_clock_exception_is_normalized_without_secret_or_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    constructions = []

    def exploding_clock():
        raise RuntimeError(POINTS_TOKEN)

    monkeypatch.setattr(jiaoch_points_collection_set, "_utc_now", exploding_clock)
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_transport_factory",
        lambda: constructions.append(True),
    )

    with pytest.raises(ValueError, match="collection failed") as caught:
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert caught.value.__context__ is None
    assert POINTS_TOKEN not in str(caught.value)
    assert constructions == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "invalid_trade_date",
    [
        "20260728",
        datetime(2026, 7, 28, tzinfo=timezone.utc),
        _DerivedDatetime(2026, 7, 28),
    ],
)
def test_trade_date_requires_exact_date_before_clock_or_transport(
    tmp_path: Path,
    monkeypatch,
    invalid_trade_date: object,
) -> None:
    events = []
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_utc_now",
        lambda: events.append("clock"),
    )
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_transport_factory",
        lambda: events.append("transport"),
    )

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=invalid_trade_date,
        )

    assert events == []
    assert list(tmp_path.iterdir()) == []


def test_closed_collection_uses_one_transport_one_points_token_and_exactly_two_direct_posts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, transport, constructions, _ = _collect(tmp_path, monkeypatch)

    assert constructions == [None]
    assert len(transport.calls) == 2
    assert [call["url"] for call in transport.calls] == [
        "http://jiaoch.site/daily_basic",
        "http://jiaoch.site/moneyflow",
    ]
    assert [json.loads(call["body"])["api_name"] for call in transport.calls] == [
        "daily_basic",
        "moneyflow",
    ]
    assert [json.loads(call["body"])["fields"] for call in transport.calls] == [
        DAILY_BASIC_FIELDS_TEXT,
        MONEYFLOW_FIELDS_TEXT,
    ]
    assert all(
        json.loads(call["body"])["params"] == {"trade_date": "20260728", "ts_code": ""}
        for call in transport.calls
    )
    assert all(json.loads(call["body"])["token"] == POINTS_TOKEN for call in transport.calls)
    assert all(MINUTE_TOKEN.encode() not in call["body"] for call in transport.calls)
    assert all(call["timeout_s"] == 7.5 for call in transport.calls)
    assert all(call["max_body_bytes"] == 32 * 1024 * 1024 for call in transport.calls)
    assert all(
        call["headers"]
        == {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "quant-jiaoch-points-collection-set/1",
        }
        for call in transport.calls
    )
    assert publication["collection_set_created"] is True


def test_manifest_binds_v2_attempts_to_one_call_without_claiming_credential_proof(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path, publication)
    verified = verify_jiaoch_points_collection_set(
        output_root=tmp_path,
        collection_set_relative_path=publication["collection_set_relative_path"],
        expected_collection_set_sha256=publication["collection_set_sha256"],
    )

    assert manifest["schema"] == "jiaoch-points-collection-set/v1"
    assert manifest["source_id"] == "jiaoch"
    assert manifest["credential_slot_id"] == "points-primary"
    assert manifest["collection_binding_status"] == "BOUND_TO_SINGLE_CLOSED_RUNTIME_CALL"
    assert manifest["trade_date"] == "2026-07-28"
    assert manifest["runtime_mapping_descriptor"]["generation_id"]
    assert manifest["runtime_mapping_descriptor"]["policy_sha256"]
    assert (
        manifest["runtime_mapping_descriptor"]["credential_proof_status"]
        == "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF"
    )
    assert manifest["credential_binding"] == {
        "basis": "ONE_SEALED_ENTRYPOINT_INVOCATION",
        "credential_proof_claimed": False,
        "same_runtime_credential_used": True,
    }
    assert manifest["producer_binding"]["schema"] == "jiaoch-points-collection-producer/v1"
    assert len(manifest["producer_binding"]["entries"]) >= 4
    assert [item["role"] for item in manifest["attempts"]] == [
        "daily-basic-cross-section",
        "moneyflow-cross-section",
    ]
    assert len({item["attempt_id"] for item in manifest["attempts"]}) == 2
    assert len({item["attempt_sha256"] for item in manifest["attempts"]}) == 2
    for item in manifest["attempts"]:
        attempt = json.loads((tmp_path / item["attempt_relative_path"]).read_bytes())
        assert attempt["schema"] == "jiaoch-points-raw-attempt/v2"
        assert attempt["authority_status"] == "UNBOUND"
        assert attempt["row_authority_status"] == "NOT_GRANTED"
        assert attempt["collection_binding"] == {
            "collection_call_id": manifest["collection_call_id"],
            "generation_id": manifest["runtime_mapping_descriptor"]["generation_id"],
            "policy_sha256": manifest["runtime_mapping_descriptor"]["policy_sha256"],
            "producer_root_sha256": manifest["producer_binding"]["root_sha256"],
            "schema": "jiaoch-points-raw-collection-binding/v1",
        }
        raw_verification = verify_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=item["attempt_relative_path"],
            expected_attempt_sha256=item["attempt_sha256"],
        )
        assert raw_verification["collection_binding"] == attempt["collection_binding"]
    assert verified == {
        "collection_set_sha256": publication["collection_set_sha256"],
        "credential_slot_id": "points-primary",
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "rows_published": False,
        "trade_date": "2026-07-28",
        "verified": True,
    }
    for field in (
        "embargo_consumed",
        "final_oos_consumed",
        "production_profile_registered",
        "production_recommendation_eligible",
        "rows_published",
    ):
        assert manifest[field] is False

    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    for secret in (POINTS_TOKEN, MINUTE_TOKEN):
        assert secret.encode() not in persisted
        assert hashlib.sha256(secret.encode()).hexdigest().encode() not in persisted


def test_legacy_public_raw_publisher_remains_v1_unbound(
    tmp_path: Path,
) -> None:
    publication = publish_jiaoch_points_raw_attempt(
        output_root=tmp_path,
        raw_body=_response_body(api_name="daily_basic"),
        credential=POINTS_TOKEN,
        credential_slot_id="points-primary",
        api_name="daily_basic",
        params={"trade_date": "20260728", "ts_code": ""},
        fields=DAILY_BASIC_FIELDS_TEXT,
        retrieved_at=RETRIEVED_AT,
        network_route="direct",
        http_status=200,
        body_complete=True,
    )
    attempt = json.loads((tmp_path / publication["attempt_relative_path"]).read_bytes())
    verification = verify_jiaoch_points_raw_attempt(
        output_root=tmp_path,
        attempt_relative_path=publication["attempt_relative_path"],
        expected_attempt_sha256=publication["attempt_sha256"],
    )

    assert attempt["schema"] == "jiaoch-points-raw-attempt/v1"
    assert "collection_binding" not in attempt
    assert verification == {
        "attempt_id": publication["attempt_id"],
        "attempt_sha256": publication["attempt_sha256"],
        "authority_status": "UNBOUND",
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
        "raw_bytes": len(_response_body(api_name="daily_basic")),
        "raw_sha256": publication["raw_sha256"],
        "row_authority_status": "NOT_GRANTED",
    }


@pytest.mark.parametrize(
    ("failure_index", "status", "complete", "expected_attempts"),
    [(0, 503, True, 1), (1, 200, False, 2)],
)
def test_bad_http_entity_leaves_partial_attempts_without_manifest(
    tmp_path: Path,
    monkeypatch,
    failure_index: int,
    status: int,
    complete: bool,
    expected_attempts: int,
) -> None:
    bodies = _response_bodies()
    outcomes = [_entity(body) for body in bodies]
    outcomes[failure_index] = _entity(
        f"failure-{failure_index}".encode(),
        status=status,
        complete=complete,
    )
    transport = RecordingTransport(outcomes)
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed") as caught:
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert caught.value.__context__ is None
    assert len(transport.calls) == expected_attempts
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == expected_attempts
    assert not (tmp_path / "points_collection_sets").exists()


def test_transport_failure_has_no_retry_fallback_or_secret_exception_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport(
        [
            _entity(_response_bodies()[0]),
            RuntimeError(f"upstream leaked {POINTS_TOKEN}"),
        ]
    )
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed") as caught:
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert caught.value.__context__ is None
    assert POINTS_TOKEN not in str(caught.value)
    assert len(transport.calls) == 2
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 1
    assert not (tmp_path / "points_collection_sets").exists()


def test_final_manifest_verification_failure_rolls_back_manifest_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport([_entity(body) for body in _response_bodies()])
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", lambda: transport)

    def failing_verifier(**_kwargs):
        raise ValueError(f"verification failed {POINTS_TOKEN}")

    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "verify_jiaoch_points_collection_set",
        failing_verifier,
    )

    with pytest.raises(ValueError, match="collection failed") as caught:
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert caught.value.__context__ is None
    assert POINTS_TOKEN not in str(caught.value)
    assert len(transport.calls) == 2
    assert len(list((tmp_path / "raw").rglob("*.body"))) == 2
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 2
    assert not list((tmp_path / "points_collection_sets").rglob("*.json"))


def test_producer_drift_is_rejected_before_manifest_create(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stable_binding = jiaoch_points_collection_set._producer_binding()
    producer_calls = 0

    def drifting_binding():
        nonlocal producer_calls
        producer_calls += 1
        if producer_calls <= 2:
            return stable_binding
        return {**stable_binding, "root_sha256": "0" * 64}

    write_labels = []
    original_writer = jiaoch_points_collection_set.raw_authority._write_create_only

    def recording_writer(path, raw, *, label, reuse_identical):
        write_labels.append(label)
        return original_writer(
            path,
            raw,
            label=label,
            reuse_identical=reuse_identical,
        )

    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_producer_binding",
        drifting_binding,
    )
    monkeypatch.setattr(
        jiaoch_points_collection_set.raw_authority,
        "_write_create_only",
        recording_writer,
    )
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_transport_factory",
        lambda: RecordingTransport([_entity(body) for body in _response_bodies()]),
    )

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert producer_calls == 3
    assert "Jiaoch points collection set" not in write_labels
    assert not list((tmp_path / "points_collection_sets").rglob("*.json"))


def test_manifest_content_address_conflict_does_not_delete_existing_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel = b"preexisting-content-address-conflict"
    conflicting_paths: list[Path] = []
    original_writer = jiaoch_points_collection_set.raw_authority._write_create_only

    def conflicting_writer(path, raw, *, label, reuse_identical):
        if label == "Jiaoch points collection set":
            path.write_bytes(sentinel)
            conflicting_paths.append(path)
            raise ValueError("content-address conflict")
        return original_writer(
            path,
            raw,
            label=label,
            reuse_identical=reuse_identical,
        )

    monkeypatch.setattr(
        jiaoch_points_collection_set.raw_authority,
        "_write_create_only",
        conflicting_writer,
    )
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_transport_factory",
        lambda: RecordingTransport([_entity(body) for body in _response_bodies()]),
    )

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert len(conflicting_paths) == 1
    assert conflicting_paths[0].read_bytes() == sentinel


def test_rollback_refuses_to_delete_identity_drifted_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel = b"identity-drifted-after-create"
    replaced_paths: list[Path] = []
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_transport_factory",
        lambda: RecordingTransport([_entity(body) for body in _response_bodies()]),
    )

    def replacing_verifier(**kwargs):
        path = Path(kwargs["output_root"]) / kwargs["collection_set_relative_path"]
        path.unlink()
        path.write_bytes(sentinel)
        replaced_paths.append(path)
        raise ValueError("verification failed after identity drift")

    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "verify_jiaoch_points_collection_set",
        replacing_verifier,
    )

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == sentinel


def test_credential_echo_on_second_response_is_rejected_before_attempt_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport(
        [
            _entity(_response_bodies()[0]),
            _entity(json.dumps({"code": 0, "echo": POINTS_TOKEN}).encode()),
        ]
    )
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert len(transport.calls) == 2
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 1
    assert not (tmp_path / "points_collection_sets").exists()
    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert POINTS_TOKEN.encode() not in persisted


@pytest.mark.parametrize(
    "bad_body",
    [
        json.dumps(
            {"code": -1, "data": None, "msg": "permission denied"},
            separators=(",", ":"),
        ).encode(),
        _response_body(
            api_name="moneyflow",
            fields=list(reversed(MONEYFLOW_FIELDS)),
        ),
        _response_body(
            api_name="moneyflow",
            items=[["600000.SH", "20260728"]],
        ),
        _response_body(
            api_name="moneyflow",
            items=[
                [
                    "600000.SH",
                    "20260727",
                    *([1] * (len(MONEYFLOW_FIELDS) - 2)),
                ]
            ],
        ),
        _response_body(api_name="moneyflow", items=[]),
    ],
)
def test_provider_status_interface_schema_or_row_integrity_failure_keeps_no_manifest(
    tmp_path: Path,
    monkeypatch,
    bad_body: bytes,
) -> None:
    transport = RecordingTransport([_entity(_response_bodies()[0]), _entity(bad_body)])
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert len(transport.calls) == 2
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 2
    assert not (tmp_path / "points_collection_sets").exists()


def test_duplicate_security_rows_fail_integrity_without_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    duplicate = [
        [
            "600000.SH",
            "20260728",
            *([1] * (len(MONEYFLOW_FIELDS) - 2)),
        ],
        [
            "600000.SH",
            "20260728",
            *([2] * (len(MONEYFLOW_FIELDS) - 2)),
        ],
    ]
    transport = RecordingTransport(
        [
            _entity(_response_bodies()[0]),
            _entity(_response_body(api_name="moneyflow", items=duplicate)),
        ]
    )
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert not (tmp_path / "points_collection_sets").exists()


def test_oversized_response_is_bounded_before_raw_attempt_or_manifest_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport([_entity(b"x" * (32 * 1024 * 1024 + 1))])
    monkeypatch.setattr(jiaoch_points_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_points_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            trade_date=TRADE_DATE,
        )

    assert len(transport.calls) == 1
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "attempts").exists()
    assert not (tmp_path / "points_collection_sets").exists()


def test_offline_verifier_never_constructs_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_transport_factory",
        lambda: pytest.fail("offline verifier attempted network"),
    )

    assert (
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )["verified"]
        is True
    )


def test_offline_verifier_rejects_manifest_tamper_path_traversal_and_hardlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    manifest_path = tmp_path / publication["collection_set_relative_path"]
    original = manifest_path.read_bytes()

    manifest_path.write_bytes(original + b"\n")
    with pytest.raises(ValueError):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )
    manifest_path.write_bytes(original)

    with pytest.raises(ValueError, match="path"):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=f"../{publication['collection_set_relative_path']}",
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )

    hardlink_source = tmp_path / "hardlink-source.json"
    hardlink_source.write_bytes(original)
    manifest_path.unlink()
    try:
        os.link(hardlink_source, manifest_path)
    except OSError:
        pytest.skip("filesystem does not support hard links")
    with pytest.raises(ValueError, match="link|reparse|identity"):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )


@pytest.mark.parametrize("kind", ["raw", "attempt"])
def test_offline_verifier_rejects_hardlinked_attempt_or_raw(
    tmp_path: Path,
    monkeypatch,
    kind: str,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path, publication)
    binding = manifest["attempts"][0]
    relative_key = "raw_relative_path" if kind == "raw" else "attempt_relative_path"
    target = tmp_path / binding[relative_key]
    source = tmp_path / f"{kind}-hardlink-source"
    source.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.link(source, target)
    except OSError:
        pytest.skip("filesystem does not support hard links")

    with pytest.raises(ValueError, match="link|reparse|identity"):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )


def test_offline_verifier_rejects_simulated_manifest_reparse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    manifest_path = (tmp_path / publication["collection_set_relative_path"]).resolve()
    original = jiaoch_points_collection_set._path_is_link_or_reparse
    monkeypatch.setattr(
        jiaoch_points_collection_set,
        "_path_is_link_or_reparse",
        lambda path: Path(path).resolve() == manifest_path or original(path),
    )

    with pytest.raises(ValueError, match="link|reparse|identity"):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )


def test_offline_verifier_rejects_manifest_hardlink_added_after_final_stat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    manifest_path = (tmp_path / publication["collection_set_relative_path"]).resolve()
    hardlink = tmp_path / "late-manifest-hardlink.json"
    original_fstat = os.fstat
    target_fstats = 0

    def linking_fstat(descriptor):
        nonlocal target_fstats
        metadata = original_fstat(descriptor)
        if os.path.samestat(metadata, manifest_path.lstat()):
            target_fstats += 1
            if target_fstats == 2:
                os.link(manifest_path, hardlink)
        return metadata

    monkeypatch.setattr(os, "fstat", linking_fstat)

    with pytest.raises(ValueError, match="link|reparse|identity"):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )
    assert hardlink.exists()


def test_rehashed_attempt_order_swap_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    forged = _manifest(tmp_path, publication)
    forged["attempts"][0], forged["attempts"][1] = (
        forged["attempts"][1],
        forged["attempts"][0],
    )
    relative, digest = _write_forged_manifest(tmp_path, forged)

    with pytest.raises(ValueError, match="attempt"):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=relative,
            expected_collection_set_sha256=digest,
        )


def test_attempt_from_another_runtime_call_cannot_be_spliced_into_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first, _, _, _ = _collect(tmp_path, monkeypatch)
    second, _, _, _ = _collect(tmp_path, monkeypatch)
    forged = _manifest(tmp_path, first)
    forged["attempts"][1] = _manifest(tmp_path, second)["attempts"][1]
    relative, digest = _write_forged_manifest(tmp_path, forged)

    with pytest.raises(ValueError, match="attempt"):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=relative,
            expected_collection_set_sha256=digest,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("generation_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "attempt|generation"),
        ("policy_sha256", "0" * 64, "policy"),
    ],
)
def test_rehashed_runtime_descriptor_drift_is_rejected(
    tmp_path: Path,
    monkeypatch,
    field: str,
    replacement: str,
    message: str,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    forged = _manifest(tmp_path, publication)
    forged["runtime_mapping_descriptor"][field] = replacement
    relative, digest = _write_forged_manifest(tmp_path, forged)

    with pytest.raises(ValueError, match=message):
        verify_jiaoch_points_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=relative,
            expected_collection_set_sha256=digest,
        )
