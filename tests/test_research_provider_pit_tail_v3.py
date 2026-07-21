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
from app.research_provider_pit_tail_v2 import build_provider_pit_tail_plan_v2
from app.research_provider_pit_tail_v3 import (
    BAK_BASIC_WIRE_ENVELOPE_CONTRACT,
    BAK_BASIC_WIRE_ENVELOPE_CONTRACT_SHA256,
    BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT,
    BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT_SHA256,
    ProviderPITTailV3Error,
    build_provider_pit_tail_plan_v3,
    validate_provider_envelope_v3,
    validate_provider_pit_tail_plan_v3,
)


def _authority():
    return load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=PROVIDER_EVIDENCE_CONTRACT_PATH.parents[4],
    )


def _call():
    return build_provider_pit_tail_plan_v3(_authority())["calls"][2]


def _body(rows):
    return _body_with_provider_metadata(rows)


def _body_with_provider_metadata(rows):
    call = _call()
    return json.dumps(
        {
            "code": 0,
            "msg": "",
            "detail": "...",
            "request_id": "fixture-request-id",
            "data": {
                "count": 0,
                "has_more": False,
                "fields": call["fields"],
                "items": rows,
            },
        },
        separators=(",", ":"),
    ).encode()


def _row(ts_code="600001.SH", list_date="20100101", *, trade_date="20260710"):
    return [trade_date, ts_code, "测试", "行业", list_date]


def test_v1_and_v2_remain_frozen_and_reject_bak_basic_sentinel():
    row = _row("688806.SH", "0")
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(build_provider_pit_tail_plan(_authority())["calls"][2], _body([row]))
    from app.research_provider_pit_tail_v2 import validate_provider_envelope_v2

    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope_v2(build_provider_pit_tail_plan_v2(_authority())["calls"][2], _body([row]))


@pytest.mark.parametrize("symbol", ["688806.SH", "688825.SH", "920079.BJ", "920117.BJ"])
def test_v3_accepts_exact_excluded_board_sentinel_and_preserves_wire_value(symbol):
    result = validate_provider_envelope_v3(_call(), _body([_row(symbol, "0")]))
    assert result["rows"][0]["ts_code"] == symbol
    assert result["rows"][0]["list_date"] == "0"
    evidence = result["normalization_evidence"]
    assert evidence["schema"] == BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT["schema"]
    assert evidence["contract_canonical_sha256"] == BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT_SHA256
    assert len(evidence["entries"]) == 1
    entry = evidence["entries"][0]
    assert entry["raw_wire_type"] == "string"
    assert entry["raw_wire_value"] == "0"
    assert entry["sentinel_reason"] == "excluded_board_before_date_validation"
    assert entry["excluded_before_date_validation"] is True
    assert entry["wire_row"] == _row(symbol, "0")
    assert result["normalization_evidence"]["raw_body_bytes"] > 0
    assert len(result["normalization_evidence"]["raw_body_sha256"]) == 64
    assert len(result["normalization_evidence"]["wire_items_canonical_sha256"]) == 64
    assert result["normalization_canonical_sha256"]


def test_v3_accepts_normal_date_and_keeps_empty_sentinel_allowlist_narrow():
    result = validate_provider_envelope_v3(_call(), _body([_row("600001.SH", "20100101")]))
    assert result["rows"][0]["list_date"] == "20100101"
    assert result["normalization_evidence"]["entries"] == []


def test_v3_accepts_the_exact_bak_basic_provider_metadata_shape_and_binds_wire_hash():
    result = validate_provider_envelope_v3(
        _call(), _body_with_provider_metadata([_row("688806.SH", "0")])
    )
    assert result["rows"][0]["list_date"] == "0"
    assert result["wire_items_canonical_sha256"]
    assert result["normalization_evidence"]["wire_envelope_contract_canonical_sha256"] == (
        BAK_BASIC_WIRE_ENVELOPE_CONTRACT_SHA256
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"extra": True}),
        lambda payload: payload["data"].update({"extra": True}),
        lambda payload: payload["data"].update({"count": 1}),
        lambda payload: payload["data"].update({"has_more": True}),
        lambda payload: payload.pop("request_id"),
    ],
)
def test_v3_rejects_provider_metadata_shape_tampering(mutator):
    payload = json.loads(_body_with_provider_metadata([_row("688806.SH", "0")]))
    mutator(payload)
    with pytest.raises(ProviderPITTailV3Error):
        validate_provider_envelope_v3(_call(), json.dumps(payload).encode())


@pytest.mark.parametrize(
    "symbol",
    ["600001.SH", "300001.SZ", "700001.SH", "688806.SZ", "688806.BJ", "920079.SH", "688001"],
)
def test_v3_rejects_in_scope_unknown_or_conflicting_sentinel(symbol):
    with pytest.raises(ProviderPITTailV3Error, match="list_date|scope|sentinel|invalid security code"):
        validate_provider_envelope_v3(_call(), _body([_row(symbol, "0")]))


@pytest.mark.parametrize("value", [0, 0.0, True, None, "", " ", "00", "+0", "00000000"])
def test_v3_rejects_non_exact_sentinel_wire_values(value):
    with pytest.raises(ProviderPITTailV3Error, match="list_date"):
        validate_provider_envelope_v3(_call(), _body([_row("688806.SH", value)]))


@pytest.mark.parametrize(
    "field_index,value",
    [(0, "0"), (1, "0"), (2, 0), (3, 0), (4, "00")],
)
def test_v3_does_not_drop_or_rewrite_other_zero_fields(field_index, value):
    row = _row("688806.SH", "0")
    row[field_index] = value
    with pytest.raises((ProviderPITTailV3Error, ProviderPITTailError)):
        validate_provider_envelope_v3(_call(), _body([row]))


def test_v3_plan_binds_scope_rule_and_successor_module_without_changing_calls():
    v2_plan = build_provider_pit_tail_plan_v2(_authority())
    plan = build_provider_pit_tail_plan_v3(_authority())
    validate_provider_pit_tail_plan_v3(plan, _authority())
    assert plan["schema"] == "provider-pit-tail-plan/v3"
    assert plan["calls"] == v2_plan["calls"]
    verifier = plan["verifier"]
    assert verifier["schema"] == "provider-pit-tail-verifier-binding/v3"
    assert verifier["v1_fallback_allowed"] is False
    assert verifier["bak_basic_wire_envelope_contract"] == BAK_BASIC_WIRE_ENVELOPE_CONTRACT
    assert verifier["bak_basic_wire_envelope_contract_canonical_sha256"] == (
        BAK_BASIC_WIRE_ENVELOPE_CONTRACT_SHA256
    )
    assert verifier["bak_basic_list_date_sentinel_contract"] == (
        BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT
    )
    assert verifier["bak_basic_list_date_sentinel_contract_canonical_sha256"] == (
        BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT_SHA256
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda plan: plan["verifier"].update({"module_file_sha256": "0" * 64}),
        lambda plan: plan["verifier"]["bak_basic_wire_envelope_contract"]["msg"].update(
            {"wire_value": "success"}
        ),
        lambda plan: plan["verifier"]["bak_basic_list_date_sentinel_contract"]["excluded_board_rules"].reverse(),
        lambda plan: plan["calls"][2].update({"row_cap": 7001}),
    ],
)
def test_v3_plan_rejects_scope_or_contract_tampering(mutator):
    plan = build_provider_pit_tail_plan_v3(_authority())
    if mutator.__code__.co_firstlineno == 0:
        pytest.fail("invalid test mutator")
    mutator(plan)
    plan["plan_canonical_sha256"] = "0" * 64
    with pytest.raises(ProviderPITTailV3Error):
        validate_provider_pit_tail_plan_v3(plan, _authority())


def test_v3_rejects_resigned_call_and_duplicate_row():
    call = _call()
    mutated = copy.deepcopy(call)
    mutated["params"]["trade_date"] = "20260709"
    mutated["request_semantics_sha256"] = "0" * 64
    with pytest.raises(ProviderPITTailV3Error, match="frozen call"):
        validate_provider_envelope_v3(mutated, _body([_row("688806.SH", "0")]))

    duplicate = _body([_row("688806.SH", "0"), _row("688806.SH", "0")])
    with pytest.raises(ProviderPITTailV3Error, match="duplicate"):
        validate_provider_envelope_v3(call, duplicate)
