from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Callable

import pytest

from app.factor_v2_low_rvol_overlay_activator import (
    EXPECTED_PREREGISTRATION_INTRODUCING_COMMIT,
    EXPECTED_PREREGISTRATION_RAW_SHA256,
    activate_low_rvol_overlay,
    adjudicate_trade_key_increment,
    build_low_rvol_selection,
    canonical_sha256,
    compute_overlay_claim_key,
    verify_phase_one_decision_receipt,
)


PREREG_SHA = "54928441f34b92497c1c67b1b26c85e4bb267f9d3b069179ae870d017e6049a5"
PREREG_COMMIT = "e8baab1ac7d3c86a700fb89b5c8d6a912e43d2c0"
SPEC_SHA = "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
EVALUATION_PRODUCER_ROOT = (
    "926b9229a2e6e47ae24f3b590fab46ded2244e757fbe92fc6bbf0dd3de1dd938"
)
VERIFIER_ROOT = "3b4c62fa4a02ced6a2f271af74deb75b3234298fe61ae21fc120fd59f2bca55f"
SELECTION_CONTRACT_SHA = (
    "ec7b0b455e6ed7ec53f81ef5696d9717136a5a6fe106dca1bf587f4f48cfa788"
)
ARM_ORDER = ["v2_control", "overnight_20", "intraday_20"]
RECEIPT_SCHEMA = "factor-v2-development-evaluation-decision-verification-receipt/v1"
SOURCE_IDENTITY = {
    "status_path": (
        "E:/AI workspace/quant-signal-lkj/data/research_runs/"
        "audited_pit_factor_v2_development_evaluation_v1_development_4"
        ".run.status.json"
    ),
    "pid": 31564,
    "started_at": "2026-07-29T02:44:43.361756+00:00",
    "runner_file_sha256": "997f89e4f38262def6f1d846fa6a47355573b76efdd65eb4a5dee651e8034a4e",
    "claim_file_sha256": "d8f176e31bfa6376aa648f1f863acfc211b69b9216b3f162be16fbdf48c59228",
    "lock_file_sha256": "d8f176e31bfa6376aa648f1f863acfc211b69b9216b3f162be16fbdf48c59228",
}
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
SAFETY_FLAGS = (
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
)
FORBIDDEN_FLAGS = (
    "second_score_database_created",
    "parameter_search_performed",
    "weights_changed",
    "second_overlay_evaluated",
    "model_retrained",
    "model_rescored",
    "trade_deleted",
)
PHASE_TWO_RESULT_FIELDS = {
    "schema_version",
    "overlay_id",
    "decision",
    "claim_key",
    "evaluation_artifact_sha256",
    "preregistration_raw_sha256",
    "selection_contract_sha256",
    "source_replay_receipt_sha256",
    "selection_receipt_sha256",
    "control_trade_keys_sha256",
    "overlay_trade_keys_sha256",
    "source_decision_receipt_sha256",
    "overlay_evaluation_receipt_sha256",
    "execution_stress_receipt_sha256",
    "evidence_complete",
    *SAFETY_FLAGS,
    *FORBIDDEN_FLAGS,
    "result_sha256",
}


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _receipt(
    artifact_sha: str = "a" * 64,
    decisions: dict[str, str] | None = None,
) -> dict[str, Any]:
    unsigned = {
        "schema_version": RECEIPT_SCHEMA,
        "temporal_role": "development",
        "factor_v2_spec_sha256": SPEC_SHA,
        "evaluation_artifact_sha256": artifact_sha,
        "evaluation_manifest_file_sha256": "b" * 64,
        "evaluation_producer_root_sha256": EVALUATION_PRODUCER_ROOT,
        "verification_producer_root_sha256": VERIFIER_ROOT,
        "arm_order": list(ARM_ORDER),
        "arm_decisions": decisions or {arm: "RED" for arm in ARM_ORDER},
        "source_run_identity": deepcopy(SOURCE_IDENTITY),
        "verified": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }
    return {**unsigned, "receipt_sha256": canonical_sha256(unsigned)}


def _fixture(
    root: Path,
    *,
    artifact_sha: str = "a" * 64,
    decisions: dict[str, str] | None = None,
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    root.mkdir(parents=True, exist_ok=True)
    receipt = _receipt(artifact_sha, decisions)
    if mutate:
        mutate(receipt)
        receipt.pop("receipt_sha256", None)
        receipt["receipt_sha256"] = canonical_sha256(receipt)
    raw = _canonical_bytes(receipt)
    raw_sha = hashlib.sha256(raw).hexdigest()
    path = root / f"{raw_sha}.json"
    path.write_bytes(raw)
    return (
        {
            "path": str(path),
            "raw_file_sha256": raw_sha,
            "size_bytes": len(raw),
            "schema_version": RECEIPT_SCHEMA,
            "receipt_sha256": receipt["receipt_sha256"],
        },
        receipt,
        raw,
    )


def _safe_result(
    *,
    decision: str = "RED",
    claim_key: str | None = None,
    evaluation_artifact_sha256: str = "a" * 64,
    **overrides: Any,
) -> dict[str, Any]:
    unsigned = {
        "schema_version": "factor-v2-low-rvol-overlay-result/v1",
        "overlay_id": "low_rvol20_rank_overlay_20",
        "decision": decision,
        "claim_key": claim_key
        or compute_overlay_claim_key(evaluation_artifact_sha256),
        "evaluation_artifact_sha256": evaluation_artifact_sha256,
        "preregistration_raw_sha256": PREREG_SHA,
        "selection_contract_sha256": SELECTION_CONTRACT_SHA,
        "source_replay_receipt_sha256": "c" * 64,
        "selection_receipt_sha256": "d" * 64,
        "control_trade_keys_sha256": "e" * 64,
        "overlay_trade_keys_sha256": "f" * 64,
        "source_decision_receipt_sha256": _receipt(
            evaluation_artifact_sha256
        )["receipt_sha256"],
        "overlay_evaluation_receipt_sha256": (
            None
            if decision == "RED_NO_INCREMENT_WITHOUT_STRESS"
            else "8" * 64
        ),
        "execution_stress_receipt_sha256": (
            None
            if decision == "RED_NO_INCREMENT_WITHOUT_STRESS"
            else "9" * 64
        ),
        "evidence_complete": True,
        **{flag: False for flag in SAFETY_FLAGS + FORBIDDEN_FLAGS},
    }
    supplied_hash = overrides.pop("result_sha256", None)
    unsigned.update(overrides)
    return {
        **unsigned,
        "result_sha256": supplied_hash or canonical_sha256(unsigned),
    }


def _candidate(
    security_id: str,
    p_base: float,
    volatility_rank: float,
    amount: float,
    signal_date: str = "2026-01-05",
    exit_date: str | None = None,
    signal_industry: str | None = None,
) -> dict[str, Any]:
    return {
        "signal_date": signal_date,
        "exit_date": exit_date or signal_date,
        "signal_industry": signal_industry or f"industry-{security_id}",
        "stable_security_id": security_id,
        "trade_key": f"{signal_date}|{security_id}",
        "predicted_positive_utility_probability": p_base,
        "realized_volatility_20_pct_rank": volatility_rank,
        "candidate_amount": amount,
    }


def test_phase_one_is_anchored_receipt_only_and_all_red(tmp_path: Path) -> None:
    assert EXPECTED_PREREGISTRATION_RAW_SHA256 == PREREG_SHA
    assert EXPECTED_PREREGISTRATION_INTRODUCING_COMMIT == PREREG_COMMIT
    descriptor, expected, raw = _fixture(tmp_path)
    reads: list[Path] = []

    def read_receipt(path: Path) -> bytes:
        reads.append(Path(path))
        return raw

    verified = verify_phase_one_decision_receipt(
        descriptor,
        read_receipt_bytes=read_receipt,
    )

    assert verified == expected
    assert set(verified) == RECEIPT_FIELDS
    assert reads == [Path(descriptor["path"])]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update({"extra": True}), "field|schema|extra"),
        (lambda value: value.pop("evaluation_manifest_file_sha256"), "field|missing"),
        (lambda value: value.update({"verified": False}), "verified"),
        (
            lambda value: value.update({"verification_producer_root_sha256": "0" * 64}),
            "producer|root",
        ),
        (
            lambda value: value.update({"evaluation_producer_root_sha256": "0" * 64}),
            "producer|root",
        ),
        (
            lambda value: value.update(
                {"source_run_identity": {**SOURCE_IDENTITY, "pid": 31565}}
            ),
            "source.run|identity",
        ),
        (
            lambda value: value.update({"production_recommendation_eligible": True}),
            "production|eligible|safety",
        ),
    ],
)
def test_phase_one_fails_closed_on_exact_receipt_drift(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    descriptor, _, _ = _fixture(tmp_path, mutate=mutate)
    with pytest.raises(RuntimeError, match=message):
        verify_phase_one_decision_receipt(descriptor)


def test_phase_one_verifies_raw_descriptor_and_receipt_self_hash(tmp_path: Path) -> None:
    descriptor, receipt, original_raw = _fixture(tmp_path)
    with pytest.raises(RuntimeError, match="raw|content|hash"):
        verify_phase_one_decision_receipt(
            {**descriptor, "raw_file_sha256": "0" * 64}
        )

    receipt["receipt_sha256"] = "1" * 64
    raw = _canonical_bytes(receipt)
    path = tmp_path / "tampered.json"
    path.write_bytes(raw)
    with pytest.raises(RuntimeError, match="self|receipt|hash"):
        verify_phase_one_decision_receipt(
            {
                **descriptor,
                "path": str(path),
                "raw_file_sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "receipt_sha256": receipt["receipt_sha256"],
            }
        )

    duplicate_raw = original_raw.replace(
        b'"verified":true',
        b'"verified":true,"verified":true',
        1,
    )
    assert duplicate_raw != original_raw
    duplicate_path = tmp_path / "duplicate-key.json"
    duplicate_path.write_bytes(duplicate_raw)
    with pytest.raises(RuntimeError, match="duplicate|JSON|key"):
        verify_phase_one_decision_receipt(
            {
                **descriptor,
                "path": str(duplicate_path),
                "raw_file_sha256": hashlib.sha256(duplicate_raw).hexdigest(),
                "size_bytes": len(duplicate_raw),
            }
        )


@pytest.mark.parametrize(
    "decisions",
    [
        {"v2_control": "GREEN", "overnight_20": "RED", "intraday_20": "RED"},
        {"v2_control": "RED", "overnight_20": "UNKNOWN", "intraday_20": "RED"},
        {"v2_control": "RED", "overnight_20": "RED"},
    ],
)
def test_non_all_red_voids_before_claim_or_phase_two(
    tmp_path: Path,
    decisions: dict[str, str],
) -> None:
    descriptor, _, _ = _fixture(tmp_path / "receipt", decisions=decisions)
    calls = 0

    def forbidden(*_args: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("phase two must not start")

    with pytest.raises(RuntimeError, match="RED|decision|arm"):
        activate_low_rvol_overlay(
            descriptor,
            claim_directory=tmp_path / "claims",
            phase_two_runner=forbidden,
        )
    assert calls == 0
    assert not (tmp_path / "claims").exists()


def test_claim_formula_and_atomic_claim_precede_phase_two(tmp_path: Path) -> None:
    descriptor, receipt, _ = _fixture(tmp_path / "receipt")
    expected = hashlib.sha256(
        (PREREG_SHA + receipt["evaluation_artifact_sha256"]).encode("ascii")
    ).hexdigest()
    assert compute_overlay_claim_key(receipt["evaluation_artifact_sha256"]) == expected

    def phase_two(
        verified: dict[str, Any],
        claim: dict[str, Any],
    ) -> dict[str, Any]:
        assert verified == receipt
        assert claim["claim_key"] == expected
        assert Path(claim["path"]).is_file()
        return _safe_result()

    result = activate_low_rvol_overlay(
        descriptor,
        claim_directory=tmp_path / "claims",
        phase_two_runner=phase_two,
    )
    assert result["claim_key"] == expected
    assert result["reused"] is False


def test_only_one_distinct_trial_and_same_key_reuses_exact_result(
    tmp_path: Path,
) -> None:
    first, _, _ = _fixture(tmp_path / "first", artifact_sha="a" * 64)
    second, _, _ = _fixture(tmp_path / "second", artifact_sha="b" * 64)
    claims = tmp_path / "claims"
    calls: list[str] = []

    def runner(*_args: Any) -> dict[str, Any]:
        calls.append("run")
        return _safe_result(decision="RED")

    initial = activate_low_rvol_overlay(
        first,
        claim_directory=claims,
        phase_two_runner=runner,
    )
    repeated = activate_low_rvol_overlay(
        first,
        claim_directory=claims,
        phase_two_runner=runner,
    )
    assert calls == ["run"]
    assert repeated["reused"] is True
    assert repeated["result"] == initial["result"]
    assert repeated["result_sha256"] == initial["result_sha256"]

    with pytest.raises(RuntimeError, match="distinct|claim|single|second"):
        activate_low_rvol_overlay(
            second,
            claim_directory=claims,
            phase_two_runner=runner,
        )
    assert calls == ["run"]


def test_concurrent_same_key_waits_and_reuses_without_rerunning_phase_two(
    tmp_path: Path,
) -> None:
    descriptor, _, _ = _fixture(tmp_path / "receipt")
    claims = tmp_path / "claims"
    phase_two_started = threading.Event()
    release_phase_two = threading.Event()
    second_finished = threading.Event()
    calls = 0
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, BaseException] = {}

    def runner(
        verified: dict[str, Any],
        claim: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        phase_two_started.set()
        assert release_phase_two.wait(timeout=5)
        return _safe_result(
            claim_key=claim["claim_key"],
            evaluation_artifact_sha256=verified[
                "evaluation_artifact_sha256"
            ],
        )

    def activate(name: str) -> None:
        try:
            results[name] = activate_low_rvol_overlay(
                descriptor,
                claim_directory=claims,
                phase_two_runner=runner,
            )
        except BaseException as exc:
            errors[name] = exc
        finally:
            if name == "second":
                second_finished.set()

    first = threading.Thread(target=activate, args=("first",))
    second = threading.Thread(target=activate, args=("second",))
    first.start()
    assert phase_two_started.wait(timeout=5)
    second.start()
    try:
        assert second_finished.wait(timeout=0.2) is False
    finally:
        release_phase_two.set()
        first.join(timeout=5)
        second.join(timeout=5)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert errors == {}
    assert calls == 1
    assert results["first"]["reused"] is False
    assert results["second"]["reused"] is True
    assert results["second"]["result"] == results["first"]["result"]
    assert results["second"]["result_sha256"] == (
        results["first"]["result_sha256"]
    )


def test_fixed_formula_sorting_and_top3_use_original_low_vol_factor() -> None:
    rows = [
        _candidate("000001.SZ", 0.50, -0.90, 999),
        _candidate("000002.SZ", 0.60, 0.50, 80),
        _candidate("000003.SZ", 0.70, 0.20, 70),
        _candidate("000004.SZ", 0.70, -0.20, 60),
        _candidate("000005.SZ", 0.80, 0.90, 50),
        _candidate("000006.SZ", 0.90, 0.00, 40),
    ]
    original = deepcopy(rows)
    result = build_low_rvol_selection(rows)
    ranked = {row["stable_security_id"]: row for row in result["ranked_rows"]}

    assert rows == original
    assert "000001.SZ" not in ranked
    assert [ranked[key]["probability_rank"] for key in sorted(ranked)] == pytest.approx(
        [1 / 6, 2.5 / 6, 2.5 / 6, 4 / 6, 5 / 6]
    )
    for source in rows[1:]:
        scored = ranked[source["stable_security_id"]]
        low_vol = 1.0 - source["realized_volatility_20_pct_rank"]
        assert scored["low_volatility_term"] == low_vol
        assert scored["rank_score"] == (
            0.80 * scored["probability_rank"] + 0.20 * low_vol
        )
    assert [row["stable_security_id"] for row in result["selected_rows"]] == [
        "000006.SZ",
        "000004.SZ",
        "000005.SZ",
    ]


def test_rank_ties_use_amount_then_stable_id_and_missing_factor_fails_arm() -> None:
    tied = [
        _candidate("000003.SZ", 0.70, 0.0, 100),
        _candidate("000001.SZ", 0.70, 0.0, 100),
        _candidate("000002.SZ", 0.70, 0.0, 200),
        _candidate("000004.SZ", 0.60, 0.9, 999),
    ]
    selected = build_low_rvol_selection(tied)["selected_rows"]
    assert [row["stable_security_id"] for row in selected] == [
        "000002.SZ",
        "000001.SZ",
        "000003.SZ",
    ]

    invalid = deepcopy(tied)
    invalid[0].pop("realized_volatility_20_pct_rank")
    with pytest.raises(RuntimeError, match="realized_volatility|factor|missing"):
        build_low_rvol_selection(invalid)


def test_selection_inherits_frozen_portfolio_constraints_across_days() -> None:
    rows = [
        _candidate("A", 0.90, 0.0, 100, exit_date="2026-01-07", signal_industry="I1"),
        _candidate("B", 0.80, 0.0, 90, signal_industry="I1"),
        _candidate("C", 0.70, 0.0, 80, exit_date="2026-01-06", signal_industry="I2"),
        _candidate("D", 0.60, 0.0, 70, signal_industry="I3"),
        _candidate(
            "A",
            0.99,
            0.0,
            120,
            signal_date="2026-01-06",
            signal_industry="IX",
        ),
        _candidate(
            "E",
            0.95,
            0.0,
            110,
            signal_date="2026-01-06",
            signal_industry="I1",
        ),
        _candidate(
            "F",
            0.90,
            0.0,
            100,
            signal_date="2026-01-06",
            exit_date="2026-01-08",
            signal_industry="I2",
        ),
        _candidate(
            "G",
            0.85,
            0.0,
            90,
            signal_date="2026-01-06",
            exit_date="2026-01-08",
            signal_industry="I3",
        ),
        _candidate(
            "H",
            0.80,
            0.0,
            80,
            signal_date="2026-01-06",
            signal_industry="I4",
        ),
        _candidate(
            "A",
            0.99,
            0.0,
            130,
            signal_date="2026-01-07",
            signal_industry="I1",
        ),
    ]

    result = build_low_rvol_selection(rows)

    assert result["selection_contract"] == {
        "selection_contract_sha256": SELECTION_CONTRACT_SHA,
        "top_n": 3,
        "max_active_positions": 3,
        "same_security_exclusion": True,
        "one_position_per_signal_industry": True,
        "same_day_exit_before_selection": True,
    }
    selection_receipt = result["selection_receipt"]
    unsigned_selection = dict(selection_receipt)
    selection_receipt_sha256 = unsigned_selection.pop("receipt_sha256")
    assert selection_receipt_sha256 == canonical_sha256(
        unsigned_selection
    )
    assert selection_receipt["selection_contract_sha256"] == (
        SELECTION_CONTRACT_SHA
    )
    assert selection_receipt["selected_trade_keys"] == [
        "2026-01-05|A",
        "2026-01-05|C",
        "2026-01-05|D",
        "2026-01-06|F",
        "2026-01-06|G",
        "2026-01-07|A",
    ]
    assert [row["trade_key"] for row in result["selected_rows"]] == [
        "2026-01-05|A",
        "2026-01-05|C",
        "2026-01-05|D",
        "2026-01-06|F",
        "2026-01-06|G",
        "2026-01-07|A",
    ]


@pytest.mark.parametrize("failure", ["exit_date", "signal_industry", "duplicate"])
def test_selection_requires_frozen_identity_and_unique_trade_keys(
    failure: str,
) -> None:
    first = _candidate("A", 0.9, 0.0, 100)
    second = _candidate("B", 0.8, 0.0, 90)
    if failure == "duplicate":
        second["trade_key"] = first["trade_key"]
    else:
        first.pop(failure)

    with pytest.raises(RuntimeError, match=failure.replace("_", ".?") + "|trade.key"):
        build_low_rvol_selection([first, second])


def test_equal_trade_keys_red_without_metrics_or_stress() -> None:
    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., dict[str, Any]]:
        def call(*_args: Any) -> dict[str, Any]:
            calls.append(name)
            raise AssertionError(f"{name} must not run")

        return call

    result = adjudicate_trade_key_increment(
        control_trade_keys=["b", "a", "c"],
        overlay_trade_keys=["c", "b", "a"],
        read_performance_metrics=forbidden("metrics"),
        run_execution_stress=forbidden("stress"),
    )
    assert result["decision"] == "RED_NO_INCREMENT_WITHOUT_STRESS"
    assert result["ordered_control_trade_keys"] == ["a", "b", "c"]
    assert result["ordered_overlay_trade_keys"] == ["a", "b", "c"]
    assert result["control_trade_keys_sha256"] == result["overlay_trade_keys_sha256"]
    assert calls == []


def test_trade_key_uniqueness_precedes_metrics_and_increment_runs_one_stress() -> None:
    calls: list[str] = []
    with pytest.raises(RuntimeError, match="unique|duplicate|trade.key"):
        adjudicate_trade_key_increment(
            control_trade_keys=["a", "a"],
            overlay_trade_keys=["a"],
            read_performance_metrics=lambda: calls.append("metrics"),
            run_execution_stress=lambda _: calls.append("stress"),
        )
    assert calls == []

    metrics = {"net_return_pct": 1.0}

    def read_metrics() -> dict[str, float]:
        calls.append("metrics")
        return metrics

    def run_stress(value: dict[str, float]) -> dict[str, str]:
        assert value is metrics
        calls.append("stress")
        return {"decision": "RED"}

    result = adjudicate_trade_key_increment(
        control_trade_keys=["a"],
        overlay_trade_keys=["a", "b"],
        read_performance_metrics=read_metrics,
        run_execution_stress=run_stress,
    )
    assert calls == ["metrics", "stress"]
    assert result["decision"] == "RED"


def test_stress_cannot_overwrite_trade_key_comparison_evidence() -> None:
    control = ["a", "b"]
    overlay = ["a", "b", "c"]

    def malicious_stress(_metrics: Any) -> dict[str, Any]:
        return {
            "decision": "RED",
            "ordered_control_trade_keys": ["forged"],
            "ordered_overlay_trade_keys": ["forged"],
            "control_trade_keys_sha256": "0" * 64,
            "overlay_trade_keys_sha256": "0" * 64,
        }

    with pytest.raises(RuntimeError, match="reserved|overlap"):
        adjudicate_trade_key_increment(
            control_trade_keys=control,
            overlay_trade_keys=overlay,
            read_performance_metrics=lambda: {},
            run_execution_stress=malicious_stress,
        )


def test_phase_two_result_is_exact_self_hashed_and_bound_to_the_claim(
    tmp_path: Path,
) -> None:
    descriptor, receipt, _ = _fixture(tmp_path / "receipt")

    def runner(
        verified: dict[str, Any],
        claim: dict[str, Any],
    ) -> dict[str, Any]:
        return _safe_result(
            claim_key=claim["claim_key"],
            evaluation_artifact_sha256=verified[
                "evaluation_artifact_sha256"
            ],
        )

    activated = activate_low_rvol_overlay(
        descriptor,
        claim_directory=tmp_path / "claims",
        phase_two_runner=runner,
    )
    result = activated["result"]
    unsigned = dict(result)
    result_sha256 = unsigned.pop("result_sha256")

    assert set(result) == PHASE_TWO_RESULT_FIELDS
    assert result_sha256 == canonical_sha256(unsigned)
    assert result["claim_key"] == activated["claim_key"]
    assert result["evaluation_artifact_sha256"] == receipt[
        "evaluation_artifact_sha256"
    ]
    assert result["source_decision_receipt_sha256"] == receipt[
        "receipt_sha256"
    ]


@pytest.mark.parametrize(
    ("mutation", "resign", "message"),
    [
        (lambda value: value.update({"extra": True}), True, "extra|field|schema"),
        (lambda value: value.pop("selection_receipt_sha256"), True, "missing|field|schema"),
        (lambda value: value.update({"decision": "ARBITRARY"}), True, "decision"),
        (lambda value: value.update({"result_sha256": "0" * 64}), False, "self|hash"),
        (lambda value: value.update({"claim_key": "0" * 64}), True, "claim"),
        (
            lambda value: value.update({"evaluation_artifact_sha256": "0" * 64}),
            True,
            "evaluation|artifact",
        ),
        (
            lambda value: value.update({"preregistration_raw_sha256": "0" * 64}),
            True,
            "preregistration",
        ),
        (
            lambda value: value.update({"selection_contract_sha256": "0" * 64}),
            True,
            "selection|contract",
        ),
        (
            lambda value: value.update({"source_replay_receipt_sha256": "invalid"}),
            True,
            "source|receipt|SHA",
        ),
        (
            lambda value: value.update({"selection_receipt_sha256": "invalid"}),
            True,
            "selection|receipt|SHA",
        ),
        (
            lambda value: value.update({"control_trade_keys_sha256": "invalid"}),
            True,
            "control|trade|SHA",
        ),
        (
            lambda value: value.update(
                {"source_decision_receipt_sha256": "0" * 64}
            ),
            True,
            "source|decision|receipt",
        ),
        (
            lambda value: value.update(
                {"overlay_evaluation_receipt_sha256": "invalid"}
            ),
            True,
            "overlay|evaluation|receipt|SHA",
        ),
        (
            lambda value: value.update(
                {"execution_stress_receipt_sha256": "invalid"}
            ),
            True,
            "execution|stress|receipt|SHA",
        ),
    ],
)
def test_phase_two_result_fails_closed_on_schema_hash_or_binding_drift(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    resign: bool,
    message: str,
) -> None:
    descriptor, _, _ = _fixture(tmp_path / "receipt")

    def runner(
        verified: dict[str, Any],
        claim: dict[str, Any],
    ) -> dict[str, Any]:
        result = _safe_result(
            claim_key=claim["claim_key"],
            evaluation_artifact_sha256=verified[
                "evaluation_artifact_sha256"
            ],
        )
        mutation(result)
        if resign:
            result.pop("result_sha256", None)
            result["result_sha256"] = canonical_sha256(result)
        return result

    with pytest.raises(RuntimeError, match=message):
        activate_low_rvol_overlay(
            descriptor,
            claim_directory=tmp_path / "claims",
            phase_two_runner=runner,
        )


def test_no_increment_result_cannot_claim_execution_stress_ran(
    tmp_path: Path,
) -> None:
    descriptor, receipt, _ = _fixture(tmp_path / "receipt")

    with pytest.raises(RuntimeError, match="stress|increment"):
        activate_low_rvol_overlay(
            descriptor,
            claim_directory=tmp_path / "claims",
            phase_two_runner=lambda _verified, claim: _safe_result(
                decision="RED_NO_INCREMENT_WITHOUT_STRESS",
                claim_key=claim["claim_key"],
                evaluation_artifact_sha256=receipt[
                    "evaluation_artifact_sha256"
                ],
                control_trade_keys_sha256="e" * 64,
                overlay_trade_keys_sha256="e" * 64,
                overlay_evaluation_receipt_sha256="8" * 64,
                execution_stress_receipt_sha256="9" * 64,
            ),
        )


@pytest.mark.parametrize("flag", SAFETY_FLAGS + FORBIDDEN_FLAGS)
def test_safety_flags_are_always_false_and_forbidden_operations_rejected(
    tmp_path: Path,
    flag: str,
) -> None:
    descriptor, _, _ = _fixture(tmp_path / "receipt")
    with pytest.raises(RuntimeError, match=flag):
        activate_low_rvol_overlay(
            descriptor,
            claim_directory=tmp_path / "claims",
            phase_two_runner=lambda *_: _safe_result(**{flag: True}),
        )
