import copy
import json

import pytest

from app.research_provider_evidence_partitions import (
    PROVIDER_EVIDENCE_CONTRACT_PATH,
    load_provider_evidence_temporal_contract,
)
from app.research_provider_pit_tail import (
    ProviderPITTailError,
    build_provider_pit_tail_plan,
    validate_provider_envelope,
)
from app.research_provider_pit_tail_v2 import (
    build_provider_pit_tail_plan_v2,
    validate_provider_envelope_v2,
)
from app.research_provider_pit_tail_v3 import (
    build_provider_pit_tail_plan_v3,
    validate_provider_envelope_v3,
)
from app.research_provider_pit_tail_v4 import (
    NAMECHANGE_SEMANTIC_EMPTY_CONTRACT,
    NAMECHANGE_SEMANTIC_EMPTY_CONTRACT_SHA256,
    ProviderPITTailV4Error,
    build_provider_pit_tail_plan_v4,
    validate_provider_envelope_v4,
    validate_provider_pit_tail_plan_v4,
)


def _authority():
    return load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=PROVIDER_EVIDENCE_CONTRACT_PATH.parents[4],
    )


def _plan():
    return build_provider_pit_tail_plan_v4(_authority())


def _namechange_call():
    return _plan()["calls"][5]


def _empty_body(**changes):
    payload = {
        "code": 0,
        "msg": "success",
        "data": {
            "fields": list(_namechange_call()["fields"]),
            "items": [],
        },
    }
    for path, value in changes.items():
        target = payload
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _nonempty_body():
    call = _namechange_call()
    payload = {
        "code": 0,
        "msg": "success",
        "data": {
            "fields": list(call["fields"]),
            "items": [["600001.SH", "旧名", "20260704", None, "20260704", "更名"]],
        },
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def test_v1_v2_v3_remain_frozen_and_reject_semantic_empty_namechange():
    authority = _authority()
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(build_provider_pit_tail_plan(authority)["calls"][5], _empty_body())
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope_v2(build_provider_pit_tail_plan_v2(authority)["calls"][5], _empty_body())
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope_v3(build_provider_pit_tail_plan_v3(authority)["calls"][5], _empty_body())


def test_v4_accepts_only_exact_namechange_semantic_empty_and_binds_evidence():
    result = validate_provider_envelope_v4(_namechange_call(), _empty_body())
    assert result["rows"] == []
    assert result["row_count"] == 0
    assert result["semantic_empty"] is True
    assert result["event_count"] == 0
    assert result["semantic_empty_reason"] == "no_events_in_partition"
    evidence = result["normalization_evidence"]
    assert evidence["schema"] == NAMECHANGE_SEMANTIC_EMPTY_CONTRACT["schema"]
    assert evidence["contract_canonical_sha256"] == NAMECHANGE_SEMANTIC_EMPTY_CONTRACT_SHA256
    assert evidence["request_semantics_sha256"] == _namechange_call()["request_semantics_sha256"]
    assert evidence["raw_body_bytes"] == len(_empty_body())
    assert len(evidence["raw_body_sha256"]) == 64
    assert len(evidence["wire_items_canonical_sha256"]) == 64
    assert result["rows_canonical_sha256"]
    assert result["normalization_canonical_sha256"]


def test_v4_nonempty_namechange_still_uses_strict_event_validation():
    result = validate_provider_envelope_v4(_namechange_call(), _nonempty_body())
    assert result["row_count"] == 1
    assert result["semantic_empty"] is False
    assert result["event_count"] == 1
    assert result["normalization_evidence"] is None


@pytest.mark.parametrize("sequence", [0, 1, 2, 3, 4])
def test_v4_rejects_empty_for_every_non_namechange_endpoint(sequence):
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope_v4(_plan()["calls"][sequence], _empty_body())


@pytest.mark.parametrize(
    "mutator",
    [
        lambda call: call["params"].update({"start_date": "20260703"}),
        lambda call: call["params"].update({"end_date": "20260711"}),
        lambda call: call["fields"].reverse(),
        lambda call: call.update({"row_cap": 9999}),
        lambda call: call.update({"sequence": 4}),
        lambda call: call.update({"empty_allowed": True}),
    ],
)
def test_v4_rejects_resigned_or_cross_partition_empty_call(mutator):
    call = copy.deepcopy(_namechange_call())
    mutator(call)
    with pytest.raises(ProviderPITTailV4Error):
        validate_provider_envelope_v4(call, _empty_body())


@pytest.mark.parametrize(
    "payload_change",
    [
        {"code": 1},
        {"msg": ""},
        {"data.fields": None},
        {"data.fields": ["ts_code"]},
        {"data.items": None},
        {"data.items": [None]},
        {"data.count": 0},
        {"data.has_more": False},
    ],
)
def test_v4_rejects_non_exact_empty_envelope(payload_change):
    with pytest.raises(ProviderPITTailV4Error):
        validate_provider_envelope_v4(_namechange_call(), _empty_body(**payload_change))


def test_v4_rejects_unknown_keys_duplicate_keys_and_invalid_json():
    with pytest.raises(ProviderPITTailV4Error):
        validate_provider_envelope_v4(_namechange_call(), _empty_body(extra=True))
    with pytest.raises(ProviderPITTailV4Error):
        validate_provider_envelope_v4(
            _namechange_call(), b'{"code":0,"code":0,"msg":"success","data":{"fields":[],"items":[]}}'
        )
    with pytest.raises(ProviderPITTailV4Error):
        validate_provider_envelope_v4(_namechange_call(), b"null")
    with pytest.raises(ProviderPITTailV4Error):
        validate_provider_envelope_v4(_namechange_call(), b"")


def test_v4_plan_binds_successor_contract_and_preserves_v3_calls():
    authority = _authority()
    v3_plan = build_provider_pit_tail_plan_v3(authority)
    plan = _plan()
    validate_provider_pit_tail_plan_v4(plan, authority)
    assert plan["schema"] == "provider-pit-tail-plan/v4"
    assert plan["calls"] == v3_plan["calls"]
    assert plan["verifier"]["schema"] == "provider-pit-tail-verifier-binding/v4"
    assert plan["verifier"]["v1_fallback_allowed"] is False
    assert plan["verifier"]["namechange_semantic_empty_contract"] == NAMECHANGE_SEMANTIC_EMPTY_CONTRACT
    assert plan["verifier"]["namechange_semantic_empty_contract_canonical_sha256"] == (
        NAMECHANGE_SEMANTIC_EMPTY_CONTRACT_SHA256
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda plan: plan["verifier"].update({"module_file_sha256": "0" * 64}),
        lambda plan: plan["verifier"]["namechange_semantic_empty_contract"].update(
            {"reason": "drop_missing_data"}
        ),
        lambda plan: plan["calls"][5]["params"].update({"end_date": "20260711"}),
        lambda plan: plan.update({"schema": "provider-pit-tail-plan/v3"}),
    ],
)
def test_v4_plan_rejects_full_resign_or_contract_tampering(mutator):
    plan = _plan()
    mutator(plan)
    plan["plan_canonical_sha256"] = "0" * 64
    with pytest.raises(ProviderPITTailV4Error):
        validate_provider_pit_tail_plan_v4(plan, _authority())

