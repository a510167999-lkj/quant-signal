"""Private disposable SQLite producer for materializer-v2 contract tests."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
from typing import Any

from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract


@dataclass(frozen=True)
class DisposableMaterializationFixture:
    sqlite_artifact_path: Path
    sqlite_artifact_file_sha256: str
    producer_receipt_path: Path
    producer_receipt_file_sha256: str
    independent_receipt_path: Path
    independent_receipt_file_sha256: str
    output_root: Path
    identity_rows: tuple[dict[str, Any], ...]
    arm_float64_rows: dict[str, tuple[bytes, ...]]
    arm_float64_roots: dict[str, str]
    identity_root_sha256: str

    def producer_kwargs(self) -> dict[str, str]:
        return {
            "sqlite_artifact_path": str(self.sqlite_artifact_path),
            "expected_sqlite_artifact_file_sha256": (
                self.sqlite_artifact_file_sha256
            ),
            "producer_receipt_path": str(self.producer_receipt_path),
            "expected_producer_receipt_file_sha256": (
                self.producer_receipt_file_sha256
            ),
            "independent_receipt_path": str(self.independent_receipt_path),
            "expected_independent_receipt_file_sha256": (
                self.independent_receipt_file_sha256
            ),
            "output_root": str(self.output_root),
        }

    def verifier_kwargs(self) -> dict[str, str]:
        return {
            "sqlite_artifact_path": str(self.sqlite_artifact_path),
            "expected_sqlite_artifact_file_sha256": (
                self.sqlite_artifact_file_sha256
            ),
            "producer_receipt_path": str(self.producer_receipt_path),
            "expected_producer_receipt_file_sha256": (
                self.producer_receipt_file_sha256
            ),
            "output_root": str(self.output_root / "independent-output"),
        }


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_self_hashed_json(
    path: Path,
    unsigned: dict[str, Any],
    *,
    self_field: str,
) -> tuple[Path, str]:
    payload = {**unsigned, self_field: sha_bytes(canonical_bytes(unsigned))}
    raw = canonical_bytes(payload)
    path.write_bytes(raw)
    return path, sha_bytes(raw)


def _f64le(values: tuple[float, ...]) -> bytes:
    return b"".join(struct.pack("<d", value) for value in values)


def _create_sqlite(
    path: Path,
) -> tuple[
    tuple[dict[str, Any], ...],
    dict[str, tuple[bytes, ...]],
    dict[str, str],
    str,
]:
    identities = (
        {
            "identity_position": 0,
            "signal_date": "2026-01-05",
            "candidate_key": "cn-a-share:000001.SZ|2026-01-05",
            "stable_security_id": "cn-a-share:000001.SZ",
            "ts_code": "000001.SZ",
            "source_input_date_label": "2026-01-02",
            "outcome_binding_sha256": sha_bytes(b"outcome:000001.SZ"),
            "fold_binding_sha256": sha_bytes(b"fold:000001.SZ"),
            "cost_binding_sha256": sha_bytes(b"cost:000001.SZ"),
        },
        {
            "identity_position": 1,
            "signal_date": "2026-01-05",
            "candidate_key": "cn-a-share:600000.SH|2026-01-05",
            "stable_security_id": "cn-a-share:600000.SH",
            "ts_code": "600000.SH",
            "source_input_date_label": "2026-01-02",
            "outcome_binding_sha256": sha_bytes(b"outcome:600000.SH"),
            "fold_binding_sha256": sha_bytes(b"fold:600000.SH"),
            "cost_binding_sha256": sha_bytes(b"cost:600000.SH"),
        },
    )
    identity_payload = [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
        }
        for row in identities
    ]
    identity_root = sha_bytes(canonical_bytes(identity_payload))
    low_rank = -1.0 / 3.0
    high_rank = 1.0 / 3.0
    base_values = (
        (
            low_rank,
            low_rank,
            low_rank,
            low_rank,
            low_rank,
            low_rank,
            low_rank,
            low_rank,
            0.5,
            -2.5,
        ),
        (
            high_rank,
            high_rank,
            high_rank,
            high_rank,
            high_rank,
            high_rank,
            high_rank,
            high_rank,
            0.5,
            -2.5,
        ),
    )
    factor_values = (
        {
            contract.TURNOVER_LEVEL_FEATURE: low_rank,
            contract.ABNORMAL_TURNOVER_FEATURE: high_rank,
        },
        {
            contract.TURNOVER_LEVEL_FEATURE: high_rank,
            contract.ABNORMAL_TURNOVER_FEATURE: low_rank,
        },
    )
    arm_float64_rows: dict[str, tuple[bytes, ...]] = {}
    for arm, features in contract.ARM_FEATURE_NAMES.items():
        encoded: list[bytes] = []
        for row_index, base in enumerate(base_values):
            by_name = dict(zip(contract.BASE_FEATURE_NAMES, base))
            by_name.update(factor_values[row_index])
            encoded.append(_f64le(tuple(float(by_name[name]) for name in features)))
        arm_float64_rows[arm] = tuple(encoded)
    arm_roots = {
        arm: sha_bytes(b"".join(rows)) for arm, rows in arm_float64_rows.items()
    }

    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA page_size=4096;
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            PRAGMA application_id=1180268626;
            PRAGMA user_version=2;
            CREATE TABLE metadata (
                key TEXT NOT NULL PRIMARY KEY,
                value_json BLOB NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE sessions (
                session_position INTEGER NOT NULL PRIMARY KEY,
                trade_date TEXT NOT NULL UNIQUE,
                temporal_role TEXT NOT NULL
            );
            CREATE TABLE identities (
                identity_position INTEGER NOT NULL PRIMARY KEY,
                signal_date TEXT NOT NULL,
                candidate_key TEXT NOT NULL,
                stable_security_id TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                source_input_date_label TEXT NOT NULL,
                outcome_binding_sha256 TEXT NOT NULL,
                fold_binding_sha256 TEXT NOT NULL,
                cost_binding_sha256 TEXT NOT NULL,
                UNIQUE (signal_date, candidate_key)
            );
            CREATE TABLE arms (
                arm_position INTEGER NOT NULL PRIMARY KEY,
                arm_name TEXT NOT NULL UNIQUE,
                feature_count INTEGER NOT NULL,
                feature_names_json BLOB NOT NULL,
                identity_root_sha256 TEXT NOT NULL,
                float64_rows_sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL
            );
            CREATE TABLE arm_feature_rows (
                arm_name TEXT NOT NULL,
                identity_position INTEGER NOT NULL,
                feature_values_f64le BLOB NOT NULL,
                PRIMARY KEY (arm_name, identity_position)
            ) WITHOUT ROWID;
            CREATE TABLE exclusions (
                candidate_key TEXT NOT NULL,
                signal_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                authority_evidence_root_sha256 TEXT NOT NULL,
                PRIMARY KEY (candidate_key, signal_date)
            ) WITHOUT ROWID;
            CREATE TABLE per_signal_ledger (
                signal_date TEXT NOT NULL PRIMARY KEY,
                parent_row_count INTEGER NOT NULL,
                eligible_row_count INTEGER NOT NULL,
                excluded_row_count INTEGER NOT NULL
            ) WITHOUT ROWID;
            """
        )
        metadata = {
            "authority_scope": contract.DISPOSABLE_AUTHORITY_SCOPE,
            "development_only": True,
            "formal_materialization_eligible": False,
            "formal_materialization_performed": False,
            "identity_root_sha256": identity_root,
            "schema": contract.FORMAL_MATERIALIZATION_SQLITE_SCHEMA,
            "verified": False,
            **{field: False for field in contract.SAFETY_FALSE_FIELDS},
        }
        connection.executemany(
            "INSERT INTO metadata(key, value_json) VALUES (?, ?)",
            ((key, canonical_bytes(value)) for key, value in sorted(metadata.items())),
        )
        connection.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (
                (0, "2026-01-02", "source_t_minus_one"),
                (1, "2026-01-05", "development_signal"),
            ),
        )
        connection.executemany(
            "INSERT INTO identities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    row["identity_position"],
                    row["signal_date"],
                    row["candidate_key"],
                    row["stable_security_id"],
                    row["ts_code"],
                    row["source_input_date_label"],
                    row["outcome_binding_sha256"],
                    row["fold_binding_sha256"],
                    row["cost_binding_sha256"],
                )
                for row in identities
            ),
        )
        for arm_position, arm in enumerate(contract.ARM_ORDER):
            features = contract.ARM_FEATURE_NAMES[arm]
            connection.execute(
                "INSERT INTO arms VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    arm_position,
                    arm,
                    len(features),
                    canonical_bytes(list(features)),
                    identity_root,
                    arm_roots[arm],
                    len(identities),
                ),
            )
            connection.executemany(
                "INSERT INTO arm_feature_rows VALUES (?, ?, ?)",
                (
                    (arm, identity_position, raw)
                    for identity_position, raw in enumerate(arm_float64_rows[arm])
                ),
            )
        connection.execute(
            "INSERT INTO per_signal_ledger VALUES (?, ?, ?, ?)",
            ("2026-01-05", 2, 2, 0),
        )
        connection.commit()
    finally:
        connection.close()
    return identities, arm_float64_rows, arm_roots, identity_root


def _sqlite_evidence(path: Path) -> dict[str, Any]:
    order_by = {
        "metadata": "key",
        "sessions": "session_position",
        "identities": "identity_position",
        "arms": "arm_position",
        "arm_feature_rows": "arm_name, identity_position",
        "exclusions": "candidate_key, signal_date",
        "per_signal_ledger": "signal_date",
    }

    def canonical_cell(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"blob_hex": value.hex()}
        return value

    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        table_roots = {}
        for table in contract.SQLITE_TABLES:
            rows = [
                [canonical_cell(value) for value in row]
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {order_by[table]}"
                ).fetchall()
            ]
            table_roots[table] = sha_bytes(canonical_bytes(rows))
        objects = [
            list(row)
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name != 'sqlite_sequence' ORDER BY type, name"
            ).fetchall()
        ]
        compile_options = sorted(
            str(row[0]) for row in connection.execute("PRAGMA compile_options")
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        pragmas = {
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "page_size": connection.execute("PRAGMA page_size").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        }
    finally:
        connection.close()
    return {
        "compile_options_root_sha256": sha_bytes(canonical_bytes(compile_options)),
        "foreign_key_check_empty": foreign_keys == [],
        "integrity_check_ok": integrity == [("ok",)],
        "pragma_contract": pragmas,
        "sqlite_master_root_sha256": sha_bytes(canonical_bytes(objects)),
        "typed_table_roots_sha256": table_roots,
    }


def create_disposable_materialization_fixture(
    tmp_path: Path,
) -> DisposableMaterializationFixture:
    fixture_root = tmp_path / "disposable-materialization-v2"
    fixture_root.mkdir()
    sqlite_path = fixture_root / "materialization.sqlite3"
    identities, arm_rows, arm_roots, identity_root = _create_sqlite(sqlite_path)
    sqlite_sha = sha_bytes(sqlite_path.read_bytes())
    sqlite_evidence = _sqlite_evidence(sqlite_path)
    producer_unsigned = {
        "artifact_file_sha256": sqlite_sha,
        "authority_scope": contract.DISPOSABLE_AUTHORITY_SCOPE,
        "development_only": True,
        "disposable_matrix_contract_verified": True,
        "formal_materialization_eligible": False,
        "formal_materialization_performed": False,
        "identity_root_sha256": identity_root,
        "schema": contract.DISPOSABLE_MATERIALIZATION_PRODUCER_RECEIPT_SCHEMA,
        "sqlite_evidence": sqlite_evidence,
        "verified": False,
        "arm_float64_rows_sha256": arm_roots,
        **{field: False for field in contract.SAFETY_FALSE_FIELDS},
    }
    producer_path, producer_sha = _write_self_hashed_json(
        fixture_root / "producer-receipt.json",
        producer_unsigned,
        self_field="receipt_sha256",
    )
    independent_unsigned = {
        "artifact_file_sha256": sqlite_sha,
        "authority_scope": contract.DISPOSABLE_AUTHORITY_SCOPE,
        "development_only": True,
        "disposable_independent_replay_fixture": True,
        "formal_materialization_eligible": False,
        "formal_materialization_performed": False,
        "identity_root_sha256": identity_root,
        "producer_receipt_file_sha256": producer_sha,
        "schema": contract.DISPOSABLE_MATERIALIZATION_INDEPENDENT_RECEIPT_SCHEMA,
        "verified": False,
        **{field: False for field in contract.SAFETY_FALSE_FIELDS},
    }
    independent_path, independent_sha = _write_self_hashed_json(
        fixture_root / "independent-receipt.json",
        independent_unsigned,
        self_field="receipt_sha256",
    )
    output_root = fixture_root / "output"
    output_root.mkdir()
    return DisposableMaterializationFixture(
        sqlite_artifact_path=sqlite_path,
        sqlite_artifact_file_sha256=sqlite_sha,
        producer_receipt_path=producer_path,
        producer_receipt_file_sha256=producer_sha,
        independent_receipt_path=independent_path,
        independent_receipt_file_sha256=independent_sha,
        output_root=output_root,
        identity_rows=identities,
        arm_float64_rows=arm_rows,
        arm_float64_roots=arm_roots,
        identity_root_sha256=identity_root,
    )
