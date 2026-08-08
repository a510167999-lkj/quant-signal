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


def _owned_bytes(ownership: dict) -> bytes:
    handle = ownership["handle"]
    handle.seek(0)
    return handle.read()


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
    system_root = tmp_path / "Windows"
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    blocker = tmp_path / "pycache-blocker"
    system_root.mkdir()
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    blocker.write_bytes(b"blocker")
    monkeypatch.setenv("SystemRoot", str(system_root))

    observed = verifier._minimal_child_environment(
        tmp_path / "runtime-tmp",
        python_executable=python,
        pycache_blocker=blocker,
    )

    assert observed["DISABLE_ENV_FILE"] == "1"
    assert observed["PYTHONHASHSEED"] == "0"
    assert observed["PYTHONNOUSERSITE"] == "1"
    assert observed["PYTHONDONTWRITEBYTECODE"] == "1"
    assert observed["PYTHONUTF8"] == "1"
    assert observed["VPS_RUNTIME_ROLE"] == "local_research"
    assert observed["TEMP"] == str((tmp_path / "runtime-tmp").resolve())
    assert observed["TMP"] == str((tmp_path / "runtime-tmp").resolve())
    assert observed["PYTHONPYCACHEPREFIX"] == str(blocker.resolve())
    assert observed["PATH"].split(verifier.os.pathsep) == [
        str(python.parent.resolve()),
        str(system_root / "System32"),
        str(system_root),
    ]
    assert "FORMAL_TEST_SECRET" not in observed
    assert observed["PATH"] != "must-not-cross"


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
        env=verifier._minimal_child_environment(
            tmp_path / "runtime-tmp",
            python_executable=python,
            pycache_blocker=blocker,
        ),
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
        environment=verifier._minimal_child_environment(
            tmp_path / "runtime-tmp",
            python_executable=python,
            pycache_blocker=blocker,
        ),
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


def test_retry1_write_once_rejects_parent_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "publication" / "claim.json"
    target.parent.mkdir()
    original = verifier._assert_no_reparse
    parent_calls = 0

    def drift_parent(path: Path, label: str):
        nonlocal parent_calls
        observed = original(path, label)
        if path == target.parent:
            parent_calls += 1
            if parent_calls == 2:
                values = {
                    name: getattr(observed, name)
                    for name in (
                        "st_mode",
                        "st_size",
                        "st_mtime_ns",
                        "st_dev",
                        "st_ino",
                    )
                }
                values["st_ino"] += 1
                return SimpleNamespace(**values)
        return observed

    monkeypatch.setattr(verifier, "_assert_no_reparse", drift_parent)

    with pytest.raises(ValueError, match="parent.*changed"):
        verifier._write_once(target, b"claim\n", "claim")


def test_retry1_write_once_rejects_linked_target_content_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "publication" / "receipt.json"
    expected = b'{"receipt":true}\n'
    original_link = verifier.os.link

    def corrupt_after_link(source: object, destination: object) -> None:
        original_link(source, destination)
        Path(destination).write_bytes(b"foreign\n")

    monkeypatch.setattr(verifier.os, "link", corrupt_after_link)

    with pytest.raises(PermissionError):
        verifier._write_once(target, expected, "receipt publication")

    assert not target.exists()


def test_retry1_claim_ownership_rejects_replace_restore_before_status(
    tmp_path: Path,
) -> None:
    claim_path = tmp_path / "claim.json"
    status_path = tmp_path / "status.json"
    claim_raw = b'{"claim":true}\n'
    verifier._write_once(claim_path, claim_raw, "claim")
    claim_ownership = verifier._open_claim_ownership(claim_path, claim_raw)
    status_ownership = verifier._reserve_status_slot(status_path)
    try:
        with pytest.raises(PermissionError):
            claim_path.write_bytes(b"foreign claim")

        assert _owned_bytes(status_ownership) == b""
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_persisted_streams_reject_dangling_reparse(
    tmp_path: Path,
) -> None:
    stdout = tmp_path / verifier.RETRY_REPLAY_STDOUT_NAME
    try:
        stdout.symlink_to(tmp_path / "missing.log")
    except OSError:
        pytest.skip("test host cannot create a symlink")

    with pytest.raises(ValueError, match="reparse"):
        verifier._persisted_retry_replay_streams(tmp_path)


def test_retry1_success_stream_descriptors_must_match_recomputed_files(
    tmp_path: Path,
) -> None:
    stdout = tmp_path / verifier.RETRY_REPLAY_STDOUT_NAME
    stderr = tmp_path / verifier.RETRY_REPLAY_STDERR_NAME
    stdout.write_bytes(b"stdout")
    stderr.write_bytes(b"stderr")
    expected = verifier._persisted_retry_replay_streams(tmp_path)
    expected["stdout"] = {**expected["stdout"], "sha256": "0" * 64}

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="stream descriptors",
    ):
        verifier._verified_retry_replay_streams(tmp_path, expected)


def test_retry1_replay_stream_ownership_blocks_writes_and_keeps_descriptors(
    tmp_path: Path,
) -> None:
    stdout = tmp_path / verifier.RETRY_REPLAY_STDOUT_NAME
    stderr = tmp_path / verifier.RETRY_REPLAY_STDERR_NAME
    stdout_raw = b"synthetic stdout"
    stderr_raw = b"synthetic stderr"
    stdout.write_bytes(stdout_raw)
    stderr.write_bytes(stderr_raw)

    streams, ownerships = verifier._owned_retry_replay_streams(tmp_path)
    try:
        for _ in range(3):
            with pytest.raises(PermissionError):
                stdout.write_bytes(b"sustained foreign write")
        assert verifier._verify_retry_replay_stream_ownerships(
            ownerships,
            streams,
        ) == streams
        assert streams["stdout"] == {
            "path": verifier.RETRY_REPLAY_STDOUT_NAME,
            "bytes": len(stdout_raw),
            "sha256": hashlib.sha256(stdout_raw).hexdigest(),
        }
        assert streams["stderr"] == {
            "path": verifier.RETRY_REPLAY_STDERR_NAME,
            "bytes": len(stderr_raw),
            "sha256": hashlib.sha256(stderr_raw).hexdigest(),
        }
    finally:
        verifier._close_retry_replay_stream_ownerships(ownerships)


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


def _small_complete_frozen_inputs(source: Path) -> dict[str, Path]:
    relative = verifier.REPLAY_PLAN["inputs"]
    metadata = source / relative["audited_pit_universe_path"]
    bundle = metadata.parent
    manifest = bundle / "manifest.json"
    raw = bundle / "raw"
    temporal = source / relative["temporal_contract_path"]
    audit = source / relative["current_pool_development_audit_path"]
    transition = (
        source
        / relative["security_code_transition_evidence_root"]
        / "contract.json"
    )
    for path, value in (
        (metadata, b"sqlite"),
        (manifest, b'{"schema_version":"synthetic-pit-bundle/v1"}\n'),
        (raw / "daily" / "20260703.json", b"daily"),
        (raw / "bak_basic" / "20260703.json", b"membership"),
        (temporal, b"temporal"),
        (audit, b"audit"),
        (transition, b"transition"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return {
        "metadata": metadata,
        "bundle": bundle,
        "manifest": manifest,
        "raw": raw,
        "temporal": temporal,
        "audit": audit,
        "transition": transition,
    }


def test_retry1_copies_complete_pit_bundle_and_preserves_metadata_relative_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    paths = _small_complete_frozen_inputs(source)
    relative_metadata = Path(
        verifier.REPLAY_PLAN["inputs"]["audited_pit_universe_path"]
    )
    bundle_manifest = verifier._universe_bundle_manifest(source)

    binding = verifier._copy_frozen_inputs(
        source,
        target,
        expected_formal_attestation=verifier._observed_frozen_attestation(
            source
        ),
        expected_universe_bundle_manifest_sha256=bundle_manifest[
            "manifest_sha256"
        ],
    )

    copied_metadata = target / relative_metadata
    assert copied_metadata.read_bytes() == paths["metadata"].read_bytes()
    assert copied_metadata.parent == target / paths["bundle"].relative_to(source)
    assert (copied_metadata.parent / "manifest.json").read_bytes() == paths[
        "manifest"
    ].read_bytes()
    assert verifier._manifest_path(copied_metadata.parent / "raw", "raw") == (
        verifier._manifest_path(paths["raw"], "raw")
    )
    assert binding["schema_version"].endswith("frozen-input-copy/v2")
    assert binding["input_count"] == 4
    assert binding["universe_bundle_manifest_sha256"] == bundle_manifest[
        "manifest_sha256"
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_manifest", "bundle"),
        ("missing_raw", "bundle"),
        ("raw_extra", "manifest"),
        ("raw_missing", "manifest"),
    ],
)
def test_retry1_bundle_copy_rejects_missing_or_drifted_raw_tree(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    paths = _small_complete_frozen_inputs(source)
    expected = verifier._universe_bundle_manifest(source)["manifest_sha256"]
    if mutation == "missing_manifest":
        paths["manifest"].unlink()
    elif mutation == "missing_raw":
        for candidate in sorted(paths["raw"].rglob("*"), reverse=True):
            candidate.unlink() if candidate.is_file() else candidate.rmdir()
        paths["raw"].rmdir()
    elif mutation == "raw_extra":
        (paths["raw"] / "extra.json").write_bytes(b"extra")
    else:
        (paths["raw"] / "daily" / "20260703.json").unlink()

    with pytest.raises((ValueError, FileNotFoundError), match=message):
        verifier._copy_frozen_inputs(
            source,
            target,
            expected_formal_attestation=verifier._observed_frozen_attestation(
                source
            ),
            expected_universe_bundle_manifest_sha256=expected,
        )


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_retry1_bundle_copy_rejects_sqlite_sidecars(
    tmp_path: Path,
    suffix: str,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    paths = _small_complete_frozen_inputs(source)
    expected = verifier._universe_bundle_manifest(source)["manifest_sha256"]
    Path(f"{paths['metadata']}{suffix}").write_bytes(b"mutable")

    with pytest.raises(ValueError, match="SQLite"):
        verifier._copy_frozen_inputs(
            source,
            target,
            expected_formal_attestation=verifier._observed_frozen_attestation(
                source
            ),
            expected_universe_bundle_manifest_sha256=expected,
        )


def test_retry1_bundle_manifest_rejects_reparse_entry(tmp_path: Path) -> None:
    source = tmp_path / "source"
    paths = _small_complete_frozen_inputs(source)
    external = tmp_path / "external.json"
    external.write_bytes(b"external")
    link = paths["raw"] / "linked.json"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("test host cannot create a symlink")

    with pytest.raises(ValueError, match="reparse"):
        verifier._universe_bundle_manifest(source)


def test_retry1_bundle_copy_rejects_midflight_source_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    paths = _small_complete_frozen_inputs(source)
    expected = verifier._universe_bundle_manifest(source)["manifest_sha256"]
    original_copy = verifier._copy_regular_file_verified
    replaced = False

    def replace_then_copy(
        source_path: Path,
        target_path: Path,
        expected_entry: dict,
        label: str,
    ) -> None:
        nonlocal replaced
        if not replaced and source_path == paths["metadata"]:
            replaced = True
            source_path.write_bytes(b"forged")
        original_copy(source_path, target_path, expected_entry, label)

    monkeypatch.setattr(
        verifier,
        "_copy_regular_file_verified",
        replace_then_copy,
    )

    with pytest.raises(ValueError, match="differs|changed"):
        verifier._copy_frozen_inputs(
            source,
            target,
            expected_formal_attestation=verifier._observed_frozen_attestation(
                source
            ),
            expected_universe_bundle_manifest_sha256=expected,
        )


@pytest.mark.parametrize("mutation", ["insert", "delete"])
def test_retry1_directory_manifest_rechecks_exact_set_after_recursion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    first = root / "a.json"
    second = root / "b.json"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    original = verifier._regular_file_manifest
    mutated = False

    def mutate_after_hash(path: Path, label: str) -> dict:
        nonlocal mutated
        observed = original(path, label)
        if not mutated and (
            (mutation == "insert" and path == first)
            or (mutation == "delete" and path == second)
        ):
            mutated = True
            if mutation == "insert":
                (root / "c.json").write_bytes(b"c")
            else:
                first.unlink()
        return observed

    monkeypatch.setattr(
        verifier,
        "_regular_file_manifest",
        mutate_after_hash,
    )

    with pytest.raises(ValueError, match="changed|exact"):
        verifier._complete_directory_manifest(root, "tree")


@pytest.mark.parametrize("mutation", ["insert", "delete"])
def test_retry1_directory_manifest_rechecks_after_final_candidate_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    first = root / "a.json"
    second = root / "b.json"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    original = verifier._assert_no_reparse
    second_checks = 0

    def mutate_during_final_scan(path: Path, label: str):
        nonlocal second_checks
        observed = original(path, label)
        if path == second:
            second_checks += 1
            if second_checks == 4:
                if mutation == "insert":
                    (root / "c.json").write_bytes(b"c")
                else:
                    first.unlink()
        return observed

    monkeypatch.setattr(
        verifier,
        "_assert_no_reparse",
        mutate_during_final_scan,
    )

    with pytest.raises(ValueError, match="changed|exact"):
        verifier._complete_directory_manifest(root, "tree")


def test_phase3_copies_only_four_frozen_input_classes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    relative = verifier.REPLAY_PLAN["inputs"]
    _small_complete_frozen_inputs(source)
    unrelated = source / "data" / "not-frozen.txt"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"do-not-copy")

    binding = verifier._copy_frozen_inputs(
        source,
        target,
        expected_formal_attestation=verifier._observed_frozen_attestation(
            source
        ),
        expected_universe_bundle_manifest_sha256=(
            verifier._universe_bundle_manifest(source)["manifest_sha256"]
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
    _small_complete_frozen_inputs(source)
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
            expected_universe_bundle_manifest_sha256=(
                verifier._universe_bundle_manifest(source)["manifest_sha256"]
            ),
        )


def test_phase3_rejects_drifted_tracked_temporal_contract(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    relative = verifier.REPLAY_PLAN["inputs"]
    _small_complete_frozen_inputs(source)
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
            expected_universe_bundle_manifest_sha256=(
                verifier._universe_bundle_manifest(source)["manifest_sha256"]
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
    retry_authority = {
        "artifact_sha256": "a" * 64,
        "retry_verifier_git_blob_sha256": "b" * 64,
        "universe_bundle_manifest_sha256": "c" * 64,
    }
    original_failed_attempt = {
        "claim_sha256": "d" * 64,
        "status_sha256": "e" * 64,
    }
    inputs = {
        "run_root": run_root,
        "completion_sha256": "1" * 64,
        "source_authority": {"source_commit": "2" * 40, "source_tree": "3" * 40},
        "result_bundle": {},
        "formal_runtime_history": {},
        "verifier_amendment": amendment,
        "retry_authority": retry_authority,
        "original_failed_attempt": original_failed_attempt,
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

    def successful_replay(*args: object, **kwargs: object) -> tuple:
        ownerships = kwargs["replay_stream_ownerships"]
        streams = {}
        for ownership in ownerships:
            verifier._seal_retry_replay_stream_ownership(ownership)
            name = (
                "stdout"
                if ownership["relative"] == verifier.RETRY_REPLAY_STDOUT_NAME
                else "stderr"
            )
            streams[name] = ownership["descriptor"]
        return (
            {"schema_version": verifier.REPLAY_RESULT_SCHEMA},
            {"snapshot_sha256": "4" * 64},
            streams["stdout"],
            streams["stderr"],
        )

    monkeypatch.setattr(verifier, "_load_retry_inputs", stable_formal_loader)
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
        successful_replay,
    )
    monkeypatch.setattr(
        verifier,
        "_validate_replay",
        lambda *args, **kwargs: {"schema_version": verifier.RELOCATION_EQUIVALENCE_SCHEMA, "verified": True, "receipt_sha256": "7" * 64},
    )
    original = verifier._write_once

    def recording_write(
        path: Path,
        raw: bytes,
        label: str,
        **kwargs: object,
    ) -> dict | None:
        writes.append(label)
        return original(path, raw, label, **kwargs)

    monkeypatch.setattr(verifier, "_write_once", recording_write)

    result = verifier.run(str(source_root))

    assert result["status"] == "completed"
    assert writes.index("independent verification artifact") < writes.index(
        "independent verification receipt"
    )
    assert writes[0] == "independent verification retry_1 claim"
    assert formal_loads == 4
    assert current_pool_probes == 2
    claim = json.loads(
        (run_root / verifier.RETRY_CLAIM_NAME).read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (
            run_root
            / verifier.RETRY_RECEIPT_ROOT_NAME
            / f"{result['receipt_sha256']}.json"
        ).read_text(encoding="utf-8")
    )
    status = json.loads(
        (run_root / verifier.RETRY_STATUS_NAME).read_text(encoding="utf-8")
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
        "_load_retry_inputs",
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
    (run_root / verifier.RETRY_CLAIM_NAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        verifier,
        "_load_retry_inputs",
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
        "retry_authority": {
            "artifact_sha256": "a" * 64,
            "retry_verifier_git_blob_sha256": "b" * 64,
            "universe_bundle_manifest_sha256": "c" * 64,
        },
        "original_failed_attempt": {
            "claim_sha256": "d" * 64,
            "status_sha256": "e" * 64,
        },
        "preflight_core": {
            "current_pool_audit_binding": {
                "canonical_sha256": "9" * 64,
                "allowed_symbol_count": 1,
            },
        },
    }
    secret_log_text = "raw-child-output-must-not-publish"

    monkeypatch.setattr(verifier, "_load_retry_inputs", lambda root: inputs)
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

    status = json.loads(
        (run_root / verifier.RETRY_STATUS_NAME).read_text(encoding="utf-8")
    )
    assert status["error_type"] == "RuntimeError"
    assert secret_log_text not in json.dumps(status)
    assert status["verifier_amendment_sha256"] == "c" * 64
    assert status["successor_verifier_git_blob_sha256"] == "d" * 64
    assert (
        status["json_document_size_policy_sha256"]
        == verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
    )


def test_retry1_child_failure_persists_stage_exit_and_log_hashes_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    python = source_root / ".venv/Scripts/python.exe"
    site_packages = source_root / ".venv/Lib/site-packages"
    python.parent.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    python.write_bytes(b"python")
    blocker = source_root / verifier.PYCACHE_BLOCKER_RELATIVE
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(
        (verifier.PROJECT_ROOT / verifier.PYCACHE_BLOCKER_RELATIVE).read_bytes()
    )
    old_claim = run_root / verifier.CLAIM_NAME
    old_status = run_root / verifier.STATUS_NAME
    old_claim.write_bytes(b"immutable-old-claim")
    old_status.write_bytes(b"immutable-old-status")
    inputs = {
        "run_root": run_root,
        "completion_sha256": "1" * 64,
        "source_authority": {
            "source_commit": "2" * 40,
            "source_tree": "3" * 40,
        },
        "result_bundle": {},
        "formal_runtime_history": {},
        "verifier_amendment": _synthetic_verifier_amendment_binding(
            formal_completion_sha256="1" * 64,
        ),
        "retry_authority": {
            "artifact_sha256": "a" * 64,
            "retry_verifier_git_blob_sha256": "b" * 64,
            "universe_bundle_manifest_sha256": "c" * 64,
        },
        "original_failed_attempt": {
            "claim_sha256": "d" * 64,
            "status_sha256": "e" * 64,
        },
        "preflight_core": {
            "frozen_input_attestation": {},
            "current_pool_audit_binding": {
                "canonical_sha256": "9" * 64,
                "allowed_symbol_count": 1,
            },
        },
    }
    raw_child_output = b"synthetic child output must not enter JSON"

    @contextmanager
    def fake_snapshot(*args: object, **kwargs: object):
        code = tmp_path / "code"
        code.mkdir(exist_ok=True)
        copied_blocker = code / verifier.PYCACHE_BLOCKER_RELATIVE
        copied_blocker.parent.mkdir(parents=True, exist_ok=True)
        copied_blocker.write_bytes(blocker.read_bytes())
        yield code

    def failed_replay(*args: object, **kwargs: object) -> object:
        ownerships = {
            ownership["relative"]: ownership
            for ownership in kwargs["replay_stream_ownerships"]
        }
        stdout_handle = ownerships[verifier.RETRY_REPLAY_STDOUT_NAME]["handle"]
        stdout_handle.write(raw_child_output)
        stdout_handle.flush()
        raise verifier.IndependentReplayProcessError(17)

    monkeypatch.setattr(verifier, "_load_retry_inputs", lambda root: inputs)
    monkeypatch.setattr(
        verifier,
        "_assert_project_interpreter",
        lambda root: python,
    )
    monkeypatch.setattr(verifier, "_detached_source_snapshot", fake_snapshot)
    monkeypatch.setattr(
        verifier,
        "_copy_frozen_inputs",
        lambda *args, **kwargs: {"root_sha256": "8" * 64},
    )
    monkeypatch.setattr(verifier, "_probe_runtime", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        verifier,
        "_probe_current_pool_audit",
        lambda *args, **kwargs: kwargs["expected_binding"],
    )
    monkeypatch.setattr(verifier, "_run_isolated_replay", failed_replay)

    with pytest.raises(verifier.IndependentReplayProcessError):
        verifier.run(str(source_root))

    assert old_claim.read_bytes() == b"immutable-old-claim"
    assert old_status.read_bytes() == b"immutable-old-status"
    status = json.loads(
        (run_root / verifier.RETRY_STATUS_NAME).read_text(encoding="utf-8")
    )
    assert status["stage"] == "replay_execution"
    assert status["error_type"] == "IndependentReplayProcessError"
    assert status["independent_replay"]["exit_code"] == 17
    assert status["independent_replay"]["stdout"] == {
        "path": verifier.RETRY_REPLAY_STDOUT_NAME,
        "bytes": len(raw_child_output),
        "sha256": hashlib.sha256(raw_child_output).hexdigest(),
    }
    failure = json.loads(
        (
            run_root / status["failure_receipt_path"]
        ).read_text(encoding="utf-8")
    )
    assert failure["child_exit_code"] == 17
    assert raw_child_output.decode() not in json.dumps(status)
    assert raw_child_output.decode() not in json.dumps(failure)


def _retry1_failure_publication_fixture(
    tmp_path: Path,
) -> tuple[Path, dict, dict, dict, dict]:
    run_root = tmp_path / "run"
    run_root.mkdir()
    claim_path = run_root / verifier.RETRY_CLAIM_NAME
    status_path = run_root / verifier.RETRY_STATUS_NAME
    claim_raw = b'{"retry_id":"retry_1"}\n'
    verifier._write_once(claim_path, claim_raw, "retry claim")
    claim_ownership = verifier._open_claim_ownership(claim_path, claim_raw)
    status_ownership = verifier._reserve_status_slot(status_path)
    bindings = {
        "completion_sha256": "1" * 64,
        "verifier_amendment": {
            "artifact_sha256": "2" * 64,
            "successor_verifier_git_blob_sha256": "3" * 64,
            "json_document_size_policy_sha256": "4" * 64,
        },
        "retry_authority": {"artifact_sha256": "5" * 64},
        "original_failed_attempt": {
            "claim_sha256": "6" * 64,
            "status_sha256": "7" * 64,
        },
    }
    return (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        {"claim_path": claim_path, "status_path": status_path},
    )


def test_retry1_failure_descriptor_error_still_terminalizes_owned_status(
    tmp_path: Path,
) -> None:
    (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        paths,
    ) = _retry1_failure_publication_fixture(tmp_path)
    (run_root / verifier.RETRY_REPLAY_STDOUT_NAME).mkdir()
    try:
        verifier._publish_retry_failure(
            run_root=run_root,
            status_ownership=status_ownership,
            claim_ownership=claim_ownership,
            stage="replay_execution",
            error=RuntimeError("private error text"),
            **bindings,
        )

        status = json.loads(_owned_bytes(status_ownership).decode("utf-8"))
        assert status["status"] == "failed"
        assert status["descriptor_error_type"] == "IndependentVerificationError"
        assert status["publication_error_type"] is None
        assert status["independent_replay"] == {
            "exit_code": None,
            "stdout": None,
            "stderr": None,
        }
        failure = json.loads(
            (run_root / status["failure_receipt_path"]).read_text(encoding="utf-8")
        )
        assert failure["descriptor_error_type"] == "IndependentVerificationError"
        assert "private error text" not in json.dumps(status)
        assert "private error text" not in json.dumps(failure)
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_existing_stream_seal_error_still_terminalizes_owned_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        _paths,
    ) = _retry1_failure_publication_fixture(tmp_path)
    ownerships = verifier._create_retry_replay_stream_ownerships(run_root)
    original_seal = verifier._seal_retry_replay_stream_ownership
    seal_calls = 0

    def fail_second_seal(ownership: dict) -> dict:
        nonlocal seal_calls
        seal_calls += 1
        if seal_calls == 2:
            raise OSError("synthetic stream seal failure")
        return original_seal(ownership)

    monkeypatch.setattr(
        verifier,
        "_seal_retry_replay_stream_ownership",
        fail_second_seal,
    )
    try:
        verifier._publish_retry_failure(
            run_root=run_root,
            status_ownership=status_ownership,
            claim_ownership=claim_ownership,
            stage="replay_execution",
            error=RuntimeError("private error text"),
            existing_replay_stream_ownerships=ownerships,
            **bindings,
        )

        status = json.loads(_owned_bytes(status_ownership).decode("utf-8"))
        assert status["status"] == "failed"
        assert status["descriptor_error_type"] == "OSError"
        assert status["independent_replay"] == {
            "exit_code": None,
            "stdout": None,
            "stderr": None,
        }
        assert all(ownership["handle"].closed for ownership in ownerships)
    finally:
        verifier._close_retry_replay_stream_ownerships(ownerships)
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_failure_receipt_write_error_still_terminalizes_owned_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        paths,
    ) = _retry1_failure_publication_fixture(tmp_path)
    original = verifier._write_once

    def fail_receipt(
        path: Path,
        raw: bytes,
        label: str,
        **kwargs: object,
    ) -> dict | None:
        if label == "independent verification retry_1 failure receipt":
            raise OSError("receipt publication failed")
        return original(path, raw, label, **kwargs)

    monkeypatch.setattr(verifier, "_write_once", fail_receipt)
    try:
        verifier._publish_retry_failure(
            run_root=run_root,
            status_ownership=status_ownership,
            claim_ownership=claim_ownership,
            stage="runtime_preparation",
            error=RuntimeError("private error text"),
            **bindings,
        )

        status = json.loads(_owned_bytes(status_ownership).decode("utf-8"))
        assert status["status"] == "failed"
        assert status["failure_receipt_sha256"] is None
        assert status["failure_receipt_path"] is None
        assert status["publication_error_type"] == "OSError"
        assert "private error text" not in json.dumps(status)
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_reverted_owned_status_write_can_terminalize_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        paths,
    ) = _retry1_failure_publication_fixture(tmp_path)
    original = verifier._verify_claim_ownership
    claim_checks = 0

    def fail_first_postwrite_check(ownership: dict) -> None:
        nonlocal claim_checks
        claim_checks += 1
        if claim_checks == 2:
            raise OSError("synthetic postwrite claim check failure")
        original(ownership)

    monkeypatch.setattr(
        verifier,
        "_verify_claim_ownership",
        fail_first_postwrite_check,
    )
    try:
        with pytest.raises(OSError, match="postwrite claim check"):
            verifier._finalize_status_slot(
                status_ownership,
                b'{"status":"completed"}\n',
                claim_ownership=claim_ownership,
            )
        assert _owned_bytes(status_ownership) == b""

        verifier._publish_retry_failure(
            run_root=run_root,
            status_ownership=status_ownership,
            claim_ownership=claim_ownership,
            stage="status_publication",
            error=OSError("private error text"),
            **bindings,
        )

        status = json.loads(_owned_bytes(status_ownership).decode("utf-8"))
        assert status["status"] == "failed"
        assert status["error_type"] == "OSError"
        assert "private error text" not in json.dumps(status)
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_failure_receipt_is_locked_until_owned_status_finalizes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        paths,
    ) = _retry1_failure_publication_fixture(tmp_path)
    original = verifier._verify_claim_ownership
    claim_checks = 0
    blocked = False

    def try_to_replace_published_receipt(ownership: dict) -> None:
        nonlocal claim_checks, blocked
        claim_checks += 1
        original(ownership)
        if claim_checks == 2:
            receipts = list(
                (run_root / verifier.RETRY_FAILURE_RECEIPT_ROOT_NAME).glob(
                    "*.json"
                )
            )
            assert len(receipts) == 1
            try:
                receipts[0].write_bytes(b"foreign\n")
            except PermissionError:
                blocked = True

    monkeypatch.setattr(
        verifier,
        "_verify_claim_ownership",
        try_to_replace_published_receipt,
    )
    try:
        verifier._publish_retry_failure(
            run_root=run_root,
            status_ownership=status_ownership,
            claim_ownership=claim_ownership,
            stage="replay_execution",
            error=RuntimeError("private error text"),
            **bindings,
        )

        status = json.loads(_owned_bytes(status_ownership).decode("utf-8"))
        failure = json.loads(
            (run_root / status["failure_receipt_path"]).read_text(encoding="utf-8")
        )
        body = dict(failure)
        artifact_sha256 = body.pop("artifact_sha256")
        assert blocked is True
        assert artifact_sha256 == status["failure_receipt_sha256"]
        assert artifact_sha256 == _sha256(body)
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_failure_status_holds_log_ownership_until_finalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        _paths,
    ) = _retry1_failure_publication_fixture(tmp_path)
    stdout = run_root / verifier.RETRY_REPLAY_STDOUT_NAME
    stdout_raw = b"synthetic failure stdout"
    stdout.write_bytes(stdout_raw)
    original = verifier._verify_claim_ownership
    claim_checks = 0
    blocked = False

    def try_sustained_log_write(ownership: dict) -> None:
        nonlocal claim_checks, blocked
        claim_checks += 1
        original(ownership)
        if claim_checks == 2:
            try:
                stdout.write_bytes(b"sustained foreign write")
            except PermissionError:
                blocked = True

    monkeypatch.setattr(
        verifier,
        "_verify_claim_ownership",
        try_sustained_log_write,
    )
    try:
        verifier._publish_retry_failure(
            run_root=run_root,
            status_ownership=status_ownership,
            claim_ownership=claim_ownership,
            stage="replay_execution",
            error=RuntimeError("private error text"),
            **bindings,
        )

        status = json.loads(_owned_bytes(status_ownership).decode("utf-8"))
        expected_stdout = {
            "path": verifier.RETRY_REPLAY_STDOUT_NAME,
            "bytes": len(stdout_raw),
            "sha256": hashlib.sha256(stdout_raw).hexdigest(),
        }
        assert blocked is True
        assert status["independent_replay"]["stdout"] == expected_stdout
        failure = json.loads(
            (run_root / status["failure_receipt_path"]).read_text(encoding="utf-8")
        )
        assert failure["independent_replay"]["stdout"] == expected_stdout
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_status_is_locked_through_final_claim_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    claim_path = tmp_path / "claim.json"
    status_path = tmp_path / "status.json"
    claim_raw = b'{"claim":true}\n'
    status_raw = b'{"status":"completed"}\n'
    verifier._write_once(claim_path, claim_raw, "claim")
    claim_ownership = verifier._open_claim_ownership(claim_path, claim_raw)
    status_ownership = verifier._reserve_status_slot(status_path)
    original = verifier._verify_claim_ownership
    claim_checks = 0
    blocked = False

    def try_to_replace_status(ownership: dict) -> None:
        nonlocal claim_checks, blocked
        claim_checks += 1
        original(ownership)
        if claim_checks == 2:
            try:
                status_path.write_bytes(b"foreign\n")
            except PermissionError:
                blocked = True

    monkeypatch.setattr(
        verifier,
        "_verify_claim_ownership",
        try_to_replace_status,
    )
    try:
        verifier._finalize_status_slot(
            status_ownership,
            status_raw,
            claim_ownership=claim_ownership,
        )

        assert blocked is True
        assert _owned_bytes(status_ownership) == status_raw
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


def test_retry1_foreign_status_injection_is_not_accepted_but_receipt_survives(
    tmp_path: Path,
) -> None:
    (
        run_root,
        claim_ownership,
        status_ownership,
        bindings,
        paths,
    ) = _retry1_failure_publication_fixture(tmp_path)
    foreign = b'{"foreign":true}\n'
    try:
        with pytest.raises(PermissionError):
            paths["status_path"].write_bytes(foreign)
        verifier._publish_retry_failure(
            run_root=run_root,
            status_ownership=status_ownership,
            claim_ownership=claim_ownership,
            stage="runtime_preparation",
            error=RuntimeError("private error text"),
            **bindings,
        )

        status = json.loads(_owned_bytes(status_ownership).decode("utf-8"))
        assert status["status"] == "failed"
        receipts = list(
            (run_root / verifier.RETRY_FAILURE_RECEIPT_ROOT_NAME).glob("*.json")
        )
        assert len(receipts) == 1
    finally:
        status_ownership["handle"].close()
        claim_ownership["handle"].close()


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
            raise AssertionError("historical R2 verification must not read live HEAD")
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
    monkeypatch.setattr(
        verifier,
        "EXPECTED_VERIFIER_AMENDMENT_EXECUTION_COMMIT",
        amendment_execution,
    )
    monkeypatch.setattr(
        verifier,
        "EXPECTED_VERIFIER_AMENDMENT_ARTIFACT_SHA256",
        artifact_sha256,
    )
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


def test_retry1_historical_r2_amendment_ignores_later_live_successor_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, formal_authority, completion_sha256, _document = (
        _post_run_amendment_fixture(monkeypatch, tmp_path)
    )
    (
        source / "tests/test_shallow_gbdt_risk_on_breadth_formal_launcher.py"
    ).write_bytes(b"drifted successor tests\n")

    observed = verifier._verified_post_run_verifier_amendment(
        source,
        formal_source_authority=formal_authority,
        formal_completion_sha256=completion_sha256,
    )

    assert observed["execution_commit"] == (
        verifier.EXPECTED_VERIFIER_AMENDMENT_EXECUTION_COMMIT
    )


def _synthetic_old_failed_evidence(
    monkeypatch: pytest.MonkeyPatch,
    run_root: Path,
) -> tuple[bytes, bytes]:
    claim = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
            "independent-verification-claim/v2"
        ),
        "pid": 123,
        "completion_sha256": "1" * 64,
        "replay_plan_sha256": verifier.EXPECTED_REPLAY_PLAN_SHA256,
        "verifier_amendment_sha256": "2" * 64,
        "successor_verifier_git_blob_sha256": "3" * 64,
        "json_document_size_policy_sha256": (
            verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
        ),
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    }
    claim_raw = _canonical_bytes(claim) + b"\n"
    claim_sha256 = hashlib.sha256(claim_raw).hexdigest()
    status = {
        "schema_version": verifier.STATUS_SCHEMA,
        "status": "failed",
        "stage": "failed",
        "verified": False,
        "receipt_sha256": None,
        "error_type": "IndependentVerificationError",
        "point_in_time": True,
        "development_only": True,
        "development_statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "production_authority": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "claim_sha256": claim_sha256,
        "completion_sha256": "1" * 64,
        "verifier_amendment_sha256": "2" * 64,
        "successor_verifier_git_blob_sha256": "3" * 64,
        "json_document_size_policy_sha256": (
            verifier.EXPECTED_JSON_DOCUMENT_SIZE_POLICY_SHA256
        ),
        "receipt_path": None,
    }
    status_raw = _canonical_bytes(status) + b"\n"
    (run_root / verifier.CLAIM_NAME).write_bytes(claim_raw)
    (run_root / verifier.STATUS_NAME).write_bytes(status_raw)
    monkeypatch.setattr(
        verifier,
        "EXPECTED_FAILED_CLAIM_SHA256",
        hashlib.sha256(claim_raw).hexdigest(),
    )
    monkeypatch.setattr(
        verifier,
        "EXPECTED_FAILED_STATUS_SHA256",
        hashlib.sha256(status_raw).hexdigest(),
    )
    return claim_raw, status_raw


def test_retry1_original_failed_evidence_is_exact_and_has_no_success_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    claim_raw, status_raw = _synthetic_old_failed_evidence(
        monkeypatch,
        run_root,
    )

    observed = verifier._verified_original_failed_attempt(run_root)

    assert observed == {
        "claim_path": verifier.CLAIM_NAME,
        "claim_sha256": hashlib.sha256(claim_raw).hexdigest(),
        "status_path": verifier.STATUS_NAME,
        "status_sha256": hashlib.sha256(status_raw).hexdigest(),
        "verification_artifacts_state": "absent",
        "receipts_state": "absent",
        "status": "failed",
        "stage": "failed",
        "verified": False,
        "error_type": "IndependentVerificationError",
    }

    verification = run_root / verifier.VERIFICATION_ROOT_NAME
    verification.mkdir()
    with pytest.raises(
        verifier.IndependentVerificationError,
        match="failed attempt",
    ):
        verifier._verified_original_failed_attempt(run_root)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("claim_sha256", "f" * 64),
        ("receipt_path", "foreign.json"),
        ("unexpected_field", True),
    ],
)
def test_retry1_original_failed_status_requires_exact_fields_and_claim_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
    value: object,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    _synthetic_old_failed_evidence(monkeypatch, run_root)
    status_path = run_root / verifier.STATUS_NAME
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status[mutation] = value
    raw = _canonical_bytes(status) + b"\n"
    status_path.write_bytes(raw)
    monkeypatch.setattr(
        verifier,
        "EXPECTED_FAILED_STATUS_SHA256",
        hashlib.sha256(raw).hexdigest(),
    )

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="failed attempt",
    ):
        verifier._verified_original_failed_attempt(run_root)


def test_retry1_original_failed_claim_requires_exact_field_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    _synthetic_old_failed_evidence(monkeypatch, run_root)
    claim_path = run_root / verifier.CLAIM_NAME
    status_path = run_root / verifier.STATUS_NAME
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["unexpected_field"] = True
    claim_raw = _canonical_bytes(claim) + b"\n"
    claim_path.write_bytes(claim_raw)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["claim_sha256"] = hashlib.sha256(claim_raw).hexdigest()
    status_raw = _canonical_bytes(status) + b"\n"
    status_path.write_bytes(status_raw)
    monkeypatch.setattr(
        verifier,
        "EXPECTED_FAILED_CLAIM_SHA256",
        hashlib.sha256(claim_raw).hexdigest(),
    )
    monkeypatch.setattr(
        verifier,
        "EXPECTED_FAILED_STATUS_SHA256",
        hashlib.sha256(status_raw).hexdigest(),
    )

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="failed attempt",
    ):
        verifier._verified_original_failed_attempt(run_root)


def _post_failure_retry_authority_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, dict, dict, dict, dict]:
    source = tmp_path / "source"
    source.mkdir()
    run_root = source / "run"
    run_root.mkdir()
    _synthetic_old_failed_evidence(monkeypatch, run_root)
    failed = verifier._verified_original_failed_attempt(run_root)
    old_amendment = {
        "artifact_sha256": "5" * 64,
        "execution_commit": "1" * 40,
    }
    r2 = old_amendment["execution_commit"]
    r3 = "2" * 40
    r3_tree = "3" * 40
    r4 = "4" * 40
    bundle_manifest = {
        "manifest_sha256": "8" * 64,
        "bundle_relative_path": "data/bundle",
        "metadata_relative_path": "data/bundle/metadata.sqlite3",
    }
    monkeypatch.setattr(
        verifier,
        "EXPECTED_VERIFIER_AMENDMENT_EXECUTION_COMMIT",
        r2,
    )
    changed_bytes = {
        ".gitattributes": (
            verifier.VERIFIER_AMENDMENT_GIT_ATTRIBUTES_RULE
            + "\n"
            + verifier.RETRY_AUTHORITY_GIT_ATTRIBUTES_RULE
            + "\n"
        ).encode(),
        verifier.VERIFIER_AMENDMENT_VERIFIER_GIT_PATH: b"retry verifier\n",
        (
            "tests/test_shallow_gbdt_risk_on_breadth_"
            "independent_verifier.py"
        ): b"retry tests\n",
    }
    for relative, raw in changed_bytes.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    changed_sha256 = {
        path: hashlib.sha256(raw).hexdigest()
        for path, raw in changed_bytes.items()
    }
    authority_root = source / verifier.RETRY_AUTHORITY_RELATIVE_ROOT
    authority_root.mkdir(parents=True)
    body = {
        "schema_version": verifier.RETRY_AUTHORITY_SCHEMA,
        "retry_id": "retry_1",
        "original_amendment_execution_commit": r2,
        "original_amendment_artifact_sha256": old_amendment[
            "artifact_sha256"
        ],
        "original_failed_claim_path": failed["claim_path"],
        "original_failed_claim_sha256": failed["claim_sha256"],
        "original_failed_status_path": failed["status_path"],
        "original_failed_status_sha256": failed["status_sha256"],
        "original_verification_artifacts_state": "absent",
        "original_receipts_state": "absent",
        "universe_bundle_relative_path": bundle_manifest[
            "bundle_relative_path"
        ],
        "universe_metadata_relative_path": bundle_manifest[
            "metadata_relative_path"
        ],
        "universe_bundle_manifest_sha256": bundle_manifest[
            "manifest_sha256"
        ],
        "retry_source_commit": r3,
        "retry_source_tree": r3_tree,
        "retry_verifier_git_path": (
            verifier.VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
        ),
        "retry_verifier_git_blob_sha256": changed_sha256[
            verifier.VERIFIER_AMENDMENT_VERIFIER_GIT_PATH
        ],
        "retry_git_blobs_sha256": changed_sha256,
        "replay_plan_sha256": verifier.EXPECTED_REPLAY_PLAN_SHA256,
        "failure_classification": verifier.RETRY_FAILURE_CLASSIFICATION,
        "scope": verifier.RETRY_AUTHORITY_SCOPE,
        "execution_topology": verifier.RETRY_AUTHORITY_TOPOLOGY,
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
            return r4
        if arguments == ("rev-list", "--parents", "-n", "1", r4):
            return f"{r4} {r3}"
        if arguments == ("rev-list", "--parents", "-n", "1", r3):
            return f"{r3} {r2}"
        if arguments == ("rev-parse", f"{r3}^{{tree}}"):
            return r3_tree
        if arguments == (
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            r2,
            r3,
        ):
            return "\n".join(f"M\t{path}" for path in changed_bytes)
        if arguments == (
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            r3,
            r4,
        ):
            return f"A\t{authority_relative}"
        raise AssertionError(arguments)

    def fake_git_bytes(root: Path, *arguments: str) -> bytes:
        if len(arguments) == 2 and arguments[0] == "show":
            prefix = f"{r3}:"
            if arguments[1].startswith(prefix):
                return changed_bytes[arguments[1].removeprefix(prefix)]
        if arguments == ("show", f"{r4}:{authority_relative}"):
            return authority_raw
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier, "SCRIPT_PATH", source / verifier.VERIFIER_AMENDMENT_VERIFIER_GIT_PATH)
    monkeypatch.setattr(verifier, "_git_output", fake_git_output)
    monkeypatch.setattr(verifier, "_git_bytes", fake_git_bytes)
    monkeypatch.setattr(
        verifier,
        "_universe_bundle_manifest",
        lambda root: dict(bundle_manifest),
    )
    return source, old_amendment, failed, bundle_manifest, document


def test_retry1_authority_binds_r2_failure_bundle_and_exact_r3_blobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, old_amendment, failed, bundle_manifest, document = (
        _post_failure_retry_authority_fixture(monkeypatch, tmp_path)
    )

    observed = verifier._verified_post_failure_retry_authority(
        source,
        original_amendment=old_amendment,
        original_failed_attempt=failed,
    )

    assert observed["artifact_sha256"] == document["artifact_sha256"]
    assert observed["execution_commit"] == "4" * 40
    assert observed["retry_source_tree"] == "3" * 40
    assert observed["universe_bundle_manifest_sha256"] == bundle_manifest[
        "manifest_sha256"
    ]
    assert observed["failure_classification"] == {
        "stage": "isolated_replay_bootstrap",
        "before": "_load_ranked_liquidity_bars",
        "child_exit_code": 7,
        "error_chain": [
            "AuditedPITDevelopmentReplayError",
            "PITReceiptError",
        ],
        "root_cause_code": "incomplete_audited_pit_bundle_copy",
    }
    assert observed["scope"] == verifier.RETRY_AUTHORITY_SCOPE


def test_retry1_authority_rejects_old_failed_status_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, old_amendment, failed, _bundle, _document = (
        _post_failure_retry_authority_fixture(monkeypatch, tmp_path)
    )
    failed["status_sha256"] = "9" * 64

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="retry authority",
    ):
        verifier._verified_post_failure_retry_authority(
            source,
            original_amendment=old_amendment,
            original_failed_attempt=failed,
        )


def test_retry1_authority_rejects_failure_classification_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, old_amendment, failed, _bundle, document = (
        _post_failure_retry_authority_fixture(monkeypatch, tmp_path)
    )
    authority_path = (
        source
        / verifier.RETRY_AUTHORITY_RELATIVE_ROOT
        / f"{document['artifact_sha256']}.json"
    )
    drifted = deepcopy(document)
    drifted["failure_classification"]["child_exit_code"] = 0
    authority_path.write_bytes(_canonical_bytes(drifted) + b"\n")

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="retry authority",
    ):
        verifier._verified_post_failure_retry_authority(
            source,
            original_amendment=old_amendment,
            original_failed_attempt=failed,
        )


def test_retry1_preflight_is_read_only_and_never_claims(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    monkeypatch.setattr(
        verifier,
        "_load_retry_inputs",
        lambda root, **kwargs: {"run_root": run_root},
    )
    monkeypatch.setattr(
        verifier,
        "run",
        lambda root: (_ for _ in ()).throw(AssertionError("run forbidden")),
    )

    assert verifier.main(["--source-root", str(tmp_path), "--preflight"]) == 0
    assert not (run_root / verifier.RETRY_CLAIM_NAME).exists()
    assert not (run_root / verifier.RETRY_STATUS_NAME).exists()


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
    _small_complete_frozen_inputs(source)
    attestation = verifier._observed_frozen_attestation(source)
    bundle_sha256 = verifier._universe_bundle_manifest(source)[
        "manifest_sha256"
    ]
    binding = verifier._copy_frozen_inputs(
        source,
        target,
        expected_formal_attestation=attestation,
        expected_universe_bundle_manifest_sha256=bundle_sha256,
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

    ownerships = verifier._create_retry_replay_stream_ownerships(scratch)
    try:
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
                replay_stream_ownerships=ownerships,
            )
    finally:
        verifier._close_retry_replay_stream_ownerships(ownerships)

    assert len(calls) == 1
    assert calls[0]["shell"] is False


@pytest.mark.parametrize("postflight_failure", ["frozen", "current_pool", "runtime"])
def test_retry1_nonzero_child_exit_wins_over_postflight_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    postflight_failure: str,
) -> None:
    roots = {
        name: tmp_path / name
        for name in ("source", "code", "data", "site-packages", "scratch")
    }
    for path in roots.values():
        path.mkdir()

    class Process:
        def wait(self) -> int:
            return 7

    monkeypatch.setattr(
        verifier.subprocess,
        "Popen",
        lambda *args, **kwargs: Process(),
    )

    def frozen_check(*args: object, **kwargs: object) -> None:
        if postflight_failure == "frozen":
            raise RuntimeError("frozen postflight failed")

    def current_pool(*args: object, **kwargs: object) -> object:
        if postflight_failure == "current_pool":
            raise RuntimeError("current-pool postflight failed")
        return kwargs["expected_binding"]

    def runtime_probe(*args: object, **kwargs: object) -> object:
        if postflight_failure == "runtime":
            raise RuntimeError("runtime postflight failed")
        return {"probe": "before"}

    monkeypatch.setattr(verifier, "_verify_frozen_copy", frozen_check)
    monkeypatch.setattr(verifier, "_probe_current_pool_audit", current_pool)
    monkeypatch.setattr(verifier, "_probe_runtime", runtime_probe)

    ownerships = verifier._create_retry_replay_stream_ownerships(roots["scratch"])
    try:
        with pytest.raises(verifier.IndependentReplayProcessError) as captured:
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
                source_root=roots["source"],
                code_root=roots["code"],
                data_root=roots["data"],
                python_executable=tmp_path / "python.exe",
                site_packages=roots["site-packages"],
                environment={},
                pycache_blocker=tmp_path / "blocker",
                formal_probe={"probe": "formal"},
                replay_probe={"probe": "before"},
                frozen_copy={},
                scratch_root=roots["scratch"],
                replay_stream_ownerships=ownerships,
            )
    finally:
        verifier._close_retry_replay_stream_ownerships(ownerships)

    assert captured.value.exit_code == 7


def test_retry1_controlled_stream_handles_block_second_writer_during_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    roots = {
        name: tmp_path / name
        for name in ("source", "code", "data", "site-packages", "scratch")
    }
    for path in roots.values():
        path.mkdir()
    ownerships = verifier._create_retry_replay_stream_ownerships(roots["scratch"])
    stdout_path = roots["scratch"] / verifier.RETRY_REPLAY_STDOUT_NAME
    child_output = b"synthetic child stdout"
    state = {"blocked": False}

    class Process:
        def wait(self) -> int:
            try:
                stdout_path.write_bytes(b"sustained foreign write")
            except PermissionError:
                state["blocked"] = True
            return 7

    def fake_popen(*args: object, **kwargs: object) -> Process:
        stdout_handle = kwargs["stdout"]
        stdout_handle.write(child_output)
        stdout_handle.flush()
        return Process()

    monkeypatch.setattr(verifier.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        verifier,
        "_verify_frozen_copy",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        verifier,
        "_probe_current_pool_audit",
        lambda *args, **kwargs: kwargs["expected_binding"],
    )
    monkeypatch.setattr(
        verifier,
        "_probe_runtime",
        lambda *args, **kwargs: {"probe": "before"},
    )

    try:
        with pytest.raises(verifier.IndependentReplayProcessError):
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
                source_root=roots["source"],
                code_root=roots["code"],
                data_root=roots["data"],
                python_executable=tmp_path / "python.exe",
                site_packages=roots["site-packages"],
                environment={},
                pycache_blocker=tmp_path / "blocker",
                formal_probe={"probe": "formal"},
                replay_probe={"probe": "before"},
                frozen_copy={},
                scratch_root=roots["scratch"],
                replay_stream_ownerships=ownerships,
            )

        stdout_ownership = next(
            ownership
            for ownership in ownerships
            if ownership["relative"] == verifier.RETRY_REPLAY_STDOUT_NAME
        )
        assert state["blocked"] is True
        assert stdout_ownership["descriptor"] == {
            "path": verifier.RETRY_REPLAY_STDOUT_NAME,
            "bytes": len(child_output),
            "sha256": hashlib.sha256(child_output).hexdigest(),
        }
    finally:
        verifier._close_retry_replay_stream_ownerships(ownerships)
