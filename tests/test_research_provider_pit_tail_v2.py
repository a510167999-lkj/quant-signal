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
    IS_OPEN_NORMALIZATION_CONTRACT,
    IS_OPEN_NORMALIZATION_CONTRACT_SHA256,
    ProviderPITTailV2Error,
    build_provider_pit_tail_plan_v2,
    validate_provider_envelope_v2,
    validate_provider_pit_tail_plan_v2,
)
from app import research_provider_pit_tail as tail_v1_module


def _authority():
    return load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=PROVIDER_EVIDENCE_CONTRACT_PATH.parents[4],
    )


def _calendar_body(call, values):
    rows = [
        ["SSE", f"202607{day:02d}", values[index], "20260703"]
        for index, day in enumerate(range(4, 11))
    ]
    return json.dumps(
        {"code": 0, "msg": "success", "data": {"fields": call["fields"], "items": rows}},
        separators=(",", ":"),
    ).encode()


def test_v1_verifier_remains_frozen_and_rejects_string_wire_values():
    call = build_provider_pit_tail_plan(_authority())["calls"][0]
    with pytest.raises(ProviderPITTailError, match="open flag"):
        validate_provider_envelope(call, _calendar_body(call, ["0", "0", "1", "1", "1", "1", "1"]))


@pytest.mark.parametrize("wire", [True, False, 0.0, 1.0])
def test_v1_exact_integer_gate_rejects_bool_and_float_without_accepting_strings(wire):
    call = build_provider_pit_tail_plan(_authority())["calls"][0]
    with pytest.raises(ProviderPITTailError, match="open flag"):
        validate_provider_envelope(call, _calendar_body(call, [wire] * 7))


@pytest.mark.parametrize("wire", [0, 1, "0", "1"])
def test_v2_accepts_only_the_four_exact_wire_values_and_canonicalizes_to_integer(wire):
    call = build_provider_pit_tail_plan_v2(_authority())["calls"][0]
    result = validate_provider_envelope_v2(call, _calendar_body(call, [wire] * 7))
    expected = int(wire)
    assert [row["is_open"] for row in result["rows"]] == [expected] * 7
    evidence = result["normalization_evidence"]
    assert evidence["schema"] == "provider-trade-cal-is-open-normalization/v1"
    assert evidence["contract_canonical_sha256"] == IS_OPEN_NORMALIZATION_CONTRACT_SHA256
    assert [entry["wire_value"] for entry in evidence["entries"]] == [wire] * 7
    assert [entry["canonical_value"] for entry in evidence["entries"]] == [expected] * 7
    assert isinstance(result["normalization_canonical_sha256"], str)
    assert len(result["normalization_canonical_sha256"]) == 64


@pytest.mark.parametrize(
    "wire",
    [True, False, 0.0, 1.0, None, "", " ", " 0", "0 ", "+1", "01", "-0", "2", -1, 2],
)
def test_v2_rejects_bool_float_null_whitespace_sign_and_every_near_miss(wire):
    call = build_provider_pit_tail_plan_v2(_authority())["calls"][0]
    with pytest.raises(ProviderPITTailV2Error, match="is_open wire value"):
        validate_provider_envelope_v2(call, _calendar_body(call, [wire] * 7))


def test_v2_accepts_a_mixed_sequence_of_only_known_wire_values():
    call = build_provider_pit_tail_plan_v2(_authority())["calls"][0]
    result = validate_provider_envelope_v2(call, _calendar_body(call, ["0", 0, "1", 1, "1", 1, "1"]))
    assert [row["is_open"] for row in result["rows"]] == [0, 0, 1, 1, 1, 1, 1]
    assert [entry["wire_type"] for entry in result["normalization_evidence"]["entries"]] == [
        "string",
        "integer",
        "string",
        "integer",
        "string",
        "integer",
        "string",
    ]


def test_v2_plan_binds_normalization_source_authority_and_failed_diagnostic_only():
    plan = build_provider_pit_tail_plan_v2(_authority())
    validate_provider_pit_tail_plan_v2(plan, _authority())
    assert plan["schema"] == "provider-pit-tail-plan/v2"
    assert plan["verifier"]["normalization_contract"] == IS_OPEN_NORMALIZATION_CONTRACT
    assert plan["verifier"]["normalization_contract_canonical_sha256"] == (
        IS_OPEN_NORMALIZATION_CONTRACT_SHA256
    )
    assert len(plan["verifier"]["module_file_sha256"]) == 64
    diagnostic = plan["failed_run_diagnostic_lineage"]
    assert diagnostic["failed_run_id"].startswith("c0ac1867")
    assert diagnostic["data_reused"] is False
    assert diagnostic["failed_actual_calls"] == 1
    assert diagnostic["observed_is_open_wire_types"] == ["string"]
    assert diagnostic["observed_is_open_wire_values"] == ["0", "1"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda plan: plan["verifier"].update({"module_file_sha256": "0" * 64}),
        lambda plan: plan["verifier"].update(
            {"normalization_contract_canonical_sha256": "0" * 64}
        ),
        lambda plan: plan["verifier"]["normalization_contract"]["mappings"].append(
            {"wire_type": "float", "wire_value": 1.0, "canonical_value": 1}
        ),
        lambda plan: plan["failed_run_diagnostic_lineage"].update({"data_reused": True}),
        lambda plan: plan["failed_run_diagnostic_lineage"].update(
            {"failed_manifest_file_sha256": "0" * 64}
        ),
        lambda plan: plan["calls"][0]["fields"].reverse(),
        lambda plan: plan["calls"][0].update({"row_cap": 9}),
        lambda plan: plan["authority"].update({"contract_sha256": "0" * 64}),
    ],
)
def test_v2_plan_full_resign_cannot_change_verifier_diagnostic_or_call_contract(mutator):
    plan = build_provider_pit_tail_plan_v2(_authority())
    mutator(plan)
    plan["plan_canonical_sha256"] = "0" * 64
    with pytest.raises(ProviderPITTailV2Error):
        validate_provider_pit_tail_plan_v2(plan, _authority())


def test_v2_retains_field_order_date_window_row_cap_and_duplicate_guards():
    call = build_provider_pit_tail_plan_v2(_authority())["calls"][0]
    valid = json.loads(_calendar_body(call, ["0", "0", "1", "1", "1", "1", "1"]))

    wrong_fields = copy.deepcopy(valid)
    wrong_fields["data"]["fields"].reverse()
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope_v2(call, json.dumps(wrong_fields).encode())

    wrong_date = copy.deepcopy(valid)
    wrong_date["data"]["items"][0][1] = "20260703"
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope_v2(call, json.dumps(wrong_date).encode())

    duplicate = copy.deepcopy(valid)
    duplicate["data"]["items"][-1] = copy.deepcopy(duplicate["data"]["items"][0])
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope_v2(call, json.dumps(duplicate).encode())

    capped = copy.deepcopy(valid)
    capped["data"]["items"].append(copy.deepcopy(capped["data"]["items"][-1]))
    with pytest.raises(ProviderPITTailError, match="row cap"):
        validate_provider_envelope_v2(call, json.dumps(capped).encode())


def test_v2_rejects_a_resigned_call_that_is_not_the_frozen_plan_call():
    call = build_provider_pit_tail_plan_v2(_authority())["calls"][0]
    mutated = copy.deepcopy(call)
    mutated["params"]["end_date"] = "20260711"
    mutated["request_semantics_sha256"] = "0" * 64
    with pytest.raises(ProviderPITTailV2Error, match="frozen call"):
        validate_provider_envelope_v2(
            mutated,
            _calendar_body(mutated, ["0", "0", "1", "1", "1", "1", "1"]),
        )


def test_v2_plan_rejects_encoding_only_authority_file_change(monkeypatch, tmp_path):
    altered = tmp_path / "contract.json"
    altered.write_bytes(PROVIDER_EVIDENCE_CONTRACT_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(tail_v1_module, "PROVIDER_EVIDENCE_CONTRACT_PATH", altered)
    with pytest.raises(ProviderPITTailV2Error, match="authority file bytes"):
        build_provider_pit_tail_plan_v2(_authority())
