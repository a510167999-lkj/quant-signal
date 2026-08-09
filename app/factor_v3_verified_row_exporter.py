from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence


_EXPORT_SCHEMA = "factor-v3-verified-row-export/v1"
_IMMUTABLE_SQLITE_CONTRACT = "mode=ro&immutable=1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TS_CODE_RE = re.compile(r"^[0-9]{6}\.(?:BJ|SH|SZ)$")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

_DAILY_UNIVERSE_SCHEMA = (
    (0, "trade_date", "TEXT", 1, None, 1, 0),
    (1, "ts_code", "TEXT", 1, None, 2, 0),
    (2, "exchange", "TEXT", 1, None, 0, 0),
    (3, "name", "TEXT", 1, None, 0, 0),
    (4, "industry", "TEXT", 0, None, 0, 0),
    (5, "list_date", "TEXT", 0, None, 0, 0),
    (6, "receipt_dataset", "TEXT", 1, None, 0, 0),
    (7, "receipt_partition", "TEXT", 1, None, 0, 0),
)
_SUSPENSION_EVENTS_SCHEMA = (
    (0, "trade_date", "TEXT", 1, None, 1, 0),
    (1, "ts_code", "TEXT", 1, None, 2, 0),
    (2, "suspend_timing", "TEXT", 1, None, 4, 0),
    (3, "suspend_type", "TEXT", 1, None, 3, 0),
    (4, "receipt_dataset", "TEXT", 1, None, 0, 0),
    (5, "receipt_partition", "TEXT", 1, None, 0, 0),
)


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int
    attributes: int


def _identity(value: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        links=value.st_nlink,
        size=value.st_size,
        modified_ns=value.st_mtime_ns,
        changed_ns=value.st_ctime_ns,
        attributes=getattr(value, "st_file_attributes", 0),
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _strict_sha256(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} sha256 rejected")
    return value


def _strict_date(value: object, *, label: str) -> str:
    if type(value) is not str or len(value) != 8 or not value.isascii():
        raise ValueError(f"{label} date rejected")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError(f"{label} date rejected") from None
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"{label} date rejected")
    return value


def _validated_trade_dates(value: object, *, label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} trade dates rejected")
    dates = tuple(
        _strict_date(item, label=f"{label} trade date") for item in value
    )
    if not dates:
        raise ValueError(f"{label} trade dates rejected")
    if len(set(dates)) != len(dates):
        raise ValueError(f"{label} trade dates must be unique")
    if tuple(sorted(dates)) != dates:
        raise ValueError(f"{label} trade dates must be ordered")
    return dates


def _is_reparse_point(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    return bool(
        stat.S_ISLNK(value.st_mode)
        or getattr(value, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _parent_chain(path: Path) -> tuple[Path, ...]:
    return tuple(reversed(path.parent.parents)) + (path.parent,)


def _validated_database_path(
    value: str | Path,
    *,
    label: str,
) -> tuple[Path, _FileIdentity, tuple[tuple[Path, _FileIdentity], ...]]:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    if ".." in path.parts:
        raise ValueError(f"{label} path rejected")
    try:
        chain = _parent_chain(path)
        chain_identities = []
        for parent in chain:
            metadata = os.lstat(parent)
            if _is_reparse_point(parent) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(f"{label} parent reparse rejected")
            chain_identities.append((parent, _identity(metadata)))
        metadata = os.lstat(path)
    except OSError:
        raise ValueError(f"{label} path rejected") from None
    if _is_reparse_point(path):
        raise ValueError(f"{label} reparse rejected")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise ValueError(f"{label} path rejected")
    resolved = path.resolve(strict=True)
    if resolved != path:
        resolved_metadata = os.lstat(resolved)
        if _identity(resolved_metadata) != _identity(metadata):
            raise ValueError(f"{label} path rejected")
    return resolved, _identity(metadata), tuple(chain_identities)


def _assert_sidecars_absent(path: Path, *, label: str) -> None:
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = Path(f"{path}{suffix}")
        if os.path.lexists(sidecar):
            raise ValueError(f"{label} SQLite sidecar rejected")


def _assert_path_unchanged(
    path: Path,
    *,
    expected_file_identity: _FileIdentity,
    expected_parent_identities: tuple[tuple[Path, _FileIdentity], ...],
    label: str,
) -> None:
    try:
        for parent, expected in expected_parent_identities:
            metadata = os.lstat(parent)
            if (
                _is_reparse_point(parent)
                or not stat.S_ISDIR(metadata.st_mode)
                or _identity(metadata) != expected
            ):
                raise ValueError(f"{label} path drift rejected")
        metadata = os.lstat(path)
    except OSError:
        raise ValueError(f"{label} path drift rejected") from None
    if (
        _is_reparse_point(path)
        or not stat.S_ISREG(metadata.st_mode)
        or _identity(metadata) != expected_file_identity
    ):
        raise ValueError(f"{label} path drift rejected")


@contextmanager
def _open_readonly_nofollow(path: Path, *, label: str) -> Iterator[int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError(f"{label} no-follow open rejected") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise ValueError(f"{label} no-follow open rejected")
        yield descriptor
    finally:
        os.close(descriptor)


def _descriptor_sha256(descriptor: int) -> str:
    duplicate = os.dup(descriptor)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(duplicate, 1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
    finally:
        os.close(duplicate)


def _validate_table_schema(
    connection: sqlite3.Connection,
    *,
    table: str,
    expected: tuple[tuple[object, ...], ...],
) -> None:
    object_row = connection.execute(
        "SELECT type FROM sqlite_schema WHERE name = ? COLLATE BINARY",
        (table,),
    ).fetchall()
    if object_row != [("table",)]:
        raise ValueError(f"{table} schema rejected")
    actual = tuple(connection.execute(f'PRAGMA table_xinfo("{table}")'))
    if actual != expected:
        raise ValueError(f"{table} schema rejected")


def _connection_sha256(
    connection: sqlite3.Connection,
    *,
    label: str,
) -> str:
    try:
        raw = connection.serialize()
    except sqlite3.Error:
        raise ValueError(f"{label} SQLite connection content rejected") from None
    if type(raw) is not bytes or not raw:
        raise ValueError(f"{label} SQLite connection content rejected")
    return hashlib.sha256(raw).hexdigest()


def _validated_universe_rows(
    rows: Sequence[tuple[object, object, object]],
    *,
    allowed_dates: tuple[str, ...],
    label: str,
) -> list[dict[str, str]]:
    allowed = set(allowed_dates)
    result: list[dict[str, str]] = []
    keys: set[tuple[str, str]] = set()
    observed_dates: set[str] = set()
    for raw_trade_date, raw_ts_code, raw_list_date in rows:
        trade_date = _strict_date(raw_trade_date, label=f"{label} universe")
        if trade_date not in allowed:
            raise ValueError(f"{label} daily_universe date coverage rejected")
        if type(raw_ts_code) is not str or _TS_CODE_RE.fullmatch(raw_ts_code) is None:
            raise ValueError(f"{label} daily_universe ts_code rejected")
        list_date = _strict_date(raw_list_date, label=f"{label} list")
        if list_date > trade_date:
            raise ValueError(f"{label} list date rejected")
        key = (trade_date, raw_ts_code)
        if key in keys:
            raise ValueError(f"{label} daily_universe duplicate key rejected")
        keys.add(key)
        observed_dates.add(trade_date)
        result.append(
            {
                "list_date": list_date,
                "trade_date": trade_date,
                "ts_code": raw_ts_code,
            }
        )
    if observed_dates != allowed:
        raise ValueError(f"{label} daily_universe date coverage rejected")
    result.sort(key=lambda row: (row["trade_date"], row["ts_code"]))
    return result


def _validated_suspension_rows(
    rows: Sequence[tuple[object, object, object]],
    *,
    allowed_dates: tuple[str, ...],
    label: str,
) -> list[dict[str, str]]:
    allowed = set(allowed_dates)
    result: list[dict[str, str]] = []
    keys: set[tuple[str, str, str]] = set()
    for raw_trade_date, raw_ts_code, raw_suspend_type in rows:
        trade_date = _strict_date(raw_trade_date, label=f"{label} suspension")
        if trade_date not in allowed:
            raise ValueError(f"{label} suspension_events date coverage rejected")
        if type(raw_ts_code) is not str or _TS_CODE_RE.fullmatch(raw_ts_code) is None:
            raise ValueError(f"{label} suspension_events ts_code rejected")
        if raw_suspend_type not in {"R", "S"}:
            raise ValueError(f"{label} suspension_events type rejected")
        key = (trade_date, raw_ts_code, raw_suspend_type)
        if key in keys:
            raise ValueError(f"{label} suspension_events duplicate key rejected")
        keys.add(key)
        result.append(
            {
                "suspend_type": raw_suspend_type,
                "trade_date": trade_date,
                "ts_code": raw_ts_code,
            }
        )
    result.sort(
        key=lambda row: (
            row["trade_date"],
            row["ts_code"],
            row["suspend_type"],
        )
    )
    return result


def _read_database_rows(
    *,
    path: Path,
    allowed_dates: tuple[str, ...],
    expected_file_identity: _FileIdentity,
    expected_parent_identities: tuple[tuple[Path, _FileIdentity], ...],
    expected_sha256: str,
    label: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if not hmac.compare_digest(
            _connection_sha256(connection, label=label),
            expected_sha256,
        ):
            raise ValueError(f"{label} SQLite connection sha256 rejected")
        _assert_path_unchanged(
            path,
            expected_file_identity=expected_file_identity,
            expected_parent_identities=expected_parent_identities,
            label=label,
        )
        _assert_sidecars_absent(path, label=label)
        _validate_table_schema(
            connection,
            table="daily_universe",
            expected=_DAILY_UNIVERSE_SCHEMA,
        )
        _validate_table_schema(
            connection,
            table="suspension_events",
            expected=_SUSPENSION_EVENTS_SCHEMA,
        )
        daily_rows = connection.execute(
            """
            SELECT trade_date, ts_code, list_date
            FROM daily_universe
            ORDER BY trade_date COLLATE BINARY, ts_code COLLATE BINARY
            """
        ).fetchall()
        suspension_rows = connection.execute(
            """
            SELECT trade_date, ts_code, suspend_type
            FROM suspension_events
            ORDER BY
                trade_date COLLATE BINARY,
                ts_code COLLATE BINARY,
                suspend_type COLLATE BINARY,
                suspend_timing COLLATE BINARY
            """
        ).fetchall()
        universe = _validated_universe_rows(
            daily_rows,
            allowed_dates=allowed_dates,
            label=label,
        )
        suspensions = _validated_suspension_rows(
            suspension_rows,
            allowed_dates=allowed_dates,
            label=label,
        )
        if not hmac.compare_digest(
            _connection_sha256(connection, label=label),
            expected_sha256,
        ):
            raise ValueError(f"{label} SQLite connection sha256 drift rejected")
        return universe, suspensions
    except sqlite3.Error:
        raise ValueError(f"{label} immutable SQLite read rejected") from None
    finally:
        connection.close()


def _export_one_database(
    *,
    path_value: str | Path,
    expected_sha256_value: object,
    trade_dates: tuple[str, ...],
    label: str,
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    expected_sha256 = _strict_sha256(
        expected_sha256_value,
        label=f"{label} database",
    )
    path, path_identity, parent_identities = _validated_database_path(
        path_value,
        label=label,
    )
    _assert_sidecars_absent(path, label=label)
    with _open_readonly_nofollow(path, label=label) as descriptor:
        opened_identity = _identity(os.fstat(descriptor))
        if opened_identity != path_identity:
            raise ValueError(f"{label} no-follow identity rejected")
        if not hmac.compare_digest(_descriptor_sha256(descriptor), expected_sha256):
            raise ValueError(f"{label} database sha256 rejected")
        universe_rows, suspension_rows = _read_database_rows(
            path=path,
            allowed_dates=trade_dates,
            expected_file_identity=path_identity,
            expected_parent_identities=parent_identities,
            expected_sha256=expected_sha256,
            label=label,
        )
        _assert_sidecars_absent(path, label=label)
        _assert_path_unchanged(
            path,
            expected_file_identity=path_identity,
            expected_parent_identities=parent_identities,
            label=label,
        )
        if _identity(os.fstat(descriptor)) != path_identity:
            raise ValueError(f"{label} descriptor drift rejected")
        if not hmac.compare_digest(_descriptor_sha256(descriptor), expected_sha256):
            raise ValueError(f"{label} postverify sha256 drift rejected")
    return (
        {
            "database_sha256": expected_sha256,
            "daily_universe_row_count": len(universe_rows),
            "suspension_event_row_count": len(suspension_rows),
            "trade_date_count": len(trade_dates),
            "trade_dates_sha256": _canonical_sha256(list(trade_dates)),
        },
        universe_rows,
        suspension_rows,
    )


def export_verified_factor_v3_rows(
    *,
    cas_snapshot_database_path: str | Path,
    expected_cas_snapshot_database_sha256: str,
    development_artifact_database_path: str | Path,
    expected_development_artifact_database_sha256: str,
    cas_snapshot_trade_dates: Sequence[str],
    development_trade_dates: Sequence[str],
) -> dict[str, Any]:
    cas_dates = _validated_trade_dates(
        cas_snapshot_trade_dates,
        label="CAS snapshot",
    )
    development_dates = _validated_trade_dates(
        development_trade_dates,
        label="development artifact",
    )
    if set(cas_dates) & set(development_dates):
        raise ValueError("CAS and development trade dates must be disjoint")
    if cas_dates[-1] >= development_dates[0]:
        raise ValueError("CAS and development trade dates must be ordered")

    cas_source, cas_universe, cas_suspensions = _export_one_database(
        path_value=cas_snapshot_database_path,
        expected_sha256_value=expected_cas_snapshot_database_sha256,
        trade_dates=cas_dates,
        label="CAS snapshot",
    )
    development_source, development_universe, development_suspensions = (
        _export_one_database(
            path_value=development_artifact_database_path,
            expected_sha256_value=expected_development_artifact_database_sha256,
            trade_dates=development_dates,
            label="development artifact",
        )
    )

    universe_rows = cas_universe + development_universe
    suspension_rows = cas_suspensions + development_suspensions
    universe_keys = {
        (row["trade_date"], row["ts_code"]) for row in universe_rows
    }
    suspension_keys = {
        (row["trade_date"], row["ts_code"], row["suspend_type"])
        for row in suspension_rows
    }
    if len(universe_keys) != len(universe_rows):
        raise ValueError("combined daily_universe duplicate key rejected")
    if len(suspension_keys) != len(suspension_rows):
        raise ValueError("combined suspension_events duplicate key rejected")

    trade_dates = list(cas_dates + development_dates)
    return {
        "daily_universe": {
            "row_count": len(universe_rows),
            "rows": universe_rows,
            "rows_sha256": _canonical_sha256(universe_rows),
            "schema": "factor-v3-daily-universe-row-snapshot/v1",
        },
        "immutable_sqlite_contract": _IMMUTABLE_SQLITE_CONTRACT,
        "schema": _EXPORT_SCHEMA,
        "sources": {
            "cas_snapshot": cas_source,
            "development_artifact": development_source,
        },
        "suspension_events": {
            "row_count": len(suspension_rows),
            "rows": suspension_rows,
            "rows_sha256": _canonical_sha256(suspension_rows),
            "schema": "factor-v3-suspension-event-row-snapshot/v1",
        },
        "trade_dates": trade_dates,
    }
