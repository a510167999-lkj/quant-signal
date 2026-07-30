from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
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
        "resume_of_bootstrap_execution_authorization_sha256": None,
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
    assert len(writes) == 1
    frame = json.loads(writes[0])
    assert frame["launch_authorization_sha256"] == result["launch_authorization_sha256"]
    assert (
        frame["bootstrap_execution_authorization_sha256"]
        == (payload["bootstrap_execution_authorization_sha256"])
    )
    assert frame["status"] == "completed"
    assert "fixture-secret-must-never-be-logged" not in writes[0].decode("utf-8")


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
            "resume_of_bootstrap_execution_authorization_sha256": (
                original_bootstrap_execution_authorization_sha256
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
    payload.update(
        {
            "action": "resume",
            "resume_of_authorization_id_sha256": payload["authorization_id_sha256"],
            "resume_of_authorization_sha256": original_sha256,
            "resume_of_authorization_nonce_sha256": payload["authorization_nonce_sha256"],
            "resume_of_bootstrap_execution_authorization_sha256": payload[
                "bootstrap_execution_authorization_sha256"
            ],
            "resume_of_replay_scope": payload["replay_scope"],
            "resume_status_path": str(claim_path),
            "resume_status_sha256": _file_sha256(claim_path),
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
