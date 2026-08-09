from __future__ import annotations

import ast
import inspect
import math
from pathlib import Path
import sqlite3
import struct

import pytest

from app import audited_pit_factor_v3_formal_materializer_v2 as producer
from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract
from app import audited_pit_factor_v3_formal_materializer_v2_independent_core as independent_core
from app import audited_pit_factor_v3_formal_materializer_v2_independent_verifier as verifier
from tests._factor_v3_formal_materializer_v2_disposable import (
    create_disposable_materialization_fixture,
    sha_bytes,
)


def _app_imports(module: object) -> set[str]:
    parsed = ast.parse(inspect.getsource(module))
    imports = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith("app"):
                imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(
                alias.name for alias in node.names if alias.name.startswith("app")
            )
    return imports


def _assert_exact_sqlite_contract(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for table, expected in contract.SQLITE_TABLE_CONTRACT.items():
            observed = tuple(
                (row[1], row[2], row[3], row[5])
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            assert observed == expected
        objects = tuple(
            (row[0], row[1], row[2])
            for row in connection.execute(
                "SELECT type, name, tbl_name FROM sqlite_master "
                "WHERE name != 'sqlite_sequence' ORDER BY type, name"
            )
        )
        assert objects == contract.SQLITE_EXPECTED_OBJECT_IDENTITIES
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        observed_pragmas = {
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "page_size": connection.execute("PRAGMA page_size").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        }
        assert observed_pragmas == contract.SQLITE_PRAGMA_CONTRACT
        arms = {
            row[0]: (tuple(__import__("json").loads(row[1])), row[2])
            for row in connection.execute(
                "SELECT arm_name, feature_names_json, float64_rows_sha256 "
                "FROM arms ORDER BY arm_position"
            )
        }
        for arm, (feature_names, expected_root) in arms.items():
            raw_rows = [
                bytes(row[0])
                for row in connection.execute(
                    "SELECT feature_values_f64le FROM arm_feature_rows "
                    "WHERE arm_name=? ORDER BY identity_position",
                    (arm,),
                )
            ]
            assert all(len(raw) == 8 * len(feature_names) for raw in raw_rows)
            assert sha_bytes(b"".join(raw_rows)) == expected_root
            for raw in raw_rows:
                values = struct.unpack(f"<{len(feature_names)}d", raw)
                assert all(math.isfinite(value) for value in values)
                for name, value in zip(feature_names, values):
                    if name in contract.RANK_FEATURE_NAMES:
                        assert -1.0 <= value <= 1.0
                    elif name in contract.ZERO_ONE_FEATURE_NAMES:
                        assert 0.0 <= value <= 1.0
    finally:
        connection.close()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


@pytest.mark.parametrize(
    "case",
    (
        "invalid-sqlite-bytes",
        "empty-receipt",
        "extra-table",
        "nan-float",
        "negative-zero",
        "wrong-length",
        "wrong-endian",
        "core-rank-out-of-range",
        "core-breadth-out-of-range",
        "core-median-nonfinite",
        "real-disposable",
        "formal-rejects-disposable",
    ),
)
def test_independent_program_is_isolated_and_replays_only_real_disposable_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    core_imports = _app_imports(independent_core)
    verifier_imports = _app_imports(verifier)
    assert core_imports == {
        "app",
    }
    assert verifier_imports == {"app"}
    core_source = inspect.getsource(independent_core)
    verifier_source = inspect.getsource(verifier)
    forbidden = (
        "audited_pit_factor_v3_formal_materializer_v2 as producer",
        "audited_pit_factor_v3_materializer",
        "build_factor_v3_arm_matrices_v2",
        "validate_exact_calendar_projection_v2",
        "_open_disposable_held_input_set_v2",
    )
    assert all(name not in core_source for name in forbidden)
    assert all(name not in verifier_source for name in forbidden)
    assert contract.PRODUCER_ROLE != contract.INDEPENDENT_VERIFIER_ROLE
    assert authority.REGISTERED_MATERIALIZER_V2_PRODUCER_SOURCE_ROOT_SHA256 is None
    assert authority.REGISTERED_MATERIALIZER_V2_INDEPENDENT_SOURCE_ROOT_SHA256 is None
    matrix_doc = inspect.getdoc(
        independent_core.independently_validate_arm_matrices_v2
    )
    assert matrix_doc is not None
    assert "rank[-1,1]" in matrix_doc
    assert "breadth[0,1]" in matrix_doc
    assert "finite median" in matrix_doc
    assert contract.FLOAT_STORAGE_CONTRACT["rank_feature_bounds"] == (-1.0, 1.0)
    assert contract.FLOAT_STORAGE_CONTRACT["zero_one_feature_bounds"] == (0.0, 1.0)
    assert contract.FLOAT_STORAGE_CONTRACT["unbounded_finite_features"] == (
        "cross_section_median_return_5d_pct",
    )

    fixture = create_disposable_materialization_fixture(tmp_path)
    _assert_exact_sqlite_contract(fixture.sqlite_artifact_path)

    def poison(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("independent verifier called producer code")

    for name in (
        "build_factor_v3_arm_matrices_v2",
        "derive_turnover_features_v2",
        "project_formal_market_scope_v2",
        "reconcile_exclusion_ledger_v2",
        "validate_exact_calendar_projection_v2",
        "validate_parent_frozen_projection_v2",
        "_open_disposable_held_input_set_v2",
        "_publish_disposable_materialization_contract_v2",
    ):
        monkeypatch.setattr(producer, name, poison)

    if case == "formal-rejects-disposable":
        assert not inspect.signature(
            verifier.independently_verify_registered_formal_materialization_v2_once
        ).parameters
        with pytest.raises(authority.FactorV3FormalMaterializerV2UnavailableError):
            verifier.independently_verify_registered_formal_materialization_v2_once()
        return

    if case.startswith("core-"):
        arm_rows = {
            arm: list(rows) for arm, rows in fixture.arm_float64_rows.items()
        }
        control = bytearray(arm_rows["control"][0])
        if case == "core-rank-out-of-range":
            feature_position = 0
            invalid_value = math.nextafter(1.0, math.inf)
        elif case == "core-breadth-out-of-range":
            feature_position = contract.BASE_FEATURE_NAMES.index(
                "cross_section_above_ma20_fraction"
            )
            invalid_value = math.nextafter(0.0, -math.inf)
        else:
            feature_position = contract.BASE_FEATURE_NAMES.index(
                "cross_section_median_return_5d_pct"
            )
            invalid_value = math.inf
        struct.pack_into("<d", control, feature_position * 8, invalid_value)
        arm_rows["control"][0] = bytes(control)
        metadata = {
            "arm_feature_counts": dict(contract.ARM_FEATURE_COUNTS),
            "arm_feature_names": {
                arm: list(names) for arm, names in contract.ARM_FEATURE_NAMES.items()
            },
            "arm_order": list(contract.ARM_ORDER),
            "identity_root_sha256": fixture.identity_root_sha256,
        }
        with pytest.raises(
            independent_core.FactorV3FormalMaterializerV2IndependentCoreError
        ):
            independent_core.independently_validate_arm_matrices_v2(
                metadata=metadata,
                identity_rows=fixture.identity_rows,
                arm_rows=arm_rows,
            )
        return

    kwargs = fixture.verifier_kwargs()
    if case == "invalid-sqlite-bytes":
        invalid = tmp_path / "invalid.sqlite3"
        invalid.write_bytes(b"not-a-sqlite-database")
        kwargs["sqlite_artifact_path"] = str(invalid)
        kwargs["expected_sqlite_artifact_file_sha256"] = sha_bytes(
            invalid.read_bytes()
        )
    elif case == "empty-receipt":
        empty = tmp_path / "empty-receipt.json"
        empty.write_bytes(b"{}")
        kwargs["producer_receipt_path"] = str(empty)
        kwargs["expected_producer_receipt_file_sha256"] = sha_bytes(b"{}")
    elif case in {
        "extra-table",
        "nan-float",
        "negative-zero",
        "wrong-length",
        "wrong-endian",
    }:
        connection = sqlite3.connect(fixture.sqlite_artifact_path)
        try:
            if case == "extra-table":
                connection.execute("CREATE TABLE unexpected(value TEXT)")
            else:
                raw = bytes(
                    connection.execute(
                        "SELECT feature_values_f64le FROM arm_feature_rows "
                        "WHERE arm_name='control' AND identity_position=0"
                    ).fetchone()[0]
                )
                if case == "nan-float":
                    raw = struct.pack("<d", math.nan) + raw[8:]
                elif case == "negative-zero":
                    raw = struct.pack("<d", -0.0) + raw[8:]
                elif case == "wrong-length":
                    raw = raw[:-1]
                else:
                    values = struct.unpack(f"<{len(raw) // 8}d", raw)
                    raw = b"".join(struct.pack(">d", value) for value in values)
                connection.execute(
                    "UPDATE arm_feature_rows SET feature_values_f64le=? "
                    "WHERE arm_name='control' AND identity_position=0",
                    (raw,),
                )
            connection.commit()
        finally:
            connection.close()
        kwargs["expected_sqlite_artifact_file_sha256"] = sha_bytes(
            fixture.sqlite_artifact_path.read_bytes()
        )

    if case != "real-disposable":
        with pytest.raises(
            verifier.FactorV3FormalMaterializerV2IndependentVerifierError
        ):
            verifier._independently_verify_disposable_materialization_v2(**kwargs)
        return

    result = verifier._independently_verify_disposable_materialization_v2(**kwargs)
    assert result["structural_replay_verified"] is True
    assert result["authority_scope"] == contract.DISPOSABLE_AUTHORITY_SCOPE
    assert result["formal_verified"] is False
    assert result["verified"] is False
    assert result["formal_materialization_eligible"] is False
    assert all(result[field] is False for field in contract.SAFETY_FALSE_FIELDS)
