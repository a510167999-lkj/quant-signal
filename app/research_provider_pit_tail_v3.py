"""Versioned provider PIT tail validation with an explicit bak_basic sentinel contract."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping

from app import research_provider_pit_tail as v1
from app import research_provider_pit_tail_v2 as v2
from app.research_scope import market_scope_contract


class ProviderPITTailV3Error(v1.ProviderPITTailError):
    """The v3 tail plan or sentinel normalization is invalid."""


_SCOPE_POLICY = market_scope_contract()
BAK_BASIC_WIRE_ENVELOPE_CONTRACT = {
    "schema": "provider-bak-basic-wire-envelope/v1",
    "endpoint": "bak_basic",
    "top_level_keys": ["code", "data", "detail", "msg", "request_id"],
    "data_keys": ["count", "fields", "has_more", "items"],
    "msg": {"wire_type": "string", "wire_value": ""},
    "detail": {"wire_type": "string"},
    "request_id": {"wire_type": "non_empty_string"},
    "count": {
        "wire_type": "integer",
        "wire_value": 0,
        "semantic": "provider_metadata_not_row_count",
    },
    "has_more": {"wire_type": "boolean", "wire_value": False},
    "unknown_keys": "reject",
    "raw_metadata_preserved": True,
    "canonicalization": "utf8-json-sort-keys-compact-no-nan/v1",
}
BAK_BASIC_WIRE_ENVELOPE_CONTRACT_SHA256 = hashlib.sha256(
    v1._canonical_bytes(BAK_BASIC_WIRE_ENVELOPE_CONTRACT)
).hexdigest()
BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT = {
    "schema": "provider-bak-basic-list-date-sentinel/v1",
    "endpoint": "bak_basic",
    "field": "list_date",
    "wire_type": "string",
    "wire_value": "0",
    "canonical_value": "0",
    "sentinel_reason": "excluded_board_before_date_validation",
    "excluded_before_date_validation": True,
    "scope_rule_id": "research-mainboard-chinext-v1-excluded-board-symbol-rules-v1",
    "scope_policy": copy.deepcopy(_SCOPE_POLICY),
    "wire_envelope_contract": copy.deepcopy(BAK_BASIC_WIRE_ENVELOPE_CONTRACT),
    "wire_envelope_contract_canonical_sha256": BAK_BASIC_WIRE_ENVELOPE_CONTRACT_SHA256,
    "classification_order": ["strict_ts_code", "exchange_suffix", "code_prefix"],
    "excluded_board_rules": [
        {
            "board": "science_technology",
            "exchange_suffix": "SH",
            "code_prefixes": ["688", "689"],
        },
        {
            "board": "beijing",
            "exchange_suffix": "BJ",
            "code_prefixes": ["4", "8", "9"],
        },
    ],
    "unknown_or_conflicting_symbol": "reject",
    "raw_wire_preserved": True,
    "canonicalization": "utf8-json-sort-keys-compact-no-nan/v1",
}
BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT_SHA256 = hashlib.sha256(
    v1._canonical_bytes(BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT)
).hexdigest()


def _module_file_sha256() -> str:
    digest = hashlib.sha256()
    with Path(__file__).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _classify_excluded_board(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        code, suffix = value.split(".")
    except ValueError:
        return None
    if len(code) != 6 or not code.isdigit():
        return None
    if suffix == "SH" and code.startswith(("688", "689")):
        return "science_technology"
    if suffix == "BJ" and code.startswith(("4", "8", "9")):
        return "beijing"
    return None


def _expected_plan(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    plan = v2.build_provider_pit_tail_plan_v2(authority_contract)
    plan["schema"] = "provider-pit-tail-plan/v3"
    plan["verifier"] = {
        "schema": "provider-pit-tail-verifier-binding/v3",
        "module_path": "app/research_provider_pit_tail_v3.py",
        "module_file_sha256": _module_file_sha256(),
        "trade_cal_normalization_contract": copy.deepcopy(v2.IS_OPEN_NORMALIZATION_CONTRACT),
        "trade_cal_normalization_contract_canonical_sha256": (
            v2.IS_OPEN_NORMALIZATION_CONTRACT_SHA256
        ),
        "bak_basic_wire_envelope_contract": copy.deepcopy(BAK_BASIC_WIRE_ENVELOPE_CONTRACT),
        "bak_basic_wire_envelope_contract_canonical_sha256": (
            BAK_BASIC_WIRE_ENVELOPE_CONTRACT_SHA256
        ),
        "bak_basic_list_date_sentinel_contract": copy.deepcopy(
            BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT
        ),
        "bak_basic_list_date_sentinel_contract_canonical_sha256": (
            BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT_SHA256
        ),
        "receipt_schema": "provider-pit-tail-formal-receipt/v3",
        "normalized_response_schema": "provider-pit-normalized-response/v3",
        "v1_fallback_allowed": False,
    }
    plan["plan_canonical_sha256"] = hashlib.sha256(v1._canonical_bytes(plan)).hexdigest()
    return plan


def build_provider_pit_tail_plan_v3(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fresh plan bound to the v3 sentinel contract."""

    return copy.deepcopy(_expected_plan(authority_contract))


def validate_provider_pit_tail_plan_v3(
    plan: Any,
    authority_contract: Mapping[str, Any],
) -> None:
    if not isinstance(plan, dict) or plan != _expected_plan(authority_contract):
        raise ProviderPITTailV3Error("provider PIT tail v3 plan differs from the frozen contract")


def _parse_bak_basic(call: Mapping[str, Any], raw_body: bytes) -> dict[str, Any]:
    if not isinstance(raw_body, bytes) or not raw_body:
        raise ProviderPITTailV3Error("provider returned an empty body")
    try:
        payload = v1.json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=v1._reject_duplicate_keys,
            parse_constant=v1._reject_json_constant,
        )
    except (UnicodeError, v1.json.JSONDecodeError, TypeError) as exc:
        raise ProviderPITTailV3Error("provider response is not strict JSON") from exc
    if set(payload) != set(BAK_BASIC_WIRE_ENVELOPE_CONTRACT["top_level_keys"]):
        raise ProviderPITTailV3Error("bak_basic provider envelope keys are invalid")
    if type(payload["code"]) is not int or payload["code"] != 0:
        raise ProviderPITTailV3Error("provider returned a non-success code")
    if type(payload["msg"]) is not str or payload["msg"] != "":
        raise ProviderPITTailV3Error("bak_basic provider msg is invalid")
    if type(payload["detail"]) is not str:
        raise ProviderPITTailV3Error("bak_basic provider detail is invalid")
    if type(payload["request_id"]) is not str or not payload["request_id"]:
        raise ProviderPITTailV3Error("bak_basic provider request_id is invalid")
    data = payload["data"]
    if not isinstance(data, dict) or set(data) != set(
        BAK_BASIC_WIRE_ENVELOPE_CONTRACT["data_keys"]
    ):
        raise ProviderPITTailV3Error("bak_basic provider data keys are invalid")
    if type(data["count"]) is not int or data["count"] != 0:
        raise ProviderPITTailV3Error("bak_basic provider count is invalid")
    if data["has_more"] is not False:
        raise ProviderPITTailV3Error("bak_basic provider has_more is invalid")
    if data["fields"] != call["fields"] or not isinstance(data["items"], list):
        raise ProviderPITTailV3Error("provider response fields or items are invalid")
    if len(data["items"]) >= call["row_cap"]:
        raise ProviderPITTailV3Error("provider response reached the frozen row cap")
    rows = v1._rows_from_envelope(
        call,
        {"code": 0, "msg": "success", "data": {"fields": data["fields"], "items": data["items"]}},
    )
    rows.sort(key=lambda row: v1._canonical_bytes(row))
    seen: set[str] = set()
    sentinel_entries: list[dict[str, Any]] = []
    for row in rows:
        code = v1._validate_code(row["ts_code"])
        if code in seen:
            raise ProviderPITTailV3Error("bak_basic contains a duplicate security")
        seen.add(code)
        if row["trade_date"] != v1._END_COMPACT:
            raise ProviderPITTailV3Error("bak_basic row escaped the cutoff")
        list_date = row["list_date"]
        if type(list_date) is not str:
            raise ProviderPITTailV3Error("bak_basic list_date is not a strict string")
        board = _classify_excluded_board(code)
        if list_date == "0":
            if board is None:
                raise ProviderPITTailV3Error(
                    "bak_basic list_date sentinel is outside the frozen excluded-board scope"
                )
            sentinel_entries.append(
                {
                    "ts_code": code,
                    "wire_row": [row[field] for field in call["fields"]],
                    "wire_row_canonical_sha256": hashlib.sha256(
                        v1._canonical_bytes([row[field] for field in call["fields"]])
                    ).hexdigest(),
                    "field": "list_date",
                    "raw_wire_type": "string",
                    "raw_wire_value": "0",
                    "canonical_value": "0",
                    "sentinel_reason": "excluded_board_before_date_validation",
                    "excluded_board": board,
                    "excluded_before_date_validation": True,
                    "scope_rule_id": BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT["scope_rule_id"],
                    "scope_rule_canonical_sha256": BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT_SHA256,
                }
            )
        else:
            if v1._compact_date(list_date, "bak_basic list_date") > v1._END_COMPACT:
                raise ProviderPITTailV3Error("bak_basic contains a future listing")
        if not isinstance(row["name"], str) or not row["name"]:
            raise ProviderPITTailV3Error("bak_basic name is invalid")
        if row["industry"] is not None and not isinstance(row["industry"], str):
            raise ProviderPITTailV3Error("bak_basic industry is invalid")

    wire_items_sha = hashlib.sha256(v1._canonical_bytes(data["items"])).hexdigest()
    wire_metadata = {
        "msg": payload["msg"],
        "detail": payload["detail"],
        "request_id": payload["request_id"],
        "count": data["count"],
        "has_more": data["has_more"],
    }
    wire_envelope_sha = hashlib.sha256(v1._canonical_bytes(wire_metadata)).hexdigest()
    normalization = {
        "schema": BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT["schema"],
        "contract_canonical_sha256": BAK_BASIC_LIST_DATE_SENTINEL_CONTRACT_SHA256,
        "wire_envelope_contract_canonical_sha256": BAK_BASIC_WIRE_ENVELOPE_CONTRACT_SHA256,
        "endpoint": "bak_basic",
        "field": "list_date",
        "raw_wire_preserved": True,
        "raw_body_bytes": len(raw_body),
        "raw_body_sha256": hashlib.sha256(raw_body).hexdigest(),
        "wire_items_canonical_sha256": wire_items_sha,
        "wire_envelope_canonical_sha256": wire_envelope_sha,
        "wire_metadata": wire_metadata,
        "entries": sentinel_entries,
    }
    normalization_sha = hashlib.sha256(v1._canonical_bytes(normalization)).hexdigest()
    return {
        "rows": rows,
        "row_count": len(rows),
        "response_fields": list(call["fields"]),
        "raw_body_bytes": len(raw_body),
        "raw_body_sha256": hashlib.sha256(raw_body).hexdigest(),
        "wire_envelope_canonical_sha256": wire_envelope_sha,
        "wire_items_canonical_sha256": wire_items_sha,
        "rows_canonical_sha256": hashlib.sha256(v1._canonical_bytes(rows)).hexdigest(),
        "semantic_empty": False,
        "normalization_evidence": normalization,
        "normalization_canonical_sha256": normalization_sha,
    }


def validate_provider_envelope_v3(
    call: Mapping[str, Any],
    raw_body: bytes,
) -> dict[str, Any]:
    """Validate the frozen v3 call, preserving only the explicit bak_basic sentinel."""

    try:
        frozen = v2._frozen_call(call)
        if frozen["endpoint"] == "bak_basic":
            return _parse_bak_basic(frozen, raw_body)
        return v2.validate_provider_envelope_v2(frozen, raw_body)
    except ProviderPITTailV3Error:
        raise
    except v1.ProviderPITTailError as exc:
        raise ProviderPITTailV3Error(str(exc)) from exc
