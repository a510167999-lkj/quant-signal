from __future__ import annotations

import ast
import inspect
from pathlib import Path

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
    assert contract.WORKER_TERMINAL_SCHEMA == (
        "factor-v3-formal-bootstrap-worker-terminal/v2"
    )
    assert contract.PUBLICATION_COMPLETION_SCHEMA == (
        "factor-v3-formal-bootstrap-publication-completion/v2"
    )
    assert contract.STDLIB_POLICY_SCHEMA == "factor-v3-formal-stdlib-policy/v2"
    assert contract.STDLIB_ROOT_ENVIRONMENT == (
        "FACTOR_V3_FORMAL_STDLIB_INVENTORY_ROOT_SHA256"
    )
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
    assert contract.validate_stdlib_policy(
        policy,
        expected_root_sha256=root_sha256,
        require_filesystem=True,
    ) == policy

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
