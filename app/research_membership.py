"""Pure contracts for causally quarantined historical membership sessions.

This module deliberately has no store, clock, environment, or network access.
Every derived value is a deterministic function of the explicitly supplied
arguments, so a missing vendor snapshot cannot be silently filled from a
future snapshot or a mutable current security master.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import AbstractSet, Any, Literal


MEMBERSHIP_GENERATION_SCHEMA_VERSION = "membership-session-generations/v1"
MEMBERSHIP_POLICY_VERSION = "strictly-past-anchor-full-session-buy-quarantine/v1"

_SOURCE_KINDS = {"causal_carry_forward", "pre_anchor_quarantine"}
_ANCHOR_REQUIRED_FIELDS = {"ts_code", "exchange", "name", "industry", "list_date"}
_ROW_FIELDS = {
    "trade_date",
    "ts_code",
    "exchange",
    "name",
    "industry",
    "list_date",
    "membership_present",
    "metadata_stale_possible",
    "unknown_metadata",
    "observed_in_daily",
    "signal_session_eligible",
    "signal_eligible",
    "source_kind",
    "metadata_anchor_date",
}
_EXCHANGE_BY_SUFFIX = {"SH": "SSE", "SZ": "SZSE"}
_TS_CODE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
_SECRET_KEYS = {"token", "api_key", "apikey", "secret"}
_NONCANONICAL_AUTHORITY_KEYS = {"role", "contract_sha256", "temporal_authority"}
_ANCHOR_DESCRIPTOR_FIELDS = {
    "trade_date",
    "receipt_dataset",
    "receipt_partition",
    "receipt_raw_sha256",
    "normalized_sha256",
    "row_count",
    "in_scope_row_count",
    "temporal_role",
    "temporal_contract_sha256",
}
_EMPTY_EVIDENCE_DESCRIPTOR_FIELDS = {
    "trade_date",
    "attempt_id",
    "raw_sha256",
    "request_semantics_sha256",
    "terminal_status",
    "classification_kind",
    "temporal_role",
    "temporal_contract_sha256",
}
_MARKET_DESCRIPTOR_FIELDS = {
    "trade_date",
    "generation_id",
    "manifest_sha256",
    "lineage_sha256",
    "temporal_role",
    "temporal_contract_sha256",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "policy_version",
    "trade_date",
    "source_kind",
    "signal_session_eligible",
    "anchor",
    "empty_evidence",
    "market_generation",
    "temporal_authority",
    "rows",
    "summary",
    "manifest_sha256",
}


class MembershipContractError(ValueError):
    """Raised when membership evidence or a projection fails closed."""


@dataclass(frozen=True)
class SnapshotBodyClassification:
    """Result of parsing a successful native membership response body."""

    kind: Literal["semantic_empty", "nonempty"]
    response_code: int
    items_count: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class MembershipProjection:
    """Complete, immutable, BUY-ineligible projection for one session."""

    trade_date: str
    source_kind: Literal["causal_carry_forward", "pre_anchor_quarantine"]
    metadata_anchor_date: str | None
    signal_session_eligible: bool
    rows: tuple[Mapping[str, Any], ...]
    unknown_codes: tuple[str, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise MembershipContractError(f"duplicate JSON key: {key!r}")
        output[key] = value
    return output


def _reject_json_constant(value: str) -> None:
    raise MembershipContractError(f"non-finite JSON constant is forbidden: {value}")


def _canonical_iso_date(value: Any, label: str) -> str:
    if type(value) is not str:
        raise MembershipContractError(f"{label} must be a canonical ISO YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MembershipContractError(f"{label} must be a canonical ISO YYYY-MM-DD string") from exc
    if parsed.isoformat() != value:
        raise MembershipContractError(f"{label} must be canonical ISO YYYY-MM-DD")
    return value


def _canonical_optional_date(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _canonical_iso_date(value, label)


def _canonical_ts_code(value: Any, label: str = "ts_code") -> str:
    if type(value) is not str or not _TS_CODE.fullmatch(value):
        raise MembershipContractError(f"invalid {label}: {value!r}")
    if value.rsplit(".", 1)[-1] not in _EXCHANGE_BY_SUFFIX:
        raise MembershipContractError(f"{label} is outside the frozen exchange scope")
    symbol, suffix = value.split(".", 1)
    if (suffix == "SH" and symbol.startswith("900")) or (
        suffix == "SZ" and symbol.startswith("200")
    ):
        raise MembershipContractError(f"{label} is outside the frozen A-share scope")
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MembershipContractError("membership payload is not canonical JSON") from exc


def _manifest_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_secret_key(key: str) -> bool:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key.strip())
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    normalized = separated.lower().replace("-", "_")
    padded = f"_{normalized}_"
    return any(f"_{secret}_" in padded for secret in _SECRET_KEYS)


def _canonical_copy(value: Any, *, path: str, _active_container_ids: set[int] | None = None) -> Any:
    """Return an owned, JSON-only, key-sorted copy and reject secret key names."""

    active = _active_container_ids if _active_container_ids is not None else set()
    is_mapping = isinstance(value, Mapping)
    is_sequence = isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    if is_mapping or is_sequence:
        identity = id(value)
        if identity in active:
            raise MembershipContractError(f"{path} contains a circular container")
        active.add(identity)
        try:
            if is_mapping:
                copied: dict[str, Any] = {}
                for raw_key, item in value.items():
                    if type(raw_key) is not str or not raw_key:
                        raise MembershipContractError(f"{path} contains a non-string or empty key")
                    if _is_secret_key(raw_key):
                        raise MembershipContractError(
                            f"secret-bearing key name is forbidden in {path}: {raw_key!r}"
                        )
                    copied[raw_key] = _canonical_copy(
                        item,
                        path=f"{path}.{raw_key}",
                        _active_container_ids=active,
                    )
                return {key: copied[key] for key in sorted(copied)}
            return [
                _canonical_copy(
                    item,
                    path=f"{path}[{index}]",
                    _active_container_ids=active,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise MembershipContractError(f"{path} contains a non-JSON value")


def classify_membership_snapshot_body(
    raw_bytes: bytes, *, required_fields: Sequence[str]
) -> SnapshotBodyClassification:
    """Classify a native code-0 body without converting malformed data to empty.

    Only a UTF-8 JSON object whose native response code is exactly integer zero,
    whose canonical field header contains every requested field, and whose
    ``items`` member is an actual list can reach a classification result.
    """

    if type(raw_bytes) is not bytes:
        raise MembershipContractError("membership snapshot body must be bytes")
    if isinstance(required_fields, (str, bytes, bytearray)) or not isinstance(
        required_fields, Sequence
    ):
        raise MembershipContractError("required_fields must be a sequence of field names")
    required = tuple(required_fields)
    if any(type(field) is not str or not field for field in required):
        raise MembershipContractError("required_fields contains an invalid field name")
    if len(set(required)) != len(required):
        raise MembershipContractError("required_fields contains a duplicate field")

    try:
        text = raw_bytes.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise MembershipContractError("membership snapshot body is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise MembershipContractError("membership snapshot body must be a JSON object")

    response_code = payload.get("code")
    if type(response_code) is not int or response_code != 0:
        raise MembershipContractError("membership snapshot response code must be integer zero")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MembershipContractError("membership snapshot data must be a JSON object")
    fields = data.get("fields")
    if not isinstance(fields, list) or any(type(field) is not str or not field for field in fields):
        raise MembershipContractError("membership snapshot fields must be field-name strings")
    if len(set(fields)) != len(fields):
        raise MembershipContractError("membership snapshot contains a duplicate field")
    missing = [field for field in required if field not in fields]
    if missing:
        raise MembershipContractError(
            "membership snapshot is missing required fields: " + ", ".join(missing)
        )
    items = data.get("items")
    if not isinstance(items, list):
        raise MembershipContractError("membership snapshot items must be a list")

    count = len(items)
    return SnapshotBodyClassification(
        kind="semantic_empty" if count == 0 else "nonempty",
        response_code=response_code,
        items_count=count,
        fields=tuple(fields),
    )


def _normalize_anchor_row(raw: Mapping[str, Any], *, anchor_trade_date: str) -> dict[str, Any]:
    allowed = _ANCHOR_REQUIRED_FIELDS | {"trade_date"}
    row_fields = set(raw)
    if row_fields != _ANCHOR_REQUIRED_FIELDS and row_fields != allowed:
        raise MembershipContractError(
            "anchor row fields must be exactly ts_code/exchange/name/industry/list_date "
            "with optional trade_date"
        )
    if (
        "trade_date" in raw
        and _canonical_iso_date(raw["trade_date"], "anchor row trade_date") != anchor_trade_date
    ):
        raise MembershipContractError("anchor row trade_date differs from anchor")

    ts_code = _canonical_ts_code(raw.get("ts_code"))
    exchange = raw.get("exchange")
    expected_exchange = _EXCHANGE_BY_SUFFIX[ts_code.rsplit(".", 1)[-1]]
    if type(exchange) is not str or exchange != expected_exchange:
        raise MembershipContractError(f"anchor exchange conflicts with ts_code: {ts_code}")
    name = raw.get("name")
    if type(name) is not str or not name.strip() or name != name.strip():
        raise MembershipContractError(f"anchor name is invalid: {ts_code}")
    industry = raw.get("industry")
    if industry is not None and (
        type(industry) is not str or industry != industry.strip() or not industry
    ):
        raise MembershipContractError(f"anchor industry is invalid: {ts_code}")
    list_date = _canonical_iso_date(raw.get("list_date"), "anchor list_date")
    if list_date > anchor_trade_date:
        raise MembershipContractError("anchor list_date is after anchor trade_date")
    return {
        "ts_code": ts_code,
        "exchange": exchange,
        "name": name,
        "industry": industry,
        "list_date": list_date,
    }


def derive_quarantined_membership(
    *,
    trade_date: str,
    anchor_trade_date: str | None,
    anchor_rows: Sequence[Mapping[str, Any]],
    daily_codes: AbstractSet[str],
) -> MembershipProjection:
    """Derive a deterministic full-session BUY quarantine from explicit inputs."""

    target = _canonical_iso_date(trade_date, "trade_date")
    if isinstance(anchor_rows, (str, bytes, bytearray)) or not isinstance(anchor_rows, Sequence):
        raise MembershipContractError("anchor_rows must be a sequence of mappings")
    if isinstance(daily_codes, (str, bytes, bytearray)) or not isinstance(daily_codes, Set):
        raise MembershipContractError("daily_codes must be a set of canonical ts_codes")

    daily = {_canonical_ts_code(code, "daily ts_code") for code in daily_codes}
    if anchor_trade_date is None:
        if anchor_rows:
            raise MembershipContractError("anchor rows are forbidden without an anchor")
        anchor = None
        source_kind = "pre_anchor_quarantine"
    else:
        anchor = _canonical_iso_date(anchor_trade_date, "anchor_trade_date")
        if anchor >= target:
            raise MembershipContractError("membership anchor must be strictly earlier than target")
        source_kind = "causal_carry_forward"

    known_by_code: dict[str, dict[str, Any]] = {}
    if anchor is not None:
        for raw in anchor_rows:
            if not isinstance(raw, Mapping):
                raise MembershipContractError("each anchor row must be a mapping")
            known = _normalize_anchor_row(raw, anchor_trade_date=anchor)
            code = known["ts_code"]
            if code in known_by_code:
                raise MembershipContractError(f"duplicate anchor code: {code}")
            known_by_code[code] = known

    rows: list[dict[str, Any]] = []
    for code in sorted(known_by_code):
        metadata = known_by_code[code]
        rows.append(
            {
                "trade_date": target,
                **metadata,
                "membership_present": True,
                "metadata_stale_possible": True,
                "unknown_metadata": False,
                "observed_in_daily": code in daily,
                "signal_session_eligible": False,
                "signal_eligible": False,
                "source_kind": source_kind,
                "metadata_anchor_date": anchor,
            }
        )

    unknown_codes = tuple(sorted(daily - set(known_by_code)))
    for code in unknown_codes:
        rows.append(
            {
                "trade_date": target,
                "ts_code": code,
                "exchange": None,
                "name": None,
                "industry": None,
                "list_date": None,
                "membership_present": False,
                "metadata_stale_possible": False,
                "unknown_metadata": True,
                "observed_in_daily": True,
                "signal_session_eligible": False,
                "signal_eligible": False,
                "source_kind": source_kind,
                "metadata_anchor_date": anchor,
            }
        )

    ordered = tuple(MappingProxyType(row) for row in sorted(rows, key=lambda item: item["ts_code"]))
    return MembershipProjection(
        trade_date=target,
        source_kind=source_kind,
        metadata_anchor_date=anchor,
        signal_session_eligible=False,
        rows=ordered,
        unknown_codes=unknown_codes,
    )


def _require_sha256(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MembershipContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_nonempty_identifier(value: Any, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise MembershipContractError(f"{label} must be a non-empty canonical string")
    return value


def _require_descriptor_fields(value: Mapping[str, Any], required: set[str], *, label: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        suffix = (
            "; descriptor requires complete temporal authority"
            if {"temporal_role", "temporal_contract_sha256"} & set(missing)
            else ""
        )
        raise MembershipContractError(
            f"{label} descriptor is missing required fields: {', '.join(missing)}{suffix}"
        )


def _canonical_temporal_authority(value: Mapping[str, Any], *, label: str) -> tuple[str, str]:
    forbidden = sorted(_NONCANONICAL_AUTHORITY_KEYS & set(value))
    if forbidden:
        raise MembershipContractError(
            f"{label} authority must use canonical top-level temporal_role and "
            "temporal_contract_sha256 only"
        )
    if "temporal_role" not in value or "temporal_contract_sha256" not in value:
        raise MembershipContractError(f"{label} requires complete temporal authority")
    role = _require_nonempty_identifier(value["temporal_role"], f"{label} temporal_role")
    contract = _require_sha256(
        value["temporal_contract_sha256"], f"{label} temporal_contract_sha256"
    )
    return role, contract


def _validate_bound_authority(
    value: Mapping[str, Any], *, expected_role: str, expected_contract: str, label: str
) -> None:
    role, contract = _canonical_temporal_authority(value, label=label)
    if role != expected_role:
        raise MembershipContractError(f"{label} temporal role differs from authority")
    if contract != expected_contract:
        raise MembershipContractError(f"{label} temporal contract differs from authority")


def _validate_anchor_descriptor(
    anchor: Mapping[str, Any], *, target: str, expected_role: str, expected_contract: str
) -> str:
    _require_descriptor_fields(anchor, _ANCHOR_DESCRIPTOR_FIELDS, label="anchor")
    anchor_date = _canonical_iso_date(anchor["trade_date"], "anchor descriptor trade_date")
    if anchor_date >= target:
        raise MembershipContractError("membership anchor must be strictly earlier than target")
    if anchor["receipt_dataset"] != "bak_basic":
        raise MembershipContractError("anchor descriptor receipt_dataset must be bak_basic")
    if anchor["receipt_partition"] != anchor_date:
        raise MembershipContractError("anchor descriptor receipt_partition must equal trade_date")
    _require_sha256(anchor["receipt_raw_sha256"], "anchor descriptor receipt_raw_sha256")
    _require_sha256(anchor["normalized_sha256"], "anchor descriptor normalized_sha256")
    if type(anchor["row_count"]) is not int or anchor["row_count"] <= 0:
        raise MembershipContractError("anchor descriptor row_count must be a positive integer")
    if (
        type(anchor["in_scope_row_count"]) is not int
        or anchor["in_scope_row_count"] <= 0
        or anchor["in_scope_row_count"] > anchor["row_count"]
    ):
        raise MembershipContractError(
            "anchor descriptor in_scope_row_count must be a positive integer no greater "
            "than row_count"
        )
    _validate_bound_authority(
        anchor,
        expected_role=expected_role,
        expected_contract=expected_contract,
        label="anchor",
    )
    return anchor_date


def _validate_empty_evidence_descriptor(
    evidence: Mapping[str, Any],
    *,
    target: str,
    expected_role: str,
    expected_contract: str,
) -> str:
    _require_descriptor_fields(evidence, _EMPTY_EVIDENCE_DESCRIPTOR_FIELDS, label="empty evidence")
    if (
        _canonical_iso_date(evidence["trade_date"], "empty evidence descriptor trade_date")
        != target
    ):
        raise MembershipContractError("empty evidence trade_date differs from target")
    attempt_id = _require_nonempty_identifier(
        evidence["attempt_id"], "empty evidence descriptor attempt_id"
    )
    _require_sha256(evidence["raw_sha256"], "empty evidence descriptor raw_sha256")
    _require_sha256(
        evidence["request_semantics_sha256"],
        "empty evidence descriptor request_semantics_sha256",
    )
    if evidence["terminal_status"] != "invalid_json":
        raise MembershipContractError(
            "empty evidence descriptor terminal_status must be invalid_json"
        )
    if evidence["classification_kind"] != "semantic_empty":
        raise MembershipContractError(
            "empty evidence descriptor classification_kind must be semantic_empty"
        )
    _validate_bound_authority(
        evidence,
        expected_role=expected_role,
        expected_contract=expected_contract,
        label="empty evidence",
    )
    return attempt_id


def _validate_market_descriptor(
    market: Mapping[str, Any],
    *,
    target: str,
    expected_role: str,
    expected_contract: str,
) -> None:
    _require_descriptor_fields(market, _MARKET_DESCRIPTOR_FIELDS, label="market generation")
    if (
        _canonical_iso_date(market["trade_date"], "market generation descriptor trade_date")
        != target
    ):
        raise MembershipContractError("market generation trade_date differs from target")
    _require_nonempty_identifier(
        market["generation_id"], "market generation descriptor generation_id"
    )
    _require_sha256(market["manifest_sha256"], "market generation descriptor manifest_sha256")
    _require_sha256(market["lineage_sha256"], "market generation descriptor lineage_sha256")
    _validate_bound_authority(
        market,
        expected_role=expected_role,
        expected_contract=expected_contract,
        label="market generation",
    )


def _validate_manifest_row(
    raw: Mapping[str, Any], *, target: str, source_kind: str, anchor_date: str | None
) -> dict[str, Any]:
    row = _canonical_copy(raw, path="rows[]")
    if set(row) != _ROW_FIELDS:
        raise MembershipContractError("membership manifest row fields differ from policy")
    if row["trade_date"] != target:
        raise MembershipContractError("membership row trade_date differs from target")
    code = _canonical_ts_code(row["ts_code"])
    if row["source_kind"] != source_kind:
        raise MembershipContractError("membership row source_kind differs from manifest")
    if row["metadata_anchor_date"] != anchor_date:
        raise MembershipContractError("membership row anchor differs from manifest")
    if row["signal_session_eligible"] is not False or row["signal_eligible"] is not False:
        raise MembershipContractError("derived membership rows must be BUY-ineligible")
    for field in (
        "membership_present",
        "metadata_stale_possible",
        "unknown_metadata",
        "observed_in_daily",
    ):
        if type(row[field]) is not bool:
            raise MembershipContractError(f"membership row {field} must be boolean")

    if source_kind == "pre_anchor_quarantine" and (
        row["membership_present"]
        or not row["unknown_metadata"]
        or row["metadata_stale_possible"]
        or not row["observed_in_daily"]
        or any(row[field] is not None for field in ("exchange", "name", "industry", "list_date"))
    ):
        raise MembershipContractError(
            "pre-anchor rows must be unknown observed rows with empty metadata"
        )

    if row["unknown_metadata"]:
        if (
            row["membership_present"]
            or row["metadata_stale_possible"]
            or not row["observed_in_daily"]
            or any(
                row[field] is not None for field in ("exchange", "name", "industry", "list_date")
            )
        ):
            raise MembershipContractError("unknown membership row flags or metadata conflict")
    else:
        if not row["membership_present"] or not row["metadata_stale_possible"]:
            raise MembershipContractError("carried membership row flags conflict")
        expected_exchange = _EXCHANGE_BY_SUFFIX[code.rsplit(".", 1)[-1]]
        if row["exchange"] != expected_exchange:
            raise MembershipContractError("carried membership exchange conflicts with ts_code")
        if type(row["name"]) is not str or not row["name"]:
            raise MembershipContractError("carried membership name is missing")
        if row["industry"] is not None and type(row["industry"]) is not str:
            raise MembershipContractError("carried membership industry is invalid")
        list_date = _canonical_iso_date(row["list_date"], "membership row list_date")
        if anchor_date is None or list_date > anchor_date:
            raise MembershipContractError(
                "membership row list_date must not follow its metadata anchor"
            )
    return row


def build_membership_manifest(
    *,
    trade_date: str,
    source_kind: str,
    anchor: Mapping[str, Any] | None,
    empty_evidence: Sequence[Mapping[str, Any]],
    market_generation: Mapping[str, Any],
    temporal_authority: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and hash a deterministic membership-generation manifest.

    The public contract intentionally returns a JSON-serializable mutable
    ``dict``. Callers must recompute or verify ``manifest_sha256`` immediately
    before persistence; mutating the returned payload invalidates its root.
    """

    target = _canonical_iso_date(trade_date, "trade_date")
    if type(source_kind) is not str or source_kind not in _SOURCE_KINDS:
        raise MembershipContractError("unsupported membership source_kind")
    if not isinstance(temporal_authority, Mapping):
        raise MembershipContractError("temporal_authority must be a mapping")
    authority = _canonical_copy(temporal_authority, path="temporal_authority")
    expected_role, expected_contract = _canonical_temporal_authority(
        authority, label="temporal_authority"
    )

    if source_kind == "causal_carry_forward":
        if not isinstance(anchor, Mapping):
            raise MembershipContractError("causal carry-forward requires an anchor")
        anchor_copy = _canonical_copy(anchor, path="anchor")
        anchor_date = _validate_anchor_descriptor(
            anchor_copy,
            target=target,
            expected_role=expected_role,
            expected_contract=expected_contract,
        )
    else:
        if anchor is not None:
            raise MembershipContractError("pre-anchor quarantine must not contain an anchor")
        anchor_copy = None
        anchor_date = None

    if (
        isinstance(empty_evidence, (str, bytes, bytearray))
        or not isinstance(empty_evidence, Sequence)
        or not empty_evidence
    ):
        raise MembershipContractError("empty_evidence must contain at least one mapping")
    evidence_copies = []
    evidence_attempt_ids: set[str] = set()
    for item in empty_evidence:
        if not isinstance(item, Mapping):
            raise MembershipContractError("empty_evidence entries must be mappings")
        copied = _canonical_copy(item, path="empty_evidence[]")
        attempt_id = _validate_empty_evidence_descriptor(
            copied,
            target=target,
            expected_role=expected_role,
            expected_contract=expected_contract,
        )
        if attempt_id in evidence_attempt_ids:
            raise MembershipContractError(f"duplicate empty evidence attempt_id: {attempt_id}")
        evidence_attempt_ids.add(attempt_id)
        evidence_copies.append(copied)
    evidence_copies.sort(key=lambda item: item["attempt_id"])

    if not isinstance(market_generation, Mapping):
        raise MembershipContractError("market_generation must be a mapping")
    market_copy = _canonical_copy(market_generation, path="market_generation")
    _validate_market_descriptor(
        market_copy,
        target=target,
        expected_role=expected_role,
        expected_contract=expected_contract,
    )

    if isinstance(rows, (str, bytes, bytearray)) or not isinstance(rows, Sequence):
        raise MembershipContractError("rows must be a sequence of mappings")
    normalized_rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise MembershipContractError("membership rows must be mappings")
        row = _validate_manifest_row(
            raw,
            target=target,
            source_kind=source_kind,
            anchor_date=anchor_date,
        )
        code = row["ts_code"]
        if code in seen_codes:
            raise MembershipContractError(f"duplicate membership row code: {code}")
        seen_codes.add(code)
        normalized_rows.append(row)
    normalized_rows.sort(key=lambda item: item["ts_code"])

    summary = {
        "row_count": len(normalized_rows),
        "membership_present_count": sum(row["membership_present"] for row in normalized_rows),
        "observed_in_daily_count": sum(row["observed_in_daily"] for row in normalized_rows),
        "unknown_metadata_count": sum(row["unknown_metadata"] for row in normalized_rows),
        "metadata_stale_possible_count": sum(
            row["metadata_stale_possible"] for row in normalized_rows
        ),
        "signal_eligible_count": 0,
        "signal_session_eligible": False,
    }
    if (
        anchor_copy is not None
        and anchor_copy["in_scope_row_count"] != summary["membership_present_count"]
    ):
        raise MembershipContractError(
            "anchor in_scope_row_count differs from projection membership count"
        )
    payload = {
        "schema_version": MEMBERSHIP_GENERATION_SCHEMA_VERSION,
        "policy_version": MEMBERSHIP_POLICY_VERSION,
        "trade_date": target,
        "source_kind": source_kind,
        "signal_session_eligible": False,
        "anchor": anchor_copy,
        "empty_evidence": evidence_copies,
        "market_generation": market_copy,
        "temporal_authority": authority,
        "rows": normalized_rows,
        "summary": summary,
    }
    return {**payload, "manifest_sha256": _manifest_sha256(payload)}


def verify_membership_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild and verify a mutable manifest immediately before persistence."""

    if not isinstance(manifest, Mapping):
        raise MembershipContractError("membership manifest must be a mapping")
    copied = _canonical_copy(manifest, path="manifest")
    if set(copied) != _MANIFEST_FIELDS:
        raise MembershipContractError("membership manifest fields differ from policy")
    claimed_hash = _require_sha256(copied["manifest_sha256"], "manifest manifest_sha256")
    rebuilt = build_membership_manifest(
        trade_date=copied["trade_date"],
        source_kind=copied["source_kind"],
        anchor=copied["anchor"],
        empty_evidence=copied["empty_evidence"],
        market_generation=copied["market_generation"],
        temporal_authority=copied["temporal_authority"],
        rows=copied["rows"],
    )
    if claimed_hash != rebuilt["manifest_sha256"]:
        raise MembershipContractError("membership manifest hash mismatch")
    if copied != rebuilt:
        raise MembershipContractError("membership manifest differs from canonical rebuild")
    return rebuilt
