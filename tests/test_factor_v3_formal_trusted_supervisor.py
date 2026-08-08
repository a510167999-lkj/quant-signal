from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
import ctypes
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from app import factor_v3_formal_trusted_supervisor as supervisor
from app import factor_v3_formal_control_contract as contract
from tests.test_factor_v3_formal_bootstrap_renderer import _GIT, _OPENSSL


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _ledger_staging_paths(parent: Path) -> tuple[Path, ...]:
    return tuple(
        candidate
        for candidate in parent.iterdir()
        if supervisor._LEDGER_STAGING_NAME_RE.fullmatch(candidate.name) is not None
    )


def _write_exact_ledger_candidates(
    parent: Path,
    count: int,
    *,
    raw: bytes,
) -> list[Path]:
    candidates: list[Path] = []
    for index in range(count):
        candidate = (
            parent
            / f"{supervisor._LEDGER_STAGING_PREFIX}{index:032x}.tmp"
        )
        candidate.write_bytes(raw)
        candidates.append(candidate)
    return candidates


def _ledger_nonlease_files(parent: Path) -> tuple[Path, ...]:
    return tuple(
        candidate
        for candidate in parent.iterdir()
        if candidate.is_file() and candidate.name != supervisor._LEDGER_LEASE_NAME
    )


def _assert_persistent_ledger_lease(parent: Path) -> None:
    lease = parent / supervisor._LEDGER_LEASE_NAME
    opened = lease.lstat()
    assert stat.S_ISREG(opened.st_mode)
    assert int(getattr(opened, "st_file_attributes", 0)) & 0x400 == 0
    assert int(getattr(opened, "st_nlink", 1)) == 1


def _cas_write(root: Path, category: str, raw: bytes, suffix: str) -> tuple[Path, str]:
    digest = _sha256(raw)
    directory = root / category / "sha256" / digest[:2]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}{suffix}"
    path.write_bytes(raw)
    return path.resolve(), digest


def _rsa_key(root: Path, name: str) -> tuple[Path, Path, bytes]:
    root.mkdir(parents=True, exist_ok=True)
    private_key = root / f"{name}-private.pem"
    public_pem = root / f"{name}-public.pem"
    subprocess.run(
        [
            str(_OPENSSL),
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:3072",
            "-out",
            str(private_key),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            str(_OPENSSL),
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-out",
            str(public_pem),
        ],
        check=True,
        capture_output=True,
    )
    public_der = subprocess.run(
        [
            str(_OPENSSL),
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-outform",
            "DER",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return private_key.resolve(), public_pem.resolve(), public_der


def _sign(private_key: Path, raw: bytes, root: Path) -> bytes:
    payload_path = root / "payload.bin"
    signature_path = root / "signature.bin"
    payload_path.write_bytes(raw)
    subprocess.run(
        [
            str(_OPENSSL),
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(signature_path),
            str(payload_path),
        ],
        check=True,
        capture_output=True,
    )
    return signature_path.read_bytes()


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        [str(_GIT), "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _worker_source(
    *,
    artifact_path: Path,
    artifact_raw: bytes,
    bootstrap_execution_authorization_sha256: str,
    authorization_nonce_sha256: str,
    stdlib_inventory_root_sha256: str,
    worker_action: str,
    stderr_text: str = "",
    prefix: str = "",
    wrong_launch_sha: bool = False,
    fail: bool = False,
) -> bytes:
    artifact_entry = {
        "bytes": len(artifact_raw),
        "path": str(artifact_path),
        "sha256": _sha256(artifact_raw),
    }
    return (
        "from __future__ import annotations\n"
        "import hashlib\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"artifact_path = Path({str(artifact_path)!r})\n"
        f"artifact_raw = {artifact_raw!r}\n"
        "artifact_path.write_bytes(artifact_raw)\n"
        f"stderr_text = {stderr_text!r}\n"
        "if stderr_text:\n"
        "    sys.stderr.write(stderr_text)\n"
        f"if {fail!r} and os.environ['FACTOR_V3_FORMAL_LAUNCH_ACTION'] != 'resume':\n"
        "    raise SystemExit(9)\n"
        "launch_sha = os.environ['FACTOR_V3_FORMAL_LAUNCH_AUTHORIZATION_SHA256']\n"
        f"if {wrong_launch_sha!r}:\n"
        "    launch_sha = '0' * 64\n"
        "frame = {\n"
        f"    'artifacts': {[artifact_entry]!r},\n"
        f"    'authorization_nonce_sha256': {authorization_nonce_sha256!r},\n"
        f"    'bootstrap_execution_authorization_sha256': "
        f"{bootstrap_execution_authorization_sha256!r},\n"
        "    'launch_action': os.environ['FACTOR_V3_FORMAL_LAUNCH_ACTION'],\n"
        "    'launch_authorization_sha256': launch_sha,\n"
        "    'result': {'secret_present': bool(os.environ.get('JIAOCH_TOKEN')), "
        "'status': 'verified'},\n"
        "    'schema': 'factor-v3-formal-bootstrap-worker-terminal/v2',\n"
        "    'status': 'completed',\n"
        f"    'stdlib_inventory_root_sha256': {stdlib_inventory_root_sha256!r},\n"
        f"    'worker_action': {worker_action!r},\n"
        "}\n"
        "raw = json.dumps(frame, ensure_ascii=False, sort_keys=True, "
        "separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\\n'\n"
        f"sys.stdout.buffer.write({prefix.encode()!r} + raw)\n"
    ).encode("utf-8")


def _fixture(
    tmp_path: Path,
    *,
    action: str = "run",
    worker_action: str = "run",
    stderr_text: str = "",
    prefix: str = "",
    wrong_launch_sha: bool = False,
    fail: bool = False,
    review_key_same_as_execution: bool = False,
) -> tuple[
    supervisor._SupervisorPins,
    dict[str, Any],
    Path,
    dict[str, str],
    list[bytes],
]:
    execution_private, execution_public_pem, execution_public_der = _rsa_key(
        tmp_path / "execution-key",
        "execution",
    )
    _review_private, _review_public_pem, review_public_der = _rsa_key(
        tmp_path / "review-key",
        "review",
    )
    if review_key_same_as_execution:
        review_public_der = execution_public_der

    repo_root = Path(__file__).resolve().parents[1]
    source_path = repo_root / "app" / "factor_v3_formal_trusted_supervisor.py"
    python_path = Path(sys.executable).resolve()
    base_python_path = Path(sys._base_executable).resolve()
    git_path = Path(_GIT).resolve()
    del execution_public_pem
    supervisor_expected_commit = _git("rev-parse", "HEAD", cwd=repo_root)
    supervisor_source_sha256 = _file_sha256(source_path)

    bootstrap_output_root = (tmp_path / "bootstrap-output").resolve()
    formal_input_root = (tmp_path / "formal-input").resolve()
    formal_output_root = (tmp_path / "formal-output").resolve()
    run_root = (tmp_path / "run-root").resolve()
    ledger_root = (tmp_path / "execution-ledger").resolve()
    for directory in (
        bootstrap_output_root,
        formal_input_root,
        formal_output_root,
        run_root,
        ledger_root,
    ):
        directory.mkdir()

    run_spec_path = formal_input_root / "run-spec.json"
    run_spec_raw = _canonical_bytes({"schema": "factor-v3-test-run-spec/v1"})
    run_spec_path.write_bytes(run_spec_raw)
    artifact_path = formal_output_root / "terminal-artifact.json"
    artifact_raw = _canonical_bytes({"status": "verified"})
    authorization_id_sha256 = _sha256(b"supervisor-test-authorization-id")
    authorization_nonce_sha256 = _sha256(b"supervisor-test-nonce")
    replay_scope = "factor-v3-formal-bootstrap-execution/v1"
    fake_python_root = (tmp_path / "fake-python").resolve()
    stdlib_root = fake_python_root / "Lib"
    platstdlib_root = fake_python_root / "DLLs"
    pycache_prefix = (tmp_path / "signed-empty-pycache").resolve()
    for directory in (stdlib_root, platstdlib_root):
        directory.mkdir(parents=True)
    pycache_prefix.write_bytes(b"factor-v3-pycache-blocker/v1\n")
    stdlib_path = (stdlib_root / "frozen_stdlib_fixture.py").resolve()
    stdlib_path.write_bytes(b"STDLIB_FIXTURE = True\n")
    stdlib_entry = {
        "bytes": stdlib_path.stat().st_size,
        "is_package": False,
        "kind": "source",
        "module": "frozen_stdlib_fixture",
        "path": str(stdlib_path),
        "relative_path": "frozen_stdlib_fixture.py",
        "root": str(stdlib_root),
        "sha256": _file_sha256(stdlib_path),
    }
    stdlib_policy = contract.canonical_stdlib_policy(
        roots=[
            {"path": str(platstdlib_root), "role": "platstdlib"},
            {"path": str(stdlib_root), "role": "stdlib"},
        ],
        entries=[stdlib_entry],
        absent_paths=[str(fake_python_root / "python311.zip")],
        pycache_prefix=str(pycache_prefix),
    )
    stdlib_inventory_root_sha256 = contract.stdlib_policy_root_sha256(stdlib_policy)
    stdlib_policy_raw = _canonical_bytes(stdlib_policy)
    stdlib_policy_path, stdlib_policy_sha256 = _cas_write(
        bootstrap_output_root,
        "stdlib_policies",
        stdlib_policy_raw,
        ".json",
    )
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    execution_key_sha256 = _sha256(execution_public_der)
    bootstrap_authorization_payload = {
        "action": worker_action,
        "authorization_id_sha256": authorization_id_sha256,
        "authorization_nonce_sha256": authorization_nonce_sha256,
        "base_python_executable_path": str(base_python_path),
        "base_python_executable_sha256": _file_sha256(base_python_path),
        "bootstrap_claim_path": str(tmp_path / "bootstrap-claim.json"),
        "bootstrap_claim_sha256": _sha256(b"bootstrap-claim"),
        "bootstrap_output_root": str(bootstrap_output_root),
        "builder_relative_path": "scripts/test_builder.py",
        "builder_sha256": _sha256(b"builder"),
        "control_contract_descriptor_sha256": (contract.control_contract_descriptor_sha256()),
        "expected_branch": "codex/test",
        "expected_commit": supervisor_expected_commit,
        "execution_authorization_key_id": f"sha256:{execution_key_sha256}",
        "execution_authorization_key_role": ("factor-v3-bootstrap-execution-authorization"),
        "expires_at_utc": (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
        "feature_attestation_sha256": _sha256(b"feature-attestation"),
        "formal_input_root_path": str(formal_input_root),
        "formal_input_root_sha256": _sha256(b"formal-input-root"),
        "formal_output_root": str(formal_output_root),
        "formal_runner_sha256": _sha256(b"formal-runner"),
        "git_executable_path": str(git_path),
        "git_executable_sha256": _file_sha256(git_path),
        "issued_at_utc": now.isoformat(timespec="seconds"),
        "not_before_utc": now.isoformat(timespec="seconds"),
        "project_id": "quant-signal-lkj",
        "python_executable_path": str(python_path),
        "python_executable_sha256": _file_sha256(python_path),
        "repo_root": str(repo_root),
        "replay_scope": replay_scope,
        "review_payload_sha256": _sha256(b"review-payload"),
        "review_protocol_sha256": _sha256(b"review-protocol"),
        "review_public_key_spki_der_base64": base64.b64encode(review_public_der).decode("ascii"),
        "review_public_key_spki_sha256": _sha256(review_public_der),
        "review_receipt_path": str(tmp_path / "review-receipt.json"),
        "review_receipt_sha256": _sha256(b"review-receipt"),
        "run_root": str(run_root),
        "run_spec_path": str(run_spec_path),
        "run_spec_sha256": _sha256(run_spec_raw),
        "runtime_template_sha256": _sha256(b"runtime-template"),
        "schema": "factor-v3-formal-bootstrap-execution-authorization/v2",
        "shim_relative_path": "scripts/test_shim.py",
        "shim_sha256": _sha256(b"shim"),
        "source_manifest": [],
        "source_root_sha256": _sha256(b"source-root"),
        "stdlib_policy": stdlib_policy,
        "stdlib_policy_root_sha256": stdlib_inventory_root_sha256,
        "supervisor_protocol": contract.worker_protocol_descriptor(),
    }
    bootstrap_authorization_raw = _canonical_bytes(
        {
            "payload": bootstrap_authorization_payload,
            "signature_base64": base64.b64encode(
                _sign(
                    execution_private,
                    _canonical_bytes(bootstrap_authorization_payload),
                    tmp_path / "execution-key",
                )
            ).decode("ascii"),
        }
    )
    (
        bootstrap_execution_authorization_path,
        bootstrap_execution_authorization_sha256,
    ) = _cas_write(
        tmp_path,
        "execution-authorizations",
        bootstrap_authorization_raw,
        ".json",
    )
    worker_raw = _worker_source(
        artifact_path=artifact_path,
        artifact_raw=artifact_raw,
        bootstrap_execution_authorization_sha256=bootstrap_execution_authorization_sha256,
        authorization_nonce_sha256=authorization_nonce_sha256,
        stdlib_inventory_root_sha256=stdlib_inventory_root_sha256,
        worker_action=worker_action,
        stderr_text=stderr_text,
        prefix=prefix,
        wrong_launch_sha=wrong_launch_sha,
        fail=fail,
    )
    worker_path, worker_sha256 = _cas_write(
        bootstrap_output_root,
        "bootstraps",
        worker_raw,
        ".py",
    )
    publication_payload = {
        "action": worker_action,
        "bootstrap_bytes": len(worker_raw),
        "bootstrap_relative_path": worker_path.relative_to(bootstrap_output_root).as_posix(),
        "bootstrap_sha256": worker_sha256,
        "execution_authorization_sha256": bootstrap_execution_authorization_sha256,
        "runtime_template_sha256": _sha256(b"runtime-template"),
        "schema": "factor-v3-formal-bootstrap-publication-receipt/v1",
    }
    publication_path, publication_sha256 = _cas_write(
        bootstrap_output_root,
        "publication_receipts",
        _canonical_bytes(publication_payload),
        ".json",
    )
    completion_payload = {
        "action": worker_action,
        "authorization_id_sha256": authorization_id_sha256,
        "authorization_nonce_sha256": authorization_nonce_sha256,
        "bootstrap_bytes": len(worker_raw),
        "bootstrap_relative_path": worker_path.relative_to(bootstrap_output_root).as_posix(),
        "bootstrap_sha256": worker_sha256,
        "bootstrap_output_root": str(bootstrap_output_root),
        "control_contract_descriptor_sha256": (contract.control_contract_descriptor_sha256()),
        "execution_authorization_sha256": bootstrap_execution_authorization_sha256,
        "receipt_bytes": publication_path.stat().st_size,
        "receipt_relative_path": publication_path.relative_to(bootstrap_output_root).as_posix(),
        "receipt_sha256": publication_sha256,
        "runtime_template_sha256": publication_payload["runtime_template_sha256"],
        "schema": contract.PUBLICATION_COMPLETION_SCHEMA,
        "status": "completed",
        "stdlib_inventory_root_sha256": stdlib_inventory_root_sha256,
        "stdlib_policy_bytes": len(stdlib_policy_raw),
        "stdlib_policy_relative_path": stdlib_policy_path.relative_to(
            bootstrap_output_root
        ).as_posix(),
        "stdlib_policy_sha256": stdlib_policy_sha256,
    }
    completion_raw = _canonical_bytes(
        {
            "payload": completion_payload,
            "signature_base64": base64.b64encode(
                _sign(
                    execution_private,
                    _canonical_bytes(completion_payload),
                    tmp_path / "execution-key",
                )
            ).decode("ascii"),
        }
    )
    completion_path, completion_sha256 = _cas_write(
        bootstrap_output_root,
        "completion_markers",
        completion_raw,
        ".json",
    )
    executed_supervisor_raw = source_path.read_bytes()
    executed_supervisor_path, executed_supervisor_sha256 = _cas_write(
        bootstrap_output_root,
        "supervisors",
        executed_supervisor_raw,
        ".py",
    )
    supervisor_loader_raw = b"fixture-supervisor-loader"
    supervisor_loader_path, supervisor_loader_sha256 = _cas_write(
        bootstrap_output_root,
        "supervisor_loaders",
        supervisor_loader_raw,
        ".py",
    )
    supervisor_publication_receipt = {
        "bootstrap_completion_marker_sha256": completion_sha256,
        "bootstrap_execution_authorization_sha256": (bootstrap_execution_authorization_sha256),
        "control_contract_source_sha256": supervisor._CONTROL_CONTRACT_SOURCE_SHA256,
        "executed_supervisor_bytes": len(executed_supervisor_raw),
        "executed_supervisor_relative_path": executed_supervisor_path.relative_to(
            bootstrap_output_root
        ).as_posix(),
        "executed_supervisor_sha256": executed_supervisor_sha256,
        "reviewed_commit": supervisor_expected_commit,
        "schema": supervisor.SUPERVISOR_PUBLICATION_RECEIPT_SCHEMA,
        "stdlib_inventory_root_sha256": stdlib_inventory_root_sha256,
        "supervisor_loader_bytes": len(supervisor_loader_raw),
        "supervisor_loader_relative_path": supervisor_loader_path.relative_to(
            bootstrap_output_root
        ).as_posix(),
        "supervisor_loader_sha256": supervisor_loader_sha256,
        "supervisor_source_sha256": supervisor_source_sha256,
    }
    (
        supervisor_publication_receipt_path,
        supervisor_publication_receipt_sha256,
    ) = _cas_write(
        bootstrap_output_root,
        "supervisor_publication_receipts",
        _canonical_bytes(supervisor_publication_receipt),
        ".json",
    )
    credential_path = (tmp_path / "points-primary.token").resolve()
    credential_path.write_text(
        "fixture-secret-must-never-be-logged",
        encoding="utf-8",
    )
    pins = supervisor._SupervisorPins(
        base_python_executable_path=str(base_python_path),
        base_python_executable_sha256=_file_sha256(base_python_path),
        bootstrap_completion_marker_path=str(completion_path),
        bootstrap_completion_marker_sha256=completion_sha256,
        bootstrap_completion_schema=contract.PUBLICATION_COMPLETION_SCHEMA,
        control_contract_source_sha256=supervisor._CONTROL_CONTRACT_SOURCE_SHA256,
        execution_public_key_spki_der_base64=base64.b64encode(execution_public_der).decode("ascii"),
        execution_public_key_spki_sha256=_sha256(execution_public_der),
        git_executable_path=str(git_path),
        git_executable_sha256=_file_sha256(git_path),
        python_executable_path=str(python_path),
        python_executable_sha256=_file_sha256(python_path),
        repo_root=str(repo_root),
        supervisor_expected_commit=supervisor_expected_commit,
        supervisor_source_relative_path="app/factor_v3_formal_trusted_supervisor.py",
        supervisor_source_sha256=supervisor_source_sha256,
        worker_protocol=contract.WORKER_PROTOCOL,
        worker_terminal_schema=contract.WORKER_TERMINAL_SCHEMA,
    )
    payload = {
        "action": action,
        "authorization_id_sha256": authorization_id_sha256,
        "authorization_nonce_sha256": authorization_nonce_sha256,
        "base_python_executable_path": str(base_python_path),
        "base_python_executable_sha256": pins.base_python_executable_sha256,
        "bootstrap_authorization_id_sha256": authorization_id_sha256,
        "bootstrap_authorization_nonce_sha256": authorization_nonce_sha256,
        "bootstrap_execution_authorization_sha256": (bootstrap_execution_authorization_sha256),
        "bootstrap_execution_authorization_path": str(bootstrap_execution_authorization_path),
        "bootstrap_output_root": str(bootstrap_output_root),
        "bootstrap_worker_path": str(worker_path),
        "bootstrap_worker_sha256": worker_sha256,
        "control_contract_descriptor_sha256": (contract.control_contract_descriptor_sha256()),
        "control_contract_source_sha256": pins.control_contract_source_sha256,
        "credential_path": (str(credential_path) if action in {"run", "resume"} else None),
        "credential_slot_id": ("points-primary" if action in {"run", "resume"} else None),
        "environment_policy": supervisor.WORKER_ENVIRONMENT_POLICY,
        "executed_supervisor_path": str(executed_supervisor_path),
        "executed_supervisor_sha256": executed_supervisor_sha256,
        "execution_key_id": f"sha256:{pins.execution_public_key_spki_sha256}",
        "execution_key_role": supervisor.BOOTSTRAP_EXECUTION_AUTHORIZATION_KEY_ROLE,
        "execution_ledger_root": str(ledger_root),
        "expires_at_utc": (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
        "formal_input_root_path": str(formal_input_root),
        "formal_input_root_sha256": _sha256(b"formal-input-root"),
        "formal_output_root": str(formal_output_root),
        "git_executable_path": str(git_path),
        "git_executable_sha256": pins.git_executable_sha256,
        "issued_at_utc": now.isoformat(timespec="seconds"),
        "not_before_utc": now.isoformat(timespec="seconds"),
        "project_id": "quant-signal-lkj",
        "publication_completion_marker_path": str(completion_path),
        "publication_completion_marker_sha256": completion_sha256,
        "publication_completion_schema": pins.bootstrap_completion_schema,
        "python_executable_path": str(python_path),
        "python_executable_sha256": pins.python_executable_sha256,
        "repo_root": str(repo_root),
        "replay_scope": replay_scope,
        "resume_of_authorization_id_sha256": None,
        "resume_of_authorization_sha256": None,
        "resume_of_authorization_nonce_sha256": None,
        "resume_of_action": None,
        "resume_of_bootstrap_execution_authorization_sha256": None,
        "resume_of_launch_authorization_schema": None,
        "resume_of_launch_authorization_signature_sha256": None,
        "resume_of_replay_scope": None,
        "resume_status_path": None,
        "resume_status_sha256": None,
        "review_public_key_spki_sha256": _sha256(review_public_der),
        "reviewed_commit": _git("rev-parse", "HEAD", cwd=repo_root),
        "run_root": str(run_root),
        "run_spec_path": str(run_spec_path),
        "run_spec_sha256": _sha256(run_spec_raw),
        "schema": supervisor.LAUNCH_AUTHORIZATION_SCHEMA,
        "source_root_sha256": _sha256(b"reviewed-source-root"),
        "stdlib_inventory_root_sha256": stdlib_inventory_root_sha256,
        "stdlib_policy_path": str(stdlib_policy_path),
        "stdlib_policy_sha256": stdlib_policy_sha256,
        "supervisor_expected_commit": pins.supervisor_expected_commit,
        "supervisor_loader_bytes": len(supervisor_loader_raw),
        "supervisor_loader_path": str(supervisor_loader_path),
        "supervisor_loader_sha256": supervisor_loader_sha256,
        "supervisor_publication_receipt_path": str(supervisor_publication_receipt_path),
        "supervisor_publication_receipt_sha256": (supervisor_publication_receipt_sha256),
        "supervisor_source_sha256": pins.supervisor_source_sha256,
        "worker_action": worker_action,
        "worker_argv": [
            str(python_path),
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={pycache_prefix}",
            str(worker_path),
        ],
        "worker_pycache_prefix": str(pycache_prefix),
        "worker_protocol": pins.worker_protocol,
        "worker_terminal_schema": pins.worker_terminal_schema,
        "worker_timeout_seconds": 60,
    }
    payload_raw = _canonical_bytes(payload)
    authorization_raw = _canonical_bytes(
        {
            "payload": payload,
            "signature_base64": base64.b64encode(
                _sign(execution_private, payload_raw, tmp_path / "execution-key")
            ).decode("ascii"),
        }
    )
    authorization_path, _authorization_sha256 = _cas_write(
        tmp_path,
        "launch_authorizations",
        authorization_raw,
        ".json",
    )
    environment = {
        name: os.environ[name]
        for name in supervisor.WORKER_ENVIRONMENT_POLICY["public_passthrough_names"]
        if name in os.environ
    }
    writes: list[bytes] = []
    return pins, payload, authorization_path, environment, writes


def _writer(writes: list[bytes]):
    def write(_descriptor: int, raw: bytes | memoryview) -> int:
        value = bytes(raw)
        writes.append(value)
        return len(value)

    return write


def _run_fixture(
    pins: supervisor._SupervisorPins,
    authorization_path: Path,
    environment: dict[str, str],
    writes: list[bytes],
) -> dict[str, Any]:
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    payload = authorization["payload"]
    return supervisor._supervise_with_pins(
        authorization_path=authorization_path,
        pins=pins,
        now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
        environment_snapshot=environment,
        output_writer=_writer(writes),
        trusted_executed_supervisor_path=payload["executed_supervisor_path"],
        trusted_executed_supervisor_sha256=payload["executed_supervisor_sha256"],
        trusted_supervisor_loader_path=payload["supervisor_loader_path"],
        trusted_supervisor_loader_sha256=payload["supervisor_loader_sha256"],
    )


def _rewrite_authorization(
    tmp_path: Path,
    payload: dict[str, Any],
    private_key: Path,
) -> Path:
    raw = _canonical_bytes(payload)
    outer = _canonical_bytes(
        {
            "payload": payload,
            "signature_base64": base64.b64encode(
                _sign(private_key, raw, private_key.parent)
            ).decode("ascii"),
        }
    )
    path, _digest = _cas_write(tmp_path, "launch_authorizations", outer, ".json")
    return path


def test_public_entrypoint_has_no_key_or_argv_parameter() -> None:
    parameters = inspect.signature(supervisor.supervise_factor_v3_formal_execution).parameters

    assert tuple(parameters) == ("authorization_path",)


def test_launch_reviewed_commit_must_equal_the_rendered_supervisor_pin(
    tmp_path: Path,
) -> None:
    pins, payload, _authorization_path, _environment, _writes = _fixture(tmp_path)
    payload["reviewed_commit"] = "0" * 40

    with pytest.raises(supervisor.FormalSupervisorError, match="fixed supervisor identity"):
        supervisor._validate_launch_payload(
            payload,
            pins=pins,
            now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
            trusted_executed_supervisor_path=payload["executed_supervisor_path"],
            trusted_executed_supervisor_sha256=payload["executed_supervisor_sha256"],
            trusted_supervisor_loader_path=payload["supervisor_loader_path"],
            trusted_supervisor_loader_sha256=payload["supervisor_loader_sha256"],
        )


def test_preflight_launch_payload_is_first_class_and_has_no_credential(
    tmp_path: Path,
) -> None:
    pins, payload, _authorization_path, _environment, _writes = _fixture(
        tmp_path,
        action="preflight",
        worker_action="preflight",
    )

    validated = supervisor._validate_launch_payload(
        payload,
        pins=pins,
        now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
        trusted_executed_supervisor_path=payload["executed_supervisor_path"],
        trusted_executed_supervisor_sha256=payload["executed_supervisor_sha256"],
        trusted_supervisor_loader_path=payload["supervisor_loader_path"],
        trusted_supervisor_loader_sha256=payload["supervisor_loader_sha256"],
    )

    assert validated["action"] == "preflight"
    assert validated["worker_action"] == "preflight"
    assert validated["credential_path"] is None
    assert validated["credential_slot_id"] is None


def test_preflight_supervisor_fails_closed_before_worker_without_native_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins, _payload, authorization_path, environment, writes = _fixture(
        tmp_path,
        action="preflight",
        worker_action="preflight",
    )
    monkeypatch.setattr(
        supervisor,
        "_run_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("worker must not start without native guard")
        ),
    )

    with pytest.raises(
        supervisor.FormalSupervisorError,
        match="external native preflight terminal guard unavailable",
    ):
        _run_fixture(pins, authorization_path, environment, writes)

    assert writes == []


def test_unrendered_template_has_no_production_entrypoint() -> None:
    with pytest.raises(supervisor.FormalSupervisorError, match="fixed production"):
        supervisor.supervise_factor_v3_formal_execution(Path("unused.json"))


def test_terminal_writer_retries_interruption_and_partial_writes() -> None:
    observed: list[bytes] = []

    def writer(_descriptor: int, raw: bytes | memoryview) -> int:
        value = bytes(raw)
        if not observed:
            observed.append(b"")
            raise InterruptedError
        observed.append(value)
        return min(2, len(value))

    supervisor._write_all(1, b"abcdef", writer=writer)

    assert observed == [b"", b"abcdef", b"cdef", b"ef"]


def test_supervisor_executes_exact_worker_and_publishes_one_terminal_frame(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(tmp_path)

    result = _run_fixture(pins, authorization_path, environment, writes)

    assert result["status"] == "completed"
    assert result["launch_authorization_sha256"] == _file_sha256(authorization_path)
    assert Path(result["claim_path"]).is_file()
    assert Path(result["completed_path"]).is_file()


def test_supervisor_persists_exact_worker_terminal_before_completed_v2(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(tmp_path)

    result = _run_fixture(pins, authorization_path, environment, writes)

    authorization_sha256 = _file_sha256(authorization_path)
    terminal_path = supervisor.worker_terminal_path_for_authorization(
        Path(str(payload["execution_ledger_root"])),
        authorization_sha256,
    )
    assert result["worker_terminal_path"] == str(terminal_path)
    assert terminal_path.read_bytes() == writes[0]
    assert result["worker_terminal_sha256"] == hashlib.sha256(writes[0]).hexdigest()
    assert result["worker_terminal_bytes"] == len(writes[0])
    assert result["worker_terminal_schema"] == (
        "factor-v3-formal-bootstrap-worker-terminal/v2"
    )
    assert terminal_path.stat().st_mtime_ns <= Path(result["completed_path"]).stat().st_mtime_ns


def test_completed_v2_has_exact_keyset_types_and_terminal_binding(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(tmp_path)

    result = _run_fixture(pins, authorization_path, environment, writes)

    completed = json.loads(Path(result["completed_path"]).read_bytes())
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
    assert completed["schema"] == (
        "factor-v3-formal-supervisor-execution-completed/v2"
    )
    assert completed["status"] == "completed"
    assert completed["resume_of_authorization_sha256"] is None
    assert completed["resume_transition_sha256"] is None
    assert completed["worker_terminal_bytes"] == len(writes[0])
    assert completed["worker_terminal_schema"] == (
        "factor-v3-formal-bootstrap-worker-terminal/v2"
    )
    assert completed["worker_terminal_sha256"] == hashlib.sha256(writes[0]).hexdigest()
    assert Path(result["completed_path"]).read_bytes() == _canonical_bytes(completed)


def test_resume_status_claim_has_exact_canonical_keyset() -> None:
    claim = {
        "action": "run",
        "authorization_id_sha256": "1" * 64,
        "authorization_nonce_sha256": "2" * 64,
        "bootstrap_execution_authorization_sha256": "3" * 64,
        "launch_authorization_schema": (
            "factor-v3-formal-supervisor-launch-authorization/v2"
        ),
        "launch_authorization_sha256": "4" * 64,
        "launch_authorization_signature_sha256": "5" * 64,
        "replay_scope": "factor-v3-formal-bootstrap-execution/v1",
        "schema": "factor-v3-formal-supervisor-execution-claim/v2",
        "status": "claimed",
    }
    raw = _canonical_bytes(claim)

    assert supervisor._validated_claim_status(raw) == claim
    with pytest.raises(supervisor.FormalSupervisorError, match="resume status"):
        supervisor._validated_claim_status(
            _canonical_bytes({**claim, "unexpected": "value"})
        )
    with pytest.raises(supervisor.FormalSupervisorError, match="resume status"):
        supervisor._validated_claim_status(
            _canonical_bytes({**claim, "status": 1})
        )
    with pytest.raises(supervisor.FormalSupervisorError, match="resume status"):
        supervisor._validated_claim_status(
            _canonical_bytes({**claim, "action": "resume"})
        )


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_removes_precommit_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "partial.json"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(supervisor.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected fsync"):
        supervisor._HeldLedgerFile(
            path,
            b'{"status":"partial"}',
            replay_label="partial test",
        )

    assert not path.exists()


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_keeps_original_delete_lease_until_close(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "committed.json"
    raw = _canonical_bytes({"schema": "test-ledger/v1"})

    held = supervisor._HeldLedgerFile(path, raw, replay_label="test ledger")
    try:
        with pytest.raises(PermissionError):
            path.read_bytes()
        held.postverify()
    finally:
        held.close()

    assert path.read_bytes() == raw


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_never_exposes_partial_final_during_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "partial-write.json"
    raw = _canonical_bytes({"schema": "test-ledger/v1", "value": "complete"})
    real_write = supervisor.os.write
    final_visibility: list[bool] = []
    calls = 0

    def partial_then_fail(descriptor: int, value: bytes | memoryview) -> int:
        nonlocal calls
        final_visibility.append(path.exists())
        calls += 1
        if calls == 1:
            return real_write(descriptor, bytes(value[:3]))
        raise OSError("injected partial write failure")

    monkeypatch.setattr(supervisor.os, "write", partial_then_fail)

    with pytest.raises(OSError, match="injected partial write"):
        supervisor._HeldLedgerFile(path, raw, replay_label="partial write test")

    assert final_visibility
    assert not any(final_visibility)
    assert not path.exists()
    assert not _ledger_nonlease_files(parent)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_never_exposes_final_before_precommit_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "fsync.json"
    final_visibility: list[bool] = []

    def fail_fsync(_descriptor: int) -> None:
        final_visibility.append(path.exists())
        raise OSError("injected staging fsync failure")

    monkeypatch.setattr(supervisor.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected staging fsync"):
        supervisor._HeldLedgerFile(
            path,
            _canonical_bytes({"schema": "test-ledger/v1"}),
            replay_label="staging fsync test",
        )

    assert final_visibility == [False]
    assert not path.exists()
    assert not _ledger_nonlease_files(parent)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_promotion_failure_preserves_staging_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "rename.json"

    def fail_rename(
        _stream: Any,
        _parent_handle: Any,
        _final_name: str,
        *,
        replay_label: str,
    ) -> None:
        del replay_label
        raise OSError("injected rename failure")

    monkeypatch.setattr(
        supervisor,
        "_rename_ledger_staging_no_replace",
        fail_rename,
        raising=False,
    )

    with pytest.raises(OSError, match="injected rename"):
        supervisor._HeldLedgerFile(
            path,
            _canonical_bytes({"schema": "test-ledger/v1"}),
            replay_label="rename test",
        )

    assert not path.exists()
    staging = _ledger_staging_paths(parent)
    assert len(staging) == 1
    opened = staging[0].lstat()
    assert stat.S_ISREG(opened.st_mode)
    assert int(getattr(opened, "st_file_attributes", 0)) & 0x400 == 0
    assert int(getattr(opened, "st_nlink", 1)) == 1


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_real_rename_then_immediate_fault_never_deletes_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "rename-committed.json"
    raw = _canonical_bytes({"schema": "rename-committed/v1"})
    real_rename = supervisor._rename_ledger_staging_no_replace

    def rename_then_fault(
        stream: Any,
        parent_handle: Any,
        final_name: str,
        *,
        replay_label: str,
    ) -> None:
        real_rename(
            stream,
            parent_handle,
            final_name,
            replay_label=replay_label,
        )
        raise OSError("injected fault after real rename")

    monkeypatch.setattr(
        supervisor,
        "_rename_ledger_staging_no_replace",
        rename_then_fault,
    )

    with pytest.raises(OSError, match="injected fault after real rename"):
        supervisor._HeldLedgerFile(path, raw, replay_label="real rename fault")

    assert path.read_bytes() == raw
    assert not _ledger_staging_paths(parent)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_collision_never_overwrites_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "collision.json"
    winner = _canonical_bytes({"schema": "winner/v1"})
    loser = _canonical_bytes({"schema": "loser/v1"})
    real_rename = supervisor._rename_ledger_staging_no_replace

    def collide_then_rename(
        stream: Any,
        parent_handle: Any,
        final_name: str,
        *,
        replay_label: str,
    ) -> None:
        path.write_bytes(winner)
        real_rename(
            stream,
            parent_handle,
            final_name,
            replay_label=replay_label,
        )

    monkeypatch.setattr(
        supervisor,
        "_rename_ledger_staging_no_replace",
        collide_then_rename,
    )

    with pytest.raises(supervisor.FormalSupervisorError, match="replay rejected"):
        supervisor._HeldLedgerFile(path, loser, replay_label="collision test")

    assert path.read_bytes() == winner
    assert len(_ledger_staging_paths(parent)) == 1


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_postcommit_failure_keeps_complete_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "postcommit.json"
    raw = _canonical_bytes({"schema": "test-ledger/v1", "value": "complete"})

    def fail_parent_flush(_handle: Any) -> None:
        raise OSError("injected postcommit parent flush failure")

    monkeypatch.setattr(
        supervisor,
        "_flush_ledger_parent_handle",
        fail_parent_flush,
        raising=False,
    )

    with pytest.raises(OSError, match="injected postcommit parent flush"):
        supervisor._HeldLedgerFile(path, raw, replay_label="postcommit test")

    assert path.read_bytes() == raw
    assert _ledger_nonlease_files(parent) == (path,)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_ledger_write_once_uses_atomic_publisher_for_every_terminal_category(
    tmp_path: Path,
) -> None:
    root = tmp_path / "execution-ledger"
    root.mkdir()
    categories = (
        "claims",
        "bootstrap_authorizations",
        "authorization_ids",
        "authorization_nonces",
        "resumed_authorizations",
        "worker_terminals",
        "completed",
    )
    published: list[tuple[Path, bytes]] = []

    with supervisor._held_directory_chain(root) as chain, ExitStack() as stack:
        for index, category in enumerate(categories):
            identity = hashlib.sha256(category.encode("ascii")).hexdigest()
            raw = _canonical_bytes(
                {
                    "category": category,
                    "index": index,
                    "schema": "test-ledger/v1",
                }
            )
            held, path, digest = supervisor._ledger_write_once(
                chain,
                stack=stack,
                category=category,
                authorization_sha256=identity,
                raw=raw,
                replay_label=f"{category} test",
            )
            held.postverify()
            assert digest == _sha256(raw)
            published.append((path, raw))

    assert [(path.read_bytes(), raw) for path, raw in published] == [
        (raw, raw) for _path, raw in published
    ]


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_never_scavenges_unproven_orphan_staging(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    orphan = parent / ".unproven-ledger-staging-orphan"
    orphan_raw = b"unproven orphan"
    orphan.write_bytes(orphan_raw)
    path = parent / "committed.json"
    raw = _canonical_bytes({"schema": "test-ledger/v1"})

    held = supervisor._HeldLedgerFile(path, raw, replay_label="orphan test")
    held.close()

    assert path.read_bytes() == raw
    assert orphan.read_bytes() == orphan_raw


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_success_releases_publisher_lease_while_final_handle_remains_held(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    first_path = parent / "first.json"
    second_path = parent / "second.json"
    first_raw = _canonical_bytes({"schema": "first/v1"})
    second_raw = _canonical_bytes({"schema": "second/v1"})
    first = supervisor._HeldLedgerFile(
        first_path,
        first_raw,
        replay_label="first publisher",
    )
    try:
        first.postverify()
        with pytest.raises(PermissionError):
            first_path.read_bytes()
        started = time.monotonic()
        second = supervisor._HeldLedgerFile(
            second_path,
            second_raw,
            replay_label="second publisher",
        )
        try:
            assert time.monotonic() - started < 1
            first.postverify()
            second.postverify()
        finally:
            second.close()
    finally:
        first.close()

    assert first_path.read_bytes() == first_raw
    assert second_path.read_bytes() == second_raw


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_short_final_name_uses_valid_rename_abi(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "x"
    raw = _canonical_bytes({"schema": "test-ledger/v1"})

    held = supervisor._HeldLedgerFile(path, raw, replay_label="short ABI test")
    held.close()

    assert path.read_bytes() == raw


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_open_osfhandle_failure_disposes_staging_and_closes_raw_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "open-osfhandle.json"
    captured_handles: list[int] = []

    def fail_open_osfhandle(handle: int, _flags: int) -> int:
        captured_handles.append(handle)
        raise OSError("injected open_osfhandle failure")

    monkeypatch.setattr(supervisor.msvcrt, "open_osfhandle", fail_open_osfhandle)

    with pytest.raises(OSError, match="injected open_osfhandle"):
        supervisor._HeldLedgerFile(
            path,
            _canonical_bytes({"schema": "test-ledger/v1"}),
            replay_label="open_osfhandle test",
        )

    assert len(captured_handles) == 1
    flags = supervisor.wintypes.DWORD()
    get_handle_information = supervisor._kernel32().GetHandleInformation
    get_handle_information.argtypes = (
        supervisor.wintypes.HANDLE,
        ctypes.POINTER(supervisor.wintypes.DWORD),
    )
    get_handle_information.restype = supervisor.wintypes.BOOL
    assert not get_handle_information(
        supervisor.wintypes.HANDLE(captured_handles[0]),
        ctypes.byref(flags),
    )
    assert not tuple(
        candidate
        for candidate in parent.iterdir()
        if candidate.name.startswith(supervisor._LEDGER_STAGING_PREFIX)
    )


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_fdopen_failure_disposes_staging_and_closes_transferred_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "fdopen.json"
    captured_descriptors: list[int] = []

    def fail_fdopen(descriptor: int, _mode: str) -> Any:
        captured_descriptors.append(descriptor)
        raise OSError("injected fdopen failure")

    monkeypatch.setattr(supervisor.os, "fdopen", fail_fdopen)

    with pytest.raises(OSError, match="injected fdopen"):
        supervisor._HeldLedgerFile(
            path,
            _canonical_bytes({"schema": "test-ledger/v1"}),
            replay_label="fdopen test",
        )

    assert len(captured_descriptors) == 1
    descriptor_was_open = True
    try:
        os.fstat(captured_descriptors[0])
    except OSError:
        descriptor_was_open = False
    finally:
        if descriptor_was_open:
            os.close(captured_descriptors[0])
    assert not descriptor_was_open
    assert not tuple(
        candidate
        for candidate in parent.iterdir()
        if candidate.name.startswith(supervisor._LEDGER_STAGING_PREFIX)
    )


def _open_active_ledger_staging(path: Path) -> Any:
    kernel32 = supervisor._kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        supervisor.wintypes.LPCWSTR,
        supervisor.wintypes.DWORD,
        supervisor.wintypes.DWORD,
        supervisor.wintypes.LPVOID,
        supervisor.wintypes.DWORD,
        supervisor.wintypes.DWORD,
        supervisor.wintypes.HANDLE,
    )
    create_file.restype = supervisor.wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000 | 0x40000000 | 0x00010000,
        0x00000001,
        None,
        3,
        0x00200000 | 0x08000000,
        None,
    )
    assert handle not in (None, ctypes.c_void_p(-1).value)
    return handle


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_stale_staging_recovery_skips_active_handle_then_reclaims_after_close(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    orphan = (
        parent
        / f"{supervisor._LEDGER_STAGING_PREFIX}{'a' * 32}.tmp"
    )
    orphan.write_bytes(b"legacy partial")
    stale_time = time.time() - 60 * 60
    os.utime(orphan, (stale_time, stale_time))
    active_handle = _open_active_ledger_staging(orphan)
    first_path = parent / "first.json"
    second_path = parent / "second.json"

    try:
        first = supervisor._HeldLedgerFile(
            first_path,
            _canonical_bytes({"schema": "first/v1"}),
            replay_label="active recovery test",
        )
        first.close()
        assert orphan.is_file()
    finally:
        supervisor._kernel32().CloseHandle(active_handle)

    second = supervisor._HeldLedgerFile(
        second_path,
        _canonical_bytes({"schema": "second/v1"}),
        replay_label="inactive recovery test",
    )
    second.close()

    assert not orphan.exists()


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_nonmatching_directory_entries_do_not_starve_stale_staging_recovery(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    for index in range(supervisor._MAX_LEDGER_STAGING_SCAN + 50):
        (parent / f".ordinary-{index:04d}").write_bytes(b"ordinary")
    orphan = parent / f"{supervisor._LEDGER_STAGING_PREFIX}{'b' * 32}.tmp"
    orphan.write_bytes(b"partial")
    stale_time = time.time() - 60 * 60
    os.utime(orphan, (stale_time, stale_time))

    held = supervisor._HeldLedgerFile(
        parent / "committed.json",
        _canonical_bytes({"schema": "nonmatch-scan/v1"}),
        replay_label="nonmatch scan test",
    )
    held.close()

    assert not orphan.exists()


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_stale_reserved_wildcard_pollution_is_bounded_and_eventually_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    pollution: list[Path] = []
    for index in range(supervisor._MAX_LEDGER_STAGING_SCAN + 50):
        candidate = (
            parent
            / f"{supervisor._LEDGER_STAGING_PREFIX}pollution-{index:04d}.tmp"
        )
        candidate.write_bytes(b"stale pollution")
        pollution.append(candidate)
    exact = parent / f"{supervisor._LEDGER_STAGING_PREFIX}{'9' * 32}.tmp"
    exact.write_bytes(b"stale exact")
    stale_time = time.time() - 60 * 60
    for candidate in [*pollution, exact]:
        os.utime(candidate, (stale_time, stale_time))
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 0.0)
    rejected_rounds = 0
    successful_recovery: int | None = None
    try:
        for _round in range(32):
            before = sum(
                candidate.exists()
                for candidate in [*pollution, exact]
            )
            try:
                recovered = supervisor._recover_stale_ledger_staging(chain, lease)
            except supervisor.FormalSupervisorError as exc:
                assert "execution ledger recovery incomplete" in str(exc)
                rejected_rounds += 1
                recovered = None
            after = sum(
                candidate.exists()
                for candidate in [*pollution, exact]
            )
            assert 0 <= before - after <= supervisor._MAX_LEDGER_STAGING_RECOVERY
            if recovered is not None:
                successful_recovery = recovered
                break
    finally:
        lease.close()
        chain.close()

    assert rejected_rounds > 1
    assert successful_recovery is not None
    assert not exact.exists()
    assert not any(candidate.exists() for candidate in pollution)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_fresh_safe_reserved_wildcard_pollution_is_preserved(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidate = (
        parent / f"{supervisor._LEDGER_STAGING_PREFIX}fresh-pollution.tmp"
    )
    candidate.write_bytes(b"fresh pollution")
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    try:
        recovered = supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()

    assert recovered == 0
    assert candidate.read_bytes() == b"fresh pollution"


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_exact_scan_limit_with_fresh_candidates_proves_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidates = _write_exact_ledger_candidates(
        parent,
        supervisor._MAX_LEDGER_STAGING_SCAN,
        raw=b"fresh",
    )
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 0.0)
    try:
        recovered = supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()

    assert recovered == 0
    assert all(candidate.read_bytes() == b"fresh" for candidate in candidates)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_exact_scan_limit_plus_one_fresh_candidate_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidates = _write_exact_ledger_candidates(
        parent,
        supervisor._MAX_LEDGER_STAGING_SCAN + 1,
        raw=b"fresh",
    )
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 0.0)
    try:
        with pytest.raises(
            supervisor.FormalSupervisorError,
            match="execution ledger recovery incomplete",
        ):
            supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()

    assert all(candidate.read_bytes() == b"fresh" for candidate in candidates)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_scan_limit_lookahead_rejects_unsafe_tail_without_processing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidates: list[Path] = []
    for index in range(supervisor._MAX_LEDGER_STAGING_SCAN + 1):
        candidate = (
            parent
            / f"{supervisor._LEDGER_STAGING_PREFIX}lookahead-{index:04d}.tmp"
        )
        assert supervisor._LEDGER_STAGING_NAME_RE.fullmatch(candidate.name) is None
        candidate.write_bytes(b"fresh")
        candidates.append(candidate)
    with supervisor._exact_ledger_staging_paths(parent) as enumerated:
        observed = list(enumerated)
    assert len(observed) == len(candidates)
    unsafe_tail = observed[-1]
    sibling = parent / "unsafe-tail-hardlink"
    os.link(unsafe_tail, sibling)
    with supervisor._exact_ledger_staging_paths(parent) as enumerated:
        observed_after_link = list(enumerated)
    assert observed_after_link[-1] == unsafe_tail
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 0.0)
    try:
        with pytest.raises(
            supervisor.FormalSupervisorError,
            match="execution ledger recovery incomplete",
        ):
            supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()

    assert unsafe_tail.stat().st_nlink == 2
    assert all(candidate.exists() for candidate in candidates)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
@pytest.mark.parametrize("pollution_kind", ["active", "directory", "hardlink"])
def test_unsafe_reserved_wildcard_pollution_is_rejected(
    tmp_path: Path,
    pollution_kind: str,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidate = (
        parent
        / f"{supervisor._LEDGER_STAGING_PREFIX}unsafe-{pollution_kind}.tmp"
    )
    sibling = parent / "hardlink-sibling"
    active_handle: Any | None = None
    if pollution_kind == "directory":
        candidate.mkdir()
    else:
        candidate.write_bytes(b"unsafe pollution")
        stale_time = time.time() - 60 * 60
        os.utime(candidate, (stale_time, stale_time))
        if pollution_kind == "active":
            active_handle = _open_active_ledger_staging(candidate)
        else:
            os.link(candidate, sibling)
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    try:
        with pytest.raises(
            supervisor.FormalSupervisorError,
            match="reserved staging pollution rejected",
        ):
            supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()
        if active_handle is not None:
            supervisor._kernel32().CloseHandle(active_handle)

    assert candidate.exists()


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
@pytest.mark.parametrize(
    (
        "deadline_phase",
        "ticks",
        "candidate_count",
        "expected_visited",
        "expected_processed",
    ),
    [
        ("before_take", (0.0, 2.0), 1, 0, 0),
        ("after_take", (0.0, 0.1, 2.0), 1, 1, 0),
        ("after_process", (0.0, 0.1, 0.2, 2.0), 1, 1, 1),
        ("at_eof", (0.0, 0.1, 2.0), 0, 0, 0),
    ],
)
def test_staging_recovery_deadline_without_timely_eof_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deadline_phase: str,
    ticks: tuple[float, ...],
    candidate_count: int,
    expected_visited: int,
    expected_processed: int,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidates = [
        parent / f"{supervisor._LEDGER_STAGING_PREFIX}{index:032x}.tmp"
        for index in range(candidate_count)
    ]
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    visited: list[str] = []
    processed: list[str] = []
    enumerator_closed: list[bool] = []
    monotonic_ticks = iter(ticks)

    @contextmanager
    def injected_candidates(_parent: Path) -> Any:
        def paths() -> Any:
            for candidate in candidates:
                visited.append(candidate.name)
                yield candidate

        try:
            yield paths()
        finally:
            enumerator_closed.append(True)

    def recover_candidate(
        candidate: Path,
        *,
        stale_before_ns: int,
        reject_unsafe: bool = False,
    ) -> bool:
        del stale_before_ns, reject_unsafe
        processed.append(candidate.name)
        return False

    monkeypatch.setattr(
        supervisor,
        "_exact_ledger_staging_paths",
        injected_candidates,
    )
    monkeypatch.setattr(
        supervisor,
        "_recover_one_stale_ledger_staging",
        recover_candidate,
    )
    monkeypatch.setattr(
        supervisor.time,
        "monotonic",
        lambda: next(monotonic_ticks, 2.0),
    )
    try:
        with pytest.raises(
            supervisor.FormalSupervisorError,
            match="execution ledger recovery incomplete",
        ):
            supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()

    assert len(visited) == expected_visited
    assert len(processed) == expected_processed
    assert enumerator_closed == [True]
    parent.rename(tmp_path / f"closed-{deadline_phase}")


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_held_ledger_file_recovery_rejection_closes_all_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    enumerator_closed: list[bool] = []

    @contextmanager
    def injected_candidates(_parent: Path) -> Any:
        try:
            yield iter(())
        finally:
            enumerator_closed.append(True)

    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(
        supervisor,
        "_exact_ledger_staging_paths",
        injected_candidates,
    )
    monkeypatch.setattr(
        supervisor.time,
        "monotonic",
        lambda: next(ticks, 2.0),
    )

    with pytest.raises(
        supervisor.FormalSupervisorError,
        match="execution ledger recovery incomplete",
    ):
        supervisor._HeldLedgerFile(
            parent / "must-not-publish.json",
            _canonical_bytes({"schema": "deadline-rejection/v1"}),
            replay_label="deadline rejection",
        )

    assert enumerator_closed == [True]
    assert not (parent / "must-not-publish.json").exists()
    parent.rename(tmp_path / "ledger-closed")


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_exact_recovery_limit_proves_eof(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidates = _write_exact_ledger_candidates(
        parent,
        supervisor._MAX_LEDGER_STAGING_RECOVERY,
        raw=b"partial",
    )
    stale_time = time.time() - 60 * 60
    for candidate in candidates:
        os.utime(candidate, (stale_time, stale_time))
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    try:
        recovered = supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()

    assert recovered == supervisor._MAX_LEDGER_STAGING_RECOVERY
    assert not any(candidate.exists() for candidate in candidates)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_recovery_limit_plus_one_rejects_then_retry_proves_eof(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidates = _write_exact_ledger_candidates(
        parent,
        supervisor._MAX_LEDGER_STAGING_RECOVERY + 1,
        raw=b"partial",
    )
    stale_time = time.time() - 60 * 60
    for candidate in candidates:
        os.utime(candidate, (stale_time, stale_time))
    chain = supervisor._HeldDirectoryChain(parent)
    lease = supervisor._HeldLedgerLease(chain)
    try:
        with pytest.raises(
            supervisor.FormalSupervisorError,
            match="execution ledger recovery incomplete",
        ):
            supervisor._recover_stale_ledger_staging(chain, lease)
        remaining_after_rejection = sum(
            candidate.exists() for candidate in candidates
        )
        recovered = supervisor._recover_stale_ledger_staging(chain, lease)
    finally:
        lease.close()
        chain.close()

    assert remaining_after_rejection == 1
    assert recovered == 1
    assert not any(candidate.exists() for candidate in candidates)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_fresh_inactive_staging_is_not_recovered(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidate = parent / f"{supervisor._LEDGER_STAGING_PREFIX}{'c' * 32}.tmp"
    candidate.write_bytes(b"fresh")

    held = supervisor._HeldLedgerFile(
        parent / "committed.json",
        _canonical_bytes({"schema": "fresh-candidate/v1"}),
        replay_label="fresh candidate test",
    )
    held.close()

    assert candidate.read_bytes() == b"fresh"


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_hardlinked_staging_is_not_recovered(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidate = parent / f"{supervisor._LEDGER_STAGING_PREFIX}{'d' * 32}.tmp"
    sibling = parent / "hardlink-sibling"
    candidate.write_bytes(b"linked")
    os.link(candidate, sibling)
    stale_time = time.time() - 60 * 60
    os.utime(candidate, (stale_time, stale_time))

    held = supervisor._HeldLedgerFile(
        parent / "committed.json",
        _canonical_bytes({"schema": "hardlink-candidate/v1"}),
        replay_label="hardlink candidate test",
    )
    held.close()

    assert candidate.read_bytes() == b"linked"
    assert sibling.read_bytes() == b"linked"
    assert candidate.stat().st_nlink == 2


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_staging_path_file_id_swap_is_not_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidate = parent / f"{supervisor._LEDGER_STAGING_PREFIX}{'e' * 32}.tmp"
    swapped = parent / "swapped"
    candidate.write_bytes(b"candidate")
    swapped.write_bytes(b"swapped")
    stale_time = time.time() - 60 * 60
    os.utime(candidate, (stale_time, stale_time))
    os.utime(swapped, (stale_time, stale_time))
    real_lstat = Path.lstat

    def swapped_lstat(path: Path) -> os.stat_result:
        if os.path.normcase(str(path)) == os.path.normcase(str(candidate)):
            return real_lstat(swapped)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", swapped_lstat)

    recovered = supervisor._recover_one_stale_ledger_staging(
        candidate,
        stale_before_ns=time.time_ns() - 60 * 1_000_000_000,
    )

    assert recovered is False
    assert candidate.read_bytes() == b"candidate"
    assert swapped.read_bytes() == b"swapped"


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_staging_reparse_metadata_is_not_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    candidate = parent / f"{supervisor._LEDGER_STAGING_PREFIX}{'f' * 32}.tmp"
    candidate.write_bytes(b"candidate")
    stale_time = time.time() - 60 * 60
    os.utime(candidate, (stale_time, stale_time))
    real_lstat = Path.lstat
    terminal = real_lstat(candidate)

    class _ReparseStat:
        st_dev = terminal.st_dev
        st_ino = terminal.st_ino
        st_mode = terminal.st_mode
        st_mtime_ns = terminal.st_mtime_ns
        st_size = terminal.st_size
        st_nlink = terminal.st_nlink
        st_file_attributes = 0x400

    def reparse_lstat(path: Path) -> Any:
        if os.path.normcase(str(path)) == os.path.normcase(str(candidate)):
            return _ReparseStat()
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    recovered = supervisor._recover_one_stale_ledger_staging(
        candidate,
        stale_before_ns=time.time_ns() - 60 * 1_000_000_000,
    )

    assert recovered is False
    assert candidate.read_bytes() == b"candidate"


def _spawn_ledger_crash_child(
    *,
    path: Path,
    raw: bytes,
    barrier: Path,
    phase: str,
) -> subprocess.Popen[bytes]:
    source = (
        "from pathlib import Path\n"
        "import sys\n"
        "import time\n"
        "from app import factor_v3_formal_trusted_supervisor as supervisor\n"
        "path = Path(sys.argv[1])\n"
        "raw = bytes.fromhex(sys.argv[2])\n"
        "barrier = Path(sys.argv[3])\n"
        "phase = sys.argv[4]\n"
        "def stop_before_rename(stream, parent_handle, final_name, *, replay_label):\n"
        "    del stream, parent_handle, final_name, replay_label\n"
        "    barrier.write_bytes(b'ready')\n"
        "    while True:\n"
        "        time.sleep(1)\n"
        "def stop_after_rename(parent_handle):\n"
        "    del parent_handle\n"
        "    barrier.write_bytes(b'ready')\n"
        "    while True:\n"
        "        time.sleep(1)\n"
        "if phase == 'pre-rename':\n"
        "    supervisor._rename_ledger_staging_no_replace = stop_before_rename\n"
        "elif phase == 'post-rename':\n"
        "    supervisor._flush_ledger_parent_handle = stop_after_rename\n"
        "else:\n"
        "    raise RuntimeError('unknown phase')\n"
        "supervisor._HeldLedgerFile(path, raw, replay_label='crash child')\n"
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            source,
            str(path),
            raw.hex(),
            str(barrier),
            phase,
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
        shell=False,
    )


def _spawn_ledger_lease_holder(
    *,
    parent: Path,
    ready: Path,
    release: Path,
    crash: bool,
) -> subprocess.Popen[bytes]:
    source = (
        "from pathlib import Path\n"
        "import sys\n"
        "import time\n"
        "from app import factor_v3_formal_trusted_supervisor as supervisor\n"
        "parent = Path(sys.argv[1])\n"
        "ready = Path(sys.argv[2])\n"
        "release = Path(sys.argv[3])\n"
        "crash = sys.argv[4] == 'crash'\n"
        "chain = supervisor._HeldDirectoryChain(parent)\n"
        "lease = supervisor._HeldLedgerLease(chain)\n"
        "ready.write_bytes(b'ready')\n"
        "try:\n"
        "    if crash:\n"
        "        while True:\n"
        "            time.sleep(1)\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not release.is_file():\n"
        "        if time.monotonic() >= deadline:\n"
        "            raise RuntimeError('release barrier timed out')\n"
        "        time.sleep(0.01)\n"
        "finally:\n"
        "    lease.close()\n"
        "    chain.close()\n"
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            source,
            str(parent),
            str(ready),
            str(release),
            "crash" if crash else "release",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
        shell=False,
    )


def _spawn_ledger_lease_contender(
    *,
    path: Path,
    started: Path,
    completed: Path,
) -> subprocess.Popen[bytes]:
    source = (
        "from pathlib import Path\n"
        "import sys\n"
        "from app import factor_v3_formal_trusted_supervisor as supervisor\n"
        "path = Path(sys.argv[1])\n"
        "started = Path(sys.argv[2])\n"
        "completed = Path(sys.argv[3])\n"
        "started.write_bytes(b'started')\n"
        "held = supervisor._HeldLedgerFile(\n"
        "    path,\n"
        "    b'{\"schema\":\"lease-contender/v1\"}',\n"
        "    replay_label='lease contender',\n"
        ")\n"
        "held.close()\n"
        "completed.write_bytes(b'completed')\n"
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            source,
            str(path),
            str(started),
            str(completed),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
        shell=False,
    )


def _wait_for_crash_barrier(
    process: subprocess.Popen[bytes],
    barrier: Path,
) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if barrier.is_file():
            return
        if process.poll() is not None:
            _stdout, stderr = process.communicate()
            pytest.fail(f"crash child exited before barrier: {stderr!r}")
        time.sleep(0.01)
    pytest.fail("crash child barrier timed out")


def _kill_crash_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=10)


def _finish_test_child(
    process: subprocess.Popen[bytes],
    *,
    label: str,
) -> None:
    _stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, f"{label} failed: {stderr!r}"


def test_ledger_lease_wait_budget_is_frozen() -> None:
    assert supervisor._LEDGER_LEASE_WAIT_SECONDS == 30


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_persistent_lease_contender_waits_for_legal_holder_then_succeeds(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    ready = tmp_path / "holder.ready"
    release = tmp_path / "holder.release"
    started = tmp_path / "contender.started"
    completed = tmp_path / "contender.completed"
    path = parent / "contender.json"
    holder = _spawn_ledger_lease_holder(
        parent=parent,
        ready=ready,
        release=release,
        crash=False,
    )
    contender: subprocess.Popen[bytes] | None = None
    try:
        _wait_for_crash_barrier(holder, ready)
        contender = _spawn_ledger_lease_contender(
            path=path,
            started=started,
            completed=completed,
        )
        _wait_for_crash_barrier(contender, started)
        time.sleep(0.2)
        assert contender.poll() is None
        released_at = time.monotonic()
        release.write_bytes(b"release")
        _finish_test_child(holder, label="lease holder")
        _finish_test_child(contender, label="lease contender")
        assert time.monotonic() - released_at < 2
    finally:
        if holder.poll() is None:
            _kill_crash_child(holder)
        if contender is not None and contender.poll() is None:
            _kill_crash_child(contender)

    assert completed.read_bytes() == b"completed"
    assert path.read_bytes() == b'{"schema":"lease-contender/v1"}'
    _assert_persistent_ledger_lease(parent)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_persistent_lease_contender_succeeds_after_holder_process_crash(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    ready = tmp_path / "holder.ready"
    release = tmp_path / "unused.release"
    started = tmp_path / "contender.started"
    completed = tmp_path / "contender.completed"
    path = parent / "crash-contender.json"
    holder = _spawn_ledger_lease_holder(
        parent=parent,
        ready=ready,
        release=release,
        crash=True,
    )
    contender: subprocess.Popen[bytes] | None = None
    try:
        _wait_for_crash_barrier(holder, ready)
        contender = _spawn_ledger_lease_contender(
            path=path,
            started=started,
            completed=completed,
        )
        _wait_for_crash_barrier(contender, started)
        time.sleep(0.2)
        assert contender.poll() is None
        released_at = time.monotonic()
        _kill_crash_child(holder)
        _finish_test_child(contender, label="crash lease contender")
        assert time.monotonic() - released_at < 2
    finally:
        if holder.poll() is None:
            _kill_crash_child(holder)
        if contender is not None and contender.poll() is None:
            _kill_crash_child(contender)

    assert completed.read_bytes() == b"completed"
    assert path.read_bytes() == b'{"schema":"lease-contender/v1"}'
    _assert_persistent_ledger_lease(parent)


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_pre_rename_process_crash_leaves_only_recoverable_staging(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "pre-crash.json"
    barrier = tmp_path / "pre-crash.ready"
    raw = _canonical_bytes({"schema": "pre-crash/v1"})
    process = _spawn_ledger_crash_child(
        path=path,
        raw=raw,
        barrier=barrier,
        phase="pre-rename",
    )
    try:
        _wait_for_crash_barrier(process, barrier)
    finally:
        _kill_crash_child(process)

    assert not path.exists()
    staging = tuple(
        candidate
        for candidate in parent.iterdir()
        if candidate.name.startswith(supervisor._LEDGER_STAGING_PREFIX)
    )
    assert len(staging) == 1
    stale_time = time.time() - 60 * 60
    os.utime(staging[0], (stale_time, stale_time))

    held = supervisor._HeldLedgerFile(path, raw, replay_label="pre-crash recovery")
    held.close()

    assert path.read_bytes() == raw
    assert not tuple(
        candidate
        for candidate in parent.iterdir()
        if candidate.name.startswith(supervisor._LEDGER_STAGING_PREFIX)
    )


@pytest.mark.skipif(os.name != "nt", reason="held ledger file is Windows-only")
def test_post_rename_process_crash_preserves_complete_final(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "ledger"
    parent.mkdir()
    path = parent / "post-crash.json"
    barrier = tmp_path / "post-crash.ready"
    raw = _canonical_bytes({"schema": "post-crash/v1"})
    process = _spawn_ledger_crash_child(
        path=path,
        raw=raw,
        barrier=barrier,
        phase="post-rename",
    )
    try:
        _wait_for_crash_barrier(process, barrier)
    finally:
        _kill_crash_child(process)

    assert path.read_bytes() == raw
    with pytest.raises(supervisor.FormalSupervisorError, match="replay rejected"):
        supervisor._HeldLedgerFile(
            path,
            _canonical_bytes({"schema": "loser/v1"}),
            replay_label="post-crash replay",
        )
    assert path.read_bytes() == raw


def test_native_credential_handle_is_requested_only_after_claim_and_not_reopened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(tmp_path)
    credential_path = Path(str(payload["credential_path"]))
    authorization_sha256 = _file_sha256(authorization_path)
    claim_path = supervisor.claim_path_for_authorization(
        Path(str(payload["execution_ledger_root"])),
        authorization_sha256,
    )
    requested: list[bool] = []
    real_init = supervisor._HeldFile.__init__

    def observed_init(
        self: supervisor._HeldFile,
        path: Path,
        *,
        expected_sha256: str | None,
        label: str,
        max_bytes: int,
        allow_empty: bool = False,
        allow_hardlinks: bool = False,
        directory_guard: supervisor._HeldFrozenDirectoryTree | None = None,
    ) -> None:
        assert label != "points-primary credential slot"
        real_init(
            self,
            path,
            expected_sha256=expected_sha256,
            label=label,
            max_bytes=max_bytes,
            allow_empty=allow_empty,
            allow_hardlinks=allow_hardlinks,
            directory_guard=directory_guard,
        )

    def provide_handle() -> int:
        assert claim_path.is_file()
        requested.append(True)
        kernel32 = supervisor._kernel32()
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        )
        create_file.restype = ctypes.c_void_p
        handle = create_file(
            str(credential_path),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        assert handle not in (None, ctypes.c_void_p(-1).value)
        return int(handle)

    monkeypatch.setattr(supervisor._HeldFile, "__init__", observed_init)

    result = supervisor._supervise_with_pins(
        authorization_path=authorization_path,
        pins=pins,
        now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
        environment_snapshot=environment,
        output_writer=_writer(writes),
        trusted_executed_supervisor_path=payload["executed_supervisor_path"],
        trusted_executed_supervisor_sha256=payload["executed_supervisor_sha256"],
        trusted_supervisor_loader_path=payload["supervisor_loader_path"],
        trusted_supervisor_loader_sha256=payload["supervisor_loader_sha256"],
        native_credential_provider=provide_handle,
    )

    assert result["status"] == "completed"
    assert requested == [True]
    assert json.loads(writes[0])["result"]["secret_present"] is True
    assert len(writes) == 1
    frame = json.loads(writes[0])
    assert frame["launch_authorization_sha256"] == result["launch_authorization_sha256"]
    assert (
        frame["bootstrap_execution_authorization_sha256"]
        == (payload["bootstrap_execution_authorization_sha256"])
    )
    assert frame["status"] == "completed"
    assert "fixture-secret-must-never-be-logged" not in writes[0].decode("utf-8")


def test_worker_credential_in_terminal_output_is_rejected_without_parent_output(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(
        tmp_path,
        prefix="fixture-secret-must-never-be-logged",
    )
    authorization_sha256 = _file_sha256(authorization_path)
    ledger_root = Path(str(payload["execution_ledger_root"]))

    with pytest.raises(
        supervisor.FormalSupervisorError,
        match="isolated worker terminal rejected",
    ):
        _run_fixture(pins, authorization_path, environment, writes)

    assert supervisor.claim_path_for_authorization(
        ledger_root,
        authorization_sha256,
    ).is_file()
    assert not supervisor.completed_path_for_authorization(
        ledger_root,
        authorization_sha256,
    ).exists()
    assert writes == []


def test_native_credential_provider_is_not_called_for_invalid_signed_payload(
    tmp_path: Path,
) -> None:
    pins, payload, _authorization_path, environment, writes = _fixture(tmp_path)
    drifted = dict(payload)
    drifted["credential_slot_id"] = "unreviewed-slot"
    authorization_path = _rewrite_authorization(
        tmp_path,
        drifted,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    requested: list[bool] = []

    def provide_handle() -> int:
        requested.append(True)
        return 1

    with pytest.raises(supervisor.FormalSupervisorError):
        supervisor._supervise_with_pins(
            authorization_path=authorization_path,
            pins=pins,
            now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
            environment_snapshot=environment,
            output_writer=_writer(writes),
            trusted_executed_supervisor_path=payload["executed_supervisor_path"],
            trusted_executed_supervisor_sha256=payload[
                "executed_supervisor_sha256"
            ],
            trusted_supervisor_loader_path=payload["supervisor_loader_path"],
            trusted_supervisor_loader_sha256=payload["supervisor_loader_sha256"],
            native_credential_provider=provide_handle,
        )

    assert requested == []
    assert writes == []


def test_native_supervisor_terminal_contains_only_bound_public_hashes(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, _writes = _fixture(tmp_path)
    credential_path = Path(str(payload["credential_path"]))
    terminal: list[bytes] = []

    def provide_handle() -> int:
        kernel32 = supervisor._kernel32()
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        )
        create_file.restype = ctypes.c_void_p
        handle = create_file(
            str(credential_path),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        assert handle not in (None, ctypes.c_void_p(-1).value)
        return int(handle)

    result = supervisor._supervise_with_native_broker(
        authorization_path=authorization_path,
        pins=pins,
        now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
        environment_snapshot=environment,
        native_credential_provider=provide_handle,
        terminal_writer=_writer(terminal),
        trusted_executed_supervisor_path=payload["executed_supervisor_path"],
        trusted_executed_supervisor_sha256=payload["executed_supervisor_sha256"],
        trusted_supervisor_loader_path=payload["supervisor_loader_path"],
        trusted_supervisor_loader_sha256=payload["supervisor_loader_sha256"],
    )

    expected = (
        "COMPLETED factor-v3-formal-native-broker-supervisor/v1\n"
        f"launch_authorization_sha256={result['launch_authorization_sha256']}\n"
        f"claim_sha256={result['claim_sha256']}\n"
        f"supervisor_completed_sha256={result['completed_sha256']}\n"
        f"worker_terminal_sha256={result['worker_terminal_sha256']}\n"
    ).encode("ascii")
    assert terminal == [expected]
    assert b"fixture-secret-must-never-be-logged" not in terminal[0]


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("worker_argv", ["python", "-c", "prelude"]),
        ("bootstrap_worker_sha256", "0" * 64),
        ("publication_completion_marker_sha256", "0" * 64),
    ),
)
def test_signed_launcher_drift_is_rejected_without_real_stdout(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    pins, payload, _authorization_path, environment, writes = _fixture(tmp_path)
    payload[field] = replacement
    authorization_path = _rewrite_authorization(
        tmp_path,
        payload,
        tmp_path / "execution-key" / "execution-private.pem",
    )

    with pytest.raises(supervisor.FormalSupervisorError):
        _run_fixture(pins, authorization_path, environment, writes)

    assert writes == []


def test_review_and_execution_key_roles_must_be_distinct(tmp_path: Path) -> None:
    pins, _payload, authorization_path, environment, writes = _fixture(
        tmp_path,
        review_key_same_as_execution=True,
    )

    with pytest.raises(supervisor.FormalSupervisorError, match="key role"):
        _run_fixture(pins, authorization_path, environment, writes)

    assert writes == []


@pytest.mark.parametrize(
    ("stderr_text", "prefix", "wrong_launch_sha"),
    (
        ("unexpected stderr", "", False),
        ("", "prefix-before-frame", False),
        ("", "", True),
    ),
)
def test_terminal_frame_requires_empty_stderr_unique_canonical_bytes_and_auth_binding(
    tmp_path: Path,
    stderr_text: str,
    prefix: str,
    wrong_launch_sha: bool,
) -> None:
    pins, _payload, authorization_path, environment, writes = _fixture(
        tmp_path,
        stderr_text=stderr_text,
        prefix=prefix,
        wrong_launch_sha=wrong_launch_sha,
    )

    with pytest.raises(supervisor.FormalSupervisorError):
        _run_fixture(pins, authorization_path, environment, writes)

    assert writes == []


def test_authorization_is_single_use_and_replay_is_rejected(tmp_path: Path) -> None:
    pins, _payload, authorization_path, environment, writes = _fixture(tmp_path)
    _run_fixture(pins, authorization_path, environment, writes)
    writes.clear()

    with pytest.raises(supervisor.FormalSupervisorError, match="replay"):
        _run_fixture(pins, authorization_path, environment, writes)

    assert writes == []


def test_different_launch_cannot_replay_same_bootstrap_authorization_tuple(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(tmp_path)
    _run_fixture(pins, authorization_path, environment, writes)
    payload["worker_timeout_seconds"] = 61
    replay_path = _rewrite_authorization(
        tmp_path,
        payload,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    assert _file_sha256(replay_path) != _file_sha256(authorization_path)
    writes.clear()

    with pytest.raises(supervisor.FormalSupervisorError, match="replay"):
        _run_fixture(pins, replay_path, environment, writes)

    assert writes == []


def test_signed_completion_marker_is_the_only_publication_selection_record(
    tmp_path: Path,
) -> None:
    pins, payload, _authorization_path, environment, writes = _fixture(tmp_path)
    receipt_path = next(
        Path(str(payload["bootstrap_output_root"])).glob("publication_receipts/sha256/*/*.json")
    )
    payload["publication_completion_marker_path"] = str(receipt_path)
    payload["publication_completion_marker_sha256"] = _file_sha256(receipt_path)
    drifted_pins = replace(
        pins,
        bootstrap_completion_marker_path=str(receipt_path),
        bootstrap_completion_marker_sha256=_file_sha256(receipt_path),
    )
    authorization_path = _rewrite_authorization(
        tmp_path,
        payload,
        tmp_path / "execution-key" / "execution-private.pem",
    )

    with pytest.raises(
        supervisor.FormalSupervisorError,
        match="completion|supervisor publication receipt",
    ):
        _run_fixture(drifted_pins, authorization_path, environment, writes)

    assert writes == []


def test_fixed_supervisor_renderer_embeds_all_production_pins(
    tmp_path: Path,
) -> None:
    pins, _payload, _authorization_path, _environment, _writes = _fixture(tmp_path)

    rendered = supervisor._render_supervisor_with_test_pins(pins)
    source = rendered.decode("utf-8")

    assert "_FIXED_PINS: _SupervisorPins = _SupervisorPins(" in source
    for frozen_value in (
        pins.execution_public_key_spki_der_base64,
        pins.execution_public_key_spki_sha256,
        pins.supervisor_expected_commit,
        pins.supervisor_source_sha256,
        pins.bootstrap_completion_marker_path,
        pins.bootstrap_completion_marker_sha256,
        pins.bootstrap_completion_schema,
        pins.control_contract_source_sha256,
        pins.worker_protocol,
        pins.worker_terminal_schema,
    ):
        assert repr(frozen_value) in source
    compile(rendered, "<rendered-factor-v3-supervisor>", "exec")


def test_supervisor_renderer_rejects_an_unpinned_template_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins, _payload, _authorization_path, _environment, _writes = _fixture(tmp_path)
    source_path = Path(supervisor.__file__).resolve()
    tampered_path = (tmp_path / "tampered-supervisor-template.py").resolve()
    tampered_path.write_bytes(
        source_path.read_bytes().replace(
            b"class FormalSupervisorError(RuntimeError):",
            b"class FormalSupervisorError(BaseException):",
            1,
        )
    )
    contract_path = source_path.with_name("factor_v3_formal_control_contract.py")
    monkeypatch.setattr(
        supervisor,
        "_EMBEDDED_CONTROL_CONTRACT_SOURCE",
        contract_path.read_bytes(),
    )
    monkeypatch.setattr(supervisor, "__file__", str(tampered_path))

    with pytest.raises(supervisor.FormalSupervisorError, match="source"):
        supervisor._render_supervisor_with_test_pins(pins)


def test_failed_claim_requires_independently_signed_resume_action(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(
        tmp_path,
        fail=True,
    )
    original_sha256 = _file_sha256(authorization_path)
    original_outer = json.loads(authorization_path.read_bytes())
    original_signature_sha256 = _sha256(
        supervisor._decoded_signature(original_outer["signature_base64"])
    )

    with pytest.raises(supervisor.FormalSupervisorError):
        _run_fixture(pins, authorization_path, environment, writes)

    original_authorization_id_sha256 = str(payload["authorization_id_sha256"])
    original_authorization_nonce_sha256 = str(payload["authorization_nonce_sha256"])
    original_bootstrap_execution_authorization_sha256 = str(
        payload["bootstrap_execution_authorization_sha256"]
    )
    original_replay_scope = str(payload["replay_scope"])
    claim_path = supervisor.claim_path_for_authorization(
        Path(str(payload["execution_ledger_root"])),
        original_sha256,
    )
    claim_sha256 = _file_sha256(claim_path)
    payload.update(
        {
            "action": "resume",
            "resume_of_authorization_id_sha256": original_authorization_id_sha256,
            "resume_of_authorization_sha256": original_sha256,
            "resume_of_authorization_nonce_sha256": original_authorization_nonce_sha256,
            "resume_of_action": "run",
            "resume_of_bootstrap_execution_authorization_sha256": (
                original_bootstrap_execution_authorization_sha256
            ),
            "resume_of_launch_authorization_schema": supervisor.LAUNCH_AUTHORIZATION_SCHEMA,
            "resume_of_launch_authorization_signature_sha256": (
                original_signature_sha256
            ),
            "resume_of_replay_scope": original_replay_scope,
            "resume_status_path": str(claim_path),
            "resume_status_sha256": claim_sha256,
        }
    )
    resume_path = _rewrite_authorization(
        tmp_path,
        payload,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    writes.clear()

    result = _run_fixture(pins, resume_path, environment, writes)

    assert result["status"] == "completed"
    assert json.loads(writes[0])["launch_action"] == "resume"


def test_artifact_claim_and_completed_files_remain_held_through_success_output(
    tmp_path: Path,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(tmp_path)
    authorization_sha256 = _file_sha256(authorization_path)
    ledger_root = Path(str(payload["execution_ledger_root"]))
    artifact_path = Path(str(payload["formal_output_root"])) / "terminal-artifact.json"
    claim_path = supervisor.claim_path_for_authorization(
        ledger_root,
        authorization_sha256,
    )
    completed_path = supervisor.completed_path_for_authorization(
        ledger_root,
        authorization_sha256,
    )
    stdlib_policy = json.loads(Path(str(payload["stdlib_policy_path"])).read_text(encoding="utf-8"))
    stdlib_path = Path(str(stdlib_policy["entries"][0]["path"]))
    blocked: list[bool] = []

    def writer(_descriptor: int, raw: bytes | memoryview) -> int:
        for path in (artifact_path, claim_path, completed_path, stdlib_path):
            replacement = path.with_name(path.name + ".moved")
            try:
                path.rename(replacement)
            except OSError:
                blocked.append(True)
            else:
                blocked.append(False)
                replacement.rename(path)
        value = bytes(raw)
        writes.append(value)
        return len(value)

    supervisor._supervise_with_pins(
        authorization_path=authorization_path,
        pins=pins,
        now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
        environment_snapshot=environment,
        output_writer=writer,
        trusted_executed_supervisor_path=payload["executed_supervisor_path"],
        trusted_executed_supervisor_sha256=payload["executed_supervisor_sha256"],
        trusted_supervisor_loader_path=payload["supervisor_loader_path"],
        trusted_supervisor_loader_sha256=payload["supervisor_loader_sha256"],
    )

    assert blocked == [True, True, True, True]


@pytest.mark.parametrize(
    "field",
    (
        "stdlib_policy_sha256",
        "stdlib_inventory_root_sha256",
        "worker_protocol",
        "worker_terminal_schema",
    ),
)
def test_stdlib_prelock_and_latest_worker_protocol_are_exact(
    tmp_path: Path,
    field: str,
) -> None:
    pins, payload, _authorization_path, environment, writes = _fixture(tmp_path)
    payload[field] = "0" * 64 if field.endswith("sha256") else "legacy/v1"
    authorization_path = _rewrite_authorization(
        tmp_path,
        payload,
        tmp_path / "execution-key" / "execution-private.pem",
    )

    with pytest.raises(supervisor.FormalSupervisorError):
        _run_fixture(pins, authorization_path, environment, writes)

    assert writes == []


def test_held_directory_chain_denies_rename_until_released(tmp_path: Path) -> None:
    root = (tmp_path / "held-root").resolve()
    root.mkdir()
    replacement = tmp_path / "replacement"

    with supervisor._held_directory_chain(root):
        with pytest.raises(OSError):
            root.rename(replacement)

    root.rename(replacement)
    assert replacement.is_dir()


def test_pinned_control_contract_is_held_through_worker_and_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins, _payload, authorization_path, environment, writes = _fixture(tmp_path)
    contract_copy = (tmp_path / "pinned-control-contract.py").resolve()
    contract_raw = Path(contract.__file__).read_bytes()
    contract_copy.write_bytes(contract_raw)
    mutation_blocked = False
    real_run_worker = supervisor._run_worker

    monkeypatch.setattr(
        supervisor,
        "_pinned_control_contract_path",
        lambda _pins: contract_copy,
    )

    def mutate_while_worker_runs(
        argv: list[str],
        *,
        cwd: str,
        environment: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[int, bytes, bytes]:
        nonlocal mutation_blocked
        try:
            contract_copy.write_bytes(b"X" * len(contract_raw))
        except OSError:
            mutation_blocked = True
        return real_run_worker(
            argv,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(supervisor, "_run_worker", mutate_while_worker_runs)

    result = _run_fixture(pins, authorization_path, environment, writes)

    assert result["status"] == "completed"
    assert mutation_blocked
    assert contract_copy.read_bytes() == contract_raw


def test_bootstrap_execution_authorization_requires_exact_shared_v2_payload(
    tmp_path: Path,
) -> None:
    pins, payload, _authorization_path, _environment, _writes = _fixture(tmp_path)
    authorization_path = Path(str(payload["bootstrap_execution_authorization_path"]))
    outer = json.loads(authorization_path.read_text(encoding="utf-8"))
    public_der = base64.b64decode(pins.execution_public_key_spki_der_base64)

    for mutation in ("missing_schema", "old_schema", "extra_field"):
        candidate = dict(outer["payload"])
        if mutation == "missing_schema":
            candidate.pop("schema", None)
        elif mutation == "old_schema":
            candidate["schema"] = "factor-v3-formal-bootstrap-execution-authorization/v1"
        else:
            candidate["unreviewed_extra"] = True
        signed = _canonical_bytes(
            {
                "payload": candidate,
                "signature_base64": base64.b64encode(
                    _sign(
                        tmp_path / "execution-key" / "execution-private.pem",
                        _canonical_bytes(candidate),
                        tmp_path / "execution-key",
                    )
                ).decode("ascii"),
            }
        )
        with pytest.raises(
            supervisor.FormalSupervisorError,
            match="authorization",
        ):
            supervisor._validated_bootstrap_execution_authorization(
                signed,
                payload=payload,
                public_der=public_der,
            )


@pytest.mark.parametrize("injection", ("extra_source", "absent_zip"))
def test_stdlib_exact_set_is_locked_before_terminal_filesystem_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    injection: str,
) -> None:
    pins, payload, authorization_path, environment, writes = _fixture(tmp_path)
    policy = json.loads(Path(str(payload["stdlib_policy_path"])).read_text(encoding="utf-8"))
    target = (
        Path(policy["roots"][1]["path"]) / "late_injected.py"
        if injection == "extra_source"
        else Path(policy["absent_paths"][0])
    )
    original = supervisor.validate_stdlib_policy
    filesystem_validations = 0
    injection_blocked = False

    def observed_validate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal filesystem_validations, injection_blocked
        result = original(*args, **kwargs)
        if kwargs.get("require_filesystem"):
            filesystem_validations += 1
            if filesystem_validations == 2:
                try:
                    target.write_bytes(b"LEAK = True\n")
                except OSError:
                    injection_blocked = True
        return result

    monkeypatch.setattr(supervisor, "validate_stdlib_policy", observed_validate)

    with pytest.raises(
        supervisor.FormalSupervisorError,
        match="stdlib|filesystem|directory",
    ):
        _run_fixture(pins, authorization_path, environment, writes)

    assert injection_blocked or target.exists()


def test_original_and_resume_share_one_atomic_worker_and_terminal_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins, payload, authorization_path, environment, original_writes = _fixture(tmp_path)
    original_sha256 = _file_sha256(authorization_path)
    original_outer = json.loads(authorization_path.read_bytes())
    original_signature_sha256 = _sha256(
        supervisor._decoded_signature(original_outer["signature_base64"])
    )
    entered_original = threading.Event()
    entered_resume = threading.Event()
    release_original = threading.Event()
    release_resume = threading.Event()
    real_run_worker = supervisor._run_worker
    worker_actions: list[str] = []

    def controlled_worker(
        argv: list[str],
        *,
        cwd: str,
        environment: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[int, bytes, bytes]:
        action = environment["FACTOR_V3_FORMAL_LAUNCH_ACTION"]
        worker_actions.append(action)
        if action == "resume":
            entered_resume.set()
            assert release_resume.wait(10)
        else:
            entered_original.set()
            assert release_original.wait(10)
        return real_run_worker(
            argv,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(supervisor, "_run_worker", controlled_worker)
    outcomes: dict[str, object] = {}

    def run_original() -> None:
        try:
            outcomes["original"] = _run_fixture(
                pins,
                authorization_path,
                environment,
                original_writes,
            )
        except BaseException as exc:
            outcomes["original"] = exc

    original_thread = threading.Thread(target=run_original)
    original_thread.start()
    assert entered_original.wait(10)
    claim_path = supervisor.claim_path_for_authorization(
        Path(str(payload["execution_ledger_root"])),
        original_sha256,
    )
    assert claim_path.is_file()
    claim_sha256 = _sha256(
        _canonical_bytes(
            {
                "action": "run",
                "authorization_id_sha256": payload["authorization_id_sha256"],
                "authorization_nonce_sha256": payload["authorization_nonce_sha256"],
                "bootstrap_execution_authorization_sha256": payload[
                    "bootstrap_execution_authorization_sha256"
                ],
                "launch_authorization_schema": supervisor.LAUNCH_AUTHORIZATION_SCHEMA,
                "launch_authorization_sha256": original_sha256,
                "launch_authorization_signature_sha256": (
                    original_signature_sha256
                ),
                "replay_scope": payload["replay_scope"],
                "schema": supervisor.CLAIM_SCHEMA,
                "status": "claimed",
            }
        )
    )
    payload.update(
        {
            "action": "resume",
            "resume_of_authorization_id_sha256": payload["authorization_id_sha256"],
            "resume_of_authorization_sha256": original_sha256,
            "resume_of_authorization_nonce_sha256": payload["authorization_nonce_sha256"],
            "resume_of_action": "run",
            "resume_of_bootstrap_execution_authorization_sha256": payload[
                "bootstrap_execution_authorization_sha256"
            ],
            "resume_of_launch_authorization_schema": supervisor.LAUNCH_AUTHORIZATION_SCHEMA,
            "resume_of_launch_authorization_signature_sha256": (
                original_signature_sha256
            ),
            "resume_of_replay_scope": payload["replay_scope"],
            "resume_status_path": str(claim_path),
            "resume_status_sha256": claim_sha256,
        }
    )
    resume_path = _rewrite_authorization(
        tmp_path,
        payload,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    resume_writes: list[bytes] = []

    def run_resume() -> None:
        try:
            outcomes["resume"] = _run_fixture(
                pins,
                resume_path,
                environment,
                resume_writes,
            )
        except BaseException as exc:
            outcomes["resume"] = exc

    resume_thread = threading.Thread(target=run_resume)
    resume_thread.start()
    if entered_resume.wait(1):
        release_resume.set()
        resume_thread.join(10)
    release_original.set()
    original_thread.join(10)
    resume_thread.join(10)

    assert worker_actions == ["run"]
    assert isinstance(outcomes["original"], dict)
    assert isinstance(outcomes["resume"], supervisor.FormalSupervisorError)
    assert len(original_writes) == 1
    assert resume_writes == []
