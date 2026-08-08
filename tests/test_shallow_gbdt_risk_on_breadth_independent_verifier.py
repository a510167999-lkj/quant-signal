from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.audited_pit_shallow_gbdt_risk_on_breadth import (
    SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC,
)
from scripts import run_shallow_gbdt_risk_on_breadth_development_1 as launcher
from scripts import verify_shallow_gbdt_risk_on_breadth_development_1 as verifier


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _write_content_addressed(directory: Path, body: dict) -> tuple[Path, str]:
    digest = _sha256(body)
    path = directory / f"{digest}.json"
    path.write_text(
        json.dumps({**body, "artifact_sha256": digest}, sort_keys=True),
        encoding="utf-8",
    )
    return path, digest


def _runtime_body(
    *,
    main_sha256: str,
    sidecars: dict[str, str],
    feature_receipt_sha256: str = "f" * 64,
    producer_root_sha256: str | None = None,
) -> dict:
    body = {
        "schema_version": verifier.EXPECTED_VERIFICATION_SCHEMA,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": (
            producer_root_sha256
            or verifier.EXPECTED_PRODUCER_ROOT_SHA256
        ),
        "main_artifact_sha256": main_sha256,
        "sidecar_artifact_sha256": sidecars,
        "checks": {
            name: True for name in verifier.REQUIRED_VERIFICATION_CHECKS
        },
        "market_breadth_feature_binding_receipt_sha256": (
            feature_receipt_sha256
        ),
        "verified": True,
    }
    body["receipt_sha256"] = _sha256(body)
    return body


def test_risk_replay_plan_and_app_jobs_argv_are_exact_and_development_only() -> None:
    output_dir = "isolated/replay-output"
    expected = launcher._command_arguments()
    expected[-1] = output_dir

    assert verifier._jobs_argv(output_dir) == expected
    assert verifier.REPLAY_PLAN["command"] == launcher.RUN_SPEC["command"]
    assert "callable" not in verifier.REPLAY_PLAN
    assert verifier.REPLAY_PLAN["execution"] == {
        "entrypoint": "app.jobs",
        "direct_callable_allowed": False,
    }
    assert "runpy.run_module(\"app.jobs\"" in verifier.ISOLATED_REPLAY_DRIVER
    assert (
        "run_audited_pit_ranked_liquidity_shallow_gbdt_"
        "risk_on_breadth_rolling_oof"
        not in verifier.ISOLATED_REPLAY_DRIVER
    )
    assert verifier.REPLAY_PLAN["scope"] == {
        "point_in_time": True,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    }


def test_replay_plan_frozen_authority_is_self_verifying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert verifier.EXPECTED_REPLAY_PLAN_SHA256 == _sha256(verifier.REPLAY_PLAN)
    verifier._assert_frozen_replay_plan()
    drifted = deepcopy(verifier.REPLAY_PLAN)
    drifted["execution"]["direct_callable_allowed"] = True
    monkeypatch.setattr(verifier, "REPLAY_PLAN", drifted)

    with pytest.raises(ValueError, match="replay plan drifted"):
        verifier._assert_frozen_replay_plan()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_breadth_receipt",
        "false_check",
        "missing_check",
        "extra_check",
        "extra_field",
        "wrong_receipt",
    ],
)
def test_runtime_verification_requires_exact_eight_checks_and_breadth_binding(
    tmp_path: Path,
    mutation: str,
) -> None:
    main_sha256 = "a" * 64
    sidecars = {
        "features": "b" * 64,
        "models": "c" * 64,
        "execution": "d" * 64,
        "selection": "e" * 64,
    }
    verification_dir = tmp_path / "verifications"
    verification_dir.mkdir()
    body = _runtime_body(main_sha256=main_sha256, sidecars=sidecars)
    if mutation == "missing_breadth_receipt":
        body.pop("market_breadth_feature_binding_receipt_sha256")
    elif mutation == "false_check":
        body["checks"]["market_breadth_filter_replayed"] = False
    elif mutation == "missing_check":
        body["checks"].pop("market_breadth_filter_replayed")
    elif mutation == "extra_check":
        body["checks"]["unknown"] = True
    elif mutation == "extra_field":
        body["unknown"] = True
    if mutation == "wrong_receipt":
        body["receipt_sha256"] = "0" * 64
    else:
        body.pop("receipt_sha256")
        body["receipt_sha256"] = _sha256(body)
    _write_content_addressed(verification_dir, body)

    if mutation:
        with pytest.raises(ValueError, match="runtime verification"):
            verifier._verify_runtime_verification(
                tmp_path,
                expected_main_sha256=main_sha256,
                expected_sidecar_sha256=sidecars,
            )


def test_runtime_verification_accepts_exact_risk_envelope(tmp_path: Path) -> None:
    main_sha256 = "a" * 64
    sidecars = {
        "features": "b" * 64,
        "models": "c" * 64,
        "execution": "d" * 64,
        "selection": "e" * 64,
    }
    verification_dir = tmp_path / "verifications"
    verification_dir.mkdir()
    body = _runtime_body(main_sha256=main_sha256, sidecars=sidecars)
    _path, artifact_sha256 = _write_content_addressed(verification_dir, body)

    observed = verifier._verify_runtime_verification(
        tmp_path,
        expected_main_sha256=main_sha256,
        expected_sidecar_sha256=sidecars,
    )

    assert observed["artifact_sha256"] == artifact_sha256
    assert observed["market_breadth_feature_binding_receipt_sha256"] == "f" * 64


def _producer_binding() -> dict:
    return {
        "schema_version": (
            "audited-pit-ranked-liquidity-shallow-gbdt-risk-on-breadth-"
            "producer/v1"
        ),
        "base_shallow_gbdt_producer_root_sha256": (
            "8b087482776fbead91b11828536d14ce9d8fe815d39bfc645acb3dd05a0686bf"
        ),
        "shared_ranked_liquidity_module_sha256": (
            "95dddededefa71763a676e5b2b604f8523ea3cb1b79f0b62becaa0021743b345"
        ),
        "risk_on_breadth_module_sha256": (
            "8ce6c0c46d214d2fbb03fc259cd7f290294c5dcd90653032bfa74b87c753d84e"
        ),
        "config_module_sha256": (
            "832ebfa52f7ec77c527164550b356f145667b3cf53328c207b27966ce1b8b260"
        ),
        "formal_dispatch_module_sha256": (
            "fcf2840163f21cad51d0b55a001a538b714c368c51f04ca79c050daebe982537"
        ),
        "risk_on_breadth_strategy_sha256": (
            verifier.EXPECTED_STRATEGY_SHA256
        ),
        "root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
    }


def _strategy_binding() -> dict:
    return {
        **deepcopy(SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC),
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
    }


def _write_result_bundle(
    tmp_path: Path,
    *,
    producer: dict | None = None,
    strategy: dict | None = None,
    scope_overrides: dict | None = None,
    filter_receipt_overrides: dict | None = None,
) -> tuple[dict, dict[str, str], str, str]:
    source = {"schema_version": "synthetic-source/v1"}
    producer = producer or _producer_binding()
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir()
    filter_receipt = {
        "schema_version": "ranked-liquidity-market-breadth-filter-receipt/v1",
        "filter": {
            "feature": "cross_section_above_ma20_fraction",
            "comparison": "greater_than_or_equal_unrounded_float64",
            "minimum": 0.5,
            "missing_policy": "fail_closed_run",
        },
        "input_candidate_count": 1,
        "input_candidate_keys_sha256": "3" * 64,
        "eligible_candidate_count": 1,
        "eligible_candidate_keys_sha256": "4" * 64,
        "excluded_candidate_count": 0,
        "excluded_candidate_keys_sha256": "5" * 64,
        **(filter_receipt_overrides or {}),
    }
    filter_receipt["receipt_sha256"] = _sha256(filter_receipt)
    filter_receipt_sha256 = filter_receipt["receipt_sha256"]
    sidecars: dict[str, str] = {}
    for name, schema in verifier.RESULT_BUNDLE_SIDECAR_SCHEMAS.items():
        payload = {
            "schema_version": schema,
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "source": source,
            "producer_code": producer,
        }
        if name == "selection":
            payload["market_breadth_filter_receipt"] = filter_receipt
        _path, sidecars[name] = _write_content_addressed(sidecar_dir, payload)
    main = {
        "schema_version": verifier.EXPECTED_RESULT_SCHEMA,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "strategy": strategy or _strategy_binding(),
        "source": source,
        "producer_code": producer,
        "sidecars": {
            name: {
                "artifact_sha256": digest,
                "relative_path": f"sidecars/{digest}.json",
            }
            for name, digest in sidecars.items()
        },
        "market_breadth_filter_receipt_sha256": filter_receipt_sha256,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "strict_artifact_native_execution": True,
            "intraday_fill_claimed": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "advancement_gate_passed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
            **(scope_overrides or {}),
        },
    }
    _path, main_sha256 = _write_content_addressed(tmp_path, main)
    runtime = _runtime_body(
        main_sha256=main_sha256,
        sidecars=sidecars,
        feature_receipt_sha256="2" * 64,
        producer_root_sha256=producer["root_sha256"],
    )
    verification_dir = tmp_path / "verifications"
    verification_dir.mkdir()
    runtime_path, _runtime_sha256 = _write_content_addressed(
        verification_dir,
        runtime,
    )
    runtime_document = json.loads(runtime_path.read_text(encoding="utf-8"))
    return runtime_document, sidecars, main_sha256, filter_receipt_sha256


@pytest.mark.parametrize(
    "field",
    ["eligible_for_profile_registration", "production_recommendation_eligible"],
)
def test_result_bundle_rejects_real_production_eligibility(
    tmp_path: Path,
    field: str,
) -> None:
    runtime, _sidecars, _main_sha256, _filter_sha256 = _write_result_bundle(
        tmp_path,
        scope_overrides={field: True},
    )

    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
        )


def test_result_bundle_rejects_forged_minimal_producer_binding(
    tmp_path: Path,
) -> None:
    runtime, _sidecars, _main_sha256, _filter_sha256 = _write_result_bundle(
        tmp_path,
        producer={"root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256},
    )

    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
        )


def test_result_bundle_rejects_forged_minimal_strategy_binding(
    tmp_path: Path,
) -> None:
    runtime, _sidecars, _main_sha256, _filter_sha256 = _write_result_bundle(
        tmp_path,
        strategy={"strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256},
    )

    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
        )


def test_result_bundle_rejects_unverified_filter_receipt_fields(
    tmp_path: Path,
) -> None:
    runtime, _sidecars, _main_sha256, _filter_sha256 = _write_result_bundle(
        tmp_path,
        filter_receipt_overrides={"unverified": True},
    )

    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
        )


@pytest.mark.parametrize("mutation", ["missing_artifact_field", "missing_file"])
def test_result_bundle_requires_physical_runtime_verification_anchor(
    tmp_path: Path,
    mutation: str,
) -> None:
    runtime, _sidecars, _main_sha256, _filter_sha256 = _write_result_bundle(
        tmp_path,
    )
    if mutation == "missing_artifact_field":
        runtime.pop("artifact_sha256")
    else:
        next((tmp_path / "verifications").iterdir()).unlink()

    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing_file", "extra_file", "wrong_schema", "wrong_relative_path"],
)
def test_result_bundle_requires_exact_content_addressed_risk_sidecars(
    tmp_path: Path,
    mutation: str,
) -> None:
    runtime, sidecars, _main_sha256, _filter_sha256 = _write_result_bundle(tmp_path)
    sidecar_dir = tmp_path / "sidecars"
    if mutation == "missing_file":
        (sidecar_dir / f"{sidecars['models']}.json").unlink()
    elif mutation == "extra_file":
        (sidecar_dir / ("9" * 64 + ".json")).write_text("{}", encoding="utf-8")
    elif mutation == "wrong_schema":
        path = sidecar_dir / f"{sidecars['execution']}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["schema_version"] = "wrong/v1"
        path.write_text(json.dumps(document), encoding="utf-8")
    elif mutation == "wrong_relative_path":
        main_path = next(
            path
            for path in tmp_path.glob("*.json")
            if len(path.stem) == 64
        )
        document = json.loads(main_path.read_text(encoding="utf-8"))
        document["sidecars"]["selection"]["relative_path"] = "wrong.json"
        main_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
        )


def test_result_bundle_keeps_feature_and_filter_receipts_distinct(
    tmp_path: Path,
) -> None:
    runtime, sidecars, main_sha256, filter_sha256 = _write_result_bundle(tmp_path)
    bundle = verifier._load_content_addressed_result_bundle(
        tmp_path,
        runtime_verification=runtime,
    )

    assert bundle["main_artifact_sha256"] == main_sha256
    assert bundle["sidecar_artifact_sha256"] == sidecars
    assert bundle["market_breadth_filter_receipt_sha256"] == filter_sha256
    assert bundle["market_breadth_feature_binding_receipt_sha256"] == "2" * 64
    assert filter_sha256 != "2" * 64


def test_result_bundle_loader_parameterizes_the_exact_replay_producer(
    tmp_path: Path,
) -> None:
    replay_producer = _relocated_risk_binding("6" * 64)
    runtime, _sidecars, _main_sha256, _filter_sha256 = _write_result_bundle(
        tmp_path,
        producer=replay_producer,
    )

    bundle = verifier._load_content_addressed_result_bundle(
        tmp_path,
        runtime_verification=runtime,
        expected_producer_binding=replay_producer,
    )

    assert bundle["main_document"]["producer_code"] == replay_producer
    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
        )
    with pytest.raises(ValueError, match="result bundle"):
        verifier._load_content_addressed_result_bundle(
            tmp_path,
            runtime_verification=runtime,
            expected_producer_binding=_relocated_risk_binding("7" * 64),
        )


def test_receipt_and_terminal_status_keep_all_production_authorities_false() -> None:
    receipt = verifier._receipt_body(verification_sha256="a" * 64)
    status = verifier._status_payload(
        verified=True,
        receipt_sha256="b" * 64,
        error_type=None,
    )
    for payload in (receipt, status):
        assert payload["profile_registration_authority"] is False
        assert payload["production_recommendation_authority"] is False
        assert payload["automatic_trading_authority"] is False
        assert payload["production_authority"] is False
        assert payload["embargo_consumed"] is False
        assert payload["final_oos_consumed"] is False
    assert receipt["receipt_alone_authoritative"] is False
    assert receipt["terminal_status_required"] is True
    assert status["verified"] is True

    failed = verifier._status_payload(
        verified=False,
        receipt_sha256=None,
        error_type="RuntimeError",
    )
    assert failed["verified"] is False
    assert failed["receipt_sha256"] is None
    assert failed["error_type"] == "RuntimeError"


def _signed_document(body: dict) -> dict:
    return {**body, "artifact_sha256": _sha256(body)}


def _ridge_binding() -> dict:
    identity = {"artifact_version": 3, "payload": "ridge-stable"}
    return {
        "schema_version": "audited-pit-ranked-liquidity-producer/v3",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _relocated_shallow_binding(library: str, *, compiler: str = "msvc") -> dict:
    body = {
        "schema_version": (
            "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
        ),
        "base_ranked_liquidity_producer_root_sha256": _ridge_binding()[
            "root_sha256"
        ],
        "xgboost_build_info": {
            "compiler": compiler,
            "libxgboost": library,
        },
    }
    return {**body, "root_sha256": _sha256(body)}


def _relocated_risk_binding(shallow_root_sha256: str) -> dict:
    body = {
        **{key: value for key, value in _producer_binding().items() if key != "root_sha256"},
        "base_shallow_gbdt_producer_root_sha256": shallow_root_sha256,
    }
    return {**body, "root_sha256": _sha256(body)}


def _models_runtime_fields(
    build_info: dict[str, str],
    *,
    oof_verified: bool = True,
) -> dict:
    fold = {
        "fit_receipt": {
            "runtime": {
                "xgboost_version": "3.0.2",
                "xgboost_build_info": deepcopy(build_info),
            },
            "payload": "fit-stable",
        },
        "predict_receipt": {
            "runtime": {
                "xgboost_version": "3.0.2",
                "xgboost_build_info": deepcopy(build_info),
            },
            "payload": "predict-stable",
        },
        "payload": "fold-stable",
    }
    fold["receipt_sha256"] = _sha256(fold)
    oof_receipt = {
        "fold_count": 1,
        "folds": [fold],
        "folds_sha256": _sha256([fold]),
        "oof_candidate_count": 1,
        "payload": "oof-stable",
    }
    oof_receipt["receipt_sha256"] = _sha256(oof_receipt)
    return {
        "oof_receipt": oof_receipt,
        "oof_replay_verification": {
            "verified": oof_verified,
            "receipt_sha256": oof_receipt["receipt_sha256"],
            "fold_count": 1,
            "oof_candidate_count": 1,
        },
    }


def _semantic_result_bundle(
    producer: dict,
    build_info: dict[str, str],
    *,
    main_payload: str = "main-stable",
    features_payload: str = "features-stable",
    models_payload: str = "models-stable",
    execution_payload: str = "execution-stable",
    selection_payload: str = "selection-stable",
    runtime_check_value: bool = True,
    feature_receipt_sha256: str = "2" * 64,
    oof_verified: bool = True,
) -> dict:
    source = {
        "schema_version": "synthetic-source/v1",
        "producer_code": deepcopy(producer),
    }
    sidecar_payloads: dict[str, dict] = {}
    payload_values = {
        "features": features_payload,
        "models": models_payload,
        "execution": execution_payload,
        "selection": selection_payload,
    }
    filter_receipt = {
        "schema_version": "ranked-liquidity-market-breadth-filter-receipt/v1",
        "filter": deepcopy(verifier.EXPECTED_MARKET_BREADTH_GATE),
        "input_candidate_count": 1,
        "input_candidate_keys_sha256": "3" * 64,
        "eligible_candidate_count": 1,
        "eligible_candidate_keys_sha256": "4" * 64,
        "excluded_candidate_count": 0,
        "excluded_candidate_keys_sha256": "5" * 64,
    }
    filter_receipt["receipt_sha256"] = _sha256(filter_receipt)
    for name, schema in verifier.RESULT_BUNDLE_SIDECAR_SCHEMAS.items():
        body = {
            "schema_version": schema,
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "source": deepcopy(source),
            "producer_code": deepcopy(producer),
            "payload": payload_values[name],
        }
        if name == "models":
            body.update(
                _models_runtime_fields(
                    build_info,
                    oof_verified=oof_verified,
                )
            )
        if name == "selection":
            body["market_breadth_filter_receipt"] = filter_receipt
        sidecar_payloads[name] = _signed_document(body)
    sidecar_hashes = {
        name: document["artifact_sha256"]
        for name, document in sidecar_payloads.items()
    }
    main = _signed_document(
        {
            "schema_version": verifier.EXPECTED_RESULT_SCHEMA,
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "strategy": _strategy_binding(),
            "source": source,
            "producer_code": producer,
            "sidecars": {
                name: {
                    "artifact_sha256": digest,
                    "relative_path": f"sidecars/{digest}.json",
                }
                for name, digest in sidecar_hashes.items()
            },
            "market_breadth_filter_receipt_sha256": filter_receipt[
                "receipt_sha256"
            ],
            "scope": {
                **verifier.EXPECTED_RESULT_SCOPE,
                "advancement_gate_passed": False,
            },
            "payload": main_payload,
        }
    )
    runtime_unsigned = {
        "schema_version": verifier.EXPECTED_VERIFICATION_SCHEMA,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": producer["root_sha256"],
        "main_artifact_sha256": main["artifact_sha256"],
        "sidecar_artifact_sha256": sidecar_hashes,
        "checks": {
            name: (
                runtime_check_value
                if name == "market_breadth_filter_replayed"
                else True
            )
            for name in verifier.REQUIRED_VERIFICATION_CHECKS
        },
        "market_breadth_feature_binding_receipt_sha256": (
            feature_receipt_sha256
        ),
        "verified": True,
    }
    runtime_unsigned["receipt_sha256"] = _sha256(runtime_unsigned)
    runtime = _signed_document(runtime_unsigned)
    return {
        "main_artifact_sha256": main["artifact_sha256"],
        "main_document": main,
        "sidecar_artifact_sha256": sidecar_hashes,
        "sidecar_documents": sidecar_payloads,
        "runtime_verification": runtime,
        "market_breadth_filter_receipt_sha256": filter_receipt[
            "receipt_sha256"
        ],
        "market_breadth_feature_binding_receipt_sha256": (
            feature_receipt_sha256
        ),
    }


def _execution_snapshot(
    formal_shallow: dict,
    replay_shallow: dict,
    formal_risk: dict,
    replay_risk: dict,
    *,
    output_dir: str = "isolated/replay-output",
    formal_library_sha256: str | None = None,
    replay_library_sha256: str | None = None,
    preflight_distribution_sha256: str | None = None,
    postflight_distribution_sha256: str | None = None,
) -> dict:
    formal_library = Path(
        formal_shallow["xgboost_build_info"]["libxgboost"]
    )
    replay_library = Path(
        replay_shallow["xgboost_build_info"]["libxgboost"]
    )
    body = {
        "schema_version": verifier.EXECUTION_SNAPSHOT_SCHEMA,
        "entrypoint": {
            "module": "app.jobs",
            "dispatch": "runpy.run_module",
            "argv": verifier._jobs_argv(output_dir),
            "output_dir": output_dir,
            "driver_sha256": _sha256(verifier.ISOLATED_REPLAY_DRIVER),
            "direct_callable_used": False,
        },
        "xgboost_relocation": {
            "formal_ridge_binding": _ridge_binding(),
            "replay_ridge_binding": _ridge_binding(),
            "formal_shallow_binding": formal_shallow,
            "replay_shallow_binding": replay_shallow,
            "formal_risk_binding": formal_risk,
            "replay_risk_binding": replay_risk,
            "formal_xgboost_library_sha256": formal_library_sha256,
            "replay_xgboost_library_sha256": (
                replay_library_sha256
                or hashlib.sha256(replay_library.read_bytes()).hexdigest()
            ),
            "formal_preflight_xgboost_distribution_files_sha256": (
                preflight_distribution_sha256
                or _test_xgboost_distribution_attestation(formal_library)[
                    "files_sha256"
                ]
            ),
            "formal_postflight_xgboost_distribution_files_sha256": (
                postflight_distribution_sha256
                or _test_xgboost_distribution_attestation(formal_library)[
                    "files_sha256"
                ]
            ),
        },
    }
    body["xgboost_relocation"]["formal_xgboost_library_sha256"] = (
        formal_library_sha256
        or hashlib.sha256(formal_library.read_bytes()).hexdigest()
    )
    return {**body, "snapshot_sha256": _sha256(body)}


def _test_xgboost_distribution_attestation(library: Path) -> dict[str, str]:
    library_sha256 = hashlib.sha256(library.read_bytes()).hexdigest()
    entries = [
        {
            "path": "xgboost/lib/xgboost.dll",
            "bytes": library.stat().st_size,
            "sha256": library_sha256,
        }
    ]
    return {
        "files_sha256": _sha256(entries),
        "library_sha256": library_sha256,
    }


def _replay_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[dict, dict, dict]:
    formal_library = (
        tmp_path / "formal-runtime" / "xgboost" / "lib" / "xgboost.dll"
    )
    replay_library = (
        tmp_path / "replay-runtime" / "xgboost" / "lib" / "xgboost.dll"
    )
    formal_library.parent.mkdir(parents=True)
    replay_library.parent.mkdir(parents=True)
    formal_library.write_bytes(b"identical-xgboost-library")
    replay_library.write_bytes(b"identical-xgboost-library")
    monkeypatch.setattr(
        verifier,
        "_xgboost_distribution_attestation",
        lambda library: _test_xgboost_distribution_attestation(Path(library)),
    )
    formal_shallow = _relocated_shallow_binding(
        str(formal_library)
    )
    replay_shallow = _relocated_shallow_binding(
        str(replay_library)
    )
    formal_risk = _relocated_risk_binding(formal_shallow["root_sha256"])
    replay_risk = _relocated_risk_binding(replay_shallow["root_sha256"])
    monkeypatch.setattr(
        verifier,
        "EXPECTED_PRODUCER_ROOT_SHA256",
        formal_risk["root_sha256"],
    )
    formal_bundle = _semantic_result_bundle(
        formal_risk,
        formal_shallow["xgboost_build_info"],
    )
    replay_bundle = _semantic_result_bundle(
        replay_risk,
        replay_shallow["xgboost_build_info"],
    )
    snapshot = _execution_snapshot(
        formal_shallow,
        replay_shallow,
        formal_risk,
        replay_risk,
    )
    history = {
        "schema_version": verifier.FORMAL_RUNTIME_HISTORY_SCHEMA,
        "preflight_xgboost_distribution_files_sha256": snapshot[
            "xgboost_relocation"
        ]["formal_preflight_xgboost_distribution_files_sha256"],
        "postflight_xgboost_distribution_files_sha256": snapshot[
            "xgboost_relocation"
        ]["formal_postflight_xgboost_distribution_files_sha256"],
        "immutable_inputs_unchanged": True,
    }
    return {
        "result_bundle": formal_bundle,
        "formal_runtime_history": history,
    }, {
        "schema_version": verifier.REPLAY_RESULT_SCHEMA,
        "execution_snapshot": snapshot,
        "result_bundle": replay_bundle,
    }, snapshot


def test_validate_replay_accepts_only_xgboost_absolute_path_relocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)

    receipt = verifier._validate_replay(inputs, replay, snapshot)

    assert receipt["schema_version"] == verifier.RELOCATION_EQUIVALENCE_SCHEMA
    assert receipt["verified"] is True
    assert receipt["market_breadth_feature_binding_receipt_sha256"] == "2" * 64
    assert receipt["formal_semantic_sha256"] == receipt["replay_semantic_sha256"]
    assert set(receipt["sidecar_semantic_sha256"]) == {
        "features",
        "models",
        "execution",
        "selection",
    }


def test_xgboost_distribution_attestation_hashes_the_complete_file_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    library = root / "xgboost" / "lib" / "xgboost.dll"
    module = root / "xgboost" / "core.py"
    library.parent.mkdir(parents=True)
    module.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"library-bytes")
    module.write_bytes(b"module-bytes")

    class Distribution:
        files = [
            Path("xgboost/core.py"),
            Path("xgboost/lib/xgboost.dll"),
        ]

        @staticmethod
        def locate_file(item: Path) -> Path:
            return root / item

    monkeypatch.setattr(
        verifier.importlib_metadata,
        "distribution",
        lambda name: Distribution() if name == "xgboost" else None,
    )
    expected_entries = [
        {
            "path": "xgboost/core.py",
            "bytes": len(b"module-bytes"),
            "sha256": hashlib.sha256(b"module-bytes").hexdigest(),
        },
        {
            "path": "xgboost/lib/xgboost.dll",
            "bytes": len(b"library-bytes"),
            "sha256": hashlib.sha256(b"library-bytes").hexdigest(),
        },
    ]

    observed = verifier._xgboost_distribution_attestation(library)

    assert observed == {
        "files_sha256": _sha256(expected_entries),
        "library_sha256": hashlib.sha256(b"library-bytes").hexdigest(),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("main_payload", "main-drift"),
        ("features_payload", "features-drift"),
        ("models_payload", "models-drift"),
        ("execution_payload", "execution-drift"),
        ("selection_payload", "selection-drift"),
        ("runtime_check_value", False),
        ("feature_receipt_sha256", "9" * 64),
    ],
)
def test_validate_replay_rejects_any_result_or_runtime_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)
    replay_risk = snapshot["xgboost_relocation"]["replay_risk_binding"]
    replay_shallow = snapshot["xgboost_relocation"][
        "replay_shallow_binding"
    ]
    replay["result_bundle"] = _semantic_result_bundle(
        replay_risk,
        replay_shallow["xgboost_build_info"],
        **{field: value},
    )

    with pytest.raises(ValueError, match="independent replay"):
        verifier._validate_replay(inputs, replay, snapshot)


def test_validate_replay_rejects_non_path_xgboost_or_library_content_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)
    formal_shallow = snapshot["xgboost_relocation"]["formal_shallow_binding"]
    replay_shallow = _relocated_shallow_binding(
        snapshot["xgboost_relocation"]["replay_shallow_binding"][
            "xgboost_build_info"
        ]["libxgboost"],
        compiler="tampered",
    )
    replay_risk = _relocated_risk_binding(replay_shallow["root_sha256"])
    drifted_snapshot = _execution_snapshot(
        formal_shallow,
        replay_shallow,
        snapshot["xgboost_relocation"]["formal_risk_binding"],
        replay_risk,
        replay_library_sha256="b" * 64,
    )
    replay["execution_snapshot"] = drifted_snapshot
    replay["result_bundle"] = _semantic_result_bundle(
        replay_risk,
        replay_shallow["xgboost_build_info"],
    )

    with pytest.raises(ValueError, match="independent replay"):
        verifier._validate_replay(inputs, replay, drifted_snapshot)


def test_validate_replay_hashes_actual_xgboost_library_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)
    replay_library = Path(
        snapshot["xgboost_relocation"]["replay_shallow_binding"][
            "xgboost_build_info"
        ]["libxgboost"]
    )
    replay_library.write_bytes(b"different-xgboost-library")

    with pytest.raises(ValueError, match="independent replay"):
        verifier._validate_replay(inputs, replay, snapshot)


def test_validate_replay_binds_pre_and_post_formal_distribution_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)
    relocation = snapshot["xgboost_relocation"]
    formal_library = Path(
        relocation["formal_shallow_binding"]["xgboost_build_info"][
            "libxgboost"
        ]
    )
    replay_library = Path(
        relocation["replay_shallow_binding"]["xgboost_build_info"][
            "libxgboost"
        ]
    )
    formal_library.write_bytes(b"post-run-replaced-library")
    replay_library.write_bytes(b"post-run-replaced-library")
    rebuilt_snapshot = _execution_snapshot(
        relocation["formal_shallow_binding"],
        relocation["replay_shallow_binding"],
        relocation["formal_risk_binding"],
        relocation["replay_risk_binding"],
    )
    replay["execution_snapshot"] = rebuilt_snapshot

    with pytest.raises(ValueError, match="independent replay"):
        verifier._validate_replay(inputs, replay, rebuilt_snapshot)


def test_validate_replay_requires_complete_successful_oof_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)
    relocation = snapshot["xgboost_relocation"]
    inputs["result_bundle"] = _semantic_result_bundle(
        relocation["formal_risk_binding"],
        relocation["formal_shallow_binding"]["xgboost_build_info"],
        oof_verified=False,
    )
    replay["result_bundle"] = _semantic_result_bundle(
        relocation["replay_risk_binding"],
        relocation["replay_shallow_binding"]["xgboost_build_info"],
        oof_verified=False,
    )

    with pytest.raises(ValueError, match="independent replay"):
        verifier._validate_replay(inputs, replay, snapshot)


def test_validate_replay_requires_exact_runpy_app_jobs_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)
    invalid_body = deepcopy(snapshot)
    invalid_body.pop("snapshot_sha256")
    invalid_body["entrypoint"]["argv"] = [
        "-m",
        "app.jobs",
        "direct-callable-shortcut",
    ]
    invalid_body["entrypoint"]["direct_callable_used"] = True
    invalid_snapshot = {
        **invalid_body,
        "snapshot_sha256": _sha256(invalid_body),
    }
    replay["execution_snapshot"] = invalid_snapshot

    with pytest.raises(ValueError, match="independent replay"):
        verifier._validate_replay(inputs, replay, invalid_snapshot)


def test_validate_replay_rejects_relative_xgboost_library_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs, replay, snapshot = _replay_case(monkeypatch, tmp_path)
    relative_shallow = _relocated_shallow_binding(
        "xgboost/lib/xgboost.dll"
    )
    relative_risk = _relocated_risk_binding(relative_shallow["root_sha256"])
    invalid_snapshot = _execution_snapshot(
        snapshot["xgboost_relocation"]["formal_shallow_binding"],
        relative_shallow,
        snapshot["xgboost_relocation"]["formal_risk_binding"],
        relative_risk,
        formal_library_sha256=snapshot["xgboost_relocation"][
            "formal_xgboost_library_sha256"
        ],
        replay_library_sha256=snapshot["xgboost_relocation"][
            "formal_xgboost_library_sha256"
        ],
        preflight_distribution_sha256=snapshot["xgboost_relocation"][
            "formal_preflight_xgboost_distribution_files_sha256"
        ],
        postflight_distribution_sha256=snapshot["xgboost_relocation"][
            "formal_postflight_xgboost_distribution_files_sha256"
        ],
    )
    replay["execution_snapshot"] = invalid_snapshot
    replay["result_bundle"] = _semantic_result_bundle(
        relative_risk,
        relative_shallow["xgboost_build_info"],
    )

    with pytest.raises(ValueError, match="independent replay"):
        verifier._validate_replay(inputs, replay, invalid_snapshot)


def test_phase3_minimal_environment_drops_unapproved_parent_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FORMAL_TEST_SECRET", "must-not-cross")
    monkeypatch.setenv("PATH", "must-not-cross")

    observed = verifier._minimal_child_environment(tmp_path)

    assert observed["DISABLE_ENV_FILE"] == "1"
    assert observed["PYTHONHASHSEED"] == "0"
    assert observed["PYTHONNOUSERSITE"] == "1"
    assert observed["PYTHONDONTWRITEBYTECODE"] == "1"
    assert observed["PYTHONUTF8"] == "1"
    assert observed["VPS_RUNTIME_ROLE"] == "local_research"
    assert observed["TEMP"] == str(tmp_path.resolve())
    assert observed["TMP"] == str(tmp_path.resolve())
    assert "FORMAL_TEST_SECRET" not in observed
    assert "PATH" not in observed


def test_phase3_json_document_size_policy_is_exact_and_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert verifier._json_document_size_limit("formal_control") == 64 * 1024 * 1024
    assert (
        verifier._json_document_size_limit("runtime_verification")
        == 64 * 1024 * 1024
    )
    assert verifier._json_document_size_limit("result_main") == 64 * 1024 * 1024
    assert (
        verifier._json_document_size_limit("result_sidecar_features")
        == 64 * 1024 * 1024
    )
    assert (
        verifier._json_document_size_limit("result_sidecar_models")
        == 64 * 1024 * 1024
    )
    assert (
        verifier._json_document_size_limit("result_sidecar_execution")
        == 512 * 1024 * 1024
    )
    assert (
        verifier._json_document_size_limit("result_sidecar_selection")
        == 768 * 1024 * 1024
    )
    assert (
        _sha256(verifier.JSON_DOCUMENT_SIZE_POLICY)
        == verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
    )

    monkeypatch.setitem(
        verifier.JSON_DOCUMENT_SIZE_POLICY["limits_bytes"]["result_sidecars"],
        "execution",
        513 * 1024 * 1024,
    )
    with pytest.raises(ValueError, match="size policy drifted"):
        verifier._json_document_size_limit("result_sidecar_execution")


def test_phase3_json_reader_rechecks_length_after_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.json"
    path.write_text('{"value":"longer-than-eight"}', encoding="utf-8")
    metadata = path.stat()
    claimed = SimpleNamespace(
        st_mode=metadata.st_mode,
        st_size=8,
        st_mtime_ns=metadata.st_mtime_ns,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino,
    )
    monkeypatch.setattr(
        verifier,
        "_json_document_size_limit",
        lambda _document_class: 8,
    )
    monkeypatch.setattr(verifier, "_assert_no_reparse", lambda *_args: claimed)
    monkeypatch.setattr(verifier.os, "fstat", lambda _fd: claimed)

    with pytest.raises(ValueError, match="bounded regular file"):
        verifier._read_json_object(
            path,
            "synthetic document",
            document_class="formal_control",
        )


def test_phase3_json_reader_rejects_class_limit_before_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.json"
    path.write_text("{}", encoding="utf-8")
    metadata = path.stat()
    oversized = SimpleNamespace(
        st_mode=metadata.st_mode,
        st_size=512 * 1024 * 1024 + 1,
        st_mtime_ns=metadata.st_mtime_ns,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino,
    )
    monkeypatch.setattr(verifier, "_assert_no_reparse", lambda *_args: oversized)

    opened = False

    def forbidden_open(*_args: object, **_kwargs: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("oversized JSON must not be opened")

    monkeypatch.setattr(
        Path,
        "open",
        forbidden_open,
    )

    with pytest.raises(ValueError, match="bounded regular file"):
        verifier._read_json_object(
            path,
            "synthetic execution sidecar",
            document_class="result_sidecar_execution",
        )
    assert opened is False


def test_phase3_json_reader_uses_one_handle_and_limit_plus_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.json"
    path.write_bytes(b"{}")
    metadata = path.stat()
    read_sizes: list[int] = []

    class RecordingHandle(io.BytesIO):
        def fileno(self) -> int:
            return 17

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return super().read(size)

    handle = RecordingHandle(b"{}")
    monkeypatch.setattr(
        verifier,
        "_json_document_size_limit",
        lambda _document_class: 8,
    )
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: handle)
    monkeypatch.setattr(verifier.os, "fstat", lambda _fd: metadata)

    document = verifier._read_json_object(
        path,
        "synthetic document",
        document_class="formal_control",
    )

    assert document == {}
    assert read_sizes == [9]


def test_phase3_json_reader_rejects_short_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.json"
    path.write_bytes(b"{}")
    metadata = path.stat()
    claimed = SimpleNamespace(
        st_mode=metadata.st_mode,
        st_size=metadata.st_size + 1,
        st_mtime_ns=metadata.st_mtime_ns,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino,
    )
    monkeypatch.setattr(verifier, "_assert_no_reparse", lambda *_args: claimed)
    monkeypatch.setattr(verifier.os, "fstat", lambda _fd: claimed)

    with pytest.raises(ValueError, match="bounded regular file"):
        verifier._read_json_object(
            path,
            "synthetic document",
            document_class="formal_control",
        )


def test_phase3_json_reader_rejects_open_handle_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.json"
    path.write_bytes(b"{}")
    metadata = path.stat()
    drifted = SimpleNamespace(
        st_mode=metadata.st_mode,
        st_size=metadata.st_size,
        st_mtime_ns=metadata.st_mtime_ns + 1,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino,
    )
    observed = iter((metadata, drifted))
    monkeypatch.setattr(verifier.os, "fstat", lambda _fd: next(observed))

    with pytest.raises(ValueError, match="bounded regular file"):
        verifier._read_json_object(
            path,
            "synthetic document",
            document_class="formal_control",
        )


def test_phase3_isolated_command_uses_exact_runpy_envelope(tmp_path: Path) -> None:
    python = tmp_path / "python.exe"
    code_root = tmp_path / "code"
    site_packages = tmp_path / "site-packages"
    output = tmp_path / "result"
    blocker = code_root / "scripts" / "formal_pycache_blocker_v1"
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(b"blocker")

    command = verifier._isolated_replay_command(
        python_executable=python,
        code_root=code_root,
        site_packages=site_packages,
        output_dir=output,
        pycache_blocker=blocker,
    )

    assert command[:7] == [
        str(python.resolve()),
        "-S",
        "-B",
        "-P",
        "-X",
        f"pycache_prefix={blocker.resolve()}",
        "-c",
    ]
    assert command[7] == verifier.ISOLATED_REPLAY_DRIVER
    assert json.loads(command[8]) == verifier._jobs_argv(str(output.resolve()))
    assert command[9:] == [str(code_root.resolve()), str(site_packages.resolve())]
    assert 'alter_sys=True' in verifier.ISOLATED_REPLAY_DRIVER


def test_phase3_formal_command_tokens_match_launcher_exactly() -> None:
    python = (verifier.PROJECT_ROOT / ".venv/Scripts/python.exe").resolve(
        strict=True
    )
    blocker = (
        verifier.PROJECT_ROOT / verifier.PYCACHE_BLOCKER_RELATIVE
    ).resolve(strict=True)

    expected = launcher._formal_research_command(
        python,
        environment={"PYTHONPYCACHEPREFIX": str(blocker)},
    )

    assert verifier._formal_command_tokens(verifier.PROJECT_ROOT, python) == expected


def test_phase3_isolated_command_executes_stub_app_jobs_once(tmp_path: Path) -> None:
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    site_packages = (verifier.PROJECT_ROOT / ".venv/Lib/site-packages").resolve(
        strict=True
    )
    python = (verifier.PROJECT_ROOT / ".venv/Scripts/python.exe").resolve(
        strict=True
    )
    app_root = code_root / "app"
    app_root.mkdir(parents=True)
    data_root.mkdir()
    (app_root / "__init__.py").write_text("", encoding="utf-8")
    (app_root / "jobs.py").write_text(
        """
import json
from pathlib import Path
import sys

output = Path(sys.argv[sys.argv.index("--output-dir") + 1])
output.mkdir(parents=True, exist_ok=True)
marker = output / "stub-invocation.json"
count = 0 if not marker.exists() else json.loads(marker.read_text())["count"]
marker.write_text(json.dumps({"argv": sys.argv, "count": count + 1}))
""".lstrip(),
        encoding="utf-8",
    )
    blocker = code_root / verifier.PYCACHE_BLOCKER_RELATIVE
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(
        (verifier.PROJECT_ROOT / verifier.PYCACHE_BLOCKER_RELATIVE).read_bytes()
    )
    output = data_root / "output"
    command = verifier._isolated_replay_command(
        python_executable=python,
        code_root=code_root,
        site_packages=site_packages,
        output_dir=output,
        pycache_blocker=blocker,
    )

    completed = verifier.subprocess.run(
        command,
        cwd=data_root,
        env=verifier._minimal_child_environment(tmp_path / "runtime-tmp"),
        stdin=verifier.subprocess.DEVNULL,
        stdout=verifier.subprocess.PIPE,
        stderr=verifier.subprocess.PIPE,
        shell=False,
        check=False,
    )

    marker = json.loads((output / "stub-invocation.json").read_text())
    assert completed.returncode == 0
    assert Path(marker["argv"][0]).resolve() == (app_root / "jobs.py").resolve()
    assert marker["argv"][1:] == verifier._jobs_argv(str(output.resolve()))[2:]
    assert marker["count"] == 1


def test_phase3_current_pool_probe_uses_detached_code_and_exact_binding(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "code"
    app_root = code_root / "app"
    app_root.mkdir(parents=True)
    (app_root / "__init__.py").write_text("", encoding="utf-8")
    (app_root / "current_pool_gate.py").write_text(
        """
from pathlib import Path

def verify_current_pool_audit(path):
    if Path(path).read_bytes() != b"audit-bytes":
        raise ValueError("wrong audit")
    return {"canonical_sha256": "a" * 64, "allowed_symbols": {"1", "2"}}
""".lstrip(),
        encoding="utf-8",
    )
    audit = tmp_path / "data" / "audit.json"
    audit.parent.mkdir()
    audit.write_bytes(b"audit-bytes")
    blocker = code_root / verifier.PYCACHE_BLOCKER_RELATIVE
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_bytes(
        (verifier.PROJECT_ROOT / verifier.PYCACHE_BLOCKER_RELATIVE).read_bytes()
    )
    python = (verifier.PROJECT_ROOT / ".venv/Scripts/python.exe").resolve(
        strict=True
    )
    site_packages = (verifier.PROJECT_ROOT / ".venv/Lib/site-packages").resolve(
        strict=True
    )
    expected = {"canonical_sha256": "a" * 64, "allowed_symbol_count": 2}

    observed = verifier._probe_current_pool_audit(
        code_root,
        audit_path=audit,
        expected_binding=expected,
        python_executable=python,
        site_packages=site_packages,
        environment=verifier._minimal_child_environment(tmp_path / "runtime-tmp"),
        pycache_blocker=blocker,
    )

    assert observed == expected


def test_phase3_requires_project_venv_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    expected = source / ".venv/Scripts/python.exe"
    observed = tmp_path / "global/python.exe"
    expected.parent.mkdir(parents=True)
    observed.parent.mkdir(parents=True)
    expected.write_bytes(b"venv")
    observed.write_bytes(b"global")
    monkeypatch.setattr(verifier.sys, "executable", str(observed))

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="project virtual environment",
    ):
        verifier._assert_project_interpreter(source)


def test_phase3_write_once_is_exclusive_and_preserves_first_bytes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "claim.json"
    first = b'{"first":true}\n'

    verifier._write_once(target, first, "claim")
    with pytest.raises(FileExistsError):
        verifier._write_once(target, b'{"second":true}\n', "claim")

    assert target.read_bytes() == first


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_phase3_sqlite_snapshot_requires_quiescent_source(
    tmp_path: Path,
    suffix: str,
) -> None:
    database = tmp_path / "metadata.sqlite3"
    database.write_bytes(b"sqlite")
    Path(f"{database}{suffix}").write_bytes(b"mutable")

    with pytest.raises(ValueError, match="SQLite"):
        verifier._assert_sqlite_quiescent(database)


def test_phase3_copies_only_four_frozen_input_classes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    relative = verifier.REPLAY_PLAN["inputs"]
    universe = source / relative["audited_pit_universe_path"]
    temporal = source / relative["temporal_contract_path"]
    audit = source / relative["current_pool_development_audit_path"]
    transition = source / relative["security_code_transition_evidence_root"]
    for path, raw in (
        (universe, b"sqlite"),
        (temporal, b"temporal"),
        (audit, b"audit"),
        (transition / "contract.json", b"transition"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    unrelated = source / "data" / "not-frozen.txt"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"do-not-copy")

    binding = verifier._copy_frozen_inputs(
        source,
        target,
        expected_formal_attestation=verifier._observed_frozen_attestation(
            source
        ),
    )

    assert binding["input_count"] == 4
    assert (target / relative["audited_pit_universe_path"]).read_bytes() == b"sqlite"
    assert (target / relative["temporal_contract_path"]).read_bytes() == b"temporal"
    assert (target / relative["current_pool_development_audit_path"]).read_bytes() == b"audit"
    assert (
        target / relative["security_code_transition_evidence_root"] / "contract.json"
    ).read_bytes() == b"transition"
    assert not (target / unrelated.relative_to(source)).exists()


def test_phase3_separate_data_root_rejects_preexisting_temporal_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    relative = verifier.REPLAY_PLAN["inputs"]
    for path, raw in (
        (source / relative["audited_pit_universe_path"], b"sqlite"),
        (source / relative["temporal_contract_path"], b"temporal"),
        (source / relative["current_pool_development_audit_path"], b"audit"),
        (
            source
            / relative["security_code_transition_evidence_root"]
            / "contract.json",
            b"transition",
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    tracked_temporal = target / relative["temporal_contract_path"]
    tracked_temporal.parent.mkdir(parents=True, exist_ok=True)
    tracked_temporal.write_bytes(b"temporal")

    with pytest.raises(FileExistsError, match="already exists"):
        verifier._copy_frozen_inputs(
            source,
            target,
            expected_formal_attestation=verifier._observed_frozen_attestation(
                source
            ),
        )


def test_phase3_rejects_drifted_tracked_temporal_contract(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    relative = verifier.REPLAY_PLAN["inputs"]
    for path, raw in (
        (source / relative["audited_pit_universe_path"], b"sqlite"),
        (source / relative["temporal_contract_path"], b"temporal"),
        (source / relative["current_pool_development_audit_path"], b"audit"),
        (
            source
            / relative["security_code_transition_evidence_root"]
            / "contract.json",
            b"transition",
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    tracked_temporal = target / relative["temporal_contract_path"]
    tracked_temporal.parent.mkdir(parents=True, exist_ok=True)
    tracked_temporal.write_bytes(b"drift")

    with pytest.raises(FileExistsError, match="already exists"):
        verifier._copy_frozen_inputs(
            source,
            target,
            expected_formal_attestation=verifier._observed_frozen_attestation(
                source
            ),
        )


def test_phase3_source_snapshot_checks_detached_c_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    scratch = tmp_path / "scratch"
    source_commit = "1" * 40
    source_tree = "2" * 40
    calls: list[tuple[str, ...]] = []

    def fake_git_run(root: Path, *arguments: str) -> None:
        calls.append(tuple(arguments))
        if arguments[:3] == ("worktree", "add", "--detach"):
            Path(arguments[3]).mkdir(parents=True)

    def fake_git_output(root: Path, *arguments: str) -> str:
        calls.append(tuple(arguments))
        if arguments == ("rev-parse", "HEAD"):
            return source_commit
        if arguments == ("rev-parse", "HEAD^{tree}"):
            return source_tree
        if arguments == ("status", "--porcelain", "--untracked-files=all"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier, "_git_run", fake_git_run)
    monkeypatch.setattr(verifier, "_git_output", fake_git_output)

    with verifier._detached_source_snapshot(
        source,
        {"source_commit": source_commit, "source_tree": source_tree},
        scratch,
    ) as code_root:
        assert code_root == scratch / "code"

    assert calls.count(("rev-parse", "HEAD")) == 2
    assert calls.count(("rev-parse", "HEAD^{tree}")) == 2
    assert calls.count(("status", "--porcelain", "--untracked-files=all")) == 2
    assert any(call[:3] == ("worktree", "add", "--detach") for call in calls)
    assert any(call[:3] == ("worktree", "remove", "--force") for call in calls)


def _synthetic_verifier_amendment_binding(
    *,
    formal_source_authority_sha256: str = "a" * 64,
    formal_execution_commit: str = "1" * 40,
    formal_completion_sha256: str = "2" * 64,
    predecessor_verifier_git_blob_sha256: str = "b" * 64,
) -> dict:
    successor_git_blobs_sha256 = {
        path: (
            "d" * 64
            if path == verifier.VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
            else "e" * 64
        )
        for path in verifier.VERIFIER_AMENDMENT_SUCCESSOR_GIT_PATHS
    }
    return {
        "schema_version": (
            "formal-post-run-independent-verifier-amendment-binding/v1"
        ),
        "artifact_sha256": "c" * 64,
        "relative_path": (
            f"{verifier.VERIFIER_AMENDMENT_RELATIVE_ROOT.as_posix()}/"
            f"{'c' * 64}.json"
        ),
        "formal_source_authority_sha256": formal_source_authority_sha256,
        "formal_execution_commit": formal_execution_commit,
        "formal_completion_sha256": formal_completion_sha256,
        "predecessor_verifier_git_blob_sha256": (
            predecessor_verifier_git_blob_sha256
        ),
        "successor_source_commit": "3" * 40,
        "successor_source_tree": "4" * 40,
        "execution_commit": "5" * 40,
        "successor_verifier_git_blob_sha256": "d" * 64,
        "successor_git_blobs_sha256": successor_git_blobs_sha256,
        "json_document_size_policy": verifier.JSON_DOCUMENT_SIZE_POLICY,
        "json_document_size_policy_sha256": (
            verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
        ),
        "replay_plan_sha256": verifier.EXPECTED_REPLAY_PLAN_SHA256,
        "scope": verifier.VERIFIER_AMENDMENT_SCOPE,
    }


def test_phase3_amendment_fields_are_bound_into_formal_chain_fingerprint() -> None:
    inputs = {
        "completion_sha256": "1" * 64,
        "verifier_amendment": _synthetic_verifier_amendment_binding(),
    }
    baseline = verifier._formal_chain_fingerprint(inputs)

    for field, replacement in (
        ("artifact_sha256", "e" * 64),
        ("successor_verifier_git_blob_sha256", "f" * 64),
        ("json_document_size_policy_sha256", "0" * 64),
    ):
        drifted = deepcopy(inputs)
        drifted["verifier_amendment"][field] = replacement
        assert verifier._formal_chain_fingerprint(drifted) != baseline


def test_phase3_amendment_authority_json_is_frozen_as_binary() -> None:
    lines = (verifier.PROJECT_ROOT / ".gitattributes").read_text(
        encoding="utf-8"
    ).splitlines()

    assert lines.count(verifier.VERIFIER_AMENDMENT_GIT_ATTRIBUTES_RULE) == 1


def test_phase3_run_publishes_verification_then_receipt_then_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    python = source_root / ".venv" / "Scripts" / "python.exe"
    site_packages = source_root / ".venv" / "Lib" / "site-packages"
    python.parent.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    python.write_bytes(b"python")
    blocker = source_root / verifier.PYCACHE_BLOCKER_RELATIVE
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(
        (verifier.PROJECT_ROOT / verifier.PYCACHE_BLOCKER_RELATIVE).read_bytes()
    )
    writes: list[str] = []
    formal_loads = 0
    current_pool_probes = 0
    amendment = _synthetic_verifier_amendment_binding(
        formal_completion_sha256="1" * 64,
    )
    inputs = {
        "run_root": run_root,
        "completion_sha256": "1" * 64,
        "source_authority": {"source_commit": "2" * 40, "source_tree": "3" * 40},
        "result_bundle": {},
        "formal_runtime_history": {},
        "verifier_amendment": amendment,
        "preflight_core": {
            "frozen_input_attestation": {},
            "current_pool_audit_binding": {
                "canonical_sha256": "9" * 64,
                "allowed_symbol_count": 1,
            },
        },
    }

    @contextmanager
    def fake_snapshot(*args: object, **kwargs: object):
        code = tmp_path / "code"
        code.mkdir(exist_ok=True)
        copied_blocker = code / verifier.PYCACHE_BLOCKER_RELATIVE
        copied_blocker.parent.mkdir(parents=True, exist_ok=True)
        copied_blocker.write_bytes(blocker.read_bytes())
        yield code

    def stable_formal_loader(root: object) -> dict:
        nonlocal formal_loads
        formal_loads += 1
        return inputs

    def current_pool_probe(*args: object, **kwargs: object) -> object:
        nonlocal current_pool_probes
        current_pool_probes += 1
        return kwargs["expected_binding"]

    monkeypatch.setattr(verifier, "_load_formal_inputs", stable_formal_loader)
    monkeypatch.setattr(
        verifier,
        "_assert_project_interpreter",
        lambda root: python,
    )
    monkeypatch.setattr(verifier, "_detached_source_snapshot", fake_snapshot)
    monkeypatch.setattr(
        verifier,
        "_copy_frozen_inputs",
        lambda *args, **kwargs: {
            "input_count": 4,
            "root_sha256": "8" * 64,
        },
    )
    monkeypatch.setattr(
        verifier,
        "_verify_frozen_copy",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(verifier, "_probe_runtime", lambda *args, **kwargs: {"probe": True})
    monkeypatch.setattr(
        verifier,
        "_probe_current_pool_audit",
        current_pool_probe,
    )
    monkeypatch.setattr(
        verifier,
        "_run_isolated_replay",
        lambda *args, **kwargs: ({"schema_version": verifier.REPLAY_RESULT_SCHEMA}, {"snapshot_sha256": "4" * 64}, {"bytes": 0, "sha256": "5" * 64}, {"bytes": 0, "sha256": "6" * 64}),
    )
    monkeypatch.setattr(
        verifier,
        "_validate_replay",
        lambda *args, **kwargs: {"schema_version": verifier.RELOCATION_EQUIVALENCE_SCHEMA, "verified": True, "receipt_sha256": "7" * 64},
    )
    original = verifier._write_once

    def recording_write(path: Path, raw: bytes, label: str) -> None:
        writes.append(label)
        original(path, raw, label)

    monkeypatch.setattr(verifier, "_write_once", recording_write)

    result = verifier.run(str(source_root))

    assert result["status"] == "completed"
    assert writes.index("independent verification artifact") < writes.index(
        "independent verification receipt"
    ) < writes.index("independent verification status")
    assert writes[0] == "independent verification claim"
    assert formal_loads == 4
    assert current_pool_probes == 2
    claim = json.loads(
        (run_root / verifier.CLAIM_NAME).read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (
            run_root
            / verifier.RECEIPT_ROOT_NAME
            / f"{result['receipt_sha256']}.json"
        ).read_text(encoding="utf-8")
    )
    status = json.loads(
        (run_root / verifier.STATUS_NAME).read_text(encoding="utf-8")
    )
    for document in (claim, receipt, status):
        assert document["verifier_amendment_sha256"] == amendment[
            "artifact_sha256"
        ]
        assert document["successor_verifier_git_blob_sha256"] == amendment[
            "successor_verifier_git_blob_sha256"
        ]
        assert document["json_document_size_policy_sha256"] == amendment[
            "json_document_size_policy_sha256"
        ]


def test_phase3_formal_chain_recheck_rejects_midflight_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    initial = {
        "completion_sha256": "1" * 64,
        "file_sha256": {"completion": "1" * 64},
        "result_bundle": {"main_artifact_sha256": "2" * 64},
        "formal_runtime_history": {"immutable_inputs_unchanged": True},
        "source_authority": {"source_commit": "3" * 40},
    }
    drifted = deepcopy(initial)
    drifted["file_sha256"]["completion"] = "4" * 64
    monkeypatch.setattr(
        verifier,
        "_load_formal_inputs",
        lambda root: drifted,
    )

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="changed during replay",
    ):
        verifier._assert_formal_chain_unchanged(
            tmp_path,
            verifier._formal_chain_fingerprint(initial),
        )


def test_phase3_second_run_is_rejected_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / verifier.CLAIM_NAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        verifier,
        "_load_formal_inputs",
        lambda root: {"run_root": run_root},
    )
    called = False

    def forbidden(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("replay must not start")

    monkeypatch.setattr(verifier, "_run_isolated_replay", forbidden)

    with pytest.raises(FileExistsError):
        verifier.run(str(tmp_path))
    assert called is False


def test_phase3_failed_status_contains_error_type_but_not_log_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    python = source_root / ".venv" / "Scripts" / "python.exe"
    site_packages = source_root / ".venv" / "Lib" / "site-packages"
    python.parent.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    python.write_bytes(b"python")
    blocker = source_root / verifier.PYCACHE_BLOCKER_RELATIVE
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(
        (verifier.PROJECT_ROOT / verifier.PYCACHE_BLOCKER_RELATIVE).read_bytes()
    )
    inputs = {
        "run_root": run_root,
        "completion_sha256": "1" * 64,
        "source_authority": {"source_commit": "2" * 40, "source_tree": "3" * 40},
        "result_bundle": {},
        "formal_runtime_history": {},
        "verifier_amendment": _synthetic_verifier_amendment_binding(
            formal_completion_sha256="1" * 64,
        ),
        "preflight_core": {
            "current_pool_audit_binding": {
                "canonical_sha256": "9" * 64,
                "allowed_symbol_count": 1,
            },
        },
    }
    secret_log_text = "raw-child-output-must-not-publish"

    monkeypatch.setattr(verifier, "_load_formal_inputs", lambda root: inputs)
    monkeypatch.setattr(
        verifier,
        "_assert_project_interpreter",
        lambda root: python,
    )
    monkeypatch.setattr(verifier, "_probe_runtime", lambda *args, **kwargs: {"probe": True})
    monkeypatch.setattr(
        verifier,
        "_probe_current_pool_audit",
        lambda *args, **kwargs: kwargs["expected_binding"],
    )
    monkeypatch.setattr(
        verifier,
        "_detached_source_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(secret_log_text)),
    )

    with pytest.raises(RuntimeError, match=secret_log_text):
        verifier.run(str(source_root))

    status = json.loads((run_root / verifier.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["error_type"] == "RuntimeError"
    assert secret_log_text not in json.dumps(status)
    assert status["verifier_amendment_sha256"] == "c" * 64
    assert status["successor_verifier_git_blob_sha256"] == "d" * 64
    assert (
        status["json_document_size_policy_sha256"]
        == verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
    )


def _write_launcher_json(path: Path, value: dict) -> str:
    raw = _canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _runtime_attestation(files_sha256: str = "a" * 64) -> dict:
    body = {
        "schema_version": "formal-python-runtime-attestation/v1",
        "distributions": [
            {
                "name": "xgboost",
                "files_sha256": files_sha256,
            }
        ],
        "environment": {"inherit_parent_environment": False},
    }
    return {**body, "root_sha256": _sha256(body)}


def _post_run_amendment_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, dict, str, dict]:
    source = tmp_path / "source"
    source.mkdir()
    verifier_path = source / "scripts/verify_shallow_gbdt_risk_on_breadth_development_1.py"
    independent_test_path = (
        source
        / "tests/test_shallow_gbdt_risk_on_breadth_independent_verifier.py"
    )
    launcher_test_path = (
        source / "tests/test_shallow_gbdt_risk_on_breadth_formal_launcher.py"
    )
    attributes_path = source / ".gitattributes"
    verifier_path.parent.mkdir(parents=True)
    independent_test_path.parent.mkdir(parents=True)
    successor_bytes = b"successor verifier\n"
    independent_test_bytes = b"successor independent tests\n"
    launcher_test_bytes = b"successor launcher tests\n"
    verifier_path.write_bytes(successor_bytes)
    independent_test_path.write_bytes(independent_test_bytes)
    launcher_test_path.write_bytes(launcher_test_bytes)
    attributes_bytes = (
        verifier.VERIFIER_AMENDMENT_GIT_ATTRIBUTES_RULE + "\n"
    ).encode("utf-8")
    attributes_path.write_bytes(attributes_bytes)
    formal_execution = "1" * 40
    successor_source = "2" * 40
    successor_tree = "3" * 40
    amendment_execution = "4" * 40
    formal_source_authority_sha256 = "5" * 64
    formal_completion_sha256 = "6" * 64
    predecessor_verifier_sha256 = "7" * 64
    successor_git_blob_bytes = {
        ".gitattributes": attributes_bytes,
        (
            "scripts/verify_shallow_gbdt_"
            "risk_on_breadth_development_1.py"
        ): successor_bytes,
        (
            "tests/test_shallow_gbdt_risk_on_breadth_"
            "independent_verifier.py"
        ): independent_test_bytes,
        (
            "tests/test_shallow_gbdt_risk_on_breadth_"
            "formal_launcher.py"
        ): launcher_test_bytes,
    }
    successor_git_blobs_sha256 = {
        path: hashlib.sha256(raw).hexdigest()
        for path, raw in successor_git_blob_bytes.items()
    }
    relative_root = verifier.VERIFIER_AMENDMENT_RELATIVE_ROOT
    authority_root = source / relative_root
    authority_root.mkdir(parents=True)
    body = {
        "schema_version": verifier.VERIFIER_AMENDMENT_SCHEMA,
        "formal_source_authority_sha256": formal_source_authority_sha256,
        "formal_execution_commit": formal_execution,
        "formal_completion_sha256": formal_completion_sha256,
        "predecessor_verifier_git_blob_sha256": predecessor_verifier_sha256,
        "successor_source_commit": successor_source,
        "successor_source_tree": successor_tree,
        "successor_verifier_git_path": (
            "scripts/verify_shallow_gbdt_risk_on_breadth_development_1.py"
        ),
        "successor_verifier_git_blob_sha256": hashlib.sha256(
            successor_bytes
        ).hexdigest(),
        "successor_git_blobs_sha256": successor_git_blobs_sha256,
        "json_document_size_policy_sha256": (
            verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
        ),
        "json_document_size_policy": verifier.JSON_DOCUMENT_SIZE_POLICY,
        "replay_plan_sha256": verifier.EXPECTED_REPLAY_PLAN_SHA256,
        "scope": verifier.VERIFIER_AMENDMENT_SCOPE,
        "execution_topology": verifier.VERIFIER_AMENDMENT_TOPOLOGY,
    }
    artifact_sha256 = _sha256(body)
    document = {**body, "artifact_sha256": artifact_sha256}
    authority_path = authority_root / f"{artifact_sha256}.json"
    authority_raw = _canonical_bytes(document) + b"\n"
    authority_path.write_bytes(authority_raw)
    authority_relative = authority_path.relative_to(source).as_posix()

    def fake_git_output(root: Path, *arguments: str) -> str:
        if arguments == ("status", "--porcelain", "--untracked-files=all"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return amendment_execution
        if arguments == (
            "rev-list",
            "--parents",
            "-n",
            "1",
            amendment_execution,
        ):
            return f"{amendment_execution} {successor_source}"
        if arguments == (
            "rev-list",
            "--parents",
            "-n",
            "1",
            successor_source,
        ):
            return f"{successor_source} {formal_execution}"
        if arguments == ("rev-parse", f"{successor_source}^{{tree}}"):
            return successor_tree
        if arguments == (
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            formal_execution,
            successor_source,
        ):
            return "\n".join(
                (
                    "M\t.gitattributes",
                    "M\tscripts/verify_shallow_gbdt_risk_on_breadth_development_1.py",
                    "M\ttests/test_shallow_gbdt_risk_on_breadth_independent_verifier.py",
                    "M\ttests/test_shallow_gbdt_risk_on_breadth_formal_launcher.py",
                )
            )
        if arguments == (
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            successor_source,
            amendment_execution,
        ):
            return f"A\t{authority_relative}"
        raise AssertionError(arguments)

    def fake_git_bytes(root: Path, *arguments: str) -> bytes:
        if len(arguments) == 2 and arguments[0] == "show":
            prefix = f"{successor_source}:"
            if arguments[1].startswith(prefix):
                return successor_git_blob_bytes[arguments[1].removeprefix(prefix)]
        if arguments == ("show", f"{amendment_execution}:{authority_relative}"):
            return authority_raw
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier, "SCRIPT_PATH", verifier_path)
    monkeypatch.setattr(verifier, "_git_output", fake_git_output)
    monkeypatch.setattr(verifier, "_git_bytes", fake_git_bytes)
    formal_authority = {
        "artifact_sha256": formal_source_authority_sha256,
        "execution_commit": formal_execution,
        "verifier_git_blob_sha256": predecessor_verifier_sha256,
    }
    return source, formal_authority, formal_completion_sha256, document


def test_phase3_post_run_verifier_amendment_binds_successor_and_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, formal_authority, completion_sha256, document = (
        _post_run_amendment_fixture(monkeypatch, tmp_path)
    )

    observed = verifier._verified_post_run_verifier_amendment(
        source,
        formal_source_authority=formal_authority,
        formal_completion_sha256=completion_sha256,
    )

    assert observed["artifact_sha256"] == document["artifact_sha256"]
    assert (
        observed["successor_verifier_git_blob_sha256"]
        == document["successor_verifier_git_blob_sha256"]
    )
    assert (
        observed["successor_git_blobs_sha256"]
        == document["successor_git_blobs_sha256"]
    )
    assert (
        observed["json_document_size_policy_sha256"]
        == verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
    )


def test_phase3_post_run_verifier_amendment_rejects_other_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, formal_authority, _completion_sha256, _document = (
        _post_run_amendment_fixture(monkeypatch, tmp_path)
    )

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="amendment authority",
    ):
        verifier._verified_post_run_verifier_amendment(
            source,
            formal_source_authority=formal_authority,
            formal_completion_sha256="8" * 64,
        )


def test_phase3_post_run_verifier_amendment_rejects_successor_test_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, formal_authority, completion_sha256, _document = (
        _post_run_amendment_fixture(monkeypatch, tmp_path)
    )
    (
        source / "tests/test_shallow_gbdt_risk_on_breadth_formal_launcher.py"
    ).write_bytes(b"drifted successor tests\n")

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="amendment authority",
    ):
        verifier._verified_post_run_verifier_amendment(
            source,
            formal_source_authority=formal_authority,
            formal_completion_sha256=completion_sha256,
        )


def _formal_chain_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, dict]:
    source = tmp_path / "source"
    run_root = source / "run"
    run_root.mkdir(parents=True)
    monkeypatch.setattr(verifier, "RUN_ROOT_RELATIVE", Path("run"))
    monkeypatch.setattr(verifier, "ATTEMPT_RELATIVE", Path("attempt.json"))
    monkeypatch.setattr(verifier, "TERMINAL_RELATIVE", Path("terminal.json"))
    run_spec = {"schema_version": "synthetic-run-spec/v1"}
    run_spec_sha256 = _sha256(run_spec)
    monkeypatch.setattr(verifier, "EXPECTED_RUN_SPEC_SHA256", run_spec_sha256)
    authority = {
        "schema_version": "formal-source-authority-binding/v1",
        "artifact_sha256": "1" * 64,
        "relative_path": "authority.json",
        "source_commit": "2" * 40,
        "source_tree": "3" * 40,
        "execution_commit": "4" * 40,
        "launcher_git_blob_sha256": "5" * 64,
        "verifier_git_blob_sha256": "6" * 64,
        "run_spec_sha256": run_spec_sha256,
        "replay_plan_sha256": verifier.EXPECTED_REPLAY_PLAN_SHA256,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
            "automatic_trading_authority": False,
        },
    }
    replay_inputs = verifier.REPLAY_PLAN["inputs"]
    frozen_paths = (
        source / replay_inputs["audited_pit_universe_path"],
        source / replay_inputs["temporal_contract_path"],
        source / replay_inputs["current_pool_development_audit_path"],
        source
        / replay_inputs["security_code_transition_evidence_root"]
        / "contract.json",
    )
    for index, path in enumerate(frozen_paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"frozen-{index}".encode())
    frozen_attestation = verifier._observed_frozen_attestation(source)
    launcher_path = (
        source / "scripts/run_shallow_gbdt_risk_on_breadth_development_1.py"
    )
    launcher_path.parent.mkdir(parents=True)
    launcher_path.write_bytes(b"launcher")
    python = source / ".venv/Scripts/python.exe"
    site_packages = source / ".venv/Lib/site-packages"
    python.parent.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    python.write_bytes(b"python")
    blocker = source / verifier.PYCACHE_BLOCKER_RELATIVE
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_bytes(
        (verifier.PROJECT_ROOT / verifier.PYCACHE_BLOCKER_RELATIVE).read_bytes()
    )
    runtime = _runtime_attestation()
    core = {
        "git_commit": authority["execution_commit"],
        "source_authority": authority,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "producer_binding": _producer_binding(),
        "current_pool_audit_binding": {
            "canonical_sha256": verifier.REPLAY_PLAN["inputs"][
                "expected_current_pool_development_audit_sha256"
            ],
            "allowed_symbol_count": 1,
        },
        "formal_launcher_sha256": hashlib.sha256(b"launcher").hexdigest(),
        "formal_launcher_git_blob_sha256": authority[
            "launcher_git_blob_sha256"
        ],
        "independent_verifier_git_blob_sha256": authority[
            "verifier_git_blob_sha256"
        ],
        "replay_plan_sha256": verifier.EXPECTED_REPLAY_PLAN_SHA256,
        "run_spec_sha256": run_spec_sha256,
        "frozen_input_attestation": frozen_attestation,
        "runtime_attestation": runtime,
    }
    claim = {
        "schema_version": "formal-single-attempt-claim/v1",
        "started_at_utc": "2026-08-08T00:00:00+00:00",
        "run_spec_sha256": run_spec_sha256,
        "execution_commit": authority["execution_commit"],
        "source_commit": authority["source_commit"],
        "source_authority_sha256": authority["artifact_sha256"],
        "output_dir": "run",
        "preflight_sha256": _sha256(core),
        "max_formal_attempts": 1,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    }
    claim_sha256 = _write_launcher_json(source / "attempt.json", claim)
    preflight = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-risk-on-breadth-preflight/v1"
        ),
        "observed_at_utc": "2026-08-08T00:00:00+00:00",
        **core,
        "run_spec": run_spec,
        "attempt_claim_sha256": claim_sha256,
        "statistical_result_available": False,
    }
    _write_launcher_json(run_root / verifier.PREFLIGHT_NAME, preflight)
    launch_arguments = verifier._jobs_argv("run")
    formal_command = verifier._formal_command_tokens(source, python)
    formal_command_sha256 = _sha256({"tokens": formal_command})
    launch = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-risk-on-breadth-launch/v1"
        ),
        "started_at_utc": "2026-08-08T00:00:00+00:00",
        "preflight_sha256": _sha256(core),
        "executable": str(python.resolve()),
        "arguments": launch_arguments,
        "command_sha256": formal_command_sha256,
        "execution_contract": {
            "interpreter_flags": ["-S", "-B", "-P"],
            "entrypoint": "runpy.run_module-app.jobs",
            "jobs_argument_prefix": ["-m", "app.jobs"],
            "sys_path": ["workspace", "venv-site-packages"],
            "pycache_prefix_from_command_line": True,
        },
        "runtime_environment_attestation_sha256": runtime["root_sha256"],
        "memory_policy": "unbounded",
        "process_tree_policy": "windows_job_object_assigned_and_drained",
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    }
    launch_sha256 = _write_launcher_json(run_root / verifier.LAUNCH_NAME, launch)
    stdout_path = run_root / verifier.FORMAL_STDOUT_NAME
    stderr_path = run_root / verifier.FORMAL_STDERR_NAME
    stdout_path.write_bytes(b"stdout")
    stderr_path.write_bytes(b"")
    resource = {
        "schema_version": "research-unbounded-job-object-command-receipt/v1",
        "pid": 1,
        "pid_role": "gated_bootstrap_supervisor",
        "started_at_utc": "2026-08-08T00:00:00+00:00",
        "finished_at_utc": "2026-08-08T00:00:01+00:00",
        "exit_code": 0,
        "command_sha256": launch["command_sha256"],
        "argument_count": len(formal_command),
        "cwd": str(source.resolve()),
        "shell": False,
        "stdin_closed": True,
        "memory_limit_enforced": False,
        "child_reaped": True,
        "job_object_assigned": True,
        "process_tree_drained": True,
        "process_tree_drain_verification": (
            "windows_job_object_active_process_count_zero"
        ),
        "stdout": {
            "path": str(stdout_path.resolve()),
            "bytes": stdout_path.stat().st_size,
            "sha256": hashlib.sha256(stdout_path.read_bytes()).hexdigest(),
        },
        "stderr": {
            "path": str(stderr_path.resolve()),
            "bytes": stderr_path.stat().st_size,
            "sha256": hashlib.sha256(stderr_path.read_bytes()).hexdigest(),
        },
    }
    resource_sha256 = _write_launcher_json(
        run_root / verifier.RESOURCE_NAME,
        resource,
    )
    main_sha256 = "9" * 64
    main_path = run_root / f"{main_sha256}.json"
    main_path.write_bytes(b"main")
    runtime_artifact_sha256 = "a" * 64
    runtime_path = run_root / "verifications" / f"{runtime_artifact_sha256}.json"
    runtime_path.parent.mkdir()
    runtime_path.write_text(
        json.dumps({"artifact_sha256": runtime_artifact_sha256}),
        encoding="utf-8",
    )
    sidecars = {
        "features": "b" * 64,
        "models": "c" * 64,
        "execution": "d" * 64,
        "selection": "e" * 64,
    }
    feature_receipt = "f" * 64
    completion = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-risk-on-breadth-completion/v1"
        ),
        "started_at_utc": "2026-08-08T00:00:00+00:00",
        "finished_at_utc": "2026-08-08T00:00:01+00:00",
        "classification": "completed_result_pending_independent_verification",
        "preflight": core,
        "post_run_preflight": core,
        "immutable_inputs_unchanged": True,
        "launch_receipt_sha256": launch_sha256,
        "attempt_claim_sha256": claim_sha256,
        "resource_receipt": {
            "path": verifier.RESOURCE_NAME,
            "sha256": resource_sha256,
            "exit_code": 0,
            "memory_limit_enforced": False,
            "job_object_assigned": True,
            "process_tree_drained": True,
        },
        "launcher_error_type": None,
        "progress": {
            "schema_version": (
                "ranked-liquidity-shallow-gbdt-risk-on-breadth-replay-progress/v1"
            ),
            "stage": "completed",
            "artifact_sha256": main_sha256,
        },
        "result_artifact": {
            "path": main_path.name,
            "file_sha256": hashlib.sha256(main_path.read_bytes()).hexdigest(),
            "canonical_artifact_sha256": main_sha256,
            "embedded_artifact_sha256": main_sha256,
            "sidecar_artifact_sha256": sidecars,
        },
        "runtime_verification": {
            "path": runtime_path.name,
            "file_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
            "canonical_artifact_sha256": runtime_artifact_sha256,
            "embedded_artifact_sha256": runtime_artifact_sha256,
            "schema_version": verifier.EXPECTED_VERIFICATION_SCHEMA,
            "main_artifact_sha256": main_sha256,
            "market_breadth_feature_binding_receipt_sha256": feature_receipt,
        },
        "result_available": True,
        "artifact_content_addressed": True,
        "runtime_verification_content_addressed": True,
        "independent_verification_complete": False,
        "statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }
    completion_path = run_root / verifier.COMPLETION_NAME
    completion_sha256 = _write_launcher_json(completion_path, completion)
    terminal = {
        "schema_version": "formal-single-attempt-terminal/v1",
        "status": "completed",
        "finished_at_utc": "2026-08-08T00:00:01+00:00",
        "attempt_claim_sha256": claim_sha256,
        "completion_sha256": completion_sha256,
        "failure_sha256": None,
        "error_type": None,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "production_authority": False,
    }
    _write_launcher_json(source / "terminal.json", terminal)
    bundle = {
        "main_artifact_path": main_path,
        "main_artifact_sha256": main_sha256,
        "main_document": {},
        "sidecar_artifact_sha256": sidecars,
        "sidecar_documents": {},
        "runtime_verification": {"artifact_sha256": runtime_artifact_sha256},
        "market_breadth_filter_receipt_sha256": "0" * 64,
        "market_breadth_feature_binding_receipt_sha256": feature_receipt,
    }
    monkeypatch.setattr(
        verifier,
        "_verified_source_authority",
        lambda root, expected: dict(expected),
    )

    def synthetic_amendment(
        root: Path,
        *,
        formal_source_authority: dict,
        formal_completion_sha256: str,
    ) -> dict:
        return _synthetic_verifier_amendment_binding(
            formal_source_authority_sha256=formal_source_authority[
                "artifact_sha256"
            ],
            formal_execution_commit=formal_source_authority[
                "execution_commit"
            ],
            formal_completion_sha256=formal_completion_sha256,
            predecessor_verifier_git_blob_sha256=formal_source_authority[
                "verifier_git_blob_sha256"
            ],
        )

    monkeypatch.setattr(
        verifier,
        "_verified_post_run_verifier_amendment",
        synthetic_amendment,
    )
    monkeypatch.setattr(
        verifier,
        "_load_content_addressed_result_bundle",
        lambda *args, **kwargs: bundle,
    )
    return source, completion_path, completion


def test_phase3_loads_exact_successful_formal_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, _completion_path, _completion = _formal_chain_fixture(
        monkeypatch,
        tmp_path,
    )

    observed = verifier._load_formal_inputs(source)

    assert observed["terminal"]["status"] == "completed"
    assert observed["completion"]["result_available"] is True
    assert observed["formal_runtime_history"] == {
        "schema_version": verifier.FORMAL_RUNTIME_HISTORY_SCHEMA,
        "preflight_xgboost_distribution_files_sha256": "a" * 64,
        "postflight_xgboost_distribution_files_sha256": "a" * 64,
        "immutable_inputs_unchanged": True,
    }


def test_phase3_formal_chain_rejects_terminal_claim_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, _completion_path, _completion = _formal_chain_fixture(
        monkeypatch,
        tmp_path,
    )
    terminal_path = source / "terminal.json"
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["attempt_claim_sha256"] = "0" * 64
    _write_launcher_json(terminal_path, terminal)

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._load_formal_inputs(source)


@pytest.mark.parametrize("mutation", ["executable", "command", "scope"])
def test_phase3_formal_chain_recomputes_full_launch_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    source, completion_path, completion = _formal_chain_fixture(
        monkeypatch,
        tmp_path,
    )
    run_root = source / verifier.RUN_ROOT_RELATIVE
    launch_path = run_root / verifier.LAUNCH_NAME
    resource_path = run_root / verifier.RESOURCE_NAME
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    resource = json.loads(resource_path.read_text(encoding="utf-8"))
    if mutation == "executable":
        launch["executable"] = str(source / "other-python.exe")
    elif mutation == "command":
        launch["command_sha256"] = "7" * 64
        resource["command_sha256"] = launch["command_sha256"]
    else:
        launch["production_authority"] = True
    launch_sha256 = _write_launcher_json(launch_path, launch)
    resource_sha256 = _write_launcher_json(resource_path, resource)
    completion["launch_receipt_sha256"] = launch_sha256
    completion["resource_receipt"]["sha256"] = resource_sha256
    completion_sha256 = _write_launcher_json(completion_path, completion)
    terminal_path = source / verifier.TERMINAL_RELATIVE
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["completion_sha256"] = completion_sha256
    _write_launcher_json(terminal_path, terminal)

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._load_formal_inputs(source)


def test_phase3_formal_runtime_history_rejects_xgboost_drift() -> None:
    preflight = {"runtime_attestation": _runtime_attestation("1" * 64)}
    postflight = {"runtime_attestation": _runtime_attestation("2" * 64)}

    with pytest.raises(ValueError, match="formal runtime history"):
        verifier._formal_runtime_history(preflight, postflight)


def test_phase3_frozen_copy_detects_post_copy_snapshot_drift(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    relative = verifier.REPLAY_PLAN["inputs"]
    paths = (
        source / relative["audited_pit_universe_path"],
        source / relative["temporal_contract_path"],
        source / relative["current_pool_development_audit_path"],
        source / relative["security_code_transition_evidence_root"] / "contract.json",
    )
    for index, path in enumerate(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"value-{index}".encode())
    attestation = verifier._observed_frozen_attestation(source)
    binding = verifier._copy_frozen_inputs(
        source,
        target,
        expected_formal_attestation=attestation,
    )
    (target / relative["temporal_contract_path"]).write_bytes(b"drift")

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._verify_frozen_copy(
            source,
            target,
            binding,
            expected_formal_attestation=attestation,
        )


def test_phase3_replay_starts_once_and_rejects_post_probe_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    code = tmp_path / "code"
    data = tmp_path / "data"
    site_packages = tmp_path / "site-packages"
    scratch = tmp_path / "scratch"
    for path in (source, code, data, site_packages, scratch):
        path.mkdir()
    calls: list[dict] = []

    class Process:
        def wait(self) -> int:
            return 0

    def fake_popen(*args: object, **kwargs: object) -> Process:
        calls.append(dict(kwargs))
        return Process()

    monkeypatch.setattr(verifier.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        verifier,
        "_verify_frozen_copy",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        verifier,
        "_probe_runtime",
        lambda *args, **kwargs: {"probe": "after-drift"},
    )
    monkeypatch.setattr(
        verifier,
        "_probe_current_pool_audit",
        lambda *args, **kwargs: kwargs["expected_binding"],
    )

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="runtime changed",
    ):
        verifier._run_isolated_replay(
            inputs={
                "formal_runtime_history": {},
                "preflight_core": {
                    "frozen_input_attestation": {},
                    "current_pool_audit_binding": {
                        "canonical_sha256": "9" * 64,
                        "allowed_symbol_count": 1,
                    },
                },
            },
            source_root=source,
            code_root=code,
            data_root=data,
            python_executable=tmp_path / "python.exe",
            site_packages=site_packages,
            environment={},
            pycache_blocker=tmp_path / "blocker",
            formal_probe={"probe": "formal"},
            replay_probe={"probe": "before"},
            frozen_copy={},
            scratch_root=scratch,
        )

    assert len(calls) == 1
    assert calls[0]["shell"] is False
