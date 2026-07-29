from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from app import jiaoch_minute_collection_set
from app.jiaoch_credential_slots import (
    HISTORICAL_MINUTE_ENV,
    POINTS_PRIMARY_ENV,
    collect_jiaoch_historical_minute_collection_set,
    create_jiaoch_credential_generation,
)
from app.jiaoch_minute_collection_set import (
    reconcile_verified_jiaoch_minute_collection_set,
    verify_jiaoch_minute_collection_set,
)
from app.jiaoch_minute_reconciliation import reconcile_jiaoch_minute_raw_bodies_unbound
from app.research_pit_transport import HttpEntityResponse


POINTS_TOKEN = "points-secret-value"
MINUTE_TOKEN = "minute-secret-value"
SESSION = date(2026, 7, 28)
TS_CODE = "600000.SH"
RETRIEVED_AT = "2026-07-29T19:30:00+08:00"
MINUTE_FIELDS = [
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
]
DAILY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "ah_vol",
    "ah_amount",
]


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


def _generation():
    return create_jiaoch_credential_generation(
        environment_snapshot={
            POINTS_PRIMARY_ENV: POINTS_TOKEN,
            HISTORICAL_MINUTE_ENV: MINUTE_TOKEN,
        }
    )


def _minute_labels() -> list[str]:
    labels = [f"{SESSION.isoformat()} 09:30:00"]
    cursor = datetime.combine(SESSION, time(9, 31))
    while cursor.time() <= time(11, 30):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=1)
    cursor = datetime.combine(SESSION, time(13, 1))
    while cursor.time() <= time(15, 0):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=1)
    assert len(labels) == 241
    return labels


def _five_minute_labels() -> list[str]:
    labels = [f"{SESSION.isoformat()} 09:30:00"]
    cursor = datetime.combine(SESSION, time(9, 35))
    while cursor.time() <= time(11, 30):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=5)
    cursor = datetime.combine(SESSION, time(13, 5))
    while cursor.time() <= time(15, 0):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=5)
    assert len(labels) == 49
    return labels


def _response_bodies(*, ts_code: str = TS_CODE) -> tuple[bytes, bytes, bytes]:
    one_minute = {
        "code": 0,
        "data": {
            "fields": MINUTE_FIELDS,
            "items": [
                [ts_code, label, 10.0, 10.0, 10.1, 9.9, 100, 1000.0]
                for label in _minute_labels()
            ],
        },
        "msg": "success",
    }
    five_minute = {
        "code": 0,
        "data": {
            "fields": MINUTE_FIELDS,
            "items": [
                [ts_code, _five_minute_labels()[0], 10.0, 10.0, 10.1, 9.9, 100, 1000.0],
                *[
                    [ts_code, label, 10.0, 10.0, 10.1, 9.9, 500, 5000.0]
                    for label in _five_minute_labels()[1:]
                ],
            ],
        },
        "msg": "success",
    }
    daily = {
        "code": 0,
        "data": {
            "fields": DAILY_FIELDS,
            "items": [
                [
                    ts_code,
                    SESSION.strftime("%Y%m%d"),
                    10.0,
                    10.1,
                    9.9,
                    10.0,
                    241.0,
                    241.0,
                    None,
                    None,
                ]
            ],
        },
        "msg": "success",
    }
    return tuple(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        for payload in (one_minute, five_minute, daily)
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
    ts_code: str = TS_CODE,
):
    bodies = _response_bodies(ts_code=ts_code)
    transport = RecordingTransport(
        list(outcomes) if outcomes is not None else [_entity(body) for body in bodies]
    )
    constructions: list[None] = []

    def factory():
        constructions.append(None)
        return transport

    monkeypatch.setattr(jiaoch_minute_collection_set, "_transport_factory", factory)
    publication = collect_jiaoch_historical_minute_collection_set(
        generation=_generation(),
        output_root=tmp_path,
        requested_ts_code=ts_code,
        execution_session=SESSION,
        retrieved_at=RETRIEVED_AT,
        timeout_seconds=7.5,
    )
    return publication, transport, constructions, bodies


def _manifest(root: Path, publication: dict) -> dict:
    return json.loads((root / publication["collection_set_relative_path"]).read_bytes())


def test_public_collection_surface_accepts_generation_but_no_secret_or_transport() -> None:
    assert set(
        inspect.signature(collect_jiaoch_historical_minute_collection_set).parameters
    ) == {
        "generation",
        "output_root",
        "requested_ts_code",
        "execution_session",
        "retrieved_at",
        "timeout_seconds",
    }
    for forbidden in ("token", "credential", "transport", "callback", "fallback", "source"):
        assert forbidden not in inspect.signature(
            collect_jiaoch_historical_minute_collection_set
        ).parameters
    assert set(inspect.signature(verify_jiaoch_minute_collection_set).parameters) == {
        "output_root",
        "collection_set_relative_path",
        "expected_collection_set_sha256",
    }


def test_closed_collection_uses_one_transport_one_minute_token_and_exactly_three_posts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, transport, constructions, _ = _collect(tmp_path, monkeypatch)

    assert constructions == [None]
    assert len(transport.calls) == 3
    assert [call["url"] for call in transport.calls] == [
        "https://jiaoch.site/stk_mins",
        "https://jiaoch.site/stk_mins",
        "https://jiaoch.site/daily",
    ]
    assert [json.loads(call["body"])["api_name"] for call in transport.calls] == [
        "stk_mins",
        "stk_mins",
        "daily",
    ]
    assert [json.loads(call["body"])["params"].get("freq") for call in transport.calls] == [
        "1min",
        "5min",
        None,
    ]
    assert all(json.loads(call["body"])["token"] == MINUTE_TOKEN for call in transport.calls)
    assert all(POINTS_TOKEN.encode() not in call["body"] for call in transport.calls)
    assert all(call["timeout_s"] == 7.5 for call in transport.calls)
    assert all(call["max_body_bytes"] == 1024 * 1024 for call in transport.calls)
    assert all(
        call["headers"]
        == {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "quant-jiaoch-minute-collection-set/1",
        }
        for call in transport.calls
    )
    assert json.loads(transport.calls[0]["body"])["params"] == {
        "end_date": "2026-07-28 16:00:00",
        "freq": "1min",
        "start_date": "2026-07-28 09:00:00",
        "ts_code": TS_CODE,
    }
    assert json.loads(transport.calls[2]["body"]) == {
        "api_name": "daily",
        "fields": "ts_code,trade_date,open,high,low,close,vol,amount,ah_vol,ah_amount",
        "params": {"trade_date": "20260728", "ts_code": TS_CODE},
        "token": MINUTE_TOKEN,
    }
    assert publication["collection_set_created"] is True


def test_manifest_binds_attempts_generation_and_producer_without_claiming_credential_proof(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path, publication)
    verified = verify_jiaoch_minute_collection_set(
        output_root=tmp_path,
        collection_set_relative_path=publication["collection_set_relative_path"],
        expected_collection_set_sha256=publication["collection_set_sha256"],
    )

    assert manifest["schema"] == "jiaoch-minute-collection-set/v1"
    assert manifest["source_id"] == "jiaoch"
    assert manifest["credential_slot_id"] == "historical-minute"
    assert manifest["collection_binding_status"] == "BOUND_TO_SINGLE_CLOSED_RUNTIME_CALL"
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
    assert manifest["producer_binding"]["schema"] == "jiaoch-minute-collection-producer/v1"
    assert len(manifest["producer_binding"]["entries"]) >= 4
    assert len(manifest["attempts"]) == 3
    assert [item["role"] for item in manifest["attempts"]] == [
        "one-minute",
        "five-minute",
        "daily-calibration",
    ]
    assert len({item["attempt_sha256"] for item in manifest["attempts"]}) == 3
    assert verified["verified"] is True
    assert verified["collection_set_sha256"] == publication["collection_set_sha256"]
    assert verified["credential_slot_id"] == "historical-minute"
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


@pytest.mark.parametrize(
    ("failure_index", "complete", "expected_attempts"),
    [(1, True, 2), (2, False, 3)],
)
def test_second_or_third_bad_http_entity_leaves_partial_attempts_without_manifest(
    tmp_path: Path,
    monkeypatch,
    failure_index: int,
    complete: bool,
    expected_attempts: int,
) -> None:
    bodies = _response_bodies()
    outcomes = [_entity(body) for body in bodies]
    outcomes[failure_index] = _entity(
        f"failure-{failure_index}".encode(),
        status=503 if complete else 200,
        complete=complete,
    )
    transport = RecordingTransport(outcomes)
    monkeypatch.setattr(jiaoch_minute_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed") as caught:
        collect_jiaoch_historical_minute_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            requested_ts_code=TS_CODE,
            execution_session=SESSION,
            retrieved_at=RETRIEVED_AT,
        )

    assert caught.value.__context__ is None
    assert len(transport.calls) == expected_attempts
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == expected_attempts
    assert not (tmp_path / "collection_sets").exists()


def test_transport_failure_has_no_retry_or_fallback_and_no_secret_exception_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bodies = _response_bodies()
    transport = RecordingTransport(
        [_entity(bodies[0]), RuntimeError(f"upstream leaked {MINUTE_TOKEN}")]
    )
    monkeypatch.setattr(jiaoch_minute_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed") as caught:
        collect_jiaoch_historical_minute_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            requested_ts_code=TS_CODE,
            execution_session=SESSION,
            retrieved_at=RETRIEVED_AT,
        )

    assert caught.value.__context__ is None
    assert MINUTE_TOKEN not in str(caught.value)
    assert len(transport.calls) == 2
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 1
    assert not (tmp_path / "collection_sets").exists()


def test_credential_echo_on_second_response_is_rejected_before_its_attempt_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bodies = _response_bodies()
    transport = RecordingTransport(
        [
            _entity(bodies[0]),
            _entity(json.dumps({"code": 0, "echo": MINUTE_TOKEN}).encode()),
        ]
    )
    monkeypatch.setattr(jiaoch_minute_collection_set, "_transport_factory", lambda: transport)

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_historical_minute_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            requested_ts_code=TS_CODE,
            execution_session=SESSION,
            retrieved_at=RETRIEVED_AT,
        )

    assert len(transport.calls) == 2
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 1
    assert not (tmp_path / "collection_sets").exists()
    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert MINUTE_TOKEN.encode() not in persisted


@pytest.mark.parametrize("ts_code", ["688001.SH", "830001.BJ", "510300.SH"])
def test_star_bse_and_fund_codes_reject_before_transport_construction(
    tmp_path: Path,
    monkeypatch,
    ts_code: str,
) -> None:
    constructions = []
    monkeypatch.setattr(
        jiaoch_minute_collection_set,
        "_transport_factory",
        lambda: constructions.append(True),
    )

    with pytest.raises(ValueError, match="collection failed"):
        collect_jiaoch_historical_minute_collection_set(
            generation=_generation(),
            output_root=tmp_path,
            requested_ts_code=ts_code,
            execution_session=SESSION,
            retrieved_at=RETRIEVED_AT,
        )

    assert constructions == []
    assert list(tmp_path.iterdir()) == []


def test_offline_verifier_never_constructs_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    monkeypatch.setattr(
        jiaoch_minute_collection_set,
        "_transport_factory",
        lambda: pytest.fail("offline verifier attempted network"),
    )

    assert (
        verify_jiaoch_minute_collection_set(
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
        verify_jiaoch_minute_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )
    manifest_path.write_bytes(original)

    with pytest.raises(ValueError, match="path"):
        verify_jiaoch_minute_collection_set(
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
    with pytest.raises(ValueError, match="link|reparse"):
        verify_jiaoch_minute_collection_set(
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
    original = jiaoch_minute_collection_set._path_is_link_or_reparse
    monkeypatch.setattr(
        jiaoch_minute_collection_set,
        "_path_is_link_or_reparse",
        lambda path: Path(path).resolve() == manifest_path or original(path),
    )

    with pytest.raises(ValueError, match="link|reparse"):
        verify_jiaoch_minute_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=publication["collection_set_relative_path"],
            expected_collection_set_sha256=publication["collection_set_sha256"],
        )


def test_rehashed_attempt_request_swap_is_rejected_by_collection_verifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, _ = _collect(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path, publication)
    manifest["attempts"][0], manifest["attempts"][1] = (
        manifest["attempts"][1],
        manifest["attempts"][0],
    )
    forged = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    digest = hashlib.sha256(forged).hexdigest()
    relative = f"collection_sets/sha256/{digest[:2]}/{digest}.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(forged)

    with pytest.raises(ValueError, match="attempt"):
        verify_jiaoch_minute_collection_set(
            output_root=tmp_path,
            collection_set_relative_path=relative,
            expected_collection_set_sha256=digest,
        )


def test_verified_adapter_upgrades_only_collection_status_and_keeps_capacity_false(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication, _, _, bodies = _collect(tmp_path, monkeypatch)
    legacy = reconcile_jiaoch_minute_raw_bodies_unbound(
        one_minute_response_body=bodies[0],
        five_minute_response_body=bodies[1],
        daily_response_body=bodies[2],
        requested_ts_code=TS_CODE,
        execution_session=SESSION,
        stable_security_id="security-600000",
        pit_security_descriptor_sha256="a" * 64,
        board="SSE_MAIN",
    )
    bound = reconcile_verified_jiaoch_minute_collection_set(
        output_root=tmp_path,
        collection_set_relative_path=publication["collection_set_relative_path"],
        expected_collection_set_sha256=publication["collection_set_sha256"],
        stable_security_id="security-600000",
        pit_security_descriptor_sha256="a" * 64,
        board="SSE_MAIN",
    )

    assert legacy.collection_set_status == "NOT_PROVIDED"
    assert legacy.collection_set_sha256 is None
    assert legacy.source_authority_status == "UNBOUND"
    assert bound.collection_set_status == "VERIFIED"
    assert bound.collection_set_sha256 == publication["collection_set_sha256"]
    assert bound.source_authority_status == "COLLECTION_SET_VERIFIED"
    assert bound.minute_amount_authority_status == "UNBOUND"
    assert bound.all_capacity_eligible is False
    assert all(row.eligible_for_capacity is False for row in bound.one_minute_rows)
    assert all(bucket.eligible_for_capacity is False for bucket in bound.five_minute_buckets)
    assert bound.production_recommendation_eligible is False
    assert bound.embargo_consumed is False
    assert bound.final_oos_consumed is False
