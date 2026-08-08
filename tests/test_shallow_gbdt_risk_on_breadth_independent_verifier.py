from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

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
) -> dict:
    body = {
        "schema_version": verifier.EXPECTED_VERIFICATION_SCHEMA,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
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


def _write_result_bundle(tmp_path: Path) -> tuple[dict, dict[str, str], str, str]:
    source = {"schema_version": "synthetic-source/v1"}
    producer = {"root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256}
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir()
    filter_receipt_sha256 = "1" * 64
    sidecars: dict[str, str] = {}
    for name, schema in verifier.RESULT_BUNDLE_SIDECAR_SCHEMAS.items():
        payload = {
            "schema_version": schema,
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "source": source,
            "producer_code": producer,
        }
        if name == "selection":
            payload["market_breadth_filter_receipt"] = {
                "receipt_sha256": filter_receipt_sha256
            }
        _path, sidecars[name] = _write_content_addressed(sidecar_dir, payload)
    main = {
        "schema_version": verifier.EXPECTED_RESULT_SCHEMA,
        "strategy": {"strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256},
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
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
    }
    _path, main_sha256 = _write_content_addressed(tmp_path, main)
    runtime = _runtime_body(
        main_sha256=main_sha256,
        sidecars=sidecars,
        feature_receipt_sha256="2" * 64,
    )
    return runtime, sidecars, main_sha256, filter_receipt_sha256


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
