"""Strictly replay one frozen current-pool fixture without network access."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sqlite3
import stat
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.current_pool_history_source import (
    build_current_pool_history_summary,
    verify_current_pool_history_descriptor,
)
from app.current_pool_risk_source import (
    build_current_pool_risk_descriptor,
    verify_current_pool_risk_descriptor,
)
from app.current_pool_source import (
    build_current_pool_descriptor,
    verify_current_pool_universe_descriptor,
)
from app.durable_io import fsync_directory, fsync_file
from app.research_pit_contracts import STOCK_BASIC_FIELDS
from app.research_pit_store import (
    MARKET_SESSION_ROW_CAPS,
    NORMALIZED_FIELDS,
    STORE_SCHEMA_VERSION,
    PITReceiptStore,
)


FIXTURE_SCHEMA = "current-pool-offline-fixture/v1"
REPLAY_SCHEMA = "current-pool-offline-replay/v1"
FIXTURE_AS_OF = "2026-07-03"
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_RAW_BYTES = 50 * 1024 * 1024
_HEX = frozenset("0123456789abcdef")
_STORE_RAW_DATASETS = frozenset(
    {"trade_cal", "daily", "adj_factor", "stk_limit", "suspend_d"}
)
_TOP_FIELDS = {
    "schema",
    "as_of",
    "retrieved_at",
    "history_start",
    "history_end",
    "shards",
    "canonical_sha256",
}
_REF_FIELDS = {
    "sequence",
    "stage",
    "role",
    "api_name",
    "params",
    "fields",
    "raw_path",
    "raw_bytes",
    "raw_sha256",
    "http_status",
    "retrieved_at",
    "row_cap",
    "row_count",
    "rows_sha256",
}
_REPLAY_FIELDS = {
    "schema",
    "input",
    "outputs",
    "pit",
    "source_modules",
    "source_root_sha256",
    "current_universe_bias",
    "development_only",
    "replay_eligible",
    "eligible_pool_count",
    "production_recommendation_eligible",
    "manifest_sha256",
}
_RISK_FIELDS = {
    "stock_st": ("ts_code", "name", "type", "type_name", "trade_date"),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    "namechange": (
        "ts_code",
        "name",
        "start_date",
        "end_date",
        "ann_date",
        "change_reason",
    ),
}
_SOURCE_MODULE_PATHS = tuple(
    sorted(
        (
            "app/current_pool_history_source.py",
            "app/current_pool_offline_replay.py",
            "app/current_pool_risk_source.py",
            "app/current_pool_source.py",
            "app/durable_io.py",
            "app/research_pit_contracts.py",
            "app/research_pit_store.py",
        )
    )
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _canonical_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical ISO date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be a canonical ISO date")
    return text


def _aware_as_of_timestamp(value: Any, as_of: str, field: str) -> str:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be an aware ISO timestamp")
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError(f"{field} must use the Shanghai +08:00 offset")
    shanghai = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    if shanghai.date().isoformat() != as_of:
        raise ValueError(f"{field} must belong to as_of")
    return text


def _strict_json(raw: bytes, label: str) -> Any:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        decoded = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            decoded,
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is not strict JSON") from exc

    def reject_nonfinite(value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        if isinstance(value, Mapping):
            for nested in value.values():
                reject_nonfinite(nested)
        elif isinstance(value, list):
            for nested in value:
                reject_nonfinite(nested)

    reject_nonfinite(payload)
    return payload


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    is_junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & reparse_flag)
        or bool(is_junction and is_junction())
    )


def _assert_plain_directory(path: Path, label: str) -> None:
    if not os.path.lexists(path) or _path_is_link_or_reparse(path):
        raise ValueError(f"{label} is missing or unsafe")
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a directory")


def _directory_identities(root: Path, parent: Path) -> list[tuple[Path, int, int]]:
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact parent escapes its root") from exc
    identities = []
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        _assert_plain_directory(current, "artifact parent")
        metadata = current.lstat()
        identities.append((current, metadata.st_dev, metadata.st_ino))
    return identities


def _assert_directory_identities(
    identities: list[tuple[Path, int, int]],
) -> None:
    for path, device, inode in identities:
        _assert_plain_directory(path, "artifact parent")
        metadata = path.lstat()
        if metadata.st_dev != device or metadata.st_ino != inode:
            raise ValueError("artifact parent identity changed while reading")


def _canonical_relative(value: Any) -> PurePosixPath:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError("raw path must be a canonical relative path")
    if "\\" in value or ":" in value:
        raise ValueError("raw path must be canonical POSIX")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or value.startswith("/")
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or Path(value).is_absolute()
    ):
        raise ValueError("raw path must be a canonical relative path")
    return relative


def _safe_file(root: Path, value: Any) -> Path:
    relative = _canonical_relative(value)
    current = root
    for part in relative.parts:
        current = current / part
        if _path_is_link_or_reparse(current):
            raise ValueError("artifact path contains a symlink or reparse point")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError("artifact path is missing or escapes its root") from exc
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError("artifact path must be a regular file")
    return current


def _read_regular_snapshot(path: Path, *, max_bytes: int) -> bytes:
    if _path_is_link_or_reparse(path):
        raise ValueError("artifact path contains a symlink or reparse point")
    try:
        named_before = path.lstat()
    except OSError as exc:
        raise ValueError("artifact file is missing") from exc
    if not stat.S_ISREG(named_before.st_mode) or not 0 < named_before.st_size <= max_bytes:
        raise ValueError("artifact file size or type is invalid")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError("artifact file could not be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != named_before.st_dev
            or before.st_ino != named_before.st_ino
            or before.st_size != named_before.st_size
        ):
            raise ValueError("artifact file identity changed before read")
        remaining = before.st_size
        chunks = []
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        named_after = path.lstat()
    except OSError as exc:
        raise ValueError("artifact file disappeared during read") from exc
    changed = [
        name
        for name, failed in (
            ("link", _path_is_link_or_reparse(path)),
            ("read_size", len(raw) != before.st_size),
            ("fd_device", after.st_dev != before.st_dev),
            ("fd_inode", after.st_ino != before.st_ino),
            ("fd_size", after.st_size != before.st_size),
            ("fd_mtime", after.st_mtime_ns != before.st_mtime_ns),
            ("path_device", named_after.st_dev != before.st_dev),
            ("path_inode", named_after.st_ino != before.st_ino),
            ("path_size", named_after.st_size != before.st_size),
        )
        if failed
    ]
    if changed:
        raise ValueError(f"artifact file changed while being read: {','.join(changed)}")
    return raw


def _read_under_root_snapshot(root: Path, path: Path, *, max_bytes: int) -> bytes:
    identities = _directory_identities(root, path.parent)
    raw = _read_regular_snapshot(path, max_bytes=max_bytes)
    _assert_directory_identities(identities)
    return raw


def _expected_refs(history_start: str, history_end: str) -> list[dict[str, Any]]:
    refs = []

    def add(
        stage: str,
        role: str,
        api_name: str,
        params: Mapping[str, str],
        fields: tuple[str, ...],
        row_cap: int,
    ) -> None:
        refs.append(
            {
                "sequence": len(refs) + 1,
                "stage": stage,
                "role": role,
                "api_name": api_name,
                "params": dict(params),
                "fields": list(fields),
                "row_cap": row_cap,
            }
        )

    for exchange in ("SSE", "SZSE"):
        for list_status in ("L", "D", "P", "G"):
            add(
                "universe",
                f"universe.stock_basic.{exchange}.{list_status}",
                "stock_basic",
                {"exchange": exchange, "list_status": list_status},
                tuple(STOCK_BASIC_FIELDS),
                6000,
            )
    add(
        "risk",
        "risk.stock_st.as_of",
        "stock_st",
        {"trade_date": FIXTURE_AS_OF.replace("-", "")},
        _RISK_FIELDS["stock_st"],
        10_000,
    )
    add(
        "risk",
        "risk.suspend_d.as_of",
        "suspend_d",
        {"trade_date": FIXTURE_AS_OF.replace("-", "")},
        _RISK_FIELDS["suspend_d"],
        10_000,
    )
    for year in range(1990, 2027):
        add(
            "risk",
            f"risk.namechange.{year}",
            "namechange",
            {
                "start_date": f"{year}0101",
                "end_date": (
                    FIXTURE_AS_OF.replace("-", "") if year == 2026 else f"{year}1231"
                ),
            },
            _RISK_FIELDS["namechange"],
            10_000,
        )
    for exchange in ("SSE", "SZSE"):
        add(
            "history",
            f"history.trade_cal.{exchange}",
            "trade_cal",
            {
                "exchange": exchange,
                "start_date": history_start.replace("-", ""),
                "end_date": history_end.replace("-", ""),
            },
            tuple(NORMALIZED_FIELDS["trade_cal"]),
            6000,
        )
    for dataset in ("daily", "adj_factor", "stk_limit", "suspend_d"):
        add(
            "history",
            f"history.market.{dataset}",
            dataset,
            {"trade_date": FIXTURE_AS_OF.replace("-", "")},
            tuple(NORMALIZED_FIELDS[dataset]),
            MARKET_SESSION_ROW_CAPS[dataset],
        )
    return refs


def _calendar_days(start: str, end: str) -> list[str]:
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    return [
        (first + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range((last - first).days + 1)
    ]


def _validate_rows(
    ref: Mapping[str, Any],
    rows: list[list[Any]],
    *,
    history_start: str,
    history_end: str,
) -> set[str] | None:
    fields = list(ref["fields"])
    positions = {field: fields.index(field) for field in fields}
    identities = [_canonical_bytes(row) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("raw response contains duplicate rows")
    stage = ref["stage"]
    api_name = ref["api_name"]
    params = ref["params"]
    compact_as_of = FIXTURE_AS_OF.replace("-", "")
    if stage == "universe":
        if any(
            row[positions["exchange"]] != params["exchange"]
            or row[positions["list_status"]] != params["list_status"]
            for row in rows
        ):
            raise ValueError("stock_basic row does not match its partition")
    elif stage == "risk" and api_name in {"stock_st", "suspend_d"}:
        if any(str(row[positions["trade_date"]]) != compact_as_of for row in rows):
            raise ValueError("risk row does not match as_of")
    elif stage == "risk" and api_name == "namechange":
        if any(
            not params["start_date"]
            <= str(row[positions["start_date"]])
            <= params["end_date"]
            for row in rows
        ):
            raise ValueError("namechange row does not match its partition")
    elif stage == "history" and api_name == "trade_cal":
        expected_dates = _calendar_days(history_start, history_end)
        if len(rows) != len(expected_dates):
            raise ValueError("trade calendar does not cover its window")
        observed_dates = []
        open_dates = set()
        open_rows = []
        for row in rows:
            if row[positions["exchange"]] != params["exchange"]:
                raise ValueError("trade calendar exchange mismatch")
            cal_date = str(row[positions["cal_date"]])
            pretrade_date = str(row[positions["pretrade_date"]])
            is_open = row[positions["is_open"]]
            if type(is_open) is not int or is_open not in {0, 1}:
                raise ValueError("trade calendar is_open is invalid")
            try:
                datetime.strptime(cal_date, "%Y%m%d")
                datetime.strptime(pretrade_date, "%Y%m%d")
            except ValueError as exc:
                raise ValueError("trade calendar date is invalid") from exc
            observed_dates.append(cal_date)
            if is_open:
                open_dates.add(cal_date)
                open_rows.append((cal_date, pretrade_date))
        if observed_dates != expected_dates:
            raise ValueError("trade calendar date coverage is inconsistent")
        for index, (cal_date, pretrade_date) in enumerate(open_rows):
            if pretrade_date >= cal_date or (
                index > 0 and pretrade_date != open_rows[index - 1][0]
            ):
                raise ValueError("trade calendar pretrade chain is invalid")
        return open_dates
    elif stage == "history":
        if any(str(row[positions["trade_date"]]) != compact_as_of for row in rows):
            raise ValueError("market response does not match the unique session")
    return None


def _verify_fixture_tree(
    root: Path, manifest_path: Path, declared_raw_paths: set[str]
) -> None:
    expected = {manifest_path.relative_to(root).as_posix(), *declared_raw_paths}
    actual = set()
    for candidate in root.rglob("*"):
        if _path_is_link_or_reparse(candidate):
            raise ValueError("fixture tree contains a symlink or reparse point")
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("fixture tree contains a non-regular node")
        actual.add(candidate.relative_to(root).as_posix())
    if actual != expected:
        raise ValueError("fixture tree does not match declared raw files")


def preflight_current_pool_offline_fixture(
    *,
    fixture_root: str | Path,
    manifest_path: str | Path,
    expected_manifest_sha256: str,
) -> dict[str, Any]:
    """Validate and snapshot all 53 fixture shards before any output is created."""

    try:
        root = Path(fixture_root)
        _assert_plain_directory(root, "fixture root")
        root = root.resolve(strict=True)
        supplied_manifest = Path(manifest_path)
        if any(part == ".." for part in supplied_manifest.parts):
            raise ValueError("fixture manifest path is unsafe")
        candidate = supplied_manifest if supplied_manifest.is_absolute() else root / supplied_manifest
        if _path_is_link_or_reparse(candidate):
            raise ValueError("fixture manifest path is unsafe")
        try:
            relative_manifest = Path(os.path.abspath(candidate)).relative_to(
                Path(os.path.abspath(root))
            )
        except ValueError as exc:
            raise ValueError("fixture manifest escapes fixture root") from exc
        manifest = _safe_file(root, relative_manifest.as_posix())
        raw_manifest = _read_under_root_snapshot(
            root, manifest, max_bytes=_MAX_MANIFEST_BYTES
        )
        file_sha256 = hashlib.sha256(raw_manifest).hexdigest()
        if not _is_sha256(expected_manifest_sha256) or not hmac.compare_digest(
            file_sha256, expected_manifest_sha256
        ):
            raise ValueError("fixture manifest file SHA256 mismatch")
        payload = _strict_json(raw_manifest, "fixture manifest")
        if not isinstance(payload, dict) or set(payload) != _TOP_FIELDS:
            raise ValueError("fixture manifest fields are invalid")
        if payload.get("schema") != FIXTURE_SCHEMA or payload.get("as_of") != FIXTURE_AS_OF:
            raise ValueError("fixture manifest schema or as_of is invalid")
        _aware_as_of_timestamp(payload.get("retrieved_at"), FIXTURE_AS_OF, "retrieved_at")
        history_start = _canonical_date(payload.get("history_start"), "history_start")
        history_end = _canonical_date(payload.get("history_end"), "history_end")
        if history_end < history_start or history_end > FIXTURE_AS_OF:
            raise ValueError("fixture history window is invalid")
        unsigned = {key: value for key, value in payload.items() if key != "canonical_sha256"}
        internal_sha256 = _sha256(unsigned)
        if not hmac.compare_digest(str(payload.get("canonical_sha256")), internal_sha256):
            raise ValueError("fixture manifest canonical SHA256 mismatch")
        shards = payload.get("shards")
        expected_refs = _expected_refs(history_start, history_end)
        if not isinstance(shards, list) or len(shards) != 53:
            raise ValueError("fixture requires exactly 53 canonical shards")
        raw_descriptors: dict[str, tuple[int, str]] = {}
        raw_paths: dict[str, Path] = {}
        for ref, expected in zip(shards, expected_refs):
            if not isinstance(ref, dict) or set(ref) != _REF_FIELDS:
                raise ValueError("fixture shard fields are invalid")
            if type(ref.get("sequence")) is not int:
                raise ValueError("fixture shard sequence must be an exact integer")
            for key, value in expected.items():
                if ref.get(key) != value:
                    raise ValueError("fixture shard order or canonical key is invalid")
            if (
                type(ref.get("raw_bytes")) is not int
                or ref["raw_bytes"] <= 0
                or ref["raw_bytes"] > _MAX_RAW_BYTES
                or not _is_sha256(ref.get("raw_sha256"))
                or ref.get("http_status") != 200
                or type(ref.get("row_count")) is not int
                or ref["row_count"] < 0
                or ref["row_count"] >= ref["row_cap"]
                or not _is_sha256(ref.get("rows_sha256"))
            ):
                raise ValueError("fixture shard receipt metadata is invalid")
            _aware_as_of_timestamp(
                ref.get("retrieved_at"), FIXTURE_AS_OF, "shard retrieved_at"
            )
            relative = _canonical_relative(ref.get("raw_path")).as_posix()
            if PurePosixPath(relative).name != f"{ref['raw_sha256']}.json":
                raise ValueError("fixture raw path is not content-addressed")
            candidate = _safe_file(root, relative)
            descriptor = (ref["raw_bytes"], ref["raw_sha256"])
            if relative in raw_descriptors and raw_descriptors[relative] != descriptor:
                raise ValueError("fixture raw path has conflicting descriptors")
            raw_descriptors[relative] = descriptor
            raw_paths[relative] = candidate
        _verify_fixture_tree(root, manifest, set(raw_paths))

        raw_catalog = {}
        envelope_catalog = {}
        calendar_sets = {}
        for ref in shards:
            relative = ref["raw_path"]
            raw = raw_catalog.get(relative)
            if raw is None:
                raw = _read_under_root_snapshot(
                    root, raw_paths[relative], max_bytes=_MAX_RAW_BYTES
                )
                if len(raw) != ref["raw_bytes"] or not hmac.compare_digest(
                    hashlib.sha256(raw).hexdigest(), ref["raw_sha256"]
                ):
                    raise ValueError("fixture raw byte count or SHA256 mismatch")
                raw_catalog[relative] = raw
            envelope = _strict_json(raw, "fixture raw response")
            if (
                not isinstance(envelope, dict)
                or set(envelope) != {"code", "msg", "data"}
                or type(envelope.get("code")) is not int
                or envelope["code"] != 0
                or not isinstance(envelope.get("data"), dict)
                or set(envelope["data"]) != {"fields", "items"}
                or envelope["data"].get("fields") != ref["fields"]
                or not isinstance(envelope["data"].get("items"), list)
            ):
                raise ValueError("fixture raw response envelope is invalid")
            rows = envelope["data"]["items"]
            if any(not isinstance(row, list) or len(row) != len(ref["fields"]) for row in rows):
                raise ValueError("fixture raw response row is invalid")
            if len(rows) != ref["row_count"] or not hmac.compare_digest(
                _sha256(rows), ref["rows_sha256"]
            ):
                raise ValueError("fixture raw row count or hash mismatch")
            open_dates = _validate_rows(
                ref,
                rows,
                history_start=history_start,
                history_end=history_end,
            )
            if open_dates is not None:
                calendar_sets[ref["params"]["exchange"]] = open_dates
            envelope_catalog[ref["sequence"]] = envelope
        if set(calendar_sets) != {"SSE", "SZSE"}:
            raise ValueError("fixture calendar exchange coverage is incomplete")
        if calendar_sets["SSE"] != calendar_sets["SZSE"] or len(calendar_sets["SSE"]) != 1:
            raise ValueError("fixture must have exactly one common open session")
        session = next(iter(calendar_sets["SSE"]))
        if session != FIXTURE_AS_OF.replace("-", ""):
            raise ValueError("fixture market session does not match as_of")
        market_retrieved = [
            datetime.fromisoformat(ref["retrieved_at"]) for ref in shards[49:53]
        ]
        if (max(market_retrieved) - min(market_retrieved)).total_seconds() >= 3600:
            raise ValueError("fixture market shards exceed the generation window")
        return {
            "schema": FIXTURE_SCHEMA,
            "fixture_root": str(root),
            "manifest_path": str(manifest),
            "manifest_bytes": len(raw_manifest),
            "manifest_file_sha256": file_sha256,
            "canonical_sha256": internal_sha256,
            "shard_count": 53,
            "stage_counts": {"universe": 8, "risk": 39, "history": 6},
            "shards_root_sha256": _sha256(shards),
            "as_of": FIXTURE_AS_OF,
            "retrieved_at": payload["retrieved_at"],
            "history_start": history_start,
            "history_end": history_end,
            "session": date.fromisoformat(FIXTURE_AS_OF).isoformat(),
            "shards": [dict(ref) for ref in shards],
            "raw_catalog": raw_catalog,
            "envelope_catalog": envelope_catalog,
        }
    except (KeyError, OSError, TypeError, ValueError):
        raise ValueError("offline fixture rejected") from None


def _artifact_descriptor(run_root: Path, path: str | Path, descriptor_sha256: str) -> dict:
    artifact = Path(path)
    raw = _read_regular_snapshot(artifact, max_bytes=_MAX_MANIFEST_BYTES)
    return {
        "path": artifact.resolve(strict=True).relative_to(run_root.resolve(strict=True)).as_posix(),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "descriptor_sha256": descriptor_sha256,
    }


def _snapshot_store_tree(root: Path) -> dict[str, Any]:
    _assert_plain_directory(root, "PIT store root")
    root = root.resolve(strict=True)
    directories = []
    files = []
    snapshots: dict[str, bytes] = {}
    for candidate in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
        if _path_is_link_or_reparse(candidate):
            raise ValueError("PIT store contains a symlink or reparse point")
        relative = candidate.relative_to(root).as_posix()
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            directories.append(relative)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("PIT store contains a non-regular node")
        if relative != "metadata.sqlite3":
            parts = PurePosixPath(relative).parts
            if (
                len(parts) != 4
                or parts[0] != "raw"
                or parts[1] not in _STORE_RAW_DATASETS
                or len(parts[2]) != 2
                or any(character not in _HEX for character in parts[2])
                or not parts[3].endswith(".json")
                or not _is_sha256(parts[3][:-5])
                or parts[2] != parts[3][:2]
            ):
                raise ValueError("PIT store contains an undeclared file")
        raw = _read_under_root_snapshot(
            root, candidate, max_bytes=512 * 1024 * 1024
        )
        digest = hashlib.sha256(raw).hexdigest()
        if relative != "metadata.sqlite3" and PurePosixPath(relative).name != f"{digest}.json":
            raise ValueError("PIT store raw content address mismatch")
        descriptor = {"path": relative, "bytes": len(raw), "sha256": digest}
        files.append(descriptor)
        snapshots[relative] = raw
    if not files or files[0]["path"] != "metadata.sqlite3":
        raise ValueError("PIT store metadata is missing")
    tree = {"directories": directories, "files": files}
    return {
        **tree,
        "root_sha256": _sha256(tree),
        "snapshots": snapshots,
    }


def _expected_parent_directories(paths: set[str]) -> set[str]:
    directories = set()
    for value in paths:
        parent = PurePosixPath(value).parent
        while parent.as_posix() != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _verify_exact_run_tree(root: Path, expected_files: set[str]) -> None:
    actual_files = set()
    actual_directories = set()
    for candidate in root.rglob("*"):
        if _path_is_link_or_reparse(candidate):
            raise ValueError("offline replay run contains a link")
        relative = candidate.relative_to(root).as_posix()
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            actual_directories.add(relative)
        elif stat.S_ISREG(metadata.st_mode):
            actual_files.add(relative)
        else:
            raise ValueError("offline replay run contains an unsafe node")
    if actual_files != expected_files or actual_directories != _expected_parent_directories(
        expected_files
    ):
        raise ValueError("offline replay run tree has unbound nodes")


def _reconstruct_store_clone(
    clone_root: Path,
    directories: list[str],
    files: list[Mapping[str, Any]],
    snapshots: Mapping[str, bytes],
) -> None:
    clone_root.mkdir()
    for relative in directories:
        (clone_root / Path(PurePosixPath(relative))).mkdir(exist_ok=False)
    for descriptor in files:
        relative = str(descriptor["path"])
        (clone_root / Path(PurePosixPath(relative))).write_bytes(snapshots[relative])


def _sqlite_semantic_sha256(path: Path) -> str:
    connection = sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        statements = list(connection.iterdump())
    finally:
        connection.close()
    return _sha256(statements)


def _source_manifest() -> tuple[list[dict[str, Any]], str]:
    repository = Path(__file__).resolve().parents[1]
    entries = []
    for relative in _SOURCE_MODULE_PATHS:
        path = repository / relative
        raw = _read_regular_snapshot(path, max_bytes=4 * 1024 * 1024)
        entries.append(
            {
                "path": relative,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return entries, _sha256(entries)


def _atomic_write_json(directory: Path, payload: dict[str, Any]) -> dict[str, Any]:
    digest = _sha256(payload)
    complete = {**payload, "manifest_sha256": digest}
    content = (
        json.dumps(complete, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    destination = directory / f"{digest}.json"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f"{destination.name}.", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except OSError:
            if not os.path.lexists(destination):
                raise
            existing = _read_regular_snapshot(destination, max_bytes=_MAX_MANIFEST_BYTES)
            if not hmac.compare_digest(existing, content):
                raise ValueError("content-addressed manifest collision") from None
        fsync_directory(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "path": str(destination),
        "manifest_sha256": digest,
        "manifest_file_sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def replay_current_pool_offline_fixture(
    *,
    fixture_root: str | Path,
    manifest_path: str | Path,
    expected_manifest_sha256: str,
    run_root: str | Path,
) -> dict[str, Any]:
    verified = preflight_current_pool_offline_fixture(
        fixture_root=fixture_root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    run = Path(run_root)
    if os.path.lexists(run):
        raise ValueError("offline replay requires a fresh nonexistent run root")
    _assert_plain_directory(run.parent, "run parent")
    run.mkdir(exist_ok=False)
    fsync_directory(run.parent)

    shards = verified["shards"]
    envelopes = verified["envelope_catalog"]
    raw_catalog = verified["raw_catalog"]
    universe_refs = shards[:8]
    universe_cursor = 0

    def fetch_universe(exchange: str, list_status: str, fields: tuple[str, ...]):
        nonlocal universe_cursor
        if universe_cursor >= len(universe_refs):
            raise ValueError("offline universe callback exceeded its shard contract")
        ref = universe_refs[universe_cursor]
        if (
            ref["params"] != {"exchange": exchange, "list_status": list_status}
            or ref["fields"] != list(fields)
        ):
            raise ValueError("offline universe callback order mismatch")
        universe_cursor += 1
        return envelopes[ref["sequence"]]

    universe_result = build_current_pool_descriptor(
        as_of=verified["as_of"],
        retrieved_at=verified["retrieved_at"],
        output_dir=run / "universe",
        fetch_partition=fetch_universe,
    )
    if universe_cursor != 8:
        raise ValueError("offline universe did not consume all shards")

    risk_refs = shards[8:47]
    risk_cursor = 0

    def fetch_risk(api_name: str, params: dict[str, str], fields: tuple[str, ...]):
        nonlocal risk_cursor
        if risk_cursor >= len(risk_refs):
            raise ValueError("offline risk callback exceeded its shard contract")
        ref = risk_refs[risk_cursor]
        if (
            ref["api_name"] != api_name
            or ref["params"] != params
            or ref["fields"] != list(fields)
        ):
            raise ValueError("offline risk callback order mismatch")
        risk_cursor += 1
        return envelopes[ref["sequence"]]

    risk_result = build_current_pool_risk_descriptor(
        as_of=verified["as_of"],
        retrieved_at=verified["retrieved_at"],
        universe_path=universe_result["path"],
        output_dir=run / "risk",
        fetch_partition=fetch_risk,
    )
    if risk_cursor != 39:
        raise ValueError("offline risk did not consume all shards")

    store_dir = run / "store"
    store = PITReceiptStore(str(store_dir))
    calendar_refs = shards[47:49]
    for ref in calendar_refs:
        params = ref["params"]
        store.ingest_tushare_response(
            dataset="trade_cal",
            partition_key=(
                f"{params['exchange']}:{verified['history_start']}:{verified['history_end']}"
            ),
            endpoint="trade_cal",
            params=params,
            raw_bytes=raw_catalog[ref["raw_path"]],
            http_status=ref["http_status"],
            retrieved_at=ref["retrieved_at"],
            row_cap=ref["row_cap"],
        )
    sessions = store.common_open_sessions(
        start_date=verified["history_start"], end_date=verified["history_end"]
    )
    if sessions != [verified["session"]]:
        raise ValueError("offline replay calendar did not resolve one session")

    market_refs = shards[49:53]
    started = min(datetime.fromisoformat(ref["retrieved_at"]) for ref in market_refs)
    generation = store.begin_or_resume_market_session_generation(
        started,
        verified["session"],
        vintage="historical_backfill",
    )
    for ref in market_refs:
        dataset = ref["api_name"]
        receipt_params = {"trade_date": verified["session"]}
        semantics = {
            "schema_version": "tushare-wire-request/v1",
            "dataset": dataset,
            "partition_key": verified["session"],
            "api_name": dataset,
            "method": "POST",
            "url": "offline://current-pool-fixture",
            "wire_params": {"trade_date": verified["session"].replace("-", "")},
            "receipt_params": receipt_params,
            "fields": ref["fields"],
            "row_cap": ref["row_cap"],
        }
        wire_digest = hashlib.sha256(
            _canonical_bytes(
                {
                    "api_name": dataset,
                    "params": ref["params"],
                    "fields": ref["fields"],
                }
            )
        ).hexdigest()
        attempt = store.record_fetch_attempt(
            dataset=dataset,
            partition_key=verified["session"],
            endpoint=dataset,
            params=receipt_params,
            fields=ref["fields"],
            wire_request_sha256=wire_digest,
            request_body_sha256=wire_digest,
            request_semantics=semantics,
            request_semantics_sha256=_sha256(semantics),
            raw_bytes=raw_catalog[ref["raw_path"]],
            http_status=ref["http_status"],
            started_at=started.isoformat(),
            retrieved_at=ref["retrieved_at"],
            elapsed_ns=1_000_000_000,
            row_cap=ref["row_cap"],
            body_complete=True,
        )
        store.stage_market_session_attempt(
            generation["generation_id"], dataset, attempt["attempt_id"]
        )
    published = store.publish_market_session_generation(generation["generation_id"])
    generation_verified = store.verify_market_session_generation(
        generation_id=generation["generation_id"]
    )
    history_result = build_current_pool_history_summary(
        universe_path=universe_result["path"],
        store_dir=store_dir,
        history_start=verified["history_start"],
        history_end=verified["history_end"],
        as_of=verified["as_of"],
        output_dir=run / "history",
    )
    receipt_verification = store.verify_receipts()

    universe_raw = _strict_json(
        _read_regular_snapshot(Path(universe_result["path"]), max_bytes=_MAX_MANIFEST_BYTES),
        "universe descriptor",
    )
    risk_raw = _strict_json(
        _read_regular_snapshot(Path(risk_result["path"]), max_bytes=_MAX_MANIFEST_BYTES),
        "risk descriptor",
    )
    history_raw = _strict_json(
        _read_regular_snapshot(Path(history_result["path"]), max_bytes=_MAX_MANIFEST_BYTES),
        "history descriptor",
    )
    verify_current_pool_universe_descriptor(universe_raw)
    verify_current_pool_risk_descriptor(risk_raw, universe_raw)
    verify_current_pool_history_descriptor(history_raw, universe_raw)

    metadata_path = store_dir / "metadata.sqlite3"
    connection = sqlite3.connect(str(metadata_path))
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    fsync_file(metadata_path)
    fsync_directory(store_dir)
    store_snapshot = _snapshot_store_tree(store_dir)
    metadata_raw = store_snapshot["snapshots"]["metadata.sqlite3"]
    source_modules, source_root_sha256 = _source_manifest()
    input_refs = [
        {
            "sequence": ref["sequence"],
            "stage": ref["stage"],
            "role": ref["role"],
            "raw_path": ref["raw_path"],
            "raw_bytes": ref["raw_bytes"],
            "raw_sha256": ref["raw_sha256"],
        }
        for ref in shards
    ]
    payload = {
        "schema": REPLAY_SCHEMA,
        "input": {
            "manifest_path": Path(verified["manifest_path"])
            .relative_to(Path(verified["fixture_root"]))
            .as_posix(),
            "manifest_bytes": verified["manifest_bytes"],
            "manifest_file_sha256": verified["manifest_file_sha256"],
            "canonical_sha256": verified["canonical_sha256"],
            "shard_count": verified["shard_count"],
            "shards_root_sha256": verified["shards_root_sha256"],
            "raw_refs": input_refs,
        },
        "outputs": {
            "universe": _artifact_descriptor(
                run, universe_result["path"], universe_result["descriptor_sha256"]
            ),
            "risk": _artifact_descriptor(
                run, risk_result["path"], risk_result["descriptor_sha256"]
            ),
            "history": _artifact_descriptor(
                run, history_result["path"], history_result["descriptor_sha256"]
            ),
        },
        "pit": {
            "store_schema": STORE_SCHEMA_VERSION,
            "metadata": {
                "path": metadata_path.relative_to(run).as_posix(),
                "bytes": len(metadata_raw),
                "sha256": hashlib.sha256(metadata_raw).hexdigest(),
            },
            "store_directories": store_snapshot["directories"],
            "store_files": store_snapshot["files"],
            "store_root_sha256": store_snapshot["root_sha256"],
            "verified_receipt_count": receipt_verification["verified_receipt_count"],
            "receipt_manifest_sha256": receipt_verification["receipt_manifest_sha256"],
            "generation": {
                "trade_date": verified["session"],
                "generation_id": published["generation_id"],
                "manifest_sha256": generation_verified["manifest_sha256"],
                "lineage_sha256": generation_verified["lineage_sha256"],
                "vintage": generation_verified["vintage"],
            },
            "market_generation_root_sha256": history_raw[
                "market_generation_root_sha256"
            ],
        },
        "source_modules": source_modules,
        "source_root_sha256": source_root_sha256,
        "current_universe_bias": True,
        "development_only": True,
        "replay_eligible": False,
        "eligible_pool_count": 0,
        "production_recommendation_eligible": False,
    }
    result = _atomic_write_json(run, payload)
    verify_current_pool_offline_replay_manifest(
        manifest_path=result["path"],
        expected_manifest_sha256=result["manifest_file_sha256"],
        fixture_root=fixture_root,
        fixture_manifest_path=manifest_path,
        expected_fixture_manifest_sha256=expected_manifest_sha256,
    )
    return result


def _verify_bound_file(root: Path, descriptor: Mapping[str, Any]) -> tuple[Path, bytes]:
    if not isinstance(descriptor, Mapping) or set(descriptor) not in (
        {"path", "bytes", "sha256"},
        {"path", "bytes", "sha256", "descriptor_sha256"},
    ):
        raise ValueError("bound file descriptor is invalid")
    path = _safe_file(root, descriptor.get("path"))
    raw = _read_regular_snapshot(path, max_bytes=512 * 1024 * 1024)
    if (
        descriptor.get("bytes") != len(raw)
        or not _is_sha256(descriptor.get("sha256"))
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), descriptor["sha256"])
    ):
        raise ValueError("bound file hash or byte count mismatch")
    return path, raw


def verify_current_pool_offline_replay_manifest(
    *,
    manifest_path: str | Path,
    expected_manifest_sha256: str,
    fixture_root: str | Path,
    fixture_manifest_path: str | Path,
    expected_fixture_manifest_sha256: str,
) -> dict[str, Any]:
    """Independently re-verify the replay manifest, fixture, outputs, and PIT store."""

    try:
        path = Path(manifest_path)
        run = path.parent.resolve(strict=True)
        replay_raw = _read_regular_snapshot(path, max_bytes=_MAX_MANIFEST_BYTES)
        if not _is_sha256(expected_manifest_sha256) or not hmac.compare_digest(
            hashlib.sha256(replay_raw).hexdigest(), expected_manifest_sha256
        ):
            raise ValueError("offline replay manifest file SHA256 mismatch")
        payload = _strict_json(replay_raw, "offline replay manifest")
        if (
            not isinstance(payload, dict)
            or set(payload) != _REPLAY_FIELDS
            or payload.get("schema") != REPLAY_SCHEMA
            or payload.get("current_universe_bias") is not True
            or payload.get("development_only") is not True
            or payload.get("replay_eligible") is not False
            or type(payload.get("eligible_pool_count")) is not int
            or payload.get("eligible_pool_count") != 0
            or payload.get("production_recommendation_eligible") is not False
        ):
            raise ValueError("offline replay manifest fields are invalid")
        unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
        digest = _sha256(unsigned)
        if (
            not hmac.compare_digest(str(payload.get("manifest_sha256")), digest)
            or path.stem != digest
        ):
            raise ValueError("offline replay content address mismatch")

        source_modules, source_root = _source_manifest()
        if (
            payload.get("source_modules") != source_modules
            or payload.get("source_root_sha256") != source_root
        ):
            raise ValueError("offline replay source binding mismatch")
        inputs = payload.get("input")
        if not isinstance(inputs, dict):
            raise ValueError("offline replay input binding is invalid")
        fixture = preflight_current_pool_offline_fixture(
            fixture_root=fixture_root,
            manifest_path=fixture_manifest_path,
            expected_manifest_sha256=expected_fixture_manifest_sha256,
        )
        expected_refs = [
            {
                "sequence": ref["sequence"],
                "stage": ref["stage"],
                "role": ref["role"],
                "raw_path": ref["raw_path"],
                "raw_bytes": ref["raw_bytes"],
                "raw_sha256": ref["raw_sha256"],
            }
            for ref in fixture["shards"]
        ]
        if inputs != {
            "manifest_path": Path(fixture["manifest_path"])
            .relative_to(Path(fixture["fixture_root"]))
            .as_posix(),
            "manifest_bytes": fixture["manifest_bytes"],
            "manifest_file_sha256": fixture["manifest_file_sha256"],
            "canonical_sha256": fixture["canonical_sha256"],
            "shard_count": fixture["shard_count"],
            "shards_root_sha256": fixture["shards_root_sha256"],
            "raw_refs": expected_refs,
        }:
            raise ValueError("offline replay input binding mismatch")

        outputs = payload.get("outputs")
        if not isinstance(outputs, dict) or set(outputs) != {"universe", "risk", "history"}:
            raise ValueError("offline replay outputs are invalid")
        _universe_path, universe_raw = _verify_bound_file(run, outputs["universe"])
        _risk_path, risk_raw = _verify_bound_file(run, outputs["risk"])
        _history_path, history_raw = _verify_bound_file(run, outputs["history"])
        universe = _strict_json(universe_raw, "offline universe descriptor")
        risk = _strict_json(risk_raw, "offline risk descriptor")
        history = _strict_json(history_raw, "offline history descriptor")
        universe_verified = verify_current_pool_universe_descriptor(universe)
        risk_verified = verify_current_pool_risk_descriptor(risk, universe)
        history_verified = verify_current_pool_history_descriptor(history, universe)
        for key, verified in (
            ("universe", universe_verified),
            ("risk", risk_verified),
            ("history", history_verified),
        ):
            if outputs[key].get("descriptor_sha256") != verified["descriptor_sha256"]:
                raise ValueError("offline replay descriptor binding mismatch")

        pit = payload.get("pit")
        if not isinstance(pit, dict) or set(pit) != {
            "store_schema",
            "metadata",
            "store_directories",
            "store_files",
            "store_root_sha256",
            "verified_receipt_count",
            "receipt_manifest_sha256",
            "generation",
            "market_generation_root_sha256",
        }:
            raise ValueError("offline replay PIT binding is invalid")
        if pit.get("store_schema") != STORE_SCHEMA_VERSION:
            raise ValueError("offline replay PIT schema mismatch")
        metadata_path, _metadata_raw = _verify_bound_file(run, pit["metadata"])
        if metadata_path != run / "store" / "metadata.sqlite3":
            raise ValueError("offline replay PIT metadata path mismatch")
        store_root = metadata_path.parent
        store_snapshot = _snapshot_store_tree(store_root)
        if (
            pit.get("store_directories") != store_snapshot["directories"]
            or pit.get("store_files") != store_snapshot["files"]
            or pit.get("store_root_sha256") != store_snapshot["root_sha256"]
        ):
            raise ValueError("offline replay PIT exact tree mismatch")
        expected_run_files = {
            path.resolve(strict=True).relative_to(run).as_posix(),
            *(str(descriptor["path"]) for descriptor in outputs.values()),
            *(f"store/{descriptor['path']}" for descriptor in store_snapshot["files"]),
        }
        _verify_exact_run_tree(run, expected_run_files)
        with tempfile.TemporaryDirectory(prefix="current-pool-offline-verify-") as temporary:
            clone_root = Path(temporary) / "store"
            _reconstruct_store_clone(
                clone_root,
                store_snapshot["directories"],
                store_snapshot["files"],
                store_snapshot["snapshots"],
            )
            clone_metadata = clone_root / "metadata.sqlite3"
            semantic_sha256 = _sqlite_semantic_sha256(clone_metadata)
            store = PITReceiptStore(str(clone_root))
            if _sqlite_semantic_sha256(clone_metadata) != semantic_sha256:
                raise ValueError("offline replay PIT verifier mutated metadata on open")
            receipts = store.verify_receipts()
            generation = pit.get("generation")
            if not isinstance(generation, dict):
                raise ValueError("offline replay generation binding is invalid")
            verified_generation = store.verify_market_session_generation(
                generation_id=generation.get("generation_id")
            )
            if _sqlite_semantic_sha256(clone_metadata) != semantic_sha256:
                raise ValueError("offline replay PIT verifier mutated metadata")
        expected_generation = {
            "trade_date": verified_generation["trade_date"],
            "generation_id": verified_generation["generation_id"],
            "manifest_sha256": verified_generation["manifest_sha256"],
            "lineage_sha256": verified_generation["lineage_sha256"],
            "vintage": verified_generation["vintage"],
        }
        expected_generation_root = _sha256([expected_generation])
        if (
            pit.get("verified_receipt_count") != receipts["verified_receipt_count"]
            or pit.get("receipt_manifest_sha256") != receipts["receipt_manifest_sha256"]
            or generation != expected_generation
            or history.get("market_generation_refs") != [expected_generation]
            or history.get("market_generation_root_sha256") != expected_generation_root
            or pit.get("market_generation_root_sha256") != expected_generation_root
        ):
            raise ValueError("offline replay PIT receipt or generation mismatch")
        return {
            "schema": REPLAY_SCHEMA,
            "manifest_sha256": digest,
            "shard_count": fixture["shard_count"],
            "eligible_pool_count": 0,
            "current_universe_bias": True,
            "development_only": True,
            "replay_eligible": False,
            "production_recommendation_eligible": False,
        }
    except (KeyError, OSError, TypeError, ValueError):
        raise ValueError("offline replay manifest rejected") from None


def preflight_current_pool_offline_fixture_v2(
    *,
    fixture_root: str | Path,
    expected_manifest_sha256: str,
    evidence_use: str,
) -> dict[str, Any]:
    """Dispatch to the versioned two-segment fixture verifier."""

    from app.current_pool_offline_fixture_v2 import (
        preflight_current_pool_offline_fixture_v2 as _preflight_v2,
    )

    return _preflight_v2(
        fixture_root=fixture_root,
        expected_manifest_sha256=expected_manifest_sha256,
        evidence_use=evidence_use,
    )


def replay_current_pool_offline_fixture_v2_calls(
    *,
    fixture_root: str | Path,
    expected_manifest_sha256: str,
    evidence_use: str,
) -> list[dict[str, Any]]:
    """Read all v2 envelopes in source-index order without builders or stores."""

    from app.current_pool_offline_fixture_v2 import (
        replay_current_pool_offline_fixture_v2_calls as _replay_v2,
    )

    return _replay_v2(
        fixture_root=fixture_root,
        expected_manifest_sha256=expected_manifest_sha256,
        evidence_use=evidence_use,
    )
