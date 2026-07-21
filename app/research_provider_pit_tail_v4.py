"""Versioned provider PIT tail contract with an exact namechange empty response."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping

from app import research_provider_pit_tail as v1
from app import research_provider_pit_tail_v3 as v3


class ProviderPITTailV4Error(v1.ProviderPITTailError):
    """The v4 tail plan or semantic-empty normalization is invalid."""


NAMECHANGE_SEMANTIC_EMPTY_CONTRACT = {
    "schema": "provider-namechange-semantic-empty/v1",
    "endpoint": "namechange",
    "sequence": 5,
    "role": "risk_namechange_tail",
    "params": {"start_date": "20260704", "end_date": "20260710"},
    "fields": ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"],
    "row_cap": 10000,
    "empty_items": [],
    "event_count": 0,
    "reason": "no_events_in_partition",
    "raw_body_required": True,
    "http_status_required": 200,
    "provider_code_required": 0,
    "provider_message_required": "success",
    "unknown_keys": "reject",
    "future_rows": "reject",
    "canonicalization": "utf8-json-sort-keys-compact-no-nan/v1",
}
NAMECHANGE_SEMANTIC_EMPTY_CONTRACT_SHA256 = hashlib.sha256(
    v1._canonical_bytes(NAMECHANGE_SEMANTIC_EMPTY_CONTRACT)
).hexdigest()


def _module_file_sha256() -> str:
    digest = hashlib.sha256()
    with Path(__file__).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _expected_plan(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    plan = v3.build_provider_pit_tail_plan_v3(authority_contract)
    plan.pop("plan_canonical_sha256")
    plan["schema"] = "provider-pit-tail-plan/v4"
    verifier = copy.deepcopy(plan["verifier"])
    verifier.update(
        {
            "schema": "provider-pit-tail-verifier-binding/v4",
            "module_path": "app/research_provider_pit_tail_v4.py",
            "module_file_sha256": _module_file_sha256(),
            "receipt_schema": "provider-pit-tail-formal-receipt/v4",
            "normalized_response_schema": "provider-pit-normalized-response/v4",
            "namechange_semantic_empty_contract": copy.deepcopy(
                NAMECHANGE_SEMANTIC_EMPTY_CONTRACT
            ),
            "namechange_semantic_empty_contract_canonical_sha256": (
                NAMECHANGE_SEMANTIC_EMPTY_CONTRACT_SHA256
            ),
            "v1_fallback_allowed": False,
        }
    )
    plan["verifier"] = verifier
    plan["plan_canonical_sha256"] = hashlib.sha256(v1._canonical_bytes(plan)).hexdigest()
    return plan


def build_provider_pit_tail_plan_v4(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fresh plan bound to the exact v4 semantic-empty contract."""

    return copy.deepcopy(_expected_plan(authority_contract))


def validate_provider_pit_tail_plan_v4(
    plan: Any,
    authority_contract: Mapping[str, Any],
) -> None:
    if not isinstance(plan, dict) or plan != _expected_plan(authority_contract):
        raise ProviderPITTailV4Error("provider PIT tail v4 plan differs from the frozen contract")


def _parse_json(raw_body: bytes) -> dict[str, Any]:
    if not isinstance(raw_body, bytes) or not raw_body:
        raise ProviderPITTailV4Error("provider returned an empty body")
    try:
        payload = v1.json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=v1._reject_duplicate_keys,
            parse_constant=v1._reject_json_constant,
        )
    except (UnicodeError, v1.json.JSONDecodeError, TypeError, v1.ProviderPITTailError) as exc:
        raise ProviderPITTailV4Error("provider response is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderPITTailV4Error("provider response envelope is invalid")
    return payload


def _parse_namechange_empty(call: Mapping[str, Any], raw_body: bytes) -> dict[str, Any]:
    payload = _parse_json(raw_body)
    if set(payload) != {"code", "msg", "data"}:
        raise ProviderPITTailV4Error("namechange provider envelope keys are invalid")
    if type(payload["code"]) is not int or payload["code"] != 0:
        raise ProviderPITTailV4Error("provider returned a non-success code")
    if payload["msg"] != "success":
        raise ProviderPITTailV4Error("namechange provider msg is invalid")
    data = payload["data"]
    if not isinstance(data, dict) or set(data) != {"fields", "items"}:
        raise ProviderPITTailV4Error("namechange provider data keys are invalid")
    if data["fields"] != call["fields"] or not isinstance(data["items"], list):
        raise ProviderPITTailV4Error("provider response fields or items are invalid")
    if data["items"]:
        result = v3.validate_provider_envelope_v3(call, raw_body)
        result["event_count"] = result["row_count"]
        result["semantic_empty_reason"] = None
        return result
    wire_items_sha = hashlib.sha256(v1._canonical_bytes(data["items"])).hexdigest()
    rows_sha = hashlib.sha256(v1._canonical_bytes([])).hexdigest()
    raw_sha = hashlib.sha256(raw_body).hexdigest()
    evidence = {
        "schema": NAMECHANGE_SEMANTIC_EMPTY_CONTRACT["schema"],
        "contract_canonical_sha256": NAMECHANGE_SEMANTIC_EMPTY_CONTRACT_SHA256,
        "endpoint": call["endpoint"],
        "sequence": call["sequence"],
        "role": call["role"],
        "params": copy.deepcopy(call["params"]),
        "fields": list(call["fields"]),
        "row_cap": call["row_cap"],
        "request_semantics_sha256": call["request_semantics_sha256"],
        "semantic_empty": True,
        "event_count": 0,
        "reason": NAMECHANGE_SEMANTIC_EMPTY_CONTRACT["reason"],
        "raw_body_bytes": len(raw_body),
        "raw_body_sha256": raw_sha,
        "wire_items_canonical_sha256": wire_items_sha,
        "rows_canonical_sha256": rows_sha,
        "provider_message": payload["msg"],
    }
    evidence_sha = hashlib.sha256(v1._canonical_bytes(evidence)).hexdigest()
    return {
        "rows": [],
        "row_count": 0,
        "event_count": 0,
        "response_fields": list(call["fields"]),
        "raw_body_bytes": len(raw_body),
        "raw_body_sha256": raw_sha,
        "wire_items_canonical_sha256": wire_items_sha,
        "rows_canonical_sha256": rows_sha,
        "semantic_empty": True,
        "semantic_empty_reason": NAMECHANGE_SEMANTIC_EMPTY_CONTRACT["reason"],
        "normalization_evidence": evidence,
        "normalization_canonical_sha256": evidence_sha,
    }


def validate_provider_envelope_v4(
    call: Mapping[str, Any],
    raw_body: bytes,
) -> dict[str, Any]:
    """Validate one frozen call, allowing only the exact namechange empty envelope."""

    try:
        frozen = v3.v2._frozen_call(call)
        if frozen["endpoint"] == "namechange":
            return _parse_namechange_empty(frozen, raw_body)
        result = v3.validate_provider_envelope_v3(frozen, raw_body)
        result["event_count"] = result["row_count"]
        result["semantic_empty_reason"] = None
        return result
    except ProviderPITTailV4Error:
        raise
    except (v1.ProviderPITTailError, TypeError, KeyError) as exc:
        raise ProviderPITTailV4Error(str(exc)) from exc

