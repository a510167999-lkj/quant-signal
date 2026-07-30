from __future__ import annotations

import ast
import base64
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import subprocess

import pytest

from app import factor_v3_formal_bootstrap_renderer as renderer
from app import factor_v3_formal_bootstrap_runtime as runtime
from app import factor_v3_formal_control_contract as contract
from app import factor_v3_formal_trusted_supervisor as supervisor


def _entry(root: Path, relative_path: str, module: str) -> dict[str, object]:
    path = root / Path(*relative_path.split("/"))
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "is_package": False,
        "kind": "source",
        "module": module,
        "path": str(path),
        "relative_path": relative_path,
        "root": str(root),
        "sha256": contract.sha256_bytes(raw),
    }


def test_shared_contract_is_the_only_v2_protocol_definition() -> None:
    assert contract.WORKER_PROTOCOL == "factor-v3-formal-supervisor-worker/v2"
    assert contract.WORKER_TERMINAL_SCHEMA == ("factor-v3-formal-bootstrap-worker-terminal/v2")
    assert contract.PUBLICATION_COMPLETION_SCHEMA == (
        "factor-v3-formal-bootstrap-publication-completion/v2"
    )
    assert contract.STDLIB_POLICY_SCHEMA == "factor-v3-formal-stdlib-policy/v2"
    assert contract.STDLIB_ROOT_ENVIRONMENT == ("FACTOR_V3_FORMAL_STDLIB_INVENTORY_ROOT_SHA256")
    assert contract.WORKER_ACTION_BY_LAUNCH_ACTION == {
        "build-spec": "build-spec",
        "resume": "run",
        "run": "run",
        "verify": "verify",
    }
    expected = contract.worker_protocol_descriptor()
    assert renderer._supervisor_protocol_descriptor() == expected
    assert runtime._supervisor_protocol_descriptor() == expected
    assert supervisor._fresh_environment_policy() == contract.worker_environment_policy()

    copied_literals = {
        contract.WORKER_PROTOCOL,
        contract.WORKER_TERMINAL_SCHEMA,
        contract.STDLIB_POLICY_SCHEMA,
        contract.STDLIB_ROOT_ENVIRONMENT,
    }
    for module in (renderer, supervisor):
        literals = {
            node.value
            for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert copied_literals.isdisjoint(literals)


def test_flat_stdlib_policy_root_is_canonical_and_binds_absence_and_empty_pycache(
    tmp_path: Path,
) -> None:
    stdlib = tmp_path / "Lib"
    platstdlib = tmp_path / "DLLs"
    pycache = tmp_path / "signed-empty-pycache"
    stdlib.mkdir()
    platstdlib.mkdir()
    pycache.mkdir()
    (stdlib / "alpha.py").write_bytes(b"VALUE = 1\n")
    (platstdlib / "beta.py").write_bytes(b"VALUE = 2\n")
    absent_zip = tmp_path / "python313.zip"
    entries = [
        _entry(platstdlib, "beta.py", "beta"),
        _entry(stdlib, "alpha.py", "alpha"),
    ]

    policy = contract.canonical_stdlib_policy(
        roots=[
            {"path": str(platstdlib), "role": "platstdlib"},
            {"path": str(stdlib), "role": "stdlib"},
        ],
        entries=entries,
        absent_paths=[str(absent_zip)],
        pycache_prefix=str(pycache),
    )
    reordered = contract.canonical_stdlib_policy(
        roots=[
            {"path": str(stdlib), "role": "stdlib"},
            {"path": str(platstdlib), "role": "platstdlib"},
        ],
        entries=list(reversed(entries)),
        absent_paths=[str(absent_zip)],
        pycache_prefix=str(pycache),
    )

    assert policy == reordered
    assert set(policy) == {
        "absent_paths",
        "entries",
        "pycache_prefix",
        "roots",
        "schema",
    }
    assert policy["roots"] == [
        {"path": str(platstdlib), "role": "platstdlib"},
        {"path": str(stdlib), "role": "stdlib"},
    ]
    assert list(policy["entries"][0]) == [
        "bytes",
        "is_package",
        "kind",
        "module",
        "path",
        "relative_path",
        "root",
        "sha256",
    ]
    assert policy["absent_paths"] == [str(absent_zip)]
    assert policy["pycache_prefix"] == str(pycache)
    root_sha256 = contract.stdlib_policy_root_sha256(policy)
    assert (
        contract.validate_stdlib_policy(
            policy,
            expected_root_sha256=root_sha256,
            require_filesystem=True,
        )
        == policy
    )

    for mutation in (
        {**policy, "absent_paths": []},
        {**policy, "pycache_prefix": str(tmp_path / "other")},
        {
            **policy,
            "entries": [
                *policy["entries"],
                {
                    **policy["entries"][0],
                    "module": "new_module",
                    "relative_path": "new_module.py",
                    "path": str(stdlib / "new_module.py"),
                },
            ],
        },
    ):
        with pytest.raises(contract.FormalControlContractError):
            contract.validate_stdlib_policy(
                mutation,
                expected_root_sha256=root_sha256,
            )

    new_module = stdlib / "new_module.py"
    new_module.write_bytes(b"VALUE = 3\n")
    with pytest.raises(contract.FormalControlContractError, match="inventory"):
        contract.validate_stdlib_policy(
            policy,
            expected_root_sha256=root_sha256,
            require_filesystem=True,
        )
    new_module.unlink()
    injected_pyc = pycache / "alpha.pyc"
    injected_pyc.write_bytes(b"untrusted-pyc")
    with pytest.raises(contract.FormalControlContractError, match="pycache"):
        contract.validate_stdlib_policy(
            policy,
            expected_root_sha256=root_sha256,
            require_filesystem=True,
        )
    injected_pyc.unlink()
    absent_zip.write_bytes(b"untrusted-zip")
    with pytest.raises(contract.FormalControlContractError, match="absent"):
        contract.validate_stdlib_policy(
            policy,
            expected_root_sha256=root_sha256,
            require_filesystem=True,
        )


def test_exact_worker_argv_signs_empty_pycache_prefix(tmp_path: Path) -> None:
    python = tmp_path / "python.exe"
    worker = tmp_path / "bootstrap.py"
    pycache = tmp_path / "pycache"
    assert contract.exact_worker_argv(
        python_executable_path=str(python),
        bootstrap_worker_path=str(worker),
        pycache_prefix=str(pycache),
    ) == [
        str(python),
        "-I",
        "-B",
        "-S",
        "-X",
        f"pycache_prefix={pycache}",
        str(worker),
    ]


def test_runtime_uses_manifest_finder_without_general_path_or_sourceless_loader() -> None:
    source = inspect.getsource(runtime._freeze_stdlib_import_boundary)
    assert "_ManifestStdlibFinder" in source
    assert "PathFinder" not in source
    assert "SourcelessFileLoader" not in source
    trusted_run = inspect.getsource(runtime._trusted_run)
    assert "importlib.machinery.PathFinder" not in trusted_run
    assert "sys.pycache_prefix = " not in trusted_run


def test_resume_replay_tuple_rejects_a_completed_original_authorization(
    tmp_path: Path,
) -> None:
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir()
    original_sha256 = "1" * 64
    completed = supervisor.completed_path_for_authorization(
        ledger_root,
        original_sha256,
    )
    completed.parent.mkdir(parents=True)
    completed.write_bytes(b"completed")
    payload = {
        "action": "resume",
        "execution_ledger_root": str(ledger_root),
        "resume_of_authorization_sha256": original_sha256,
    }
    with pytest.raises(supervisor.FormalSupervisorError, match="completed"):
        supervisor._reject_completed_resume(payload)


def test_actual_completion_supervisor_runtime_and_be3_dispatch_chain(
    tmp_path: Path,
) -> None:
    from tests.test_factor_v3_formal_bootstrap_authorization import (
        _be3_crossline_authorized_fixture,
        _canonical_bytes,
        _execution_test_key,
        _sign,
        _write_completion_authorization,
    )

    config, execution_payload, execution_authorization_path, public_der = (
        _be3_crossline_authorized_fixture(tmp_path)
    )
    completion_payload = renderer._plan_factor_v3_formal_bootstrap_publication_with_test_trust(
        authorization_path=execution_authorization_path,
        trusted_public_key_spki_der=public_der,
    )
    completion_authorization_path = _write_completion_authorization(
        tmp_path,
        completion_payload,
    )
    publication = renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=execution_authorization_path,
        completion_authorization_path=completion_authorization_path,
        trusted_public_key_spki_der=public_der,
    )

    integration_root = Path(supervisor.__file__).resolve().parents[1]
    supervisor_source = Path(supervisor.__file__).resolve()
    git_path = Path(str(execution_payload["git_executable_path"]))
    supervisor_commit = subprocess.run(
        [str(git_path), "-C", str(integration_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    completion_path = Path(str(publication["completion_marker_path"]))
    pins = supervisor._SupervisorPins(
        base_python_executable_path=str(execution_payload["base_python_executable_path"]),
        base_python_executable_sha256=str(execution_payload["base_python_executable_sha256"]),
        bootstrap_completion_marker_path=str(completion_path),
        bootstrap_completion_marker_sha256=contract.sha256_bytes(completion_path.read_bytes()),
        bootstrap_completion_schema=contract.PUBLICATION_COMPLETION_SCHEMA,
        execution_public_key_spki_der_base64=base64.b64encode(public_der).decode("ascii"),
        execution_public_key_spki_sha256=contract.sha256_bytes(public_der),
        git_executable_path=str(git_path),
        git_executable_sha256=str(execution_payload["git_executable_sha256"]),
        python_executable_path=str(execution_payload["python_executable_path"]),
        python_executable_sha256=str(execution_payload["python_executable_sha256"]),
        repo_root=str(integration_root),
        supervisor_expected_commit=supervisor_commit,
        supervisor_source_relative_path=("app/factor_v3_formal_trusted_supervisor.py"),
        supervisor_source_sha256=contract.sha256_bytes(supervisor_source.read_bytes()),
        worker_protocol=contract.WORKER_PROTOCOL,
        worker_terminal_schema=contract.WORKER_TERMINAL_SCHEMA,
    )
    now = datetime(2026, 7, 30, 12, 5, 0, tzinfo=timezone.utc)
    ledger_root = (tmp_path / "actual-combined-ledger").resolve()
    ledger_root.mkdir()
    launch_payload = {
        "action": "verify",
        "authorization_id_sha256": execution_payload["authorization_id_sha256"],
        "authorization_nonce_sha256": execution_payload["authorization_nonce_sha256"],
        "base_python_executable_path": pins.base_python_executable_path,
        "base_python_executable_sha256": pins.base_python_executable_sha256,
        "bootstrap_execution_authorization_path": str(execution_authorization_path),
        "bootstrap_execution_authorization_sha256": contract.sha256_bytes(
            execution_authorization_path.read_bytes()
        ),
        "bootstrap_output_root": publication["bootstrap_output_root"],
        "bootstrap_worker_path": publication["bootstrap_path"],
        "bootstrap_worker_sha256": publication["bootstrap_sha256"],
        "control_contract_descriptor_sha256": (contract.control_contract_descriptor_sha256()),
        "environment_policy": contract.worker_environment_policy(),
        "execution_key_id": f"sha256:{pins.execution_public_key_spki_sha256}",
        "execution_ledger_root": str(ledger_root),
        "expires_at_utc": (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
        "formal_input_root_path": execution_payload["formal_input_root_path"],
        "formal_input_root_sha256": execution_payload["formal_input_root_sha256"],
        "formal_output_root": execution_payload["formal_output_root"],
        "git_executable_path": pins.git_executable_path,
        "git_executable_sha256": pins.git_executable_sha256,
        "issued_at_utc": now.isoformat(timespec="seconds"),
        "project_id": "quant-signal-lkj",
        "publication_completion_marker_path": str(completion_path),
        "publication_completion_marker_sha256": pins.bootstrap_completion_marker_sha256,
        "publication_completion_schema": pins.bootstrap_completion_schema,
        "python_executable_path": pins.python_executable_path,
        "python_executable_sha256": pins.python_executable_sha256,
        "repo_root": execution_payload["repo_root"],
        "replay_scope": contract.EXECUTION_REPLAY_SCOPE,
        "resume_of_authorization_id_sha256": None,
        "resume_of_authorization_nonce_sha256": None,
        "resume_of_authorization_sha256": None,
        "resume_of_bootstrap_execution_authorization_sha256": None,
        "resume_of_replay_scope": None,
        "resume_status_path": None,
        "resume_status_sha256": None,
        "review_public_key_spki_sha256": execution_payload["review_public_key_spki_sha256"],
        "reviewed_commit": execution_payload["expected_commit"],
        "run_root": execution_payload["run_root"],
        "run_spec_path": execution_payload["run_spec_path"],
        "run_spec_sha256": execution_payload["run_spec_sha256"],
        "schema": supervisor.LAUNCH_AUTHORIZATION_SCHEMA,
        "source_root_sha256": execution_payload["source_root_sha256"],
        "stdlib_inventory_root_sha256": execution_payload["stdlib_policy_root_sha256"],
        "stdlib_policy_path": publication["stdlib_policy_path"],
        "stdlib_policy_sha256": publication["stdlib_policy_sha256"],
        "supervisor_expected_commit": pins.supervisor_expected_commit,
        "supervisor_source_sha256": pins.supervisor_source_sha256,
        "worker_action": "verify",
        "worker_argv": contract.exact_worker_argv(
            python_executable_path=pins.python_executable_path,
            bootstrap_worker_path=str(publication["bootstrap_path"]),
            pycache_prefix=str(execution_payload["stdlib_policy"]["pycache_prefix"]),
        ),
        "worker_protocol": contract.WORKER_PROTOCOL,
        "worker_pycache_prefix": execution_payload["stdlib_policy"]["pycache_prefix"],
        "worker_terminal_schema": contract.WORKER_TERMINAL_SCHEMA,
        "worker_timeout_seconds": 120,
    }
    private_key, _execution_public_der = _execution_test_key(tmp_path)
    launch_raw = _canonical_bytes(
        {
            "payload": launch_payload,
            "signature_base64": base64.b64encode(
                _sign(
                    tmp_path,
                    private_key,
                    _canonical_bytes(launch_payload),
                )
            ).decode("ascii"),
        }
    )
    launch_sha256 = contract.sha256_bytes(launch_raw)
    launch_directory = tmp_path / "launch_authorizations" / "sha256" / launch_sha256[:2]
    launch_directory.mkdir(parents=True)
    launch_path = (launch_directory / f"{launch_sha256}.json").resolve()
    launch_path.write_bytes(launch_raw)
    environment = {
        name: os.environ[name] for name in contract.PUBLIC_ENVIRONMENT if name in os.environ
    }
    writes: list[bytes] = []

    result = supervisor._supervise_with_pins(
        authorization_path=launch_path,
        pins=pins,
        now_utc=now,
        environment_snapshot=environment,
        output_writer=lambda _descriptor, raw: writes.append(bytes(raw)) or len(bytes(raw)),
    )

    assert result["status"] == "completed"
    assert len(writes) == 1
    frame = json.loads(writes[0])
    assert frame["schema"] == contract.WORKER_TERMINAL_SCHEMA
    assert frame["result"] == {
        "run_root": execution_payload["run_root"],
        "run_spec_path": execution_payload["run_spec_path"],
        "source": "be3-crossline-lightweight-runner",
        "status": "verified",
    }
