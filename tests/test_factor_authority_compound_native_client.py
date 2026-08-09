from __future__ import annotations

# ruff: noqa: E731

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

from app import factor_authority_compound_native_client as native_client


NATIVE_PURPOSES = (
    "parent_producer",
    "parent_verifier",
    "evaluator_producer",
    "evaluator_verifier",
    "native_run",
    "native_verify",
    "native_terminal",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compile_disposable_broker(output: Path) -> Path:
    compiler = shutil.which("gcc")
    assert compiler is not None
    source_root = Path(__file__).parent / "native"
    completed = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-municode",
            str(source_root / "factor_authority_compound_native_abi.c"),
            str(source_root / "factor_authority_compound_native_harness.c"),
            "-o",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return output


def _write_fixture_manifest(path: Path, executable: Path) -> str:
    payload = {
        "schema": native_client.COMPOUND_NATIVE_MANIFEST_SCHEMA,
        "broker_file_sha256": _sha256_file(executable),
        "handoff_ready": False,
        "disposable_compiled_fixture": True,
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_production_native_registry_is_closed_and_opaque_types_are_client_owned() -> None:
    assert native_client.REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256 is None
    assert native_client.REGISTERED_COMPOUND_NATIVE_BROKER_PATH is None
    assert native_client.REGISTERED_COMPOUND_NATIVE_BROKER_FILE_SHA256 is None
    assert native_client.HANDOFF_READY == 0
    for capability_type in (
        native_client.HeldCompoundNativeSessionSet,
        native_client.HeldDeploymentPolicyAuthority,
        native_client.HeldCompoundRunSpec,
        native_client.HeldCompoundRootLease,
        native_client.HeldRegisteredCas,
        native_client.HeldRoleProduction,
        native_client.HeldNativeCompletion,
    ):
        with pytest.raises(TypeError, match="native-client owned"):
            capability_type()
    with pytest.raises(
        native_client.FactorAuthorityCompoundNativeClientError,
        match="manifest|broker|registration",
    ):
        native_client.open_registered_compound_native_session_set()


@pytest.mark.parametrize(
    "entrypoint",
    ["run-spec", "root-lease", "role", "completion", "postverify", "forged", "close"],
)
def test_plain_mapping_or_python_forgery_never_upgrades_to_native_authority(
    entrypoint: str,
) -> None:
    plain: dict[str, Any] = {
        "schema": native_client.COMPOUND_NATIVE_COMPLETION_SCHEMA,
        "process_id": 1,
        "job_identity_sha256": "0" * 64,
        "broker_signature_sha256": "1" * 64,
    }
    if entrypoint == "run-spec":
        call = lambda: native_client.postverify_compound_run_spec(
            session_set=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
        )
    elif entrypoint == "root-lease":
        call = lambda: native_client.postverify_compound_root_lease(
            session_set=plain,  # type: ignore[arg-type]
            root_lease=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
        )
    elif entrypoint == "role":
        call = lambda: native_client.postverify_role_production(
            session_set=plain,  # type: ignore[arg-type]
            production=plain,  # type: ignore[arg-type]
            expected_role="parent_producer",
            run_spec=plain,  # type: ignore[arg-type]
        )
    elif entrypoint == "completion":
        call = lambda: native_client.acquire_native_run_completion(
            session_set=plain,  # type: ignore[arg-type]
            run_spec=plain,  # type: ignore[arg-type]
            root_lease=plain,  # type: ignore[arg-type]
            parent_producer=plain,  # type: ignore[arg-type]
            evaluator_producer=plain,  # type: ignore[arg-type]
        )
    elif entrypoint == "postverify":
        call = lambda: native_client.postverify_native_completion(
            session_set=plain,  # type: ignore[arg-type]
            completion=plain,  # type: ignore[arg-type]
            expected_phase="run",
            run_spec=plain,  # type: ignore[arg-type]
            root_lease=plain,  # type: ignore[arg-type]
        )
    elif entrypoint == "forged":
        forged = object.__new__(native_client.HeldNativeCompletion)
        call = lambda: native_client.postverify_native_completion(
            session_set=plain,  # type: ignore[arg-type]
            completion=forged,
            expected_phase="run",
            run_spec=plain,  # type: ignore[arg-type]
            root_lease=plain,  # type: ignore[arg-type]
        )
    else:
        call = lambda: native_client.close_compound_native_session_set(
            plain,  # type: ignore[arg-type]
        )
    with pytest.raises(
        native_client.FactorAuthorityCompoundNativeClientError,
        match="opaque|native|session|capability",
    ):
        call()


def test_compiled_disposable_broker_proves_seven_distinct_process_job_roots(
    tmp_path: Path,
) -> None:
    executable = _compile_disposable_broker(tmp_path / "compound-native-red.exe")
    manifest = tmp_path / "disposable-native-manifest.json"
    manifest_raw_sha256 = _write_fixture_manifest(manifest, executable)
    session_set = native_client._open_disposable_test_compound_native_session_set(
        executable=executable,
        expected_executable_sha256=_sha256_file(executable),
        fixture_manifest_authority_path=manifest,
        expected_fixture_manifest_raw_sha256=manifest_raw_sha256,
    )
    try:
        evidence = native_client.postverify_distinct_native_jobs(
            session_set=session_set
        )
        assert tuple(evidence["purposes"]) == NATIVE_PURPOSES
        assert len(set(evidence["process_ids"])) == len(NATIVE_PURPOSES)
        assert len(set(evidence["job_identity_sha256"])) == len(NATIVE_PURPOSES)
        assert all(evidence["process_live"])
        assert all(evidence["process_handles_retained"])
        assert all(evidence["job_handles_retained"])
    finally:
        closed = native_client.close_compound_native_session_set(session_set)
    assert closed["success_flush_postverified"] is False
    assert tuple(closed["reaped_purposes"]) == NATIVE_PURPOSES
    assert all(closed["process_reaped"])
    assert all(closed["job_handles_closed"])
