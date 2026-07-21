"""Versioned exact wire normalization for the provider PIT tail."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping

from app import research_provider_pit_tail as v1


class ProviderPITTailV2Error(v1.ProviderPITTailError):
    """The v2 tail plan or exact wire normalization is invalid."""


_EXPECTED_AUTHORITY_FILE_SHA256 = (
    "f06720111efebe2cd842740d90828a5c2373342f6fc72fee8a813261e3f9d09b"
)


IS_OPEN_NORMALIZATION_CONTRACT = {
    "schema": "provider-trade-cal-is-open-normalization/v1",
    "endpoint": "trade_cal",
    "field": "is_open",
    "mappings": [
        {"wire_type": "integer", "wire_value": 0, "canonical_value": 0},
        {"wire_type": "integer", "wire_value": 1, "canonical_value": 1},
        {"wire_type": "string", "wire_value": "0", "canonical_value": 0},
        {"wire_type": "string", "wire_value": "1", "canonical_value": 1},
    ],
    "canonical_type": "integer",
    "unknown_values": "reject",
    "bool_allowed": False,
    "float_allowed": False,
    "null_allowed": False,
    "mixed_known_wire_types_allowed": True,
    "raw_wire_preserved": True,
    "canonicalization": "utf8-json-sort-keys-compact-no-nan/v1",
}
IS_OPEN_NORMALIZATION_CONTRACT_SHA256 = hashlib.sha256(
    v1._canonical_bytes(IS_OPEN_NORMALIZATION_CONTRACT)
).hexdigest()

_FAILED_RUN_DIAGNOSTIC = {
    "schema": "provider-pit-tail-wire-diagnostic/v1",
    "failed_run_id": "c0ac1867285e080f9b27f33d2d2dcb6092be6633909fa08e6f4cdf74cefaa3c5",
    "failed_run_tree_sha256": "366a2ad08270ab3ef0023273ebb344e3257841ef2bf54fe285baae17c87ffdbf",
    "failed_manifest_file_sha256": "a2270f26890002c1d00353bfcd24a0c74330cc9d2818b912acd7e0909052d0aa",
    "failed_manifest_canonical_sha256": "1209f43e74371b5dee032128f90001c1bd9bcfdf8051ad938d647850b179f449",
    "failed_checkpoint_file_sha256": "b0c689e31b7cd44a319caee78d53aa7e6edf56b895f5b57a4237b13713ec03be",
    "failed_checkpoint_canonical_sha256": "fce8aaa372da6c7fe9fd2a0d719246e854b13329d883f570e912388c03134ba0",
    "failed_database_file_sha256": "fe1ae171508ab0165fbcf255ac5bf31d978d851cd94f4fe4d3a5d6bec4557e95",
    "failed_raw_file_sha256": "5fdf0b832c9458dbf2b7a6be6365dac9885de4ed99c1076551517590259f7e9c",
    "failed_actual_calls": 1,
    "failed_sequence": 0,
    "observed_endpoint": "trade_cal",
    "observed_field": "is_open",
    "observed_is_open_wire_types": ["string"],
    "observed_is_open_wire_values": ["0", "1"],
    "verdict": "v1_rejected_exact_provider_string_wire_type",
    "data_reused": False,
}
_FAILED_RUN_DIAGNOSTIC_SHA256 = hashlib.sha256(
    v1._canonical_bytes(_FAILED_RUN_DIAGNOSTIC)
).hexdigest()


def _module_file_sha256() -> str:
    digest = hashlib.sha256()
    with Path(__file__).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _frozen_call(call: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(call, Mapping) or type(call.get("sequence")) is not int:
        raise ProviderPITTailV2Error("v2 verifier requires a frozen call")
    sequence = call["sequence"]
    calls = v1._calls()
    if sequence < 0 or sequence >= len(calls) or dict(call) != calls[sequence]:
        raise ProviderPITTailV2Error("v2 verifier requires the exact frozen call")
    return calls[sequence]


def _expected_plan(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    plan = v1.build_provider_pit_tail_plan(authority_contract)
    if plan["authority"]["contract_file_sha256"] != _EXPECTED_AUTHORITY_FILE_SHA256:
        raise ProviderPITTailV2Error("provider evidence authority file bytes changed")
    plan.pop("plan_canonical_sha256")
    plan["schema"] = "provider-pit-tail-plan/v2"
    plan["verifier"] = {
        "schema": "provider-pit-tail-verifier-binding/v2",
        "module_path": "app/research_provider_pit_tail_v2.py",
        "module_file_sha256": _module_file_sha256(),
        "normalization_contract": copy.deepcopy(IS_OPEN_NORMALIZATION_CONTRACT),
        "normalization_contract_canonical_sha256": IS_OPEN_NORMALIZATION_CONTRACT_SHA256,
        "receipt_schema": "provider-pit-tail-formal-receipt/v2",
        "normalized_response_schema": "provider-pit-normalized-response/v2",
        "v1_fallback_allowed": False,
    }
    diagnostic = copy.deepcopy(_FAILED_RUN_DIAGNOSTIC)
    diagnostic["diagnostic_canonical_sha256"] = _FAILED_RUN_DIAGNOSTIC_SHA256
    plan["failed_run_diagnostic_lineage"] = diagnostic
    plan["plan_canonical_sha256"] = hashlib.sha256(v1._canonical_bytes(plan)).hexdigest()
    return plan


def build_provider_pit_tail_plan_v2(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fresh plan bound to the exact v2 normalizer and failed diagnostic."""

    return copy.deepcopy(_expected_plan(authority_contract))


def validate_provider_pit_tail_plan_v2(
    plan: Any,
    authority_contract: Mapping[str, Any],
) -> None:
    if not isinstance(plan, dict) or plan != _expected_plan(authority_contract):
        raise ProviderPITTailV2Error("provider PIT tail v2 plan differs from the frozen contract")


def _normalize_is_open(value: Any) -> tuple[int, str]:
    if type(value) is int and value in {0, 1}:
        return value, "integer"
    if type(value) is str and value in {"0", "1"}:
        return int(value), "string"
    raise ProviderPITTailV2Error("trade_cal is_open wire value is not exactly authorized")


def validate_provider_envelope_v2(
    call: Mapping[str, Any],
    raw_body: bytes,
) -> dict[str, Any]:
    """Validate one response, normalizing only the exact trade_cal wire variants."""

    frozen = _frozen_call(call)
    if frozen["endpoint"] != "trade_cal":
        result = v1.validate_provider_envelope(frozen, raw_body)
        result["normalization_evidence"] = None
        result["normalization_canonical_sha256"] = None
        return result

    if not isinstance(raw_body, bytes) or not raw_body:
        raise v1.ProviderPITTailError("provider returned an empty body")
    try:
        payload = v1.json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=v1._reject_duplicate_keys,
            parse_constant=v1._reject_json_constant,
        )
    except (UnicodeError, v1.json.JSONDecodeError, TypeError) as exc:
        raise v1.ProviderPITTailError("provider response is not strict JSON") from exc
    wire_rows = v1._rows_from_envelope(frozen, payload)
    wire_rows.sort(key=lambda row: row["cal_date"])
    canonical_rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for row in wire_rows:
        canonical, wire_type = _normalize_is_open(row["is_open"])
        canonical_row = dict(row)
        canonical_row["is_open"] = canonical
        canonical_rows.append(canonical_row)
        entries.append(
            {
                "cal_date": row["cal_date"],
                "wire_type": wire_type,
                "wire_value": row["is_open"],
                "canonical_value": canonical,
            }
        )
    if len({v1._canonical_bytes(row) for row in canonical_rows}) != len(canonical_rows):
        raise ProviderPITTailV2Error("normalization created a duplicate canonical row")
    v1._validate_trade_cal(frozen, canonical_rows)
    normalization = {
        "schema": IS_OPEN_NORMALIZATION_CONTRACT["schema"],
        "contract_canonical_sha256": IS_OPEN_NORMALIZATION_CONTRACT_SHA256,
        "endpoint": "trade_cal",
        "field": "is_open",
        "raw_wire_preserved": True,
        "entries": entries,
    }
    normalization_sha = hashlib.sha256(v1._canonical_bytes(normalization)).hexdigest()
    return {
        "rows": canonical_rows,
        "row_count": len(canonical_rows),
        "response_fields": list(frozen["fields"]),
        "wire_items_canonical_sha256": hashlib.sha256(
            v1._canonical_bytes(payload["data"]["items"])
        ).hexdigest(),
        "rows_canonical_sha256": hashlib.sha256(v1._canonical_bytes(canonical_rows)).hexdigest(),
        "semantic_empty": False,
        "normalization_evidence": normalization,
        "normalization_canonical_sha256": normalization_sha,
    }
