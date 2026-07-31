from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest
from app import factor_v3_formal_bootstrap_renderer as bootstrap_renderer
from app import factor_v3_formal_control_contract as contract
from app import factor_v3_formal_supervisor_control as control
from app import factor_v3_formal_trusted_supervisor as supervisor
from tests.test_factor_v3_formal_bootstrap_authorization import (
    _authorized_fixture,
    _execution_test_key,
    _write_completion_authorization,
)


def _bootstrap_publication(
    tmp_path: Path,
    *,
    action: str = "verify",
) -> tuple[Path, Path, bytes]:
    source_app = Path(__file__).resolve().parents[1] / "app"
    reviewed_sources = {
        f"app/{name}": (source_app / name).read_bytes()
        for name in (
            "factor_v3_formal_control_contract.py",
            "factor_v3_formal_supervisor_loader_runtime.py",
            "factor_v3_formal_trusted_supervisor.py",
        )
    }
    (
        config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(
        tmp_path,
        action=action,
        extra_reviewed_sources=reviewed_sources,
    )
    repo_root = Path(str(config["repo_root"]))
    assert subprocess.run(
        [
            str(config["git_executable_path"]),
            "-C",
            str(repo_root),
            "status",
            "--porcelain",
        ],
        check=True,
        capture_output=True,
    ).stdout == b""
    assert (
        subprocess.run(
            [
                str(config["git_executable_path"]),
                "-C",
                str(repo_root),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="ascii",
        ).stdout.strip()
        == config["expected_commit"]
    )
    exclude_raw = (repo_root / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    )
    assert all(relative_path not in exclude_raw for relative_path in reviewed_sources)
    completion_payload = (
        bootstrap_renderer._plan_factor_v3_formal_bootstrap_publication_with_test_trust(
            authorization_path=authorization_path,
            trusted_public_key_spki_der=trusted_public_der,
        )
    )
    completion_authorization_path = _write_completion_authorization(
        tmp_path,
        completion_payload,
    )
    bootstrap_publication = bootstrap_renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        completion_authorization_path=completion_authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    return (
        authorization_path,
        Path(bootstrap_publication["completion_marker_path"]),
        trusted_public_der,
    )


def test_supervisor_publication_is_deterministic_and_revalidated_from_sources(
    tmp_path: Path,
) -> None:
    authorization_path, completion_path, trusted_public_der = _bootstrap_publication(tmp_path)

    first = control._publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    second = control._publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    validated = control._validate_publication_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        publication_receipt_path=first["supervisor_publication_receipt_path"],
        trusted_public_key_spki_der=trusted_public_der,
    )

    assert first == second
    assert validated["executed_supervisor_path"] == first["executed_supervisor_path"]
    assert validated["executed_supervisor_sha256"] == first["executed_supervisor_sha256"]
    assert validated["supervisor_loader_sha256"] == first["supervisor_loader_sha256"]
    assert Path(validated["executed_supervisor_path"]).read_bytes()
    assert Path(validated["supervisor_publication_receipt_path"]).read_bytes()


@pytest.mark.parametrize("action", ("verify", "preflight"))
def test_launch_v2_binds_actual_artifact_receipt_and_external_loader(
    tmp_path: Path,
    action: str,
) -> None:
    authorization_path, completion_path, trusted_public_der = _bootstrap_publication(
        tmp_path,
        action=action,
    )
    publication_result = control._publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    publication = control._validate_publication_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        publication_receipt_path=publication_result["supervisor_publication_receipt_path"],
        trusted_public_key_spki_der=trusted_public_der,
    )
    ledger_root = (tmp_path / "supervisor-ledger").resolve()
    ledger_root.mkdir()
    now = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)

    payload = control._launch_payload(
        publication=publication,
        action=action,
        credential_path=None,
        execution_ledger_root=ledger_root,
        now_utc=now,
        resume_authorization_path=None,
        resume_status_path=None,
    )
    validated = supervisor._validate_launch_payload(
        payload,
        pins=publication["pins"],
        now_utc=now,
        trusted_executed_supervisor_path=publication["executed_supervisor_path"],
        trusted_executed_supervisor_sha256=publication["executed_supervisor_sha256"],
        trusted_supervisor_loader_path=publication["supervisor_loader_path"],
        trusted_supervisor_loader_sha256=publication["supervisor_loader_sha256"],
    )

    assert validated["schema"] == supervisor.LAUNCH_AUTHORIZATION_SCHEMA
    assert (
        validated["supervisor_publication_receipt_sha256"]
        == publication["supervisor_publication_receipt_sha256"]
    )
    assert validated["credential_path"] is None
    assert validated["credential_slot_id"] is None
    assert validated["worker_action"] == action


def test_signed_verify_runs_only_through_external_exact_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization_path, completion_path, trusted_public_der = _bootstrap_publication(tmp_path)
    publication = control._publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    ledger_root = (tmp_path / "supervisor-ledger").resolve()
    ledger_root.mkdir()
    private_key_path, _public_der = _execution_test_key(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    launch_authorization = (
        control._build_factor_v3_formal_supervisor_launch_authorization_with_trust(
            authorization_path=authorization_path,
            completion_marker_path=completion_path,
            publication_receipt_path=publication["supervisor_publication_receipt_path"],
            private_key_path=private_key_path,
            trusted_public_key_spki_der=trusted_public_der,
            verify_reviewed_sources=True,
            action="verify",
            execution_ledger_root=ledger_root,
            now_utc=now,
        )
    )

    real_run = subprocess.run
    observed: list[subprocess.CompletedProcess[bytes]] = []

    def capture(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        completed = real_run(*args, **kwargs)
        observed.append(completed)
        return completed

    monkeypatch.setattr(control.subprocess, "run", capture)
    try:
        result = control._launch_factor_v3_formal_supervisor_with_trust(
            authorization_path=authorization_path,
            completion_marker_path=completion_path,
            publication_receipt_path=publication["supervisor_publication_receipt_path"],
            launch_authorization_path=launch_authorization["launch_authorization_path"],
            trusted_public_key_spki_der=trusted_public_der,
            environment_snapshot={
                name: os.environ[name] for name in contract.PUBLIC_ENVIRONMENT if name in os.environ
            },
        )
    except control.FormalSupervisorControlError as exc:
        stderr = observed[-1].stderr.decode("utf-8", errors="replace")
        raise AssertionError(stderr) from exc

    assert result["status"] == "completed"


def test_external_loader_run_claims_before_credential_and_resumes_without_leak(
    tmp_path: Path,
) -> None:
    authorization_path, completion_path, trusted_public_der = _bootstrap_publication(
        tmp_path,
        action="run",
    )
    publication = control._publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    ledger_root = (tmp_path / "supervisor-ledger").resolve()
    ledger_root.mkdir()
    credential_path = (tmp_path / "points-primary.token").resolve()
    private_key_path, _public_der = _execution_test_key(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    launch_authorization = (
        control._build_factor_v3_formal_supervisor_launch_authorization_with_trust(
            authorization_path=authorization_path,
            completion_marker_path=completion_path,
            publication_receipt_path=publication[
                "supervisor_publication_receipt_path"
            ],
            private_key_path=private_key_path,
            trusted_public_key_spki_der=trusted_public_der,
            verify_reviewed_sources=True,
            action="run",
            credential_path=credential_path,
            execution_ledger_root=ledger_root,
            now_utc=now,
        )
    )
    launch_path = Path(str(launch_authorization["launch_authorization_path"]))
    launch_sha256 = str(launch_authorization["launch_authorization_sha256"])

    with pytest.raises(
        control.FormalSupervisorControlError,
        match="trusted supervisor execution rejected",
    ):
        control._launch_factor_v3_formal_supervisor_with_trust(
            authorization_path=authorization_path,
            completion_marker_path=completion_path,
            publication_receipt_path=publication[
                "supervisor_publication_receipt_path"
            ],
            launch_authorization_path=launch_path,
            trusted_public_key_spki_der=trusted_public_der,
            environment_snapshot={
                name: os.environ[name]
                for name in contract.PUBLIC_ENVIRONMENT
                if name in os.environ
            },
        )

    claim_path = supervisor.claim_path_for_authorization(ledger_root, launch_sha256)
    launch_payload = json.loads(launch_path.read_bytes())["payload"]
    nonce_replay_sha256 = contract.sha256_bytes(
        contract.canonical_bytes(
            {
                "authorization_nonce_sha256": launch_payload[
                    "authorization_nonce_sha256"
                ],
                "replay_scope": launch_payload["replay_scope"],
            }
        )
    )
    for category, identity in (
        ("bootstrap_authorizations", launch_payload["bootstrap_execution_authorization_sha256"]),
        ("authorization_ids", launch_payload["authorization_id_sha256"]),
        ("authorization_nonces", nonce_replay_sha256),
    ):
        assert (
            ledger_root
            / category
            / "sha256"
            / str(identity)[:2]
            / f"{identity}.json"
        ).is_file()
    assert claim_path.is_file()
    assert not supervisor.completed_path_for_authorization(ledger_root, launch_sha256).exists()

    credential_value = "test-only-points-primary-never-log"
    credential_path.write_text(credential_value, encoding="utf-8", newline="\n")
    resume_now = datetime.now(timezone.utc).replace(microsecond=0)
    resume_authorization = (
        control._build_factor_v3_formal_supervisor_launch_authorization_with_trust(
            authorization_path=authorization_path,
            completion_marker_path=completion_path,
            publication_receipt_path=publication[
                "supervisor_publication_receipt_path"
            ],
            private_key_path=private_key_path,
            trusted_public_key_spki_der=trusted_public_der,
            verify_reviewed_sources=True,
            action="resume",
            credential_path=credential_path,
            execution_ledger_root=ledger_root,
            resume_authorization_path=launch_path,
            resume_status_path=claim_path,
            now_utc=resume_now,
        )
    )
    result = control._launch_factor_v3_formal_supervisor_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        publication_receipt_path=publication["supervisor_publication_receipt_path"],
        launch_authorization_path=resume_authorization["launch_authorization_path"],
        trusted_public_key_spki_der=trusted_public_der,
        environment_snapshot={
            name: os.environ[name]
            for name in contract.PUBLIC_ENVIRONMENT
            if name in os.environ
        },
    )

    resume_sha256 = str(resume_authorization["launch_authorization_sha256"])
    original_outer = json.loads(launch_path.read_bytes())
    original_claim_raw = claim_path.read_bytes()
    original_claim = json.loads(original_claim_raw)
    resume_path = Path(str(resume_authorization["launch_authorization_path"]))
    resume_outer = json.loads(resume_path.read_bytes())
    resume_claim_path = supervisor.claim_path_for_authorization(
        ledger_root,
        resume_sha256,
    )
    resume_claim_raw = resume_claim_path.read_bytes()
    resume_claim = json.loads(resume_claim_raw)
    transition_path = (
        ledger_root
        / "resumed_authorizations"
        / "sha256"
        / launch_sha256[:2]
        / f"{launch_sha256}.json"
    )
    transition_raw = transition_path.read_bytes()
    transition = json.loads(transition_raw)
    completed = json.loads(
        supervisor.completed_path_for_authorization(
            ledger_root,
            resume_sha256,
        ).read_bytes()
    )
    assert result["status"] == "completed"
    assert original_claim["schema"] == "factor-v3-formal-supervisor-execution-claim/v2"
    assert original_claim["action"] == "run"
    assert original_claim["launch_authorization_schema"] == original_outer["payload"]["schema"]
    assert original_claim["launch_authorization_signature_sha256"] == contract.sha256_bytes(
        supervisor._decoded_signature(original_outer["signature_base64"])
    )
    assert resume_claim["schema"] == "factor-v3-formal-supervisor-execution-claim/v2"
    assert resume_claim["action"] == "resume"
    assert resume_claim["launch_authorization_schema"] == resume_outer["payload"]["schema"]
    assert resume_claim["launch_authorization_signature_sha256"] == contract.sha256_bytes(
        supervisor._decoded_signature(resume_outer["signature_base64"])
    )
    assert transition == {
        "original_action": "run",
        "original_authorization_id_sha256": original_claim["authorization_id_sha256"],
        "original_authorization_nonce_sha256": original_claim["authorization_nonce_sha256"],
        "original_bootstrap_execution_authorization_sha256": original_claim[
            "bootstrap_execution_authorization_sha256"
        ],
        "original_claim_sha256": contract.sha256_bytes(original_claim_raw),
        "original_launch_authorization_schema": original_claim[
            "launch_authorization_schema"
        ],
        "original_launch_authorization_sha256": launch_sha256,
        "original_launch_authorization_signature_sha256": original_claim[
            "launch_authorization_signature_sha256"
        ],
        "original_replay_scope": original_claim["replay_scope"],
        "resume_action": "resume",
        "resume_authorization_id_sha256": resume_claim["authorization_id_sha256"],
        "resume_authorization_nonce_sha256": resume_claim["authorization_nonce_sha256"],
        "resume_bootstrap_execution_authorization_sha256": resume_claim[
            "bootstrap_execution_authorization_sha256"
        ],
        "resume_claim_sha256": contract.sha256_bytes(resume_claim_raw),
        "resume_launch_authorization_schema": resume_claim["launch_authorization_schema"],
        "resume_launch_authorization_sha256": resume_sha256,
        "resume_launch_authorization_signature_sha256": resume_claim[
            "launch_authorization_signature_sha256"
        ],
        "resume_replay_scope": resume_claim["replay_scope"],
        "schema": "factor-v3-formal-supervisor-resume-transition/v1",
        "status": "resumed",
    }
    assert resume_claim_path.is_file()
    assert supervisor.completed_path_for_authorization(
        ledger_root,
        resume_sha256,
    ).is_file()
    assert completed["claim_sha256"] == contract.sha256_bytes(resume_claim_raw)
    assert completed["launch_authorization_sha256"] == resume_sha256
    assert completed["resume_of_authorization_sha256"] == launch_sha256
    assert completed["resume_transition_sha256"] == contract.sha256_bytes(transition_raw)
    assert set(completed) == {
        "artifact_manifest_sha256",
        "claim_sha256",
        "launch_authorization_sha256",
        "resume_of_authorization_sha256",
        "resume_transition_sha256",
        "schema",
        "status",
        "worker_terminal_bytes",
        "worker_terminal_schema",
        "worker_terminal_sha256",
    }
    assert completed["schema"] == "factor-v3-formal-supervisor-execution-completed/v2"
    assert completed["status"] == "completed"
    assert credential_value not in json.dumps(result, sort_keys=True)
    for ledger_path in ledger_root.rglob("*.json"):
        assert credential_value.encode("utf-8") not in ledger_path.read_bytes()


def test_resume_signer_rejects_noncanonical_untrusted_and_chained_originals(
    tmp_path: Path,
) -> None:
    authorization_path, completion_path, trusted_public_der = _bootstrap_publication(
        tmp_path,
        action="run",
    )
    publication_result = control._publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    publication = control._validate_publication_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        publication_receipt_path=publication_result["supervisor_publication_receipt_path"],
        trusted_public_key_spki_der=trusted_public_der,
    )
    ledger_root = (tmp_path / "resume-negative-ledger").resolve()
    ledger_root.mkdir()
    private_key_path, _public_der = _execution_test_key(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    original = control._build_factor_v3_formal_supervisor_launch_authorization_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_path,
        publication_receipt_path=publication_result["supervisor_publication_receipt_path"],
        private_key_path=private_key_path,
        trusted_public_key_spki_der=trusted_public_der,
        verify_reviewed_sources=True,
        action="run",
        credential_path=(tmp_path / "missing.token").resolve(),
        execution_ledger_root=ledger_root,
        now_utc=now,
    )
    original_path = Path(str(original["launch_authorization_path"]))
    original_outer = json.loads(original_path.read_bytes())
    status_path = (tmp_path / "handwritten-claim.json").resolve()
    status_path.write_bytes(b"{}")
    bootstrap_root = Path(str(publication["config"]["bootstrap_output_root"]))

    def write_envelope(outer: dict[str, object], *, forced_digest: str | None = None) -> Path:
        raw = contract.canonical_bytes(outer)
        digest = forced_digest or contract.sha256_bytes(raw)
        path = (
            bootstrap_root
            / "launch_authorizations"
            / "sha256"
            / digest[:2]
            / f"{digest}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path.resolve()

    invalid_paths: list[Path] = [
        write_envelope({"payload": original_outer["payload"]}),
    ]
    signature_drift = dict(original_outer)
    signature_drift["signature_base64"] = "eA=="
    invalid_paths.append(write_envelope(signature_drift))
    v1_outer = json.loads(original_path.read_bytes())
    v1_outer["payload"]["schema"] = "factor-v3-formal-supervisor-launch-authorization/v1"
    v1_outer["signature_base64"] = base64.b64encode(
        control._openssl(
            ["dgst", "-sha256", "-sign", str(private_key_path)],
            input_raw=contract.canonical_bytes(v1_outer["payload"]),
        )
    ).decode("ascii")
    invalid_paths.append(write_envelope(v1_outer))
    verify_outer = json.loads(original_path.read_bytes())
    verify_outer["payload"].update(
        {
            "action": "verify",
            "credential_path": None,
            "credential_slot_id": None,
            "worker_action": "verify",
        }
    )
    verify_outer["signature_base64"] = base64.b64encode(
        control._openssl(
            ["dgst", "-sha256", "-sign", str(private_key_path)],
            input_raw=contract.canonical_bytes(verify_outer["payload"]),
        )
    ).decode("ascii")
    invalid_paths.append(write_envelope(verify_outer))
    chained_outer = json.loads(original_path.read_bytes())
    chained_outer["payload"].update(
        {
            "action": "resume",
            "resume_of_authorization_id_sha256": chained_outer["payload"][
                "authorization_id_sha256"
            ],
            "resume_of_authorization_nonce_sha256": chained_outer["payload"][
                "authorization_nonce_sha256"
            ],
            "resume_of_authorization_sha256": "1" * 64,
            "resume_of_bootstrap_execution_authorization_sha256": chained_outer[
                "payload"
            ]["bootstrap_execution_authorization_sha256"],
            "resume_of_replay_scope": chained_outer["payload"]["replay_scope"],
            "resume_status_path": str(status_path),
            "resume_status_sha256": contract.sha256_bytes(status_path.read_bytes()),
        }
    )
    chained_outer["signature_base64"] = base64.b64encode(
        control._openssl(
            ["dgst", "-sha256", "-sign", str(private_key_path)],
            input_raw=contract.canonical_bytes(chained_outer["payload"]),
        )
    ).decode("ascii")
    invalid_paths.append(write_envelope(chained_outer))
    invalid_paths.append(write_envelope(original_outer, forced_digest="0" * 64))

    for invalid_path in invalid_paths:
        with pytest.raises(
            control.FormalSupervisorControlError,
            match="resume launch rejected",
        ):
            control._build_factor_v3_formal_supervisor_launch_authorization_with_trust(
                authorization_path=authorization_path,
                completion_marker_path=completion_path,
                publication_receipt_path=publication_result[
                    "supervisor_publication_receipt_path"
                ],
                private_key_path=private_key_path,
                trusted_public_key_spki_der=trusted_public_der,
                verify_reviewed_sources=True,
                action="resume",
                credential_path=(tmp_path / "missing.token").resolve(),
                execution_ledger_root=ledger_root,
                resume_authorization_path=invalid_path,
                resume_status_path=status_path,
                now_utc=now,
            )
