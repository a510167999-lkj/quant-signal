"""Frozen point-in-time universe artifacts for historical research.

The artifact in this module is intentionally narrower than a fully eligible
research bundle.  It proves daily universe membership and historical names;
raw prices, corporate actions, and executable trading rules must be frozen by
separate components before final-OOS eligibility can ever be asserted.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from app.research_authority import (
    audited_authority_from_universe,
    audited_coverage_from_universe,
    is_composite_universe,
)


SCHEMA_VERSION = "pit_universe_artifact/v1"
REQUIRED_SOURCE_HASHES = {
    "stock_basic_raw_sha256",
    "bak_basic_raw_sha256",
    "trade_cal_raw_sha256",
}
REQUIRED_MARKET_COMPONENTS = {
    "raw_execution_bars",
    "corporate_actions",
    "causal_signal_bars",
}
STRICT_EVIDENCE_SCHEMA_VERSION = "research_pit_evidence_bundle/v5"
COMPOSITE_STRICT_EVIDENCE_SCHEMA_VERSION = "research_pit_evidence_bundle/v7"
_AUDITED_AUTHORITY_KEYS = {
    "artifact_root_sha256",
    "coverage_audit_sha256",
    "temporal_contract_sha256",
    "temporal_role",
    "artifact_manifest_sha256",
    "market_generation_root_sha256",
    "stock_generation_lineage_sha256",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _safe_relative_file(root: Path, value: Any) -> Path:
    relative = Path(str(value or ""))
    if not str(value or "").strip() or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe artifact path: {value!r}")
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(relative)
    current = root_resolved
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"artifact path may not contain symlinks: {value!r}")
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"artifact path escapes bundle root: {value!r}") from exc
    if not candidate.is_file():
        raise ValueError(f"artifact component is missing: {value!r}")
    return candidate


def _file_descriptor(root: Path, path: Path) -> Dict[str, Any]:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    try:
        relative = path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"component is outside bundle root: {path}") from exc
    candidate = _safe_relative_file(root_resolved, str(relative))
    content = candidate.read_bytes()
    return {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _verify_file_descriptor(root: Path, descriptor: Mapping[str, Any]) -> Path:
    path = _safe_relative_file(root, descriptor.get("path"))
    content = path.read_bytes()
    if descriptor.get("bytes") != len(content):
        raise ValueError(f"artifact component byte count mismatch: {descriptor.get('path')}")
    if descriptor.get("sha256") != hashlib.sha256(content).hexdigest():
        raise ValueError(f"artifact component hash mismatch: {descriptor.get('path')}")
    return path


def _json_row_count(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"artifact component is not canonical JSON: {path.name}") from exc
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return len(payload["rows"])
    raise ValueError(f"artifact component must contain JSON rows: {path.name}")


def _write_content_addressed_json(
    directory: Path, filename: str, payload: Mapping[str, Any]
) -> Dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    file_sha256 = hashlib.sha256(content).hexdigest()
    if destination.exists():
        if destination.read_bytes() != content:
            raise ValueError("content-addressed JSON artifact mismatch")
    else:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{filename}.", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return {
        "filename": filename,
        "path": str(destination),
        "file_sha256": file_sha256,
        "bytes": len(content),
    }


def _iso_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _optional_iso_date(value: Any, field: str) -> Optional[str]:
    if value is None or str(value).strip() in {"", "None", "NaT", "nan"}:
        return None
    return _iso_date(value, field)


def _exchange_for(ts_code: str, declared: Any) -> str:
    suffix_map = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
    suffix = ts_code.rsplit(".", 1)[-1].upper() if "." in ts_code else ""
    exchange = str(declared or suffix_map.get(suffix) or "").strip().upper()
    aliases = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
    exchange = aliases.get(exchange, exchange)
    if exchange not in {"SSE", "SZSE", "BSE"}:
        raise ValueError(f"unsupported exchange for {ts_code}: {declared!r}")
    if suffix in suffix_map and suffix_map[suffix] != exchange:
        raise ValueError(f"exchange conflicts with ts_code: {ts_code}")
    return exchange


def _normalize_master(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    symbols = set()
    ts_codes = set()
    for raw in rows:
        ts_code = str(raw.get("ts_code") or "").strip().upper()
        symbol = str(raw.get("symbol") or ts_code.split(".", 1)[0]).strip()
        if len(symbol) != 6 or not symbol.isdigit() or "." not in ts_code:
            raise ValueError(f"invalid security identifier: {ts_code or symbol!r}")
        if symbol in symbols or ts_code in ts_codes:
            raise ValueError(f"duplicate security in master: {ts_code}")
        symbols.add(symbol)
        ts_codes.add(ts_code)
        list_status = str(raw.get("list_status") or "").strip().upper()
        if list_status not in {"L", "D", "P"}:
            raise ValueError(f"unsupported list_status for {ts_code}: {list_status!r}")
        listed_on = _iso_date(
            raw.get("list_date") or raw.get("listed_on"), "list_date"
        )
        delisted_on = _optional_iso_date(
            raw.get("delist_date") or raw.get("delisted_on"), "delist_date"
        )
        if list_status == "D" and not delisted_on:
            raise ValueError(f"delisted security requires delist_date: {ts_code}")
        if delisted_on and delisted_on < listed_on:
            raise ValueError(f"delist_date precedes list_date: {ts_code}")
        normalized.append(
            {
                "ts_code": ts_code,
                "symbol": symbol,
                "exchange": _exchange_for(ts_code, raw.get("exchange")),
                "list_status": list_status,
                "listed_on": listed_on,
                "delisted_on": delisted_on,
                "name_current_or_final": str(
                    raw.get("name") or raw.get("name_current_or_final") or ""
                ).strip(),
            }
        )
    if not normalized:
        raise ValueError("security master is empty")
    return sorted(normalized, key=lambda row: (row["symbol"], row["ts_code"]))


def _normalize_calendar(
    rows: Iterable[Any], start_date: str, end_date: str
) -> List[str]:
    sessions = set()
    for raw in rows:
        if isinstance(raw, Mapping):
            is_open = raw.get("is_open", 1)
            if str(is_open).strip().lower() not in {"1", "true"}:
                continue
            value = raw.get("cal_date") or raw.get("trade_date") or raw.get("date")
        else:
            value = raw
        session = _iso_date(value, "calendar date")
        if start_date <= session <= end_date:
            if session in sessions:
                raise ValueError(f"duplicate trading session: {session}")
            sessions.add(session)
    if not sessions:
        raise ValueError("trading calendar has no open sessions in coverage")
    return sorted(sessions)


def _is_active(master: Mapping[str, Any], session: str) -> bool:
    return master["listed_on"] <= session and (
        not master.get("delisted_on") or session < master["delisted_on"]
    )


def _normalize_daily_universe(
    rows: Iterable[Mapping[str, Any]],
    master: List[Dict[str, Any]],
    sessions: List[str],
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    by_code = {row["ts_code"]: row for row in master}
    session_set = set(sessions)
    normalized = []
    keys = set()
    for raw in rows:
        session = _iso_date(raw.get("trade_date"), "trade_date")
        if not (start_date <= session <= end_date):
            continue
        if session not in session_set:
            raise ValueError(f"daily universe row is not an open session: {session}")
        ts_code = str(raw.get("ts_code") or "").strip().upper()
        security = by_code.get(ts_code)
        if security is None:
            raise ValueError(f"daily universe security missing from master: {ts_code}")
        key = (session, security["symbol"])
        if key in keys:
            raise ValueError(f"duplicate daily universe row: {session}/{security['symbol']}")
        keys.add(key)
        if not _is_active(security, session):
            raise ValueError(
                f"daily universe security outside listing lifecycle: {session}/{ts_code}"
            )
        historical_name = str(raw.get("name") or "").strip()
        if not historical_name:
            raise ValueError(f"historical name is missing: {session}/{ts_code}")
        normalized.append(
            {
                "trade_date": session,
                "ts_code": ts_code,
                "symbol": security["symbol"],
                "exchange": security["exchange"],
                "market": "a",
                "name": historical_name,
                "industry": str(raw.get("industry") or "").strip() or None,
            }
        )
    dates_with_rows = {row["trade_date"] for row in normalized}
    missing = [session for session in sessions if session not in dates_with_rows]
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"missing daily universe snapshot for open session: {preview}")
    return sorted(normalized, key=lambda row: (row["trade_date"], row["symbol"]))


def _validate_source_manifest(source_manifest: Mapping[str, Any]) -> Dict[str, Any]:
    manifest = deepcopy(dict(source_manifest))
    if not str(manifest.get("provider") or "").strip():
        raise ValueError("source manifest requires provider")
    missing = sorted(
        key for key in REQUIRED_SOURCE_HASHES if not _valid_sha256(manifest.get(key))
    )
    if missing:
        raise ValueError(
            "source manifest requires verifiable raw hashes: " + ", ".join(missing)
        )
    return manifest


def build_pit_universe_payload(
    *,
    security_master: Iterable[Mapping[str, Any]],
    trade_calendar: Iterable[Any],
    daily_universe: Iterable[Mapping[str, Any]],
    source_manifest: Mapping[str, Any],
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """Normalize and hash an exact daily universe over a frozen date range."""

    coverage_start = _iso_date(start_date, "start_date")
    coverage_end = _iso_date(end_date, "end_date")
    if coverage_end < coverage_start:
        raise ValueError("end_date precedes start_date")
    master = _normalize_master(security_master)
    sessions = _normalize_calendar(trade_calendar, coverage_start, coverage_end)
    daily = _normalize_daily_universe(
        daily_universe,
        master,
        sessions,
        coverage_start,
        coverage_end,
    )
    sources = _validate_source_manifest(source_manifest)
    component_hashes = {
        "security_master_sha256": _sha256(master),
        "calendar_sha256": _sha256(sessions),
        "daily_universe_sha256": _sha256(daily),
        "source_manifest_sha256": _sha256(sources),
    }
    universe_sha256 = _sha256(
        {
            "schema_version": SCHEMA_VERSION,
            "coverage": {"start_date": coverage_start, "end_date": coverage_end},
            "membership_policy": "exact_daily_vendor_snapshot_with_master_lifecycle_v1",
            **component_hashes,
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_role": "development_only",
        "coverage": {"start_date": coverage_start, "end_date": coverage_end},
        "membership_policy": "exact_daily_vendor_snapshot_with_master_lifecycle_v1",
        "security_master": master,
        "trade_sessions": sessions,
        "daily_universe": daily,
        "source_manifest": sources,
        "hashes": {**component_hashes, "universe_sha256": universe_sha256},
        "quality": {
            "daily_session_presence_complete": True,
            "daily_session_coverage_complete": False,
            "current_snapshot_dependency": "unverified",
            "historical_names_present": True,
            "raw_to_normalized_lineage_verified": False,
            "final_oos_eligible": False,
            "known_gaps": [
                "daily_snapshot_completeness_not_proven",
                "raw_to_normalized_lineage_not_verified",
                "raw_execution_bars_not_bound",
                "corporate_actions_not_bound",
                "historical_st_and_suspension_intervals_not_bound",
            ],
        },
    }


def _verify_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported PIT universe schema")
    rebuilt = build_pit_universe_payload(
        security_master=payload.get("security_master") or [],
        trade_calendar=payload.get("trade_sessions") or [],
        daily_universe=payload.get("daily_universe") or [],
        source_manifest=payload.get("source_manifest") or {},
        start_date=(payload.get("coverage") or {}).get("start_date"),
        end_date=(payload.get("coverage") or {}).get("end_date"),
    )
    if payload.get("hashes") != rebuilt["hashes"]:
        raise ValueError("PIT universe component hash mismatch")
    if payload.get("membership_policy") != rebuilt["membership_policy"]:
        raise ValueError("PIT universe semantic policy mismatch")
    if payload.get("quality") != rebuilt["quality"]:
        raise ValueError("PIT universe quality declaration mismatch")
    return rebuilt


@dataclass(frozen=True)
class PointInTimeUniverse:
    """Read-only, hash-verified daily membership index."""

    payload: Dict[str, Any]
    _items: Dict[Tuple[str, str], Dict[str, Any]]
    _items_by_date: Dict[str, Tuple[Dict[str, Any], ...]]
    _sessions: frozenset[str]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PointInTimeUniverse":
        verified = _verify_payload(payload)
        items: Dict[Tuple[str, str], Dict[str, Any]] = {}
        rows_by_date: Dict[str, List[Dict[str, Any]]] = {}
        for row in verified["daily_universe"]:
            normalized = dict(row)
            key = (normalized["trade_date"], normalized["symbol"])
            if key in items:
                raise ValueError("PIT universe contains duplicate daily symbols")
            items[key] = normalized
            rows_by_date.setdefault(normalized["trade_date"], []).append(normalized)
        items_by_date = {
            session: tuple(sorted(rows, key=lambda item: item["symbol"]))
            for session, rows in rows_by_date.items()
        }
        return cls(
            payload=verified,
            _items=items,
            _items_by_date=items_by_date,
            _sessions=frozenset(verified["trade_sessions"]),
        )

    @classmethod
    def from_file(cls, path: str) -> "PointInTimeUniverse":
        artifact_path = Path(path)
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        universe = cls.from_payload(payload)
        stem = artifact_path.stem.lower()
        if len(stem) == 64 and stem != universe.universe_sha256:
            raise ValueError("PIT universe filename hash mismatch")
        return universe

    @property
    def universe_sha256(self) -> str:
        return self.payload["hashes"]["universe_sha256"]

    @property
    def calendar_sha256(self) -> str:
        return self.payload["hashes"]["calendar_sha256"]

    @property
    def source_manifest_sha256(self) -> str:
        return self.payload["hashes"]["source_manifest_sha256"]

    @property
    def start_date(self) -> str:
        return self.payload["coverage"]["start_date"]

    @property
    def end_date(self) -> str:
        return self.payload["coverage"]["end_date"]

    def item_as_of(self, symbol: str, signal_date: str) -> Optional[Dict[str, Any]]:
        session = _iso_date(signal_date, "signal_date")
        if not (self.start_date <= session <= self.end_date):
            raise ValueError(f"signal date is outside PIT universe coverage: {session}")
        if session not in self._sessions:
            raise ValueError(f"signal date is not a covered trading session: {session}")
        item = self._items.get((session, str(symbol)))
        return dict(item) if item else None

    def items_as_of(self, signal_date: str) -> List[Dict[str, Any]]:
        """Return a deterministic copy of the complete daily PIT membership."""

        session = _iso_date(signal_date, "signal_date")
        if not (self.start_date <= session <= self.end_date):
            raise ValueError(f"signal date is outside PIT universe coverage: {session}")
        if session not in self._sessions:
            raise ValueError(f"signal date is not a covered trading session: {session}")
        return [dict(item) for item in self._items_by_date.get(session, ())]

    def open_sessions(self, start_date: str, end_date: str) -> List[str]:
        """Return the complete covered open-session sequence for a bounded range."""

        first = _iso_date(start_date, "start_date")
        last = _iso_date(end_date, "end_date")
        if last < first:
            raise ValueError("end_date precedes start_date")
        if first < self.start_date or last > self.end_date:
            raise ValueError("requested session range is outside PIT universe coverage")
        return sorted(session for session in self._sessions if first <= session <= last)

    def seed_items(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        first = _iso_date(start_date, "start_date")
        last = _iso_date(end_date, "end_date")
        if first < self.start_date or last > self.end_date or last < first:
            raise ValueError("requested seed range is outside PIT universe coverage")
        by_symbol: Dict[str, Dict[str, Any]] = {}
        for row in self.payload["daily_universe"]:
            if first <= row["trade_date"] <= last:
                by_symbol.setdefault(
                    row["symbol"],
                    {
                        "symbol": row["symbol"],
                        "market": "a",
                        "name": row["name"],
                        "exchange": row["exchange"],
                    },
                )
        return [by_symbol[symbol] for symbol in sorted(by_symbol)]


def _verify_universe_raw_sources(
    artifact_path: Path, universe: PointInTimeUniverse
) -> None:
    source_manifest = universe.payload["source_manifest"]
    raw_artifacts = source_manifest.get("raw_artifacts") or {}
    if set(raw_artifacts) != {"stock_basic", "bak_basic", "trade_cal"}:
        raise ValueError("PIT universe lacks verified raw source artifacts")
    for name, descriptor in raw_artifacts.items():
        path = _verify_file_descriptor(artifact_path.parent, descriptor)
        expected = source_manifest.get(f"{name}_raw_sha256")
        if expected != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"PIT universe raw source hash mismatch: {name}")


def write_frozen_market_data_manifest(
    directory: str,
    *,
    components: Mapping[str, str],
    start_date: str,
    end_date: str,
    source: Mapping[str, Any],
) -> Dict[str, Any]:
    """Bind raw execution bars and causally adjusted signal inputs to files."""

    root = Path(directory).resolve()
    if set(components) != REQUIRED_MARKET_COMPONENTS:
        missing = sorted(REQUIRED_MARKET_COMPONENTS - set(components))
        extra = sorted(set(components) - REQUIRED_MARKET_COMPONENTS)
        raise ValueError(f"market manifest component mismatch: missing={missing}, extra={extra}")
    coverage_start = _iso_date(start_date, "start_date")
    coverage_end = _iso_date(end_date, "end_date")
    if coverage_end < coverage_start:
        raise ValueError("market manifest end_date precedes start_date")
    if not str(source.get("provider") or "").strip() or not str(
        source.get("knowledge_cutoff") or ""
    ).strip():
        raise ValueError("market manifest requires provider and knowledge_cutoff")
    descriptors = {}
    for name in sorted(components):
        path = Path(components[name])
        descriptor = _file_descriptor(root, path)
        row_count = _json_row_count(path)
        if row_count <= 0:
            raise ValueError(f"market component is empty: {name}")
        descriptors[name] = {**descriptor, "rows": row_count}
    payload = {
        "schema_version": "frozen_market_data_manifest/v1",
        "coverage": {"start_date": coverage_start, "end_date": coverage_end},
        "source": deepcopy(dict(source)),
        "semantics": {
            "execution_price_basis": "raw_ohlcv",
            "signal_price_basis": "causal_total_return_v1",
            "vendor_adjustment": "none",
            "corporate_action_policy": "available_at_and_effective_date_v1",
        },
        "components": descriptors,
    }
    digest = _sha256(payload)
    complete = {**payload, "source_manifest_sha256": digest}
    descriptor = _write_content_addressed_json(root, f"market-{digest}.json", complete)
    return {**descriptor, "source_manifest_sha256": digest}


def _verify_frozen_market_data_manifest(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("market data manifest is not valid JSON") from exc
    if payload.get("schema_version") != "frozen_market_data_manifest/v1":
        raise ValueError("unsupported market data manifest schema")
    expected_semantics = {
        "execution_price_basis": "raw_ohlcv",
        "signal_price_basis": "causal_total_return_v1",
        "vendor_adjustment": "none",
        "corporate_action_policy": "available_at_and_effective_date_v1",
    }
    if payload.get("semantics") != expected_semantics:
        raise ValueError("market data manifest has ineligible price semantics")
    components = payload.get("components") or {}
    if set(components) != REQUIRED_MARKET_COMPONENTS:
        raise ValueError("market data manifest has incomplete components")
    for name, descriptor in components.items():
        component_path = _verify_file_descriptor(path.parent, descriptor)
        if int(descriptor.get("rows") or -1) != _json_row_count(component_path):
            raise ValueError(f"market component row count mismatch: {name}")
    claimed = payload.get("source_manifest_sha256")
    semantic_payload = dict(payload)
    semantic_payload.pop("source_manifest_sha256", None)
    if claimed != _sha256(semantic_payload):
        raise ValueError("market data source manifest hash mismatch")
    return payload


def write_research_evidence_bundle(
    directory: str,
    *,
    pit_universe_path: str,
    market_data_manifest_path: str,
    audited_authority: Mapping[str, Any] = None,
) -> Dict[str, Any]:
    """Publish a bundle whose hashes are all derived from readable components."""

    root = Path(directory).resolve()
    universe_path = Path(pit_universe_path)
    market_path = Path(market_data_manifest_path)
    universe_descriptor = _file_descriptor(root, universe_path)
    market_descriptor = _file_descriptor(root, market_path)
    universe = PointInTimeUniverse.from_file(str(universe_path))
    _verify_universe_raw_sources(universe_path, universe)
    market = _verify_frozen_market_data_manifest(market_path)
    if market["coverage"] != universe.payload["coverage"]:
        raise ValueError("universe and market coverage differ")
    authority_keys = {
        "artifact_root_sha256",
        "coverage_audit_sha256",
        "temporal_contract_sha256",
        "temporal_role",
        "artifact_manifest_sha256",
        "market_generation_root_sha256",
        "stock_generation_lineage_sha256",
    }
    authority = deepcopy(dict(audited_authority)) if audited_authority is not None else None
    if authority is not None:
        if set(authority) != authority_keys:
            raise ValueError("audited authority fields are incomplete")
        for key in authority_keys - {"temporal_role"}:
            value = authority[key]
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"audited authority hash is invalid: {key}")
        if authority["temporal_role"] != "development":
            raise ValueError("strict evidence requires development audited authority")
    payload = {
        "schema_version": "research_pit_evidence_bundle/v2"
        if authority is not None
        else "research_pit_evidence_bundle/v1",
        "artifacts": {
            "pit_universe": universe_descriptor,
            "market_data_manifest": market_descriptor,
        },
        "hashes": {
            "universe_sha256": universe.universe_sha256,
            "calendar_sha256": universe.calendar_sha256,
            "source_manifest_sha256": market["source_manifest_sha256"],
        },
        "coverage": deepcopy(universe.payload["coverage"]),
        "eligibility": {
            "development_integrity": True,
            **(
                {
                    "eligible_for_development_validation": False,
                    "eligible_for_final_validation": False,
                    "final_oos_eligible": False,
                    "strict_validation_eligible": False,
                }
                if authority is not None
                else {"final_validation": False}
            ),
            "reasons": [
                "component_row_semantics_not_fully_verified",
                "qualified_trade_lineage_not_bound",
                "producer_code_not_bound",
            ],
        },
    }
    if authority is not None:
        payload["audited_authority"] = authority
    digest = _sha256(payload)
    complete = {**payload, "evidence_bundle_sha256": digest}
    descriptor = _write_content_addressed_json(root, f"evidence-{digest}.json", complete)
    return {**descriptor, "evidence_bundle_sha256": digest, **payload["hashes"]}


def _audited_authority_from_universe(audited_universe: Any) -> Dict[str, Any]:
    return audited_authority_from_universe(audited_universe)


def write_strict_research_evidence_bundle(
    directory: str,
    *,
    audited_universe: Any,
    artifact_native_evidence_path: str,
) -> Dict[str, Any]:
    """Compile replayed artifact-native evidence into development-only proof.

    This compiler deliberately cannot grant final-OOS or live-trading status.
    It only removes the older bundle's hard-coded development blocker after the
    qualified trades, signal evaluator, outcome replay and audited artifact
    lineage have all been freshly verified by the native evidence verifier.
    """

    # Local import avoids a module cycle: artifact_native_evidence imports the
    # validation module, which in turn imports this verifier.
    from app.artifact_native_evidence import verify_artifact_native_evidence

    root = Path(directory).resolve()
    native_path = Path(artifact_native_evidence_path).resolve()
    native_descriptor = _file_descriptor(root, native_path)
    native = verify_artifact_native_evidence(
        str(native_path),
        audited_universe=audited_universe,
        artifact_root=str(root),
    )
    native_eligibility = native.get("eligibility") or {}
    if (
        native_eligibility.get("eligible_for_development_validation") is not True
        or native_eligibility.get("eligible_for_final_validation") is not False
        or native_eligibility.get("final_oos_eligible") is not False
        or native_eligibility.get("reasons") != []
    ):
        raise ValueError(
            "artifact-native evidence is not eligible for development validation"
        )
    authority = _audited_authority_from_universe(audited_universe)
    coverage = audited_coverage_from_universe(audited_universe)
    hashes = {
        "universe_sha256": audited_universe.universe_sha256,
        "calendar_sha256": audited_universe.calendar_sha256,
        "source_manifest_sha256": audited_universe.source_manifest_sha256,
        "qualified_trades_sha256": native["qualified_trades"][
            "qualified_trades_sha256"
        ],
        "qualified_trade_lineage_sha256": native["trade_lineage_sha256"],
    }
    if not all(_valid_sha256(value) for value in hashes.values()):
        raise ValueError("strict evidence contains an invalid contract hash")
    payload = {
        "schema_version": (
            COMPOSITE_STRICT_EVIDENCE_SCHEMA_VERSION
            if is_composite_universe(audited_universe)
            else STRICT_EVIDENCE_SCHEMA_VERSION
        ),
        "artifact_role": "development_only",
        "artifacts": {"artifact_native_evidence": native_descriptor},
        "hashes": hashes,
        "coverage": coverage,
        "audited_authority": authority,
        "market_scope": deepcopy(native["market_scope"]),
        "eligibility": {
            "development_integrity": True,
            "eligible_for_development_validation": True,
            "eligible_for_final_validation": False,
            "final_oos_eligible": False,
            "strict_validation_eligible": True,
            "reasons": [],
        },
        "limitations": [
            "historical_backfill_is_not_contemporaneous_live_proof",
            "artifact_is_not_final_oos_eligible",
            "automatic_order_submission_is_out_of_scope",
        ],
    }
    digest = _sha256(payload)
    complete = {**payload, "evidence_bundle_sha256": digest}
    descriptor = _write_content_addressed_json(
        root, f"strict-evidence-{digest}.json", complete
    )
    return {**descriptor, "evidence_bundle_sha256": digest, **hashes}


def _verify_strict_research_evidence_bundle(
    bundle_path: Path,
    payload: Mapping[str, Any],
    *,
    audited_universe: Any,
    artifact_root: str,
) -> Dict[str, Any]:
    from app.artifact_native_evidence import verify_artifact_native_evidence

    if audited_universe is None:
        raise ValueError("strict evidence requires an audited universe")
    root = Path(artifact_root).resolve() if artifact_root else bundle_path.parent.resolve()
    try:
        bundle_path.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("strict evidence bundle is outside its artifact root") from exc
    artifacts = payload.get("artifacts") or {}
    if set(artifacts) != {"artifact_native_evidence"}:
        raise ValueError("strict research evidence has incomplete artifacts")
    native_path = _verify_file_descriptor(root, artifacts["artifact_native_evidence"])
    native = verify_artifact_native_evidence(
        str(native_path),
        audited_universe=audited_universe,
        artifact_root=str(root),
    )
    native_eligibility = native.get("eligibility") or {}
    expected_eligibility = {
        "development_integrity": True,
        "eligible_for_development_validation": True,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
        "strict_validation_eligible": True,
        "reasons": [],
    }
    if (
        native_eligibility.get("eligible_for_development_validation") is not True
        or native_eligibility.get("eligible_for_final_validation") is not False
        or native_eligibility.get("final_oos_eligible") is not False
        or native_eligibility.get("reasons") != []
        or payload.get("eligibility") != expected_eligibility
    ):
        raise ValueError("strict research evidence eligibility mismatch")
    expected_authority = _audited_authority_from_universe(audited_universe)
    if payload.get("audited_authority") != expected_authority:
        raise ValueError("strict research evidence audited authority mismatch")
    if payload.get("market_scope") != native.get("market_scope"):
        raise ValueError("strict research evidence market scope mismatch")
    expected_hashes = {
        "universe_sha256": audited_universe.universe_sha256,
        "calendar_sha256": audited_universe.calendar_sha256,
        "source_manifest_sha256": audited_universe.source_manifest_sha256,
        "qualified_trades_sha256": native["qualified_trades"][
            "qualified_trades_sha256"
        ],
        "qualified_trade_lineage_sha256": native["trade_lineage_sha256"],
    }
    if payload.get("hashes") != expected_hashes:
        raise ValueError("strict research evidence contract hash mismatch")
    coverage = audited_coverage_from_universe(audited_universe)
    if payload.get("coverage") != coverage:
        raise ValueError("strict research evidence coverage mismatch")
    expected_limitations = [
        "historical_backfill_is_not_contemporaneous_live_proof",
        "artifact_is_not_final_oos_eligible",
        "automatic_order_submission_is_out_of_scope",
    ]
    if payload.get("artifact_role") != "development_only" or payload.get(
        "limitations"
    ) != expected_limitations:
        raise ValueError("strict research evidence scope mismatch")
    semantic_payload = dict(payload)
    claimed = semantic_payload.pop("evidence_bundle_sha256", None)
    actual = _sha256(semantic_payload)
    if claimed != actual:
        raise ValueError("research evidence bundle hash mismatch")
    sessions = audited_universe.open_sessions(coverage["start_date"], coverage["end_date"])
    if not sessions:
        raise ValueError("strict research evidence has no trade sessions")
    return {
        **expected_hashes,
        "evidence_bundle_sha256": actual,
        "coverage": coverage,
        "trade_sessions": tuple(sessions),
        "universe": audited_universe,
        "eligible_for_development_validation": True,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
        "eligibility": deepcopy(expected_eligibility),
        "integrity_only": False,
        "schema_version": (
            COMPOSITE_STRICT_EVIDENCE_SCHEMA_VERSION
            if is_composite_universe(audited_universe)
            else STRICT_EVIDENCE_SCHEMA_VERSION
        ),
        "audited_authority": deepcopy(expected_authority),
        "artifact_native_evidence": native,
    }


def write_strict_qualified_trades_payload(
    directory: str,
    *,
    source_payload_path: str,
    strict_evidence_bundle_path: str,
    audited_universe: Any,
) -> Dict[str, Any]:
    """Publish a new qualified payload with the current verified data contract.

    The source qualified file remains immutable because artifact-native
    evidence binds its exact bytes.  This function creates a second,
    content-addressed payload whose trade rows are identical and whose summary
    receives the strict development-only contract.
    """

    from app.research_validation import (
        qualified_trades_sha256,
        validate_point_in_time_contract,
    )

    root = Path(directory).resolve()
    source_path = Path(source_payload_path).resolve()
    source_descriptor = _file_descriptor(root, source_path)
    try:
        source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("qualified trades source payload is not valid JSON") from exc
    if isinstance(source_payload, list):
        trades = source_payload
        compiled_payload: Dict[str, Any] = {"qualified_trades": deepcopy(trades)}
        summary: Dict[str, Any] = {}
    elif isinstance(source_payload, dict):
        trades = source_payload.get("qualified_trades")
        compiled_payload = deepcopy(source_payload)
        existing_summary = source_payload.get("summary") or {}
        if not isinstance(existing_summary, dict):
            raise ValueError("qualified trades source summary is invalid")
        summary = deepcopy(existing_summary)
    else:
        raise ValueError("qualified trades source payload is invalid")
    if not isinstance(trades, list) or not trades or not all(
        isinstance(trade, dict) for trade in trades
    ):
        raise ValueError("qualified trades source rows are invalid")
    bundle_path = Path(strict_evidence_bundle_path).resolve()
    bundle_descriptor = _file_descriptor(root, bundle_path)
    verified = verify_research_evidence_bundle(
        str(bundle_path),
        audited_universe=audited_universe,
        artifact_root=str(root),
    )
    actual_trades_sha256 = qualified_trades_sha256(trades)
    if actual_trades_sha256 != verified["qualified_trades_sha256"]:
        raise ValueError("strict evidence is bound to different qualified trades")
    native_qualified = verified["artifact_native_evidence"]["qualified_trades"]
    if source_descriptor["path"] != native_qualified.get("path"):
        raise ValueError("strict evidence source payload path mismatch")
    audited_authority = deepcopy(verified["audited_authority"])
    contract = {
        "schema_version": (
            "research_data_contract/v4"
            if is_composite_universe(audited_universe)
            else "research_data_contract/v2"
        ),
        "artifact_role": "development_only",
        "point_in_time": True,
        "eligible_for_development_validation": True,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
        "entry_decision_cutoff": "next_open",
        "known_biases": [],
        "qualified_trades_sha256": actual_trades_sha256,
        "qualified_trade_lineage_sha256": verified[
            "qualified_trade_lineage_sha256"
        ],
        "universe_sha256": verified["universe_sha256"],
        "calendar_sha256": verified["calendar_sha256"],
        "source_manifest_sha256": verified["source_manifest_sha256"],
        "artifact_root_sha256": audited_universe.artifact_root_sha256,
        "coverage_audit_sha256": audited_universe.coverage_audit_sha256,
        "temporal_contract_sha256": audited_authority[
            "temporal_contract_sha256"
        ],
        "temporal_role": audited_authority["temporal_role"],
        "audited_authority": audited_authority,
        "market_scope": deepcopy(verified["artifact_native_evidence"]["market_scope"]),
        "evidence_bundle_path": bundle_descriptor["path"],
        "evidence_bundle_sha256": verified["evidence_bundle_sha256"],
    }
    if not is_composite_universe(audited_universe):
        contract.update(
            {
                "artifact_manifest_sha256": audited_authority[
                    "artifact_manifest_sha256"
                ],
                "market_generation_root_sha256": audited_authority[
                    "market_generation_root_sha256"
                ],
                "stock_generation_lineage_sha256": audited_authority[
                    "stock_generation_lineage_sha256"
                ],
            }
        )
    summary["research_data_contract"] = contract
    summary["research_proof_scope"] = {
        "artifact_role": "development_only",
        "development_validation_eligible": True,
        "final_oos_eligible": False,
        "live_proof": False,
        "automatic_order_submission": False,
        "limitations": [
            "historical_backfill_is_not_contemporaneous_live_proof",
            "artifact_is_not_final_oos_eligible",
        ],
    }
    compiled_payload["summary"] = summary
    compiled_payload["qualified_trades"] = deepcopy(trades)
    validated = validate_point_in_time_contract(
        summary,
        trades,
        artifact_base_dir=str(root),
        audited_universe=audited_universe,
    )
    if validated.get("eligible_for_development_validation") is not True:
        raise ValueError("compiled qualified trades contract did not validate")
    semantic_sha256 = _sha256(compiled_payload)
    descriptor = _write_content_addressed_json(
        root,
        f"strict-qualified-{semantic_sha256}.json",
        compiled_payload,
    )
    return {
        **descriptor,
        "payload_sha256": semantic_sha256,
        "qualified_trades_sha256": actual_trades_sha256,
        "qualified_trade_lineage_sha256": verified[
            "qualified_trade_lineage_sha256"
        ],
        "evidence_bundle_sha256": verified["evidence_bundle_sha256"],
        "validation_verified": True,
    }


def verify_research_evidence_bundle(
    path: str,
    *,
    audited_universe: Any = None,
    artifact_root: str = None,
) -> Dict[str, Any]:
    """Rehash every transitive component and return verified contract hashes."""

    bundle_path = Path(path).resolve()
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("research evidence bundle is not valid JSON") from exc
    schema_version = payload.get("schema_version")
    if schema_version in {
        STRICT_EVIDENCE_SCHEMA_VERSION,
        COMPOSITE_STRICT_EVIDENCE_SCHEMA_VERSION,
    }:
        expected_schema = (
            COMPOSITE_STRICT_EVIDENCE_SCHEMA_VERSION
            if is_composite_universe(audited_universe)
            else STRICT_EVIDENCE_SCHEMA_VERSION
        )
        if schema_version != expected_schema:
            raise ValueError("strict evidence schema does not match audited authority kind")
        return _verify_strict_research_evidence_bundle(
            bundle_path,
            payload,
            audited_universe=audited_universe,
            artifact_root=artifact_root,
        )
    if schema_version not in {
        "research_pit_evidence_bundle/v1",
        "research_pit_evidence_bundle/v2",
    }:
        raise ValueError("unsupported research evidence bundle schema")
    artifacts = payload.get("artifacts") or {}
    if set(artifacts) != {"pit_universe", "market_data_manifest"}:
        raise ValueError("research evidence bundle has incomplete artifacts")
    universe_path = _verify_file_descriptor(bundle_path.parent, artifacts["pit_universe"])
    market_path = _verify_file_descriptor(
        bundle_path.parent, artifacts["market_data_manifest"]
    )
    universe = PointInTimeUniverse.from_file(str(universe_path))
    _verify_universe_raw_sources(universe_path, universe)
    market = _verify_frozen_market_data_manifest(market_path)
    expected_hashes = {
        "universe_sha256": universe.universe_sha256,
        "calendar_sha256": universe.calendar_sha256,
        "source_manifest_sha256": market["source_manifest_sha256"],
    }
    if payload.get("hashes") != expected_hashes:
        raise ValueError("research evidence bundle contract hash mismatch")
    if payload.get("coverage") != universe.payload["coverage"]:
        raise ValueError("research evidence bundle coverage mismatch")
    expected_eligibility = {
        "development_integrity": True,
        **(
            {
                "eligible_for_development_validation": False,
                "eligible_for_final_validation": False,
                "final_oos_eligible": False,
                "strict_validation_eligible": False,
            }
            if schema_version == "research_pit_evidence_bundle/v2"
            else {"final_validation": False}
        ),
        "reasons": [
            "component_row_semantics_not_fully_verified",
            "qualified_trade_lineage_not_bound",
            "producer_code_not_bound",
        ],
    }
    if payload.get("eligibility") != expected_eligibility:
        raise ValueError("research evidence bundle eligibility mismatch")
    if schema_version == "research_pit_evidence_bundle/v2":
        authority = payload.get("audited_authority")
        authority_keys = {
            "artifact_root_sha256",
            "coverage_audit_sha256",
            "temporal_contract_sha256",
            "temporal_role",
            "artifact_manifest_sha256",
            "market_generation_root_sha256",
            "stock_generation_lineage_sha256",
        }
        if not isinstance(authority, dict) or set(authority) != authority_keys:
            raise ValueError("research evidence audited authority is incomplete")
        if authority["temporal_role"] != "development":
            raise ValueError("research evidence audited authority role is invalid")
        for key in authority_keys - {"temporal_role"}:
            value = authority[key]
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"research evidence audited authority hash is invalid: {key}")
    semantic_payload = dict(payload)
    claimed = semantic_payload.pop("evidence_bundle_sha256", None)
    actual = _sha256(semantic_payload)
    if claimed != actual:
        raise ValueError("research evidence bundle hash mismatch")
    integrity_only = bool(payload["eligibility"].get("reasons"))
    return {
        **expected_hashes,
        "evidence_bundle_sha256": actual,
        "coverage": deepcopy(payload["coverage"]),
        "trade_sessions": tuple(universe.payload["trade_sessions"]),
        "universe": universe,
        "eligible_for_development_validation": schema_version == "research_pit_evidence_bundle/v2"
        and not integrity_only,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
        "eligibility": deepcopy(payload["eligibility"]),
        "integrity_only": integrity_only,
        "schema_version": schema_version,
        "audited_authority": deepcopy(payload.get("audited_authority")),
    }


def write_pit_universe_artifact(directory: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Atomically publish a content-addressed PIT universe JSON artifact."""

    universe = PointInTimeUniverse.from_payload(payload)
    artifact_dir = Path(directory)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{universe.universe_sha256}.json"
    destination = artifact_dir / filename
    content = (json.dumps(universe.payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    file_sha256 = hashlib.sha256(content).hexdigest()
    if destination.exists():
        if destination.read_bytes() != content:
            raise ValueError("content-addressed PIT universe artifact mismatch")
    else:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{universe.universe_sha256}.", suffix=".tmp", dir=str(artifact_dir)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return {
        "filename": filename,
        "path": str(destination),
        "universe_sha256": universe.universe_sha256,
        "file_sha256": file_sha256,
        "bytes": len(content),
        "security_count": len(universe.payload["security_master"]),
        "session_count": len(universe.payload["trade_sessions"]),
        "daily_row_count": len(universe.payload["daily_universe"]),
    }
