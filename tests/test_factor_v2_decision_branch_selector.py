from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.factor_v2_decision_branch_selector import (
    build_factor_v2_decision_branch_receipt,
    select_factor_v2_decision_branch,
)


ARM_ORDER = ("v2_control", "overnight_20", "intraday_20")
LOW_RVOL_BRANCH = "low_rvol20_rank_overlay_20"
RECEIPT_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-decision-verification-receipt/v1"
)
FACTOR_V2_SPEC_SHA256 = (
    "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
)
EVALUATION_PRODUCER_ROOT_SHA256 = (
    "926b9229a2e6e47ae24f3b590fab46ded2244e757fbe92fc6bbf0dd3de1dd938"
)
VERIFICATION_PRODUCER_ROOT_SHA256 = (
    "3b4c62fa4a02ced6a2f271af74deb75b3234298fe61ae21fc120fd59f2bca55f"
)
RECEIPT_FIELDS = {
    "schema_version",
    "temporal_role",
    "factor_v2_spec_sha256",
    "evaluation_artifact_sha256",
    "evaluation_manifest_file_sha256",
    "evaluation_producer_root_sha256",
    "verification_producer_root_sha256",
    "arm_order",
    "arm_decisions",
    "source_run_identity",
    "verified",
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
    "receipt_sha256",
}
BRANCH_RECEIPT_SCHEMA_VERSION = (
    "factor-v2-decision-branch-selector-receipt/v1"
)
SELECTION_RULE = (
    "first_green_in_arm_order_else_low_rvol20_rank_overlay_20"
)
BRANCH_RECEIPT_FIELDS = {
    "schema_version",
    "source_decision_receipt_raw_file_sha256",
    "source_decision_receipt_sha256",
    "evaluation_artifact_sha256",
    "arm_order",
    "arm_decisions",
    "selection_rule",
    "selected_branch",
    "selected_arm",
    "low_rvol_overlay_status",
    "verified",
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
    "receipt_sha256",
}
SOURCE_RUN_IDENTITY = {
    "status_path": (
        "E:/AI workspace/quant-signal-lkj/data/research_runs/"
        "audited_pit_factor_v2_development_evaluation_v1_development_4"
        ".run.status.json"
    ),
    "pid": 31564,
    "started_at": "2026-07-29T02:44:43.361756+00:00",
    "runner_file_sha256": (
        "997f89e4f38262def6f1d846fa6a47355573b76efdd65eb4a5dee651e8034a4e"
    ),
    "claim_file_sha256": (
        "d8f176e31bfa6376aa648f1f863acfc211b69b9216b3f162be16fbdf48c59228"
    ),
    "lock_file_sha256": (
        "d8f176e31bfa6376aa648f1f863acfc211b69b9216b3f162be16fbdf48c59228"
    ),
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _receipt(
    decisions: dict[str, str] | None = None,
) -> dict[str, Any]:
    unsigned = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "temporal_role": "development",
        "factor_v2_spec_sha256": FACTOR_V2_SPEC_SHA256,
        "evaluation_artifact_sha256": "a" * 64,
        "evaluation_manifest_file_sha256": "b" * 64,
        "evaluation_producer_root_sha256": (
            EVALUATION_PRODUCER_ROOT_SHA256
        ),
        "verification_producer_root_sha256": (
            VERIFICATION_PRODUCER_ROOT_SHA256
        ),
        "arm_order": list(ARM_ORDER),
        "arm_decisions": decisions
        or {arm: "RED" for arm in ARM_ORDER},
        "source_run_identity": dict(SOURCE_RUN_IDENTITY),
        "verified": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }
    return {**unsigned, "receipt_sha256": _canonical_sha256(unsigned)}


def _resign(receipt: dict[str, Any]) -> None:
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = _canonical_sha256(unsigned)


def _write_receipt(
    tmp_path: Path,
    receipt: dict[str, Any],
) -> tuple[Path, str]:
    raw = _canonical_bytes(receipt)
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    path = tmp_path / f"{raw_sha256}.json"
    path.write_bytes(raw)
    return path, raw_sha256


def _select(tmp_path: Path, receipt: dict[str, Any]) -> str:
    path, raw_sha256 = _write_receipt(tmp_path, receipt)
    return select_factor_v2_decision_branch(
        path,
        expected_raw_file_sha256=raw_sha256,
    )


@pytest.mark.parametrize(
    ("decisions", "expected"),
    [
        (
            {
                "v2_control": "GREEN",
                "overnight_20": "GREEN",
                "intraday_20": "GREEN",
            },
            "v2_control",
        ),
        (
            {
                "v2_control": "RED",
                "overnight_20": "GREEN",
                "intraday_20": "GREEN",
            },
            "overnight_20",
        ),
        (
            {
                "v2_control": "RED",
                "overnight_20": "RED",
                "intraday_20": "GREEN",
            },
            "intraday_20",
        ),
        (
            {
                "v2_control": "RED",
                "overnight_20": "RED",
                "intraday_20": "RED",
            },
            LOW_RVOL_BRANCH,
        ),
    ],
)
def test_selects_first_green_in_frozen_order_or_unique_low_rvol_on_all_red(
    tmp_path: Path,
    decisions: dict[str, str],
    expected: str,
) -> None:
    selected = _select(tmp_path, _receipt(decisions))

    assert type(selected) is str
    assert selected == expected


@pytest.mark.parametrize(
    (
        "decisions",
        "selected_branch",
        "selected_arm",
        "low_rvol_overlay_status",
    ),
    [
        (
            {
                "v2_control": "RED",
                "overnight_20": "GREEN",
                "intraday_20": "GREEN",
            },
            "overnight_20",
            "overnight_20",
            "VOID",
        ),
        (
            {
                "v2_control": "RED",
                "overnight_20": "RED",
                "intraday_20": "RED",
            },
            LOW_RVOL_BRANCH,
            None,
            "ELIGIBLE",
        ),
    ],
)
def test_builds_exact_self_hashed_branch_receipt_for_execution_stress(
    tmp_path: Path,
    decisions: dict[str, str],
    selected_branch: str,
    selected_arm: str | None,
    low_rvol_overlay_status: str,
) -> None:
    source = _receipt(decisions)
    path, raw_sha256 = _write_receipt(tmp_path, source)

    branch_receipt = build_factor_v2_decision_branch_receipt(
        path,
        expected_raw_file_sha256=raw_sha256,
    )

    assert set(branch_receipt) == BRANCH_RECEIPT_FIELDS
    assert branch_receipt == {
        "schema_version": BRANCH_RECEIPT_SCHEMA_VERSION,
        "source_decision_receipt_raw_file_sha256": raw_sha256,
        "source_decision_receipt_sha256": source["receipt_sha256"],
        "evaluation_artifact_sha256": source[
            "evaluation_artifact_sha256"
        ],
        "arm_order": list(ARM_ORDER),
        "arm_decisions": decisions,
        "selection_rule": SELECTION_RULE,
        "selected_branch": selected_branch,
        "selected_arm": selected_arm,
        "low_rvol_overlay_status": low_rvol_overlay_status,
        "verified": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
        "receipt_sha256": branch_receipt["receipt_sha256"],
    }
    unsigned = dict(branch_receipt)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    assert _canonical_sha256(unsigned) == receipt_sha256
    assert {
        "return",
        "win_rate",
        "profit_factor",
        "drawdown",
        "metrics",
    }.isdisjoint(branch_receipt)


def test_branch_receipt_builder_fails_closed_on_invalid_source(
    tmp_path: Path,
) -> None:
    source = _receipt()
    source["metrics"] = {"profit_factor": 99.0}
    _resign(source)
    path, raw_sha256 = _write_receipt(tmp_path, source)

    with pytest.raises(RuntimeError):
        build_factor_v2_decision_branch_receipt(
            path,
            expected_raw_file_sha256=raw_sha256,
        )


@pytest.mark.parametrize("drift", ["missing", "extra"])
def test_requires_exactly_the_fifteen_receipt_fields(
    tmp_path: Path,
    drift: str,
) -> None:
    receipt = _receipt()
    assert set(receipt) == RECEIPT_FIELDS
    if drift == "missing":
        receipt.pop("evaluation_manifest_file_sha256")
    else:
        receipt["profit_factor"] = 99.0
    _resign(receipt)

    with pytest.raises(RuntimeError):
        _select(tmp_path, receipt)


def test_rejects_raw_file_hash_mismatch(tmp_path: Path) -> None:
    path, _ = _write_receipt(tmp_path, _receipt())

    with pytest.raises(RuntimeError):
        select_factor_v2_decision_branch(
            path,
            expected_raw_file_sha256="0" * 64,
        )


def test_rejects_tampering_even_when_the_raw_file_hash_matches(
    tmp_path: Path,
) -> None:
    receipt = _receipt()
    receipt["arm_decisions"]["v2_control"] = "GREEN"
    path, raw_sha256 = _write_receipt(tmp_path, receipt)

    with pytest.raises(RuntimeError):
        select_factor_v2_decision_branch(
            path,
            expected_raw_file_sha256=raw_sha256,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("factor_v2_spec_sha256", "0" * 64),
        ("evaluation_producer_root_sha256", "0" * 64),
        ("verification_producer_root_sha256", "0" * 64),
    ],
)
def test_rejects_untrusted_spec_or_producer_roots(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    receipt = _receipt()
    receipt[field] = value
    _resign(receipt)

    with pytest.raises(RuntimeError):
        _select(tmp_path, receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status_path", "E:/replacement/run.status.json"),
        ("pid", 31565),
        ("started_at", "2026-07-29T02:44:44+00:00"),
        ("runner_file_sha256", "0" * 64),
        ("claim_file_sha256", "0" * 64),
        ("lock_file_sha256", "0" * 64),
    ],
)
def test_requires_the_exact_witnessed_source_run_identity(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    receipt = _receipt()
    receipt["source_run_identity"][field] = value
    _resign(receipt)

    with pytest.raises(RuntimeError):
        _select(tmp_path, receipt)


@pytest.mark.parametrize("drift", ["missing", "extra"])
def test_rejects_source_identity_field_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    receipt = _receipt()
    if drift == "missing":
        receipt["source_run_identity"].pop("claim_file_sha256")
    else:
        receipt["source_run_identity"]["manifest_path"] = "forbidden.json"
    _resign(receipt)

    with pytest.raises(RuntimeError):
        _select(tmp_path, receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verified", False),
        ("embargo_consumed", True),
        ("final_oos_consumed", True),
        ("production_recommendation_eligible", True),
    ],
)
def test_requires_verified_receipt_and_all_safety_flags_closed(
    tmp_path: Path,
    field: str,
    value: bool,
) -> None:
    receipt = _receipt()
    receipt[field] = value
    _resign(receipt)

    with pytest.raises(RuntimeError):
        _select(tmp_path, receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "unknown/v1"),
        ("temporal_role", "final_oos"),
        ("arm_order", ["intraday_20", "overnight_20", "v2_control"]),
    ],
)
def test_rejects_unknown_or_reordered_contract_values(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    receipt = _receipt()
    receipt[field] = value
    _resign(receipt)

    with pytest.raises(RuntimeError):
        _select(tmp_path, receipt)


@pytest.mark.parametrize(
    "decisions",
    [
        {
            "v2_control": "UNKNOWN",
            "overnight_20": "RED",
            "intraday_20": "RED",
        },
        {
            "v2_control": "RED",
            "overnight_20": "RED",
        },
        {
            "v2_control": "RED",
            "overnight_20": "RED",
            "intraday_20": "RED",
            "low_rvol20_rank_overlay_20": "GREEN",
        },
    ],
)
def test_rejects_unknown_missing_or_extra_arm_decisions(
    tmp_path: Path,
    decisions: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError):
        _select(tmp_path, _receipt(decisions))


def test_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    receipt = _receipt()
    raw = _canonical_bytes(receipt)
    duplicated = raw.replace(
        b'{"arm_decisions":',
        b'{"schema_version":"duplicate/v1","arm_decisions":',
        1,
    )
    raw_sha256 = hashlib.sha256(duplicated).hexdigest()
    path = tmp_path / f"{raw_sha256}.json"
    path.write_bytes(duplicated)

    with pytest.raises(RuntimeError):
        select_factor_v2_decision_branch(
            path,
            expected_raw_file_sha256=raw_sha256,
        )


def test_reads_only_receipt_bytes_and_returns_no_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, raw_sha256 = _write_receipt(
        tmp_path,
        _receipt(
            {
                "v2_control": "GREEN",
                "overnight_20": "RED",
                "intraday_20": "RED",
            }
        ),
    )
    allowed = receipt_path.resolve()
    opened_paths: list[Path] = []
    original_builtin_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open

    def assert_receipt_path(value: Any) -> None:
        if isinstance(value, int):
            return
        path = Path(value).resolve()
        opened_paths.append(path)
        if path != allowed:
            raise AssertionError(
                f"receipt-only selector attempted to read {path}"
            )

    def guarded_builtin_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        assert_receipt_path(file)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_io_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        assert_receipt_path(file)
        return original_io_open(file, *args, **kwargs)

    def guarded_os_open(file: Any, *args: Any, **kwargs: Any) -> int:
        assert_receipt_path(file)
        return original_os_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)

    selected = select_factor_v2_decision_branch(
        receipt_path,
        expected_raw_file_sha256=raw_sha256,
    )

    assert opened_paths
    assert set(opened_paths) == {allowed}
    assert type(selected) is str
    assert selected == "v2_control"
