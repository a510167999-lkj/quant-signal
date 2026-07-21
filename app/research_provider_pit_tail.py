"""Frozen six-call provider PIT tail plan and response validation."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from app.research_provider_evidence_partitions import (
    EXPECTED_PROVIDER_EVIDENCE_CONTRACT_SHA256,
    PROVIDER_EVIDENCE_CONTRACT_PATH,
    assert_provider_evidence_range_allowed,
)


class ProviderPITTailError(ValueError):
    """The frozen provider PIT tail plan or response is invalid."""


_CUTOFF = "2026-07-10"
_START_COMPACT = "20260704"
_END_COMPACT = "20260710"
_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_LEGACY_RUN_ID = "daaf52069b5544888214ba2f8fd6690e896b043301c49e227030a389da5d4e73"
_LEGACY_RUN_TREE = "9b2eb8aae31f04059a3d81edc282d299facf1faaf9727b282e8510c9b1515f86"
_LEGACY_MANIFEST_FILE_SHA = "973dff3278c6aa4696c1d5dcd42439b8132897d2b34a22e21474ec71170aa449"
_LEGACY_RECEIPT_TREE = "ada4f6a1b5c36269f862a3b47c0eece7d8a6169079341a5dddc82da59b2bcc00"
_INVALID_AUDIT_CANONICAL_SHA = "160363910a9f36bb5f278f922329a12e25d8a7110460ebfb2bee9830f8873311"
_INVALID_AUDIT_FILE_SHA = "2a0fc55630b673045d1f85cd13dcbb5c7846b0147dd8701216e12da1d08bdd72"


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderPITTailError("value is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ProviderPITTailError("authority contract file is unavailable") from exc
    return digest.hexdigest()


def _call(
    sequence: int,
    role: str,
    endpoint: str,
    params: dict[str, str],
    fields: list[str],
    row_cap: int,
) -> dict[str, Any]:
    semantics = {
        "endpoint": endpoint,
        "params": params,
        "fields": fields,
        "row_cap": row_cap,
    }
    return {
        "sequence": sequence,
        "role": role,
        **semantics,
        "empty_allowed": False,
        "request_semantics_sha256": _sha256_bytes(_canonical_bytes(semantics)),
    }


def _calls() -> list[dict[str, Any]]:
    return [
        _call(
            0,
            "calendar_sse",
            "trade_cal",
            {"exchange": "SSE", "start_date": _START_COMPACT, "end_date": _END_COMPACT},
            ["exchange", "cal_date", "is_open", "pretrade_date"],
            8,
        ),
        _call(
            1,
            "calendar_szse",
            "trade_cal",
            {"exchange": "SZSE", "start_date": _START_COMPACT, "end_date": _END_COMPACT},
            ["exchange", "cal_date", "is_open", "pretrade_date"],
            8,
        ),
        _call(
            2,
            "provider_universe_anchor",
            "bak_basic",
            {"trade_date": _END_COMPACT},
            ["trade_date", "ts_code", "name", "industry", "list_date"],
            7000,
        ),
        _call(
            3,
            "risk_stock_st_cutoff",
            "stock_st",
            {"trade_date": _END_COMPACT},
            ["ts_code", "name", "type", "type_name", "trade_date"],
            10000,
        ),
        _call(
            4,
            "risk_suspend_cutoff",
            "suspend_d",
            {"trade_date": _END_COMPACT},
            ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
            10000,
        ),
        _call(
            5,
            "risk_namechange_tail",
            "namechange",
            {"start_date": _START_COMPACT, "end_date": _END_COMPACT},
            ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"],
            10000,
        ),
    ]


def _expected_plan(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    assert_provider_evidence_range_allowed(
        authority_contract,
        "2026-07-04",
        _CUTOFF,
        "provider_evidence_collect",
    )
    if authority_contract.get("contract_sha256") != EXPECTED_PROVIDER_EVIDENCE_CONTRACT_SHA256:
        raise ProviderPITTailError("provider evidence authority mismatch")
    payload: dict[str, Any] = {
        "schema": "provider-pit-tail-plan/v1",
        "evidence_role": "research_development_provider_pit",
        "official_exchange_pit": False,
        "data_cutoff": _CUTOFF,
        "collection_window": {"start_date": "2026-07-04", "end_date": _CUTOFF},
        "authority": {
            "schema": authority_contract["schema_version"],
            "contract_path": PROVIDER_EVIDENCE_CONTRACT_PATH.relative_to(
                PROVIDER_EVIDENCE_CONTRACT_PATH.parents[4]
            ).as_posix(),
            "contract_file_sha256": _file_sha256(PROVIDER_EVIDENCE_CONTRACT_PATH),
            "contract_sha256": authority_contract["contract_sha256"],
            "operation": "provider_evidence_collect",
        },
        "execution": {
            "workers": 1,
            "batch_size": 1,
            "max_attempts": 1,
            "retries": 0,
            "cache": 0,
        },
        "legacy_risk_lineage": {
            "run_id": _LEGACY_RUN_ID,
            "run_tree_sha256": _LEGACY_RUN_TREE,
            "manifest_file_sha256": _LEGACY_MANIFEST_FILE_SHA,
            "receipt_tree_sha256": _LEGACY_RECEIPT_TREE,
            "receipt_count": 39,
            "covered_through": "2026-07-03",
            "status": "invalid_fail_closed",
            "invalid_audit_canonical_sha256": _INVALID_AUDIT_CANONICAL_SHA,
            "invalid_audit_file_sha256": _INVALID_AUDIT_FILE_SHA,
            "historical_run_success_adopted": False,
        },
        "planned_calls": 6,
        "endpoint_counts": {
            "adj_factor": 0,
            "bak_basic": 1,
            "daily": 0,
            "namechange": 1,
            "stock_basic": 0,
            "stock_st": 1,
            "stk_limit": 0,
            "suspend_d": 1,
            "trade_cal": 2,
        },
        "calls": _calls(),
        "safety": {
            "current_snapshot_crosscheck_only": True,
            "paper_trading_validated": False,
            "live_ready": False,
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
            "strategy_or_validation_authorized": False,
        },
    }
    payload["plan_canonical_sha256"] = _sha256_bytes(_canonical_bytes(payload))
    return payload


def build_provider_pit_tail_plan(authority_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return the only six-call plan authorized by the temporal successor."""

    return copy.deepcopy(_expected_plan(authority_contract))


def validate_provider_pit_tail_plan(
    plan: Any,
    authority_contract: Mapping[str, Any],
) -> None:
    if not isinstance(plan, dict) or plan != _expected_plan(authority_contract):
        raise ProviderPITTailError("provider PIT tail plan differs from the frozen contract")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderPITTailError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ProviderPITTailError(f"non-finite JSON constant is forbidden: {value}")


def _compact_date(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ProviderPITTailError(f"{label} is not a compact calendar date")
    try:
        parsed = date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError as exc:
        raise ProviderPITTailError(f"{label} is not a compact calendar date") from exc
    if parsed.strftime("%Y%m%d") != value:
        raise ProviderPITTailError(f"{label} is not a compact calendar date")
    return value


def _validate_code(value: Any) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise ProviderPITTailError("provider row contains an invalid security code")
    return value


def _rows_from_envelope(call: Mapping[str, Any], payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"code", "msg", "data"}:
        raise ProviderPITTailError("provider response envelope is invalid")
    if type(payload["code"]) is not int or payload["code"] != 0:
        raise ProviderPITTailError("provider returned a non-success code")
    if payload["msg"] not in {None, "success"}:
        raise ProviderPITTailError("provider returned a non-success message")
    data = payload["data"]
    if not isinstance(data, dict) or set(data) != {"fields", "items"}:
        raise ProviderPITTailError("provider response data envelope is invalid")
    if data["fields"] != call["fields"] or not isinstance(data["items"], list):
        raise ProviderPITTailError("provider response fields or items are invalid")
    if not data["items"] and call["empty_allowed"] is not True:
        raise ProviderPITTailError("provider response is semantically empty")
    if len(data["items"]) >= call["row_cap"]:
        raise ProviderPITTailError("provider response reached the frozen row cap")
    rows: list[dict[str, Any]] = []
    for item in data["items"]:
        if not isinstance(item, list) or len(item) != len(call["fields"]):
            raise ProviderPITTailError("provider row shape is invalid")
        rows.append(dict(zip(call["fields"], item)))
    if len({_canonical_bytes(row) for row in rows}) != len(rows):
        raise ProviderPITTailError("provider response contains a duplicate row")
    return rows


def _validate_trade_cal(call: Mapping[str, Any], rows: list[dict[str, Any]]) -> None:
    expected_dates = [
        (date(2026, 7, 4) + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(7)
    ]
    if [row["cal_date"] for row in rows] != expected_dates:
        raise ProviderPITTailError("trade_cal does not contain the exact ordered window")
    for row in rows:
        if (
            row["exchange"] != call["params"]["exchange"]
            or type(row["is_open"]) is not int
            or row["is_open"] not in {0, 1}
        ):
            raise ProviderPITTailError("trade_cal exchange or open flag is invalid")
        _compact_date(row["cal_date"], "trade_cal cal_date")
        _compact_date(row["pretrade_date"], "trade_cal pretrade_date")


def _validate_bak_basic(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        code = _validate_code(row["ts_code"])
        if code in seen:
            raise ProviderPITTailError("bak_basic contains a duplicate security")
        seen.add(code)
        if row["trade_date"] != _END_COMPACT:
            raise ProviderPITTailError("bak_basic row escaped the cutoff")
        if _compact_date(row["list_date"], "bak_basic list_date") > _END_COMPACT:
            raise ProviderPITTailError("bak_basic contains a future listing")
        if not isinstance(row["name"], str) or not row["name"]:
            raise ProviderPITTailError("bak_basic name is invalid")
        if row["industry"] is not None and not isinstance(row["industry"], str):
            raise ProviderPITTailError("bak_basic industry is invalid")


def _validate_same_day_risk(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        code = _validate_code(row["ts_code"])
        if code in seen:
            raise ProviderPITTailError("same-day risk response contains a duplicate security")
        seen.add(code)
        if row["trade_date"] != _END_COMPACT:
            raise ProviderPITTailError("same-day risk row escaped the cutoff")


def _validate_namechange(rows: list[dict[str, Any]]) -> None:
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        code = _validate_code(row["ts_code"])
        start = _compact_date(row["start_date"], "namechange start_date")
        ann = _compact_date(row["ann_date"], "namechange ann_date")
        end = row["end_date"]
        if not _START_COMPACT <= start <= _END_COMPACT or ann > _END_COMPACT:
            raise ProviderPITTailError("namechange row escaped the authorized tail")
        if end is not None:
            end = _compact_date(end, "namechange end_date")
            if end < start:
                raise ProviderPITTailError("namechange end_date precedes start_date")
        key = (code, start, str(row["name"]))
        if key in keys:
            raise ProviderPITTailError("namechange response contains a duplicate event")
        keys.add(key)


def validate_provider_envelope(call: Mapping[str, Any], raw_body: bytes) -> dict[str, Any]:
    """Strictly parse and validate one response against its frozen call spec."""

    if not isinstance(raw_body, bytes) or not raw_body:
        raise ProviderPITTailError("provider returned an empty body")
    try:
        payload = json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProviderPITTailError("provider response is not strict JSON") from exc
    rows = _rows_from_envelope(call, payload)
    endpoint = call["endpoint"]
    if endpoint == "trade_cal":
        rows.sort(key=lambda row: row["cal_date"])
        _validate_trade_cal(call, rows)
    elif endpoint == "bak_basic":
        rows.sort(key=lambda row: _canonical_bytes(row))
        _validate_bak_basic(rows)
    elif endpoint in {"stock_st", "suspend_d"}:
        rows.sort(key=lambda row: _canonical_bytes(row))
        _validate_same_day_risk(rows)
    elif endpoint == "namechange":
        rows.sort(key=lambda row: _canonical_bytes(row))
        _validate_namechange(rows)
    else:
        raise ProviderPITTailError("provider endpoint is not authorized")
    return {
        "rows": rows,
        "row_count": len(rows),
        "response_fields": list(call["fields"]),
        "rows_canonical_sha256": _sha256_bytes(_canonical_bytes(rows)),
        "semantic_empty": False,
    }


def validate_calendar_pair(sse: Mapping[str, Any], szse: Mapping[str, Any]) -> str:
    """Return the shared latest open session, rejecting any exchange divergence."""

    sse_rows = sse.get("rows")
    szse_rows = szse.get("rows")
    if not isinstance(sse_rows, list) or not isinstance(szse_rows, list):
        raise ProviderPITTailError("calendar evidence is missing")
    sse_dates = [(row.get("cal_date"), row.get("is_open")) for row in sse_rows]
    szse_dates = [(row.get("cal_date"), row.get("is_open")) for row in szse_rows]
    if sse_dates != szse_dates:
        raise ProviderPITTailError("SSE and SZSE calendars disagree")
    open_dates = [value for value, is_open in sse_dates if is_open == 1]
    if not open_dates:
        raise ProviderPITTailError("calendar window contains no common open session")
    evaluation = max(open_dates)
    if evaluation != _END_COMPACT:
        raise ProviderPITTailError("cutoff is not the shared evaluation trade date")
    return f"{evaluation[:4]}-{evaluation[4:6]}-{evaluation[6:]}"
