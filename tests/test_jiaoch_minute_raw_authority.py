from __future__ import annotations

import hashlib
import inspect
import json
import os
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


def test_daily_endpoint_is_derived_internally_for_the_same_minute_slot(
    tmp_path: Path,
) -> None:
    publication = publish_jiaoch_minute_raw_attempt(
        output_root=tmp_path,
        raw_body=b'{"code":0,"msg":"ok","data":{"fields":[],"items":[]}}',
        credential=TOKEN,
        credential_slot_id="historical-minute",
        api_name="daily",
        params={"ts_code": "600000.SH", "trade_date": "20260728"},
        fields="ts_code,trade_date,open,high,low,close,vol,amount,ah_vol,ah_amount",
        retrieved_at=RETRIEVED_AT,
        network_route="direct",
        http_status=200,
        body_complete=True,
    )

    attempt = _load_attempt(tmp_path, publication)
    assert attempt["endpoint"] == "https://jiaoch.site/daily"
    assert attempt["request_semantics"]["endpoint"] == "https://jiaoch.site/daily"
    assert "endpoint" not in inspect.signature(publish_jiaoch_minute_raw_attempt).parameters
    assert (
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )["authority_status"]
        == "UNBOUND"
    )


def test_non_200_incomplete_response_is_preserved_as_unbound_attempt(
    tmp_path: Path,
) -> None:
    publication = publish_jiaoch_minute_raw_attempt(
        output_root=tmp_path,
        raw_body=b"gateway timeout",
        credential=TOKEN,
        credential_slot_id="historical-minute",
        api_name="stk_mins",
        params=PARAMS,
        fields="",
        retrieved_at=RETRIEVED_AT,
        network_route="direct",
        http_status=503,
        body_complete=False,
    )

    attempt = _load_attempt(tmp_path, publication)
    assert attempt["http_status"] == 503
    assert attempt["body_complete"] is False
    assert attempt["authority_status"] == "UNBOUND"
    assert (tmp_path / publication["raw_relative_path"]).read_bytes() == b"gateway timeout"


@pytest.mark.parametrize(
    "overrides",
    [
        {"raw_body": bytearray(RAW)},
        {"raw_body": b"x" * (1024 * 1024 + 1)},
        {"credential_slot_id": "../minute"},
        {"api_name": "stk_auction_o"},
        {"network_route": "automatic-fallback"},
        {"http_status": True},
        {"http_status": 99},
        {"http_status": 600},
        {"body_complete": 1},
    ],
)
def test_publisher_strictly_rejects_invalid_body_slot_api_route_or_http_metadata(
    tmp_path: Path,
    overrides: dict,
) -> None:
    arguments = {
        "output_root": tmp_path,
        "raw_body": RAW,
        "credential": TOKEN,
        "credential_slot_id": "historical-minute",
        "api_name": "stk_mins",
        "params": PARAMS,
        "fields": "",
        "retrieved_at": RETRIEVED_AT,
        "network_route": "direct",
        "http_status": 200,
        "body_complete": True,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError):
        publish_jiaoch_minute_raw_attempt(**arguments)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("api_name", "params", "fields"),
    [
        ("stk_mins", {**PARAMS, "freq": "15min"}, ""),
        ("stk_mins", {**PARAMS, "ts_code": "688001.SH"}, ""),
        ("stk_mins", {**PARAMS, "unexpected": "value"}, ""),
        (
            "stk_mins",
            {**PARAMS, "start_date": "20260728", "end_date": "20260728"},
            "",
        ),
        (
            "stk_mins",
            {**PARAMS, "start_date": "2026-7-28 09:00:00"},
            "",
        ),
        (
            "daily",
            {"ts_code": "600000.SH", "trade_date": "20260728"},
            "ts_code,trade_date,open,high,low,close,vol,amount",
        ),
        (
            "daily",
            {"ts_code": "600000.SH", "trade_date": "2026-07-28"},
            "ts_code,trade_date,open,high,low,close,vol,amount,ah_vol,ah_amount",
        ),
        (
            "daily",
            {"ts_code": "600000.SH", "trade_date": "20260728", "extra": "value"},
            "ts_code,trade_date,open,high,low,close,vol,amount,ah_vol,ah_amount",
        ),
    ],
)
def test_request_semantics_are_closed_for_minute_and_daily_calibration(
    tmp_path: Path,
    api_name: str,
    params: dict[str, str],
    fields: str,
) -> None:
    with pytest.raises(ValueError, match="request"):
        publish_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            raw_body=RAW,
            credential=TOKEN,
            credential_slot_id="historical-minute",
            api_name=api_name,
            params=params,
            fields=fields,
            retrieved_at=RETRIEVED_AT,
            network_route="direct",
            http_status=200,
            body_complete=True,
        )

    assert list(tmp_path.iterdir()) == []


def test_made_up_credential_slot_is_rejected_before_writes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="slot"):
        publish_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            raw_body=RAW,
            credential=TOKEN,
            credential_slot_id="made-up-slot",
            api_name="stk_mins",
            params=PARAMS,
            fields="",
            retrieved_at=RETRIEVED_AT,
            network_route="direct",
            http_status=200,
            body_complete=True,
        )

    assert list(tmp_path.iterdir()) == []


def test_attempt_size_is_checked_before_attempt_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(jiaoch_minute_raw_authority, "_MAX_ATTEMPT_BYTES", 1)

    with pytest.raises(ValueError, match="attempt.*size"):
        _publish(tmp_path)

    assert not (tmp_path / "attempts").exists()


def test_publisher_reopens_with_the_offline_verifier_before_return(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict] = []
    original = verify_jiaoch_minute_raw_attempt

    def recording_verifier(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(
        jiaoch_minute_raw_authority,
        "verify_jiaoch_minute_raw_attempt",
        recording_verifier,
    )

    publication = _publish(tmp_path)

    assert calls == [
        {
            "output_root": tmp_path.resolve(),
            "attempt_relative_path": publication["attempt_relative_path"],
            "expected_attempt_sha256": publication["attempt_sha256"],
        }
    ]


def test_json_equivalent_escaped_credential_echo_is_rejected_before_writes(
    tmp_path: Path,
) -> None:
    raw = b'{"echo":"unit-secret\\/path"}'

    with pytest.raises(ValueError, match="credential echo"):
        _publish(tmp_path, raw=raw)

    assert list(tmp_path.iterdir()) == []


def test_excessively_deep_json_is_rejected_by_bounded_echo_scan_before_writes(
    tmp_path: Path,
) -> None:
    raw = b"[" * 1500 + b"0" + b"]" * 1500

    with pytest.raises(ValueError, match="credential echo scan"):
        _publish(tmp_path, raw=raw)

    assert list(tmp_path.iterdir()) == []


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


def test_existing_oversized_raw_conflict_is_rejected_before_unbounded_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    digest = hashlib.sha256(RAW).hexdigest()
    destination = tmp_path / "raw" / "sha256" / digest[:2] / f"{digest}.body"
    destination.parent.mkdir(parents=True)
    with destination.open("wb") as handle:
        handle.seek(1024 * 1024)
        handle.write(b"x")
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == destination:
            raise AssertionError("oversized raw object was read without a bound")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(ValueError, match="raw content-addressed object.*size"):
        _publish(tmp_path)

    assert destination.stat().st_size == 1024 * 1024 + 1
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


def test_offline_verifier_rejects_identity_swap_between_preflight_and_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication = _publish(tmp_path)
    raw_path = (tmp_path / publication["raw_relative_path"]).resolve()
    replacement = tmp_path / "same-bytes-replacement.body"
    replacement.write_bytes(RAW)
    original_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path).resolve() == raw_path:
            os.replace(replacement, raw_path)
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ValueError, match="identity"):
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )
    assert swapped is True


def test_offline_verifier_rejects_hardlink_added_after_final_descriptor_stat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication = _publish(tmp_path)
    raw_path = (tmp_path / publication["raw_relative_path"]).resolve()
    hardlink = tmp_path / "late-raw-hardlink.body"
    original_fstat = os.fstat
    target_fstats = 0

    def linking_fstat(descriptor):
        nonlocal target_fstats
        metadata = original_fstat(descriptor)
        if os.path.samestat(metadata, raw_path.lstat()):
            target_fstats += 1
            if target_fstats == 2:
                os.link(raw_path, hardlink)
        return metadata

    monkeypatch.setattr(os, "fstat", linking_fstat)

    with pytest.raises(ValueError, match="link|reparse|identity"):
        verify_jiaoch_minute_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )
    assert hardlink.exists()


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

    with pytest.raises(ValueError, match="attempt|raw object"):
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
