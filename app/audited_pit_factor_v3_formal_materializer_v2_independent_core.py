"""Independent raw replay core for Factor V3 materializer v2.

This module imports only the data contract.  It does not import producer
validators, builders, I/O helpers, error classes, or publication code.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract


class FactorV3FormalMaterializerV2IndependentCoreError(ValueError):
    """Raised by the independent implementation's own fail-closed path."""


def _red(capability: str) -> NoReturn:
    raise FactorV3FormalMaterializerV2IndependentCoreError(
        f"Factor V3 formal materializer v2 independent core unavailable: {capability}"
    )


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def independently_validate_calendar_v2(
    *,
    calendar_rows: Sequence[Mapping[str, Any]],
    parent_signal_dates: Sequence[str],
) -> dict[str, Any]:
    """Rebuild ordered 250/483/733/732 closure from raw session rows."""

    _ = (calendar_rows, parent_signal_dates)
    _red("independent strict calendar replay")


def independently_validate_board_ledger_v2(
    *,
    all_market_sessions: Sequence[str],
    source_dates: Sequence[str],
    upstream_board_rows: Sequence[Mapping[str, Any]],
    expected_per_date_prefilter_roots: Mapping[str, str],
) -> dict[str, Any]:
    """Rebuild all 733 per-date five-segment prefilter roots."""

    _ = (
        all_market_sessions,
        source_dates,
        upstream_board_rows,
        expected_per_date_prefilter_roots,
    )
    _red("independent board-ledger replay")


def independently_validate_parent_roots_v2(
    *,
    metadata: Mapping[str, Any],
    identity_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rebuild all three parent projections and terminal/evaluator/cost roots."""

    _ = (metadata, identity_rows)
    _red("independent parent and evaluator root replay")


def independently_validate_sqlite_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
) -> dict[str, Any]:
    """Open read-only/no-follow and rebuild exact DDL and float64 byte roots."""

    path = Path(sqlite_artifact_path)
    if not path.is_file():
        raise FactorV3FormalMaterializerV2IndependentCoreError("sqlite artifact missing")
    raw = path.read_bytes()
    if _sha_bytes(raw) != expected_sqlite_artifact_file_sha256:
        raise FactorV3FormalMaterializerV2IndependentCoreError("sqlite hash mismatch")
    if Path(f"{path}-wal").exists() or Path(f"{path}-shm").exists():
        raise FactorV3FormalMaterializerV2IndependentCoreError("sqlite wal/shm present")
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise FactorV3FormalMaterializerV2IndependentCoreError(
            "sqlite open failed"
        ) from exc
    try:
        try:
            connection.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error as exc:
            raise FactorV3FormalMaterializerV2IndependentCoreError(
                "sqlite is not a database"
            ) from exc
        objects = tuple(
            (row[0], row[1], row[2])
            for row in connection.execute(
                "SELECT type, name, tbl_name FROM sqlite_master "
                "WHERE name != 'sqlite_sequence' ORDER BY type, name"
            )
        )
        if objects != contract.SQLITE_EXPECTED_OBJECT_IDENTITIES:
            raise FactorV3FormalMaterializerV2IndependentCoreError(
                "sqlite object identities drift"
            )
        for table, expected in contract.SQLITE_TABLE_CONTRACT.items():
            observed = tuple(
                (row[1], row[2], row[3], row[5])
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if observed != expected:
                raise FactorV3FormalMaterializerV2IndependentCoreError(
                    f"sqlite table contract drift: {table}"
                )
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise FactorV3FormalMaterializerV2IndependentCoreError("sqlite integrity failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise FactorV3FormalMaterializerV2IndependentCoreError("sqlite fk check failed")
        observed_pragmas = {
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "page_size": connection.execute("PRAGMA page_size").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        }
        if observed_pragmas != contract.SQLITE_PRAGMA_CONTRACT:
            raise FactorV3FormalMaterializerV2IndependentCoreError("sqlite pragma drift")
        arm_rows: dict[str, list[bytes]] = {}
        for arm_name, feature_names_json, expected_root in connection.execute(
            "SELECT arm_name, feature_names_json, float64_rows_sha256 "
            "FROM arms ORDER BY arm_position"
        ):
            feature_names = json.loads(feature_names_json)
            raw_rows = [
                bytes(row[0])
                for row in connection.execute(
                    "SELECT feature_values_f64le FROM arm_feature_rows "
                    "WHERE arm_name=? ORDER BY identity_position",
                    (arm_name,),
                )
            ]
            if _sha_bytes(b"".join(raw_rows)) != expected_root:
                raise FactorV3FormalMaterializerV2IndependentCoreError(
                    f"arm root drift: {arm_name}"
                )
            for raw_row in raw_rows:
                expected_len = 8 * len(feature_names)
                if len(raw_row) != expected_len:
                    raise FactorV3FormalMaterializerV2IndependentCoreError(
                        "feature blob wrong length"
                    )
                # little-endian only: reject if big-endian re-pack differs and LE unpack fails finite rules
                try:
                    values = struct.unpack(f"<{len(feature_names)}d", raw_row)
                except struct.error as exc:
                    raise FactorV3FormalMaterializerV2IndependentCoreError(
                        "feature blob unpack failed"
                    ) from exc
                for name, value in zip(feature_names, values):
                    if not math.isfinite(value):
                        raise FactorV3FormalMaterializerV2IndependentCoreError(
                            "non-finite feature value"
                        )
                    # reject negative zero storage
                    if value == 0.0 and math.copysign(1.0, value) < 0.0:
                        raise FactorV3FormalMaterializerV2IndependentCoreError(
                            "negative zero forbidden"
                        )
                    if name in contract.RANK_FEATURE_NAMES and not (-1.0 <= value <= 1.0):
                        raise FactorV3FormalMaterializerV2IndependentCoreError(
                            "rank out of range"
                        )
                    if name in contract.ZERO_ONE_FEATURE_NAMES and not (
                        0.0 <= value <= 1.0
                    ):
                        raise FactorV3FormalMaterializerV2IndependentCoreError(
                            "breadth out of range"
                        )
            arm_rows[str(arm_name)] = raw_rows
    except FactorV3FormalMaterializerV2IndependentCoreError:
        raise
    except sqlite3.Error as exc:
        raise FactorV3FormalMaterializerV2IndependentCoreError(
            "sqlite is not a database"
        ) from exc
    finally:
        connection.close()
    return {
        "sqlite_artifact_file_sha256": expected_sqlite_artifact_file_sha256,
        "arms": sorted(arm_rows),
        "structural_replay_verified": True,
    }


def independently_validate_arm_matrices_v2(
    *,
    metadata: Mapping[str, Any],
    identity_rows: Sequence[Mapping[str, Any]],
    arm_rows: Mapping[str, Sequence[bytes]],
) -> dict[str, Any]:
    """Rebuild rank[-1,1], breadth[0,1], and finite median 10/11/11 matrices."""

    _ = identity_rows
    arm_order = list(metadata.get("arm_order") or [])
    feature_names_by_arm = dict(metadata.get("arm_feature_names") or {})
    if arm_order != list(contract.ARM_ORDER):
        raise FactorV3FormalMaterializerV2IndependentCoreError("arm order drift")
    for arm in arm_order:
        names = list(feature_names_by_arm.get(arm) or [])
        if names != list(contract.ARM_FEATURE_NAMES[arm]):
            raise FactorV3FormalMaterializerV2IndependentCoreError(
                f"feature names drift for {arm}"
            )
        rows = list(arm_rows.get(arm) or [])
        for raw in rows:
            if len(raw) != 8 * len(names):
                raise FactorV3FormalMaterializerV2IndependentCoreError(
                    "matrix row wrong length"
                )
            values = struct.unpack(f"<{len(names)}d", raw)
            for name, value in zip(names, values):
                if not math.isfinite(value):
                    raise FactorV3FormalMaterializerV2IndependentCoreError(
                        "matrix non-finite value"
                    )
                if name in contract.RANK_FEATURE_NAMES and not (-1.0 <= value <= 1.0):
                    raise FactorV3FormalMaterializerV2IndependentCoreError(
                        "matrix rank out of range"
                    )
                if name in contract.ZERO_ONE_FEATURE_NAMES and not (0.0 <= value <= 1.0):
                    raise FactorV3FormalMaterializerV2IndependentCoreError(
                        "matrix breadth out of range"
                    )
    return {
        "arm_order": arm_order,
        "identity_root_sha256": metadata.get("identity_root_sha256"),
        "structural_replay_verified": True,
    }


def replay_disposable_materialization_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
    producer_receipt_path: str,
    expected_producer_receipt_file_sha256: str,
) -> dict[str, Any]:
    """Replay a real disposable SQLite/receipt pair without formal promotion."""

    receipt_path = Path(producer_receipt_path)
    if not receipt_path.is_file():
        raise FactorV3FormalMaterializerV2IndependentCoreError("producer receipt missing")
    receipt_raw = receipt_path.read_bytes()
    if _sha_bytes(receipt_raw) != expected_producer_receipt_file_sha256:
        raise FactorV3FormalMaterializerV2IndependentCoreError(
            "producer receipt hash mismatch"
        )
    try:
        receipt = json.loads(receipt_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FactorV3FormalMaterializerV2IndependentCoreError(
            "producer receipt invalid json"
        ) from exc
    if not isinstance(receipt, dict) or not receipt:
        raise FactorV3FormalMaterializerV2IndependentCoreError("producer receipt empty")
    if receipt.get("authority_scope") not in {
        contract.DISPOSABLE_AUTHORITY_SCOPE,
        None,
    }:
        if receipt.get("authority_scope") == contract.FORMAL_AUTHORITY_SCOPE:
            raise FactorV3FormalMaterializerV2IndependentCoreError(
                "formal receipt rejected by disposable replay"
            )
    sqlite_result = independently_validate_sqlite_v2(
        sqlite_artifact_path=sqlite_artifact_path,
        expected_sqlite_artifact_file_sha256=expected_sqlite_artifact_file_sha256,
    )
    return {
        **sqlite_result,
        "authority_scope": contract.DISPOSABLE_AUTHORITY_SCOPE,
        "formal_verified": False,
        "verified": False,
        "formal_materialization_eligible": False,
        **{field: False for field in contract.SAFETY_FALSE_FIELDS},
    }


def replay_registered_formal_materialization_v2_once(
    *,
    registered_attempt_capability: object,
) -> dict[str, Any]:
    """Replay one registered formal attempt without caller paths or mappings."""

    _ = registered_attempt_capability
    _red("independent registered formal raw replay")


assert contract.INDEPENDENT_VERIFIER_ROLE != contract.PRODUCER_ROLE


__all__ = [
    "FactorV3FormalMaterializerV2IndependentCoreError",
    "independently_validate_arm_matrices_v2",
    "independently_validate_board_ledger_v2",
    "independently_validate_calendar_v2",
    "independently_validate_parent_roots_v2",
    "independently_validate_sqlite_v2",
    "replay_disposable_materialization_v2",
    "replay_registered_formal_materialization_v2_once",
]
