from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app import audited_pit_factor_v3_formal_materializer_v2 as producer
from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract
from app import audited_pit_factor_v3_formal_materializer_v2_runner as runner


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")


def _runner_app_import_names() -> set[str]:
    parsed = ast.parse(inspect.getsource(runner))
    return {
        alias.name
        for node in ast.walk(parsed)
        if isinstance(node, ast.ImportFrom) and node.module == "app"
        for alias in node.names
    }


def _attempt_roots(tmp_path: Path) -> dict[str, Path]:
    attempt_root = tmp_path / "attempt-root"
    return {
        name: attempt_root / name for name in runner.DISPOSABLE_ATTEMPT_ROOT_NAMES
    }


def _claim_kwargs(roots: dict[str, Path]) -> dict[str, str]:
    return {
        "semantic_input_root_sha256": "a" * 64,
        "claim_root": str(roots["claim"]),
        "status_root": str(roots["status"]),
        "artifact_root": str(roots["artifact"]),
        "independent_verifier_root": str(roots["independent-verifier"]),
        "publication_root": str(roots["publication"]),
    }


@pytest.mark.parametrize("mode", contract.RUNNER_CLI_MODES)
def test_registered_runner_fails_before_self_signed_input_or_io_when_tcb_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    assert _runner_app_import_names() == {
        "audited_pit_factor_v3_formal_materializer_v2_authority",
        "audited_pit_factor_v3_formal_materializer_v2_contract",
    }
    assert runner.REGISTERED_PRODUCER_PROGRAM_NAME != (
        runner.REGISTERED_VERIFIER_PROGRAM_NAME
    )
    assert contract.RUNNER_ROLE not in {
        contract.PRODUCER_ROLE,
        contract.INDEPENDENT_VERIFIER_ROLE,
    }
    registered_slots = (
        authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_PRODUCER_SOURCE_ROOT_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_INDEPENDENT_SOURCE_ROOT_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_RUNNER_SOURCE_ROOT_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_ATTEMPT_LOCATOR_RAW_SHA256,
        authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER,
        authority.REGISTERED_SQLITE_RUNTIME_TCB_SHA256,
    )
    assert registered_slots == (None,) * len(registered_slots)

    malicious_payload = {
        **{field: True for field in contract.ACTIVATION_V2_REQUIRED_TRUE_FIELDS},
        **{
            field: "f" * 64
            for field in contract.REGISTERED_ACTIVATION_EXACT_RAW_CLOSURE_FIELDS
        },
        "authority_scope": contract.FORMAL_AUTHORITY_SCOPE,
        "schema": contract.FORMAL_ACTIVATION_INDEPENDENT_RECEIPT_SCHEMA,
    }
    malicious_path = tmp_path / "caller-self-signed-all-true.json"
    malicious_bytes = _canonical_bytes(malicious_payload)
    malicious_path.write_bytes(malicious_bytes)
    malicious_sha = hashlib.sha256(malicious_bytes).hexdigest()
    caller_output = tmp_path / "caller-output"
    io_calls: list[tuple[str, str]] = []

    def io_probe(operation: str, path: str) -> None:
        io_calls.append((operation, path))

    monkeypatch.setattr(runner, "_runner_io_probe", io_probe)
    operation = (
        runner.run_registered_factor_v3_formal_materialization_v2_once
        if mode == "run"
        else runner.verify_registered_factor_v3_formal_materialization_v2_once
    )
    assert not inspect.signature(operation).parameters
    try:
        with pytest.raises(
            runner.FactorV3FormalMaterializerV2RunnerUnavailableError
        ):
            operation()
    finally:
        assert io_calls == []
        assert malicious_path.read_bytes() == malicious_bytes
        assert hashlib.sha256(malicious_path.read_bytes()).hexdigest() == malicious_sha
        assert not caller_output.exists()


@pytest.mark.parametrize(
    "case",
    (
        "preexisting-claim",
        "overlapping-roots",
        "concurrent-claim",
        "preexisting-failure-status",
        "failure-terminal-no-resume",
        "cli-retry",
        "cli-resume",
        "safety-all-false",
        "safety-one-true",
    ),
)
def test_runner_is_create_only_single_attempt_root_isolated_and_lifecycle_false(
    tmp_path: Path,
    case: str,
) -> None:
    assert contract.RUNNER_ATTEMPT_STATES == (
        "EMPTY",
        "CLAIMED",
        "PRODUCED",
        "INDEPENDENTLY_VERIFIED",
        "PUBLISHED",
        "FAILED_TERMINAL",
    )
    assert contract.RUNNER_CLI_MODES == ("run", "verify")
    assert contract.FAILURE_STAGE_ORDER == ("create-failure-terminal-cas",)
    assert "create-failure-terminal-cas" not in contract.PUBLICATION_STAGE_ORDER
    assert contract.PUBLICATION_STAGE_ORDER[-1] == "create-final-publication-cas"
    assert not inspect.signature(
        runner.run_registered_factor_v3_formal_materialization_v2_once
    ).parameters
    assert not inspect.signature(
        runner.verify_registered_factor_v3_formal_materialization_v2_once
    ).parameters
    forbidden_parameters = {
        "attempt_id",
        "force",
        "max_attempts",
        "output_root",
        "resume",
        "retries",
        "retry",
    }
    for operation in (
        runner._claim_disposable_materialization_attempt_v2_once,
        runner._race_disposable_materialization_attempt_claims_v2,
        runner._mark_disposable_materialization_attempt_failed_terminal_v2,
    ):
        assert forbidden_parameters.isdisjoint(inspect.signature(operation).parameters)

    roots = _attempt_roots(tmp_path)
    resolved_roots = [path.resolve() for path in roots.values()]
    assert len(set(resolved_roots)) == len(resolved_roots)
    assert all(
        not left.is_relative_to(right)
        for left in resolved_roots
        for right in resolved_roots
        if left != right
    )
    claim_kwargs = _claim_kwargs(roots)

    if case == "preexisting-claim":
        roots["claim"].mkdir(parents=True)
        claim_path = roots["claim"] / "claim.json"
        original = b'{"schema":"foreign-preexisting-claim"}\n'
        claim_path.write_bytes(original)
        try:
            with pytest.raises(runner.FactorV3FormalMaterializerV2RunnerError):
                runner._claim_disposable_materialization_attempt_v2_once(
                    **claim_kwargs
                )
        finally:
            assert claim_path.read_bytes() == original
            assert all(
                not roots[name].exists()
                for name in (
                    "status",
                    "artifact",
                    "independent-verifier",
                    "publication",
                )
            )
        return

    if case == "overlapping-roots":
        claim_kwargs["status_root"] = claim_kwargs["claim_root"]
        with pytest.raises(runner.FactorV3FormalMaterializerV2RunnerError):
            runner._claim_disposable_materialization_attempt_v2_once(**claim_kwargs)
        return

    if case == "concurrent-claim":
        result = runner._race_disposable_materialization_attempt_claims_v2(
            **claim_kwargs,
            contender_count=8,
        )
        assert result["contender_count"] == 8
        assert result["winner_count"] == 1
        assert result["loser_count"] == 7
        assert result["claim_create_only"] is True
        assert result["claim_status"] == "CLAIMED"
        assert len(tuple(roots["claim"].glob("*.json"))) == 1
        return

    if case == "preexisting-failure-status":
        claim_path = tmp_path / "held-claim.json"
        claim_path.write_bytes(b'{"schema":"disposable-held-claim"}\n')
        roots["status"].mkdir(parents=True)
        terminal_path = roots["status"] / "failed-terminal.json"
        original = b'{"schema":"foreign-preexisting-terminal"}\n'
        terminal_path.write_bytes(original)
        try:
            with pytest.raises(runner.FactorV3FormalMaterializerV2RunnerError):
                runner._mark_disposable_materialization_attempt_failed_terminal_v2(
                    claim_path=str(claim_path),
                    status_root=str(roots["status"]),
                    reason_code="INJECTED_PREDECESSOR_FAILURE",
                )
        finally:
            assert terminal_path.read_bytes() == original
            assert not roots["publication"].exists()
        return

    if case == "failure-terminal-no-resume":
        claim_path = tmp_path / "held-claim.json"
        claim_path.write_bytes(b'{"schema":"disposable-held-claim"}\n')
        failed = runner._mark_disposable_materialization_attempt_failed_terminal_v2(
            claim_path=str(claim_path),
            status_root=str(roots["status"]),
            reason_code="INJECTED_PREDECESSOR_FAILURE",
        )
        assert failed["status"] == "FAILED_TERMINAL"
        assert failed["retry_allowed"] is False
        assert failed["resume_allowed"] is False
        with pytest.raises(runner.FactorV3FormalMaterializerV2RunnerError):
            runner._claim_disposable_materialization_attempt_v2_once(**claim_kwargs)
        assert not roots["publication"].exists()
        return

    if case in {"cli-retry", "cli-resume"}:
        forbidden_flag = "--retry" if case == "cli-retry" else "--resume"
        with pytest.raises(SystemExit):
            runner.main(["run", forbidden_flag])
        return

    safety_payload = {field: False for field in contract.SAFETY_FALSE_FIELDS}
    if case == "safety-one-true":
        safety_payload["model_training_started"] = True
        with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
            producer.validate_materialization_safety_state_v2(
                payload=safety_payload
            )
        return

    safety_result = producer.validate_materialization_safety_state_v2(
        payload=safety_payload
    )
    assert all(safety_result[field] is False for field in contract.SAFETY_FALSE_FIELDS)

