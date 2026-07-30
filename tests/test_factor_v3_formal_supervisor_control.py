from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
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
) -> tuple[Path, Path, bytes]:
    (
        config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    source_app = Path(__file__).resolve().parents[1] / "app"
    reviewed_app = Path(str(config["repo_root"])) / "app"
    for name in (
        "factor_v3_formal_control_contract.py",
        "factor_v3_formal_supervisor_loader_runtime.py",
        "factor_v3_formal_trusted_supervisor.py",
    ):
        shutil.copyfile(source_app / name, reviewed_app / name)
    exclude_path = Path(str(config["repo_root"])) / ".git" / "info" / "exclude"
    with exclude_path.open("a", encoding="utf-8", newline="\n") as stream:
        for name in (
            "factor_v3_formal_control_contract.py",
            "factor_v3_formal_supervisor_loader_runtime.py",
            "factor_v3_formal_trusted_supervisor.py",
        ):
            stream.write(f"/app/{name}\n")
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


def test_launch_v2_binds_actual_artifact_receipt_and_external_loader(
    tmp_path: Path,
) -> None:
    authorization_path, completion_path, trusted_public_der = _bootstrap_publication(tmp_path)
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
        action="verify",
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
            verify_reviewed_sources=False,
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
