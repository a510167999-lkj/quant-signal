from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app import jiaoch_minute_raw_authority
from app.jiaoch_minute_raw_authority import (
    publish_jiaoch_minute_raw_attempt,
    verify_jiaoch_minute_raw_attempt,
)


TOKEN = "unit-secret/path"
RETRIEVED_AT = "2026-07-29T17:30:00+08:00"
PARAMS = {
    "ts_code": "600000.SH",
    "freq": "1min",
    "start_date": "2026-07-28 09:00:00",
    "end_date": "2026-07-28 16:00:00",
}
RAW = (
    b'{"code":0,"msg":"ok","data":{"fields":["ts_code","trade_time"],'
    b'"items":[["600000.SH","2026-07-28 09:30:00"]]}}'
)


def _publish(tmp_path: Path, *, raw: bytes = RAW, token: str = TOKEN) -> dict:
    return publish_jiaoch_minute_raw_attempt(
        output_root=tmp_path,
        raw_body=raw,
        credential=token,
        credential_slot_id="historical-minute",
        api_name="stk_mins",
        params=PARAMS,
        fields="",
        retrieved_at=RETRIEVED_AT,
        network_route="direct",
        http_status=200,
        body_complete=True,
    )


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _load_attempt(root: Path, publication: dict) -> dict:
    return json.loads((root / publication["attempt_relative_path"]).read_bytes())


def _write_forged_attempt(root: Path, payload: dict) -> tuple[str, str]:
    raw = _canonical_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"attempts/sha256/{digest[:2]}/{digest}.json"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return relative, digest


def test_same_raw_body_reuses_one_object_but_each_call_has_an_independent_attempt(
    tmp_path: Path,
) -> None:
    first = _publish(tmp_path)
    second = _publish(tmp_path)

    assert first["raw_sha256"] == second["raw_sha256"] == hashlib.sha256(RAW).hexdigest()
    assert first["raw_relative_path"] == second["raw_relative_path"]
    assert first["raw_created"] is True
    assert second["raw_created"] is False
    assert first["attempt_id"] != second["attempt_id"]
    assert first["attempt_sha256"] != second["attempt_sha256"]
    assert first["attempt_relative_path"] != second["attempt_relative_path"]
    assert first["attempt_created"] is second["attempt_created"] is True
    assert len(list((tmp_path / "raw").rglob("*.body"))) == 1
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 2

    first_verified = verify_jiaoch_minute_raw_attempt(
        output_root=tmp_path,
        attempt_relative_path=first["attempt_relative_path"],
        expected_attempt_sha256=first["attempt_sha256"],
    )
    second_verified = verify_jiaoch_minute_raw_attempt(
        output_root=tmp_path,
        attempt_relative_path=second["attempt_relative_path"],
        expected_attempt_sha256=second["attempt_sha256"],
    )
    assert first_verified["attempt_id"] != second_verified["attempt_id"]
    assert first_verified["raw_sha256"] == second_verified["raw_sha256"]
    assert first_verified["authority_status"] == second_verified["authority_status"] == "UNBOUND"


def test_attempt_schema_binds_request_route_raw_object_and_all_safety_false(
    tmp_path: Path,
) -> None:
    publication = _publish(tmp_path)
    attempt = _load_attempt(tmp_path, publication)

    assert set(attempt) == {
        "api_name",
        "attempt_id",
        "authority_status",
        "body_complete",
        "collector_version",
        "credential_echo_check",
        "credential_slot_id",
        "embargo_consumed",
        "endpoint",
        "final_oos_consumed",
        "http_status",
        "production_profile_registered",
        "production_recommendation_eligible",
        "raw_object",
        "raw_response_persisted",
        "request_semantics",
        "request_semantics_sha256",
        "retrieved_at",
        "route",
        "rows_published",
        "schema",
        "source_id",
    }
    assert attempt["schema"] == "jiaoch-minute-raw-attempt/v1"
    assert attempt["source_id"] == "jiaoch"
    assert attempt["credential_slot_id"] == "historical-minute"
    assert attempt["endpoint"] == "https://jiaoch.site/stk_mins"
    assert attempt["api_name"] == "stk_mins"
    assert attempt["request_semantics"] == {
        "api_name": "stk_mins",
        "endpoint": "https://jiaoch.site/stk_mins",
        "fields": "",
        "method": "POST",
        "params": PARAMS,
    }
    assert (
        attempt["request_semantics_sha256"]
        == hashlib.sha256(_canonical_bytes(attempt["request_semantics"])).hexdigest()
    )
    assert attempt["retrieved_at"] == RETRIEVED_AT
    assert attempt["route"] == {
        "network_route": "direct",
        "redirect_policy": "refuse",
        "request_protocol": "tushare-path-per-interface/v1",
        "transport": "app.research_pit_transport.UrllibTushareTransport",
    }
    assert attempt["http_status"] == 200
    assert attempt["body_complete"] is True
    assert attempt["raw_object"] == {
        "bytes": len(RAW),
        "relative_path": publication["raw_relative_path"],
        "sha256": hashlib.sha256(RAW).hexdigest(),
    }
    assert attempt["collector_version"] == "app.jiaoch_minute_raw_authority/1"
    assert attempt["credential_echo_check"] == "PASSED_AT_COLLECTION_NOT_OFFLINE_REPRODUCIBLE"
    assert attempt["raw_response_persisted"] is True
    assert attempt["authority_status"] == "UNBOUND"
    for field in (
        "production_profile_registered",
        "production_recommendation_eligible",
        "embargo_consumed",
        "final_oos_consumed",
        "rows_published",
    ):
        assert attempt[field] is False

    persisted = b"\n".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert TOKEN.encode("utf-8") not in persisted
    assert hashlib.sha256(TOKEN.encode("utf-8")).hexdigest().encode("ascii") not in persisted


@pytest.mark.parametrize(
    ("token", "raw"),
    [
        (TOKEN, json.dumps({"token": TOKEN}).encode("utf-8")),
        (
            "凭据秘密",
            json.dumps({"echo": "凭据秘密"}, ensure_ascii=True).encode("ascii"),
        ),
    ],
)
def test_credential_echo_is_rejected_before_any_raw_or_attempt_write(
    tmp_path: Path,
    token: str,
    raw: bytes,
) -> None:
    with pytest.raises(ValueError, match="credential echo"):
        _publish(tmp_path, raw=raw, token=token)

    assert list(tmp_path.iterdir()) == []


def test_secret_named_request_parameter_is_rejected_before_writes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="secret"):
        publish_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            raw_body=RAW,
            credential=TOKEN,
            credential_slot_id="historical-minute",
            api_name="stk_mins",
            params={**PARAMS, "token": "not-even-the-live-token"},
            fields="",
            retrieved_at=RETRIEVED_AT,
            network_route="direct",
            http_status=200,
            body_complete=True,
        )

    assert list(tmp_path.iterdir()) == []


def test_existing_conflicting_raw_object_is_rejected_without_overwrite(
    tmp_path: Path,
) -> None:
    digest = hashlib.sha256(RAW).hexdigest()
    destination = tmp_path / "raw" / "sha256" / digest[:2] / f"{digest}.body"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"attacker bytes")

    with pytest.raises(ValueError, match="raw content-address"):
        _publish(tmp_path)

    assert destination.read_bytes() == b"attacker bytes"
    assert not (tmp_path / "attempts").exists()


def test_offline_verifier_recomputes_raw_sha_and_size(tmp_path: Path) -> None:
    publication = _publish(tmp_path)
    raw_path = tmp_path / publication["raw_relative_path"]
    raw_path.write_bytes(RAW + b" ")

    with pytest.raises(ValueError, match="raw object"):
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra_field="forged"),
        lambda value: value.update(authority_status="BOUND"),
        lambda value: value.update(embargo_consumed=True),
        lambda value: value["raw_object"].update(bytes=value["raw_object"]["bytes"] + 1),
    ],
)
def test_rehashed_attempt_with_extra_tampered_or_unsafe_fields_is_rejected(
    tmp_path: Path,
    mutation,
) -> None:
    publication = _publish(tmp_path)
    forged = _load_attempt(tmp_path, publication)
    mutation(forged)
    relative, digest = _write_forged_attempt(tmp_path, forged)

    with pytest.raises(ValueError, match="attempt"):
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=relative,
            expected_attempt_sha256=digest,
        )


def test_rehashed_attempt_cannot_escape_raw_root_with_path_traversal(
    tmp_path: Path,
) -> None:
    publication = _publish(tmp_path)
    forged = _load_attempt(tmp_path, publication)
    forged["raw_object"]["relative_path"] = "../../outside.body"
    relative, digest = _write_forged_attempt(tmp_path, forged)

    with pytest.raises(ValueError, match="raw object path"):
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=relative,
            expected_attempt_sha256=digest,
        )


def test_attempt_path_traversal_is_rejected(tmp_path: Path) -> None:
    publication = _publish(tmp_path)

    with pytest.raises(ValueError, match="attempt path"):
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=f"../{publication['attempt_relative_path']}",
            expected_attempt_sha256=publication["attempt_sha256"],
        )


def test_offline_verifier_rejects_simulated_raw_reparse_point(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication = _publish(tmp_path)
    raw_path = (tmp_path / publication["raw_relative_path"]).resolve()
    original = jiaoch_minute_raw_authority._path_is_link_or_reparse

    monkeypatch.setattr(
        jiaoch_minute_raw_authority,
        "_path_is_link_or_reparse",
        lambda path: Path(path).resolve() == raw_path or original(path),
    )

    with pytest.raises(ValueError, match="link|reparse"):
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )


def test_offline_verifier_accepts_no_token_network_or_callback() -> None:
    assert set(inspect.signature(verify_jiaoch_minute_raw_attempt).parameters) == {
        "output_root",
        "attempt_relative_path",
        "expected_attempt_sha256",
    }
