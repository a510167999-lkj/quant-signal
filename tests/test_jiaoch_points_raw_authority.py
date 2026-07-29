from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from app import jiaoch_points_raw_authority
from app.jiaoch_points_raw_authority import (
    publish_jiaoch_points_raw_attempt,
    verify_jiaoch_points_raw_attempt,
)


TOKEN = "points-unit-secret/path"
RETRIEVED_AT = "2026-07-29T17:30:00+08:00"
PARAMS = {"trade_date": "20260728", "ts_code": ""}
DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,turnover_rate,turnover_rate_f,free_share,float_share,total_mv,circ_mv"
)
MONEYFLOW_FIELDS = (
    "ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
    "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,"
    "buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,"
    "sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount"
)
RAW = (
    b'{"code":0,"msg":"ok","data":{"fields":["ts_code","trade_date"],'
    b'"items":[["600000.SH","20260728"]]}}'
)


def _publish(
    tmp_path: Path,
    *,
    raw: bytes = RAW,
    token: str = TOKEN,
    api_name: str = "daily_basic",
    fields: str = DAILY_BASIC_FIELDS,
    http_status: int = 200,
    body_complete: bool = True,
) -> dict:
    return publish_jiaoch_points_raw_attempt(
        output_root=tmp_path,
        raw_body=raw,
        credential=token,
        credential_slot_id="points-primary",
        api_name=api_name,
        params=PARAMS,
        fields=fields,
        retrieved_at=RETRIEVED_AT,
        network_route="direct",
        http_status=http_status,
        body_complete=body_complete,
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


def test_same_raw_body_reuses_cas_but_each_call_has_an_independent_uuid_attempt(
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
    assert first["attempt_created"] is second["attempt_created"] is True
    assert len(list((tmp_path / "raw").rglob("*.body"))) == 1
    assert len(list((tmp_path / "attempts").rglob("*.json"))) == 2

    verified = verify_jiaoch_points_raw_attempt(
        output_root=tmp_path,
        attempt_relative_path=first["attempt_relative_path"],
        expected_attempt_sha256=first["attempt_sha256"],
    )
    assert verified == {
        "attempt_id": first["attempt_id"],
        "attempt_sha256": first["attempt_sha256"],
        "authority_status": "UNBOUND",
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
        "raw_bytes": len(RAW),
        "raw_sha256": first["raw_sha256"],
        "row_authority_status": "NOT_GRANTED",
    }


@pytest.mark.parametrize(
    ("api_name", "fields", "endpoint"),
    [
        ("daily_basic", DAILY_BASIC_FIELDS, "https://jiaoch.site/daily_basic"),
        ("moneyflow", MONEYFLOW_FIELDS, "https://jiaoch.site/moneyflow"),
    ],
)
def test_attempt_binds_closed_cross_section_request_and_all_safety_false(
    tmp_path: Path,
    api_name: str,
    fields: str,
    endpoint: str,
) -> None:
    publication = _publish(tmp_path, api_name=api_name, fields=fields)
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
        "row_authority_status",
        "rows_published",
        "schema",
        "source_id",
        "source_semantics",
    }
    assert attempt["schema"] == "jiaoch-points-raw-attempt/v1"
    assert attempt["source_id"] == "jiaoch"
    assert attempt["credential_slot_id"] == "points-primary"
    assert attempt["api_name"] == api_name
    assert attempt["endpoint"] == endpoint
    assert attempt["request_semantics"] == {
        "api_name": api_name,
        "endpoint": endpoint,
        "fields": fields,
        "method": "POST",
        "params": PARAMS,
    }
    assert (
        attempt["request_semantics_sha256"]
        == hashlib.sha256(_canonical_bytes(attempt["request_semantics"])).hexdigest()
    )
    assert attempt["route"] == {
        "network_route": "direct",
        "redirect_policy": "refuse",
        "request_protocol": "tushare-path-per-interface/v1",
        "transport": "app.research_pit_transport.UrllibTushareTransport",
    }
    assert attempt["raw_object"] == {
        "bytes": len(RAW),
        "relative_path": publication["raw_relative_path"],
        "sha256": hashlib.sha256(RAW).hexdigest(),
    }
    assert attempt["collector_version"] == "app.jiaoch_points_raw_authority/1"
    assert attempt["credential_echo_check"] == "PASSED_AT_COLLECTION_NOT_OFFLINE_REPRODUCIBLE"
    assert attempt["raw_response_persisted"] is True
    assert attempt["authority_status"] == "UNBOUND"
    assert attempt["row_authority_status"] == "NOT_GRANTED"
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
    assert b"credential_fingerprint" not in persisted
    assert b"token_hash" not in persisted


def test_official_points_and_interface_semantics_are_frozen_without_overclaiming(
    tmp_path: Path,
) -> None:
    daily = _load_attempt(tmp_path, _publish(tmp_path))
    moneyflow = _load_attempt(
        tmp_path,
        _publish(tmp_path, api_name="moneyflow", fields=MONEYFLOW_FIELDS),
    )

    assert daily["source_semantics"] == {
        "availability": {
            "documented_update_window": "15:00-17:00",
            "timezone": "Asia/Shanghai",
        },
        "minimum_points": 2000,
        "permission_model": "points-interface",
    }
    assert moneyflow["source_semantics"] == {
        "amount_unit": "ten_thousand_cny",
        "minimum_points": 2000,
        "net_mf_semantics": "source_native_l2_active_buy_sell_not_bucket_recomputed",
        "permission_model": "points-interface",
        "volume_unit": "lot",
    }


def test_moneyflow_bucket_arithmetic_is_not_a_raw_authority_hard_check(
    tmp_path: Path,
) -> None:
    inconsistent_raw = (
        b'{"code":0,"msg":"ok","data":{"fields":["buy_sm_vol","sell_sm_vol",'
        b'"net_mf_vol"],"items":[[100,5,-999999]]}}'
    )

    publication = _publish(
        tmp_path,
        raw=inconsistent_raw,
        api_name="moneyflow",
        fields=MONEYFLOW_FIELDS,
    )

    attempt = _load_attempt(tmp_path, publication)
    assert attempt["authority_status"] == "UNBOUND"
    assert attempt["row_authority_status"] == "NOT_GRANTED"
    assert (tmp_path / publication["raw_relative_path"]).read_bytes() == inconsistent_raw


def test_non_200_or_incomplete_body_is_retained_but_never_grants_authority(
    tmp_path: Path,
) -> None:
    publication = _publish(
        tmp_path,
        raw=b"gateway timeout",
        http_status=503,
        body_complete=False,
    )

    attempt = _load_attempt(tmp_path, publication)
    assert attempt["http_status"] == 503
    assert attempt["body_complete"] is False
    assert attempt["authority_status"] == "UNBOUND"
    assert attempt["row_authority_status"] == "NOT_GRANTED"
    assert attempt["rows_published"] is False
    assert attempt["production_recommendation_eligible"] is False


@pytest.mark.parametrize(
    ("api_name", "params", "fields"),
    [
        ("daily_basic", {"trade_date": "2026-07-28", "ts_code": ""}, DAILY_BASIC_FIELDS),
        ("daily_basic", {"trade_date": "20260728"}, DAILY_BASIC_FIELDS),
        ("daily_basic", {"trade_date": "20260728", "ts_code": "600000.SH"}, DAILY_BASIC_FIELDS),
        ("daily_basic", {"start_date": "20260728", "end_date": "20260728"}, DAILY_BASIC_FIELDS),
        ("daily_basic", PARAMS, DAILY_BASIC_FIELDS.replace(",free_share", "")),
        ("moneyflow", PARAMS, MONEYFLOW_FIELDS.replace(",net_mf_amount", "")),
        ("moneyflow", {"trade_date": "20260728", "token": "fake"}, MONEYFLOW_FIELDS),
        ("daily", PARAMS, DAILY_BASIC_FIELDS),
    ],
)
def test_only_single_trade_date_full_cross_section_and_fixed_fields_are_allowed(
    tmp_path: Path,
    api_name: str,
    params: dict[str, str],
    fields: str,
) -> None:
    with pytest.raises(ValueError, match="request|api_name|secret"):
        publish_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            raw_body=RAW,
            credential=TOKEN,
            credential_slot_id="points-primary",
            api_name=api_name,
            params=params,
            fields=fields,
            retrieved_at=RETRIEVED_AT,
            network_route="direct",
            http_status=200,
            body_complete=True,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"raw_body": bytearray(RAW)},
        {"raw_body": b"x" * (1024 * 1024 + 1)},
        {"credential_slot_id": "historical-minute"},
        {"credential_slot_id": "../points"},
        {"network_route": "automatic-fallback"},
        {"http_status": True},
        {"http_status": 99},
        {"http_status": 600},
        {"body_complete": 1},
    ],
)
def test_invalid_body_slot_route_or_http_metadata_is_rejected_before_writes(
    tmp_path: Path,
    overrides: dict,
) -> None:
    arguments = {
        "output_root": tmp_path,
        "raw_body": RAW,
        "credential": TOKEN,
        "credential_slot_id": "points-primary",
        "api_name": "daily_basic",
        "params": PARAMS,
        "fields": DAILY_BASIC_FIELDS,
        "retrieved_at": RETRIEVED_AT,
        "network_route": "direct",
        "http_status": 200,
        "body_complete": True,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError):
        publish_jiaoch_points_raw_attempt(**arguments)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("token", "raw"),
    [
        (TOKEN, json.dumps({"token": TOKEN}).encode("utf-8")),
        (
            "凭据秘密",
            json.dumps({"echo": "凭据秘密"}, ensure_ascii=True).encode("ascii"),
        ),
        (TOKEN, b'{"echo":"points-unit-secret\\/path"}'),
    ],
)
def test_credential_echo_is_rejected_before_any_write(
    tmp_path: Path,
    token: str,
    raw: bytes,
) -> None:
    with pytest.raises(ValueError, match="credential echo"):
        _publish(tmp_path, raw=raw, token=token)

    assert list(tmp_path.iterdir()) == []


def test_excessively_deep_json_is_rejected_by_bounded_echo_scan_before_writes(
    tmp_path: Path,
) -> None:
    raw = b"[" * 1500 + b"0" + b"]" * 1500

    with pytest.raises(ValueError, match="credential echo scan"):
        _publish(tmp_path, raw=raw)

    assert list(tmp_path.iterdir()) == []


def test_existing_conflicting_or_oversized_raw_cas_is_rejected_without_overwrite(
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


def test_offline_verifier_recomputes_raw_hash_and_size(tmp_path: Path) -> None:
    publication = _publish(tmp_path)
    raw_path = tmp_path / publication["raw_relative_path"]
    raw_path.write_bytes(RAW + b" ")

    with pytest.raises(ValueError, match="raw object"):
        verify_jiaoch_points_raw_attempt(
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
        verify_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )
    assert swapped is True


@pytest.mark.parametrize("kind", ["raw", "attempt"])
def test_offline_verifier_rejects_hardlinked_objects(
    tmp_path: Path,
    kind: str,
) -> None:
    publication = _publish(tmp_path)
    relative_key = "raw_relative_path" if kind == "raw" else "attempt_relative_path"
    target = tmp_path / publication[relative_key]
    alias = tmp_path / f"{kind}-hardlink"
    os.link(target, alias)

    with pytest.raises(ValueError, match="link|reparse"):
        verify_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra_field="forged"),
        lambda value: value.update(authority_status="BOUND"),
        lambda value: value.update(row_authority_status="GRANTED"),
        lambda value: value.update(embargo_consumed=True),
        lambda value: value["request_semantics"].update(params={"trade_date": "20260729"}),
        lambda value: value["raw_object"].update(bytes=value["raw_object"]["bytes"] + 1),
    ],
)
def test_rehashed_tampered_attempt_is_rejected(
    tmp_path: Path,
    mutation,
) -> None:
    publication = _publish(tmp_path)
    forged = _load_attempt(tmp_path, publication)
    mutation(forged)
    relative, digest = _write_forged_attempt(tmp_path, forged)

    with pytest.raises(ValueError, match="attempt|request|raw object"):
        verify_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=relative,
            expected_attempt_sha256=digest,
        )


def test_rehashed_attempt_cannot_escape_raw_root(tmp_path: Path) -> None:
    publication = _publish(tmp_path)
    forged = _load_attempt(tmp_path, publication)
    forged["raw_object"]["relative_path"] = "../../outside.body"
    relative, digest = _write_forged_attempt(tmp_path, forged)

    with pytest.raises(ValueError, match="raw object path"):
        verify_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=relative,
            expected_attempt_sha256=digest,
        )


def test_attempt_path_traversal_and_simulated_reparse_are_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    publication = _publish(tmp_path)
    with pytest.raises(ValueError, match="attempt path"):
        verify_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=f"../{publication['attempt_relative_path']}",
            expected_attempt_sha256=publication["attempt_sha256"],
        )

    raw_path = (tmp_path / publication["raw_relative_path"]).resolve()
    original = jiaoch_points_raw_authority._path_is_link_or_reparse
    monkeypatch.setattr(
        jiaoch_points_raw_authority,
        "_path_is_link_or_reparse",
        lambda path: Path(path).resolve() == raw_path or original(path),
    )
    with pytest.raises(ValueError, match="link|reparse"):
        verify_jiaoch_points_raw_attempt(
            output_root=tmp_path,
            attempt_relative_path=publication["attempt_relative_path"],
            expected_attempt_sha256=publication["attempt_sha256"],
        )


def test_publisher_reopens_with_offline_verifier_before_return(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict] = []
    original = verify_jiaoch_points_raw_attempt

    def recording_verifier(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(
        jiaoch_points_raw_authority,
        "verify_jiaoch_points_raw_attempt",
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


def test_offline_verifier_accepts_no_token_network_or_callback() -> None:
    assert set(inspect.signature(verify_jiaoch_points_raw_attempt).parameters) == {
        "output_root",
        "attempt_relative_path",
        "expected_attempt_sha256",
    }
    assert "endpoint" not in inspect.signature(publish_jiaoch_points_raw_attempt).parameters
