from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from app import factor_v3_verified_row_exporter as exporter


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_database(
    path: Path,
    *,
    universe_rows: list[tuple[str, str, str | None]],
    suspension_rows: list[tuple[str, str, str, str]],
    daily_schema_suffix: str = "",
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            f"""
            CREATE TABLE daily_universe (
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                exchange TEXT NOT NULL,
                name TEXT NOT NULL,
                industry TEXT,
                list_date TEXT,
                receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'bak_basic'),
                receipt_partition TEXT NOT NULL,
                PRIMARY KEY (trade_date, ts_code)
                {daily_schema_suffix}
            );
            CREATE TABLE suspension_events (
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                suspend_timing TEXT NOT NULL,
                suspend_type TEXT NOT NULL CHECK (suspend_type IN ('S', 'R')),
                receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'suspend_d'),
                receipt_partition TEXT NOT NULL,
                PRIMARY KEY (trade_date, ts_code, suspend_type, suspend_timing)
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO daily_universe (
                trade_date, ts_code, exchange, name, industry, list_date,
                receipt_dataset, receipt_partition
            ) VALUES (?, ?, 'SSE', 'fixture', NULL, ?, 'bak_basic', ?)
            """,
            [(*row, row[0]) for row in universe_rows],
        )
        connection.executemany(
            """
            INSERT INTO suspension_events (
                trade_date, ts_code, suspend_timing, suspend_type,
                receipt_dataset, receipt_partition
            ) VALUES (?, ?, ?, ?, 'suspend_d', ?)
            """,
            [(*row, row[0]) for row in suspension_rows],
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def verified_databases(tmp_path: Path) -> dict[str, object]:
    cas = tmp_path / "snapshot.sqlite3"
    development = tmp_path / "development.sqlite3"
    _write_database(
        cas,
        universe_rows=[
            ("20230103", "600001.SH", "20200101"),
            ("20230104", "000001.SZ", "19910403"),
        ],
        suspension_rows=[("20230104", "000001.SZ", "09:30", "S")],
    )
    _write_database(
        development,
        universe_rows=[
            ("20240102", "688001.SH", "20200101"),
            ("20240103", "430001.BJ", "20210101"),
        ],
        suspension_rows=[("20240103", "430001.BJ", "09:30", "R")],
    )
    return {
        "cas": cas,
        "development": development,
        "cas_dates": ("20230103", "20230104"),
        "development_dates": ("20240102", "20240103"),
    }


def _export(arguments: dict[str, object]) -> dict[str, object]:
    cas = arguments["cas"]
    development = arguments["development"]
    assert isinstance(cas, Path)
    assert isinstance(development, Path)
    return exporter.export_verified_factor_v3_rows(
        cas_snapshot_database_path=cas,
        expected_cas_snapshot_database_sha256=_sha256(cas),
        development_artifact_database_path=development,
        expected_development_artifact_database_sha256=_sha256(development),
        cas_snapshot_trade_dates=arguments["cas_dates"],
        development_trade_dates=arguments["development_dates"],
    )


def test_exports_canonical_rows_and_roots_from_both_immutable_databases(
    verified_databases: dict[str, object],
) -> None:
    result = _export(verified_databases)

    expected_universe = [
        {"list_date": "20200101", "trade_date": "20230103", "ts_code": "600001.SH"},
        {"list_date": "19910403", "trade_date": "20230104", "ts_code": "000001.SZ"},
        {"list_date": "20200101", "trade_date": "20240102", "ts_code": "688001.SH"},
        {"list_date": "20210101", "trade_date": "20240103", "ts_code": "430001.BJ"},
    ]
    expected_suspensions = [
        {"suspend_type": "S", "trade_date": "20230104", "ts_code": "000001.SZ"},
        {"suspend_type": "R", "trade_date": "20240103", "ts_code": "430001.BJ"},
    ]
    assert result["schema"] == "factor-v3-verified-row-export/v1"
    assert result["immutable_sqlite_contract"] == "mode=ro&immutable=1"
    assert result["trade_dates"] == [
        "20230103",
        "20230104",
        "20240102",
        "20240103",
    ]
    assert result["daily_universe"] == {
        "row_count": 4,
        "rows": expected_universe,
        "rows_sha256": _canonical_sha256(expected_universe),
        "schema": "factor-v3-daily-universe-row-snapshot/v1",
    }
    assert result["suspension_events"] == {
        "row_count": 2,
        "rows": expected_suspensions,
        "rows_sha256": _canonical_sha256(expected_suspensions),
        "schema": "factor-v3-suspension-event-row-snapshot/v1",
    }
    assert result["sources"] == {
        "cas_snapshot": {
            "database_sha256": _sha256(verified_databases["cas"]),
            "daily_universe_row_count": 2,
            "suspension_event_row_count": 1,
            "trade_date_count": 2,
            "trade_dates_sha256": _canonical_sha256(["20230103", "20230104"]),
        },
        "development_artifact": {
            "database_sha256": _sha256(verified_databases["development"]),
            "daily_universe_row_count": 2,
            "suspension_event_row_count": 1,
            "trade_date_count": 2,
            "trade_dates_sha256": _canonical_sha256(["20240102", "20240103"]),
        },
    }


@pytest.mark.parametrize(
    ("argument_key", "value", "match"),
    [
        ("cas_dates", ("20230104", "20230103"), "ordered"),
        ("cas_dates", ("20230103", "20230103"), "unique"),
        ("development_dates", ("20230104", "20240103"), "disjoint"),
        ("development_dates", ("20240102", "20241301"), "date"),
    ],
)
def test_rejects_invalid_or_overlapping_date_authority(
    verified_databases: dict[str, object],
    argument_key: str,
    value: tuple[str, ...],
    match: str,
) -> None:
    arguments = dict(verified_databases)
    arguments[argument_key] = value
    with pytest.raises(ValueError, match=match):
        _export(arguments)


def test_rejects_out_of_range_or_incomplete_daily_universe_coverage(
    verified_databases: dict[str, object],
) -> None:
    arguments = dict(verified_databases)
    arguments["cas_dates"] = ("20230103", "20230105")
    with pytest.raises(ValueError, match="coverage"):
        _export(arguments)


def test_rejects_schema_mismatch(tmp_path: Path) -> None:
    cas = tmp_path / "bad.sqlite3"
    development = tmp_path / "development.sqlite3"
    connection = sqlite3.connect(cas)
    try:
        connection.executescript(
            """
            CREATE TABLE daily_universe (
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                list_date INTEGER,
                PRIMARY KEY (trade_date, ts_code)
            );
            CREATE TABLE suspension_events (
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                suspend_type TEXT NOT NULL,
                PRIMARY KEY (trade_date, ts_code, suspend_type)
            );
            INSERT INTO daily_universe VALUES ('20230103', '600001.SH', 20200101);
            """
        )
        connection.commit()
    finally:
        connection.close()
    _write_database(
        development,
        universe_rows=[("20240102", "000001.SZ", "19910403")],
        suspension_rows=[],
    )
    with pytest.raises(ValueError, match="schema"):
        exporter.export_verified_factor_v3_rows(
            cas_snapshot_database_path=cas,
            expected_cas_snapshot_database_sha256=_sha256(cas),
            development_artifact_database_path=development,
            expected_development_artifact_database_sha256=_sha256(development),
            cas_snapshot_trade_dates=("20230103",),
            development_trade_dates=("20240102",),
        )


def test_rejects_duplicate_projected_suspension_key(tmp_path: Path) -> None:
    cas = tmp_path / "snapshot.sqlite3"
    development = tmp_path / "development.sqlite3"
    _write_database(
        cas,
        universe_rows=[("20230103", "600001.SH", "20200101")],
        suspension_rows=[
            ("20230103", "600001.SH", "09:30", "S"),
            ("20230103", "600001.SH", "10:00", "S"),
        ],
    )
    _write_database(
        development,
        universe_rows=[("20240102", "000001.SZ", "19910403")],
        suspension_rows=[],
    )
    with pytest.raises(ValueError, match="duplicate"):
        exporter.export_verified_factor_v3_rows(
            cas_snapshot_database_path=cas,
            expected_cas_snapshot_database_sha256=_sha256(cas),
            development_artifact_database_path=development,
            expected_development_artifact_database_sha256=_sha256(development),
            cas_snapshot_trade_dates=("20230103",),
            development_trade_dates=("20240102",),
        )


def test_rejects_relative_path_hash_mismatch_sidecar_and_reparse(
    verified_databases: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cas = verified_databases["cas"]
    development = verified_databases["development"]
    assert isinstance(cas, Path)
    assert isinstance(development, Path)
    with pytest.raises(ValueError, match="absolute"):
        exporter.export_verified_factor_v3_rows(
            cas_snapshot_database_path=Path(cas.name),
            expected_cas_snapshot_database_sha256=_sha256(cas),
            development_artifact_database_path=development,
            expected_development_artifact_database_sha256=_sha256(development),
            cas_snapshot_trade_dates=verified_databases["cas_dates"],
            development_trade_dates=verified_databases["development_dates"],
        )
    with pytest.raises(ValueError, match="sha256"):
        exporter.export_verified_factor_v3_rows(
            cas_snapshot_database_path=cas,
            expected_cas_snapshot_database_sha256="0" * 64,
            development_artifact_database_path=development,
            expected_development_artifact_database_sha256=_sha256(development),
            cas_snapshot_trade_dates=verified_databases["cas_dates"],
            development_trade_dates=verified_databases["development_dates"],
        )

    sidecar = Path(f"{cas}-wal")
    sidecar.write_bytes(b"not-finalized")
    with pytest.raises(ValueError, match="sidecar"):
        _export(verified_databases)
    sidecar.unlink()

    original = exporter._is_reparse_point
    monkeypatch.setattr(
        exporter,
        "_is_reparse_point",
        lambda path: Path(path) == cas or original(Path(path)),
    )
    with pytest.raises(ValueError, match="reparse"):
        _export(verified_databases)


def test_postquery_toctou_is_fail_closed(
    verified_databases: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cas = verified_databases["cas"]
    assert isinstance(cas, Path)
    original = exporter._read_database_rows

    def touch_after_read(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        if kwargs["path"] == cas:
            stat_result = cas.stat()
            os.utime(
                cas,
                ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000),
            )
        return result

    monkeypatch.setattr(exporter, "_read_database_rows", touch_after_read)
    with pytest.raises(ValueError, match="drift"):
        _export(verified_databases)


def test_sqlite_connection_content_must_match_the_hashed_nofollow_handle(
    verified_databases: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cas = verified_databases["cas"]
    assert isinstance(cas, Path)
    replacement = tmp_path / "replacement.sqlite3"
    _write_database(
        replacement,
        universe_rows=[
            ("20230103", "688001.SH", "20200101"),
            ("20230104", "430001.BJ", "20210101"),
        ],
        suspension_rows=[],
    )
    real_connect = sqlite3.connect

    def forged_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if str(cas).replace("\\", "/") in str(database).replace("\\", "/"):
            source = real_connect(replacement)
            memory = real_connect(":memory:")
            try:
                source.backup(memory)
            finally:
                source.close()
            return memory
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(exporter.sqlite3, "connect", forged_connect)
    with pytest.raises(ValueError, match="connection.*(sha256|content)"):
        _export(verified_databases)
