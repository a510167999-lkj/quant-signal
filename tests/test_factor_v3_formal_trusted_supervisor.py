from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from app import factor_v3_formal_trusted_supervisor as supervisor
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
        f"if {fail!r}:\n"
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
        "    'schema': 'factor-v3-formal-bootstrap-worker-terminal/v1',\n"
        "    'status': 'completed',\n"
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
    pins = supervisor._SupervisorPins(
        base_python_executable_path=str(base_python_path),
        base_python_executable_sha256=_file_sha256(base_python_path),
        execution_public_key_pem_path=str(execution_public_pem),
        execution_public_key_spki_sha256=_sha256(execution_public_der),
        git_executable_path=str(git_path),
        git_executable_sha256=_file_sha256(git_path),
        python_executable_path=str(python_path),
        python_executable_sha256=_file_sha256(python_path),
        repo_root=str(repo_root),
        supervisor_expected_commit=_git("rev-parse", "HEAD", cwd=repo_root),
        supervisor_source_relative_path="app/factor_v3_formal_trusted_supervisor.py",
        supervisor_source_sha256=_file_sha256(source_path),
    )

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
    authorization_nonce_sha256 = _sha256(b"supervisor-test-nonce")
    bootstrap_execution_authorization_sha256 = _sha256(b"bootstrap-execution-authorization")
    worker_raw = _worker_source(
        artifact_path=artifact_path,
        artifact_raw=artifact_raw,
        bootstrap_execution_authorization_sha256=bootstrap_execution_authorization_sha256,
        authorization_nonce_sha256=authorization_nonce_sha256,
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
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "action": action,
        "authorization_nonce_sha256": authorization_nonce_sha256,
        "base_python_executable_path": str(base_python_path),
        "base_python_executable_sha256": pins.base_python_executable_sha256,
        "bootstrap_execution_authorization_sha256": (
            bootstrap_execution_authorization_sha256
        ),
        "bootstrap_output_root": str(bootstrap_output_root),
        "bootstrap_worker_path": str(worker_path),
        "bootstrap_worker_sha256": worker_sha256,
        "environment_policy": supervisor.WORKER_ENVIRONMENT_POLICY,
        "execution_key_id": f"sha256:{pins.execution_public_key_spki_sha256}",
        "execution_ledger_root": str(ledger_root),
        "expires_at_utc": (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
        "formal_input_root_path": str(formal_input_root),
        "formal_input_root_sha256": _sha256(b"formal-input-root"),
        "formal_output_root": str(formal_output_root),
        "git_executable_path": str(git_path),
        "git_executable_sha256": pins.git_executable_sha256,
        "issued_at_utc": now.isoformat(timespec="seconds"),
        "project_id": "quant-signal-lkj",
        "publication_completion_receipt_path": str(publication_path),
        "publication_completion_receipt_sha256": publication_sha256,
        "python_executable_path": str(python_path),
        "python_executable_sha256": pins.python_executable_sha256,
        "repo_root": str(repo_root),
        "resume_of_authorization_sha256": None,
        "resume_status_path": None,
        "resume_status_sha256": None,
        "review_public_key_spki_sha256": _sha256(review_public_der),
        "reviewed_commit": _git("rev-parse", "HEAD", cwd=repo_root),
        "run_root": str(run_root),
        "run_spec_path": str(run_spec_path),
        "run_spec_sha256": _sha256(run_spec_raw),
        "schema": "factor-v3-formal-supervisor-launch-authorization/v1",
        "source_root_sha256": _sha256(b"reviewed-source-root"),
        "supervisor_expected_commit": pins.supervisor_expected_commit,
        "supervisor_source_sha256": pins.supervisor_source_sha256,
        "worker_action": worker_action,
        "worker_argv": [
            str(python_path),
            "-I",
            "-B",
            "-S",
            str(worker_path),
        ],
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
    environment["JIAOCH_TOKEN"] = "fixture-secret-must-never-be-logged"
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
    return supervisor._supervise_with_pins(
        authorization_path=authorization_path,
        pins=pins,
        now_utc=datetime(2026, 7, 30, 12, 1, 0, tzinfo=timezone.utc),
        environment_snapshot=environment,
        output_writer=_writer(writes),
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
    parameters = inspect.signature(
        supervisor.supervise_factor_v3_formal_execution
    ).parameters

    assert tuple(parameters) == ("authorization_path",)


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
    assert frame["bootstrap_execution_authorization_sha256"] == (
        payload["bootstrap_execution_authorization_sha256"]
    )
    assert frame["status"] == "completed"
    assert "fixture-secret-must-never-be-logged" not in writes[0].decode("utf-8")


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("worker_argv", ["python", "-c", "prelude"]),
        ("bootstrap_worker_sha256", "0" * 64),
        ("publication_completion_receipt_sha256", "0" * 64),
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

    claim_path = supervisor.claim_path_for_authorization(
        Path(str(payload["execution_ledger_root"])),
        original_sha256,
    )
    claim_sha256 = _file_sha256(claim_path)
    worker_path = Path(str(payload["bootstrap_worker_path"]))
    worker_raw = _worker_source(
        artifact_path=Path(str(payload["formal_output_root"])) / "terminal-artifact.json",
        artifact_raw=_canonical_bytes({"status": "verified"}),
        bootstrap_execution_authorization_sha256=str(
            payload["bootstrap_execution_authorization_sha256"]
        ),
        authorization_nonce_sha256=_sha256(b"resume-nonce"),
        worker_action="run",
    )
    assert len(worker_raw) > 0
    worker_path.write_bytes(worker_raw)
    payload.update(
        {
            "action": "resume",
            "authorization_nonce_sha256": _sha256(b"resume-nonce"),
            "bootstrap_worker_sha256": _sha256(worker_raw),
            "resume_of_authorization_sha256": original_sha256,
            "resume_status_path": str(claim_path),
            "resume_status_sha256": claim_sha256,
            "worker_argv": [
                payload["python_executable_path"],
                "-I",
                "-B",
                "-S",
                str(worker_path),
            ],
        }
    )
    publication = json.loads(
        Path(str(payload["publication_completion_receipt_path"])).read_text(encoding="utf-8")
    )
    publication["bootstrap_bytes"] = len(worker_raw)
    publication["bootstrap_sha256"] = _sha256(worker_raw)
    publication_raw = _canonical_bytes(publication)
    publication_path, publication_sha256 = _cas_write(
        Path(str(payload["bootstrap_output_root"])),
        "publication_receipts",
        publication_raw,
        ".json",
    )
    payload["publication_completion_receipt_path"] = str(publication_path)
    payload["publication_completion_receipt_sha256"] = publication_sha256
    resume_path = _rewrite_authorization(
        tmp_path,
        payload,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    writes.clear()

    result = _run_fixture(pins, resume_path, environment, writes)

    assert result["status"] == "completed"
    assert json.loads(writes[0])["launch_action"] == "resume"


def test_held_directory_chain_denies_rename_until_released(tmp_path: Path) -> None:
    root = (tmp_path / "held-root").resolve()
    root.mkdir()
    replacement = tmp_path / "replacement"

    with supervisor._held_directory_chain(root):
        with pytest.raises(OSError):
            root.rename(replacement)

    root.rename(replacement)
    assert replacement.is_dir()
