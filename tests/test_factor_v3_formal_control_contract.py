from __future__ import annotations

import ast
import base64
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

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


def test_supervisor_import_cannot_execute_dirty_shared_contract_before_pins(
    tmp_path: Path,
) -> None:
    package = (tmp_path / "untrusted-active-worktree" / "app").resolve()
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"")
    supervisor_source = Path(supervisor.__file__).read_bytes()
    (package / "factor_v3_formal_trusted_supervisor.py").write_bytes(supervisor_source)
    leak_path = (tmp_path / "shared-contract-token-leak.txt").resolve()
    (package / "factor_v3_formal_control_contract.py").write_text(
        "\n".join(
            (
                "import os",
                "from pathlib import Path",
                "Path(os.environ['LEAK_PATH']).write_text(",
                "    os.environ['JIAOCH_TOKEN'], encoding='utf-8'",
                ")",
                "EXECUTION_REPLAY_SCOPE = 'dirty'",
                "PUBLICATION_COMPLETION_SCHEMA = 'dirty'",
                "STDLIB_POLICY_SCHEMA = 'dirty'",
                "STDLIB_ROOT_ENVIRONMENT = 'DIRTY'",
                "WORKER_ACTION_BY_LAUNCH_ACTION = {}",
                "WORKER_PROTOCOL = 'dirty'",
                "WORKER_TERMINAL_FIELDS = []",
                "WORKER_TERMINAL_SCHEMA = 'dirty'",
                "def control_contract_descriptor_sha256(): return '0' * 64",
                "def exact_worker_argv(**_kwargs): return []",
                "def validate_stdlib_policy(value, **_kwargs): return value",
                "def worker_environment_policy():",
                "    return {'public_passthrough_names': [], "
                "'required_secret_names_by_action': {}, 'marker_names': []}",
            )
        ),
        encoding="utf-8",
    )
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "TEMP", "TMP", "WINDIR")
        if name in os.environ
    }
    environment.update(
        {
            "JIAOCH_TOKEN": "fake-token-must-not-be-observable",
            "LEAK_PATH": str(leak_path),
        }
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                f"import sys;sys.path.insert(0,{str(package.parent)!r});"
                "import app.factor_v3_formal_trusted_supervisor"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )

    assert completed.returncode != 0
    assert not leak_path.exists()


def test_flat_stdlib_policy_root_is_canonical_and_binds_absence_and_pycache_blocker(
    tmp_path: Path,
) -> None:
    stdlib = tmp_path / "Lib"
    platstdlib = tmp_path / "DLLs"
    pycache = tmp_path / "signed-empty-pycache"
    stdlib.mkdir()
    platstdlib.mkdir()
    pycache.write_bytes(b"factor-v3-pycache-blocker/v1\n")
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
    hardlink = tmp_path / "pycache-blocker-hardlink"
    os.link(pycache, hardlink)
    with pytest.raises(contract.FormalControlContractError, match="pycache"):
        contract.validate_stdlib_policy(
            policy,
            expected_root_sha256=root_sha256,
            require_filesystem=True,
        )
    hardlink.unlink()

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
    pycache.unlink()
    pycache.mkdir()
    with pytest.raises(contract.FormalControlContractError, match="pycache"):
        contract.validate_stdlib_policy(
            policy,
            expected_root_sha256=root_sha256,
            require_filesystem=True,
        )
    pycache.rmdir()
    pycache.write_bytes(b"factor-v3-pycache-blocker/v1\n")
    absent_zip.write_bytes(b"untrusted-zip")
    with pytest.raises(contract.FormalControlContractError, match="absent"):
        contract.validate_stdlib_policy(
            policy,
            expected_root_sha256=root_sha256,
            require_filesystem=True,
        )


def test_signed_pycache_location_cannot_accept_late_unchecked_pyc(
    tmp_path: Path,
) -> None:
    stdlib = (tmp_path / "Lib").resolve()
    platstdlib = (tmp_path / "DLLs").resolve()
    stdlib.mkdir()
    platstdlib.mkdir()
    victim = stdlib / "victim.py"
    malicious_source = tmp_path / "malicious-victim.py"
    leak_path = (tmp_path / "pycache-token-leak.txt").resolve()
    malicious = (
        "import os\n"
        "from pathlib import Path\n"
        f"Path({str(leak_path)!r}).write_text("
        "os.environ['JIAOCH_TOKEN'], encoding='utf-8')\n"
    ).encode()
    harmless = b"VALUE = 1\n#" + b" " * (len(malicious) - len(b"VALUE = 1\n#"))
    victim.write_bytes(harmless)
    malicious_source.write_bytes(malicious)
    timestamp = 1_700_000_000
    os.utime(victim, (timestamp, timestamp))
    os.utime(malicious_source, (timestamp, timestamp))
    entry = _entry(stdlib, "victim.py", "victim")
    canonical_parameters = inspect.signature(contract.canonical_stdlib_policy).parameters
    if "pycache_blocker" in canonical_parameters:
        blocker = (tmp_path / "signed-pycache-blocker").resolve()
        blocker.write_bytes(b"factor-v3-pycache-blocker/v1\n")
        policy = contract.canonical_stdlib_policy(
            roots=[
                {"path": str(platstdlib), "role": "platstdlib"},
                {"path": str(stdlib), "role": "stdlib"},
            ],
            entries=[entry],
            absent_paths=[str(tmp_path / "python311.zip")],
            pycache_blocker={
                "bytes": blocker.stat().st_size,
                "path": str(blocker),
                "sha256": contract.sha256_bytes(blocker.read_bytes()),
            },
        )
        prefix = blocker
    else:
        prefix = (tmp_path / "signed-empty-pycache").resolve()
        prefix.write_bytes(b"factor-v3-pycache-blocker/v1\n")
        policy = contract.canonical_stdlib_policy(
            roots=[
                {"path": str(platstdlib), "role": "platstdlib"},
                {"path": str(stdlib), "role": "stdlib"},
            ],
            entries=[entry],
            absent_paths=[str(tmp_path / "python311.zip")],
            pycache_prefix=str(prefix),
        )
    contract.validate_stdlib_policy(policy, require_filesystem=True)
    previous_prefix = sys.pycache_prefix
    try:
        sys.pycache_prefix = str(prefix)
        injected_pyc = Path(__import__("importlib").util.cache_from_source(str(victim)))
    finally:
        sys.pycache_prefix = previous_prefix
    try:
        injected_pyc.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(
            str(malicious_source),
            cfile=str(injected_pyc),
            dfile=str(victim),
            doraise=True,
        )
    except OSError:
        pass
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "TEMP", "TMP", "WINDIR")
        if name in os.environ
    }
    environment["JIAOCH_TOKEN"] = "fake-token-must-not-be-observable"

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-X",
            f"pycache_prefix={prefix}",
            "-c",
            f"import sys;sys.path.insert(0,{str(stdlib)!r});import victim",
        ],
        check=False,
        capture_output=True,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0
    assert not leak_path.exists()


def test_stdlib_zero_byte_rules_are_kind_exact(tmp_path: Path) -> None:
    stdlib = (tmp_path / "Lib").resolve()
    platstdlib = (tmp_path / "DLLs").resolve()
    pycache = (tmp_path / "pycache").resolve()
    stdlib.mkdir()
    platstdlib.mkdir()
    pycache.write_bytes(b"factor-v3-pycache-blocker/v1\n")
    empty_source = stdlib / "empty.py"
    empty_source.write_bytes(b"")
    source_entry = _entry(stdlib, "empty.py", "empty")

    policy = contract.canonical_stdlib_policy(
        roots=[
            {"path": str(platstdlib), "role": "platstdlib"},
            {"path": str(stdlib), "role": "stdlib"},
        ],
        entries=[source_entry],
        absent_paths=[str(tmp_path / "python311.zip")],
        pycache_prefix=str(pycache),
    )
    assert contract.validate_stdlib_policy(policy, require_filesystem=True) == policy

    for kind in ("extension", "dll"):
        binary = dict(source_entry)
        binary.update(
            {
                "is_package": False,
                "kind": kind,
                "module": "empty_binary" if kind == "extension" else None,
                "relative_path": "empty_binary.pyd" if kind == "extension" else "empty.dll",
            }
        )
        binary_path = (platstdlib / str(binary["relative_path"])).resolve()
        binary_path.write_bytes(b"")
        binary["path"] = str(binary_path)
        binary["root"] = str(platstdlib)
        with pytest.raises(
            contract.FormalControlContractError,
            match="entry",
        ):
            contract.canonical_stdlib_policy(
                roots=[
                    {"path": str(platstdlib), "role": "platstdlib"},
                    {"path": str(stdlib), "role": "stdlib"},
                ],
                entries=[binary],
                absent_paths=[str(tmp_path / "python311.zip")],
                pycache_prefix=str(pycache),
            )


def test_supervisor_stdlib_files_share_one_frozen_directory_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdlib = (tmp_path / "fake-python" / "Lib").resolve()
    platstdlib = (tmp_path / "fake-python" / "DLLs").resolve()
    pycache = (tmp_path / "pycache-blocker").resolve()
    stdlib.mkdir(parents=True)
    platstdlib.mkdir(parents=True)
    pycache.write_bytes(b"factor-v3-pycache-blocker/v1\n")
    entries = []
    for index in range(12):
        relative_path = f"fixture_{index}.py"
        path = stdlib / relative_path
        path.write_bytes(f"VALUE = {index}\n".encode("ascii"))
        entries.append(_entry(stdlib, relative_path, f"fixture_{index}"))
    policy = contract.canonical_stdlib_policy(
        roots=[
            {"path": str(platstdlib), "role": "platstdlib"},
            {"path": str(stdlib), "role": "stdlib"},
        ],
        entries=entries,
        absent_paths=[str(stdlib.parent / "python311.zip")],
        pycache_prefix=str(pycache),
    )
    root_sha256 = contract.stdlib_policy_root_sha256(policy)
    held_chain_parents: list[Path] = []
    original_init = supervisor._HeldDirectoryChain.__init__

    def observed_init(self: object, path: Path) -> None:
        held_chain_parents.append(path)
        original_init(self, path)

    monkeypatch.setattr(supervisor._HeldDirectoryChain, "__init__", observed_init)

    with ExitStack() as stack:
        handles = supervisor._hold_stdlib_inventory(
            contract.canonical_bytes(policy),
            payload={"stdlib_inventory_root_sha256": root_sha256},
            authorization={"stdlib_policy": policy},
            stack=stack,
        )

        stdlib_roots = (stdlib, platstdlib)
        per_file_chains = [
            path
            for path in held_chain_parents
            if any(path == root or root in path.parents for root in stdlib_roots)
        ]
        assert per_file_chains == []
        directory_guards = [
            handle for handle in handles if isinstance(handle, supervisor._HeldFrozenDirectoryTree)
        ]
        assert len(directory_guards) == 1


def test_shared_stdlib_directory_guard_terminally_reopens_every_held_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdlib = (tmp_path / "fake-python" / "Lib").resolve()
    platstdlib = (tmp_path / "fake-python" / "DLLs").resolve()
    nested = stdlib / "nested"
    pycache = (tmp_path / "pycache-blocker").resolve()
    nested.mkdir(parents=True)
    platstdlib.mkdir(parents=True)
    pycache.write_bytes(b"factor-v3-pycache-blocker/v1\n")
    source = nested / "fixture.py"
    source.write_bytes(b"VALUE = 1\n")
    policy = contract.canonical_stdlib_policy(
        roots=[
            {"path": str(platstdlib), "role": "platstdlib"},
            {"path": str(stdlib), "role": "stdlib"},
        ],
        entries=[_entry(stdlib, "nested/fixture.py", "nested.fixture")],
        absent_paths=[str(stdlib.parent / "python311.zip")],
        pycache_prefix=str(pycache),
    )
    root_sha256 = contract.stdlib_policy_root_sha256(policy)

    with ExitStack() as stack:
        handles = supervisor._hold_stdlib_inventory(
            contract.canonical_bytes(policy),
            payload={"stdlib_inventory_root_sha256": root_sha256},
            authorization={"stdlib_policy": policy},
            stack=stack,
        )
        guard = next(
            handle for handle in handles if isinstance(handle, supervisor._HeldFrozenDirectoryTree)
        )
        expected_paths = {
            os.path.normcase(str(path)) for _handle, path, _identity in guard._handles
        }
        reopened_paths: list[Path] = []
        original_open = supervisor._open_directory_handle

        def observed_open(path: Path, **kwargs: object) -> tuple[object, tuple[int, int, int]]:
            reopened_paths.append(path)
            return original_open(path, **kwargs)

        monkeypatch.setattr(supervisor, "_open_directory_handle", observed_open)

        guard.postverify()

        assert {os.path.normcase(str(path)) for path in reopened_paths} == expected_paths
        assert len(reopened_paths) == len(expected_paths)


def test_runtime_installs_early_exact_loader_before_filesystem_imports() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    marker = "# EARLY_EXACT_IMPORT_BOUNDARY_COMPLETE"
    assert marker in source
    prefix = source.split(marker, 1)[0]
    assert "import sys" in prefix
    assert "from pathlib import Path" not in prefix
    assert "\nimport os\n" not in prefix
    assert "\nimport importlib.machinery\n" not in prefix
    for raw in (b"", b"abc", bytes(range(256))):
        assert runtime._early_sha256(raw) == contract.sha256_bytes(raw)


def test_rendered_supervisor_has_a_real_public_cli_entrypoint() -> None:
    source = Path(supervisor.__file__).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert (
        "supervise_factor_v3_formal_execution"
        in source.split(
            'if __name__ == "__main__":',
            1,
        )[1]
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

    now = datetime.now(timezone.utc).replace(microsecond=0)
    config, execution_payload, execution_authorization_path, public_der = (
        _be3_crossline_authorized_fixture(
            tmp_path,
            authorization_now=now,
        )
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
        control_contract_source_sha256=supervisor._CONTROL_CONTRACT_SOURCE_SHA256,
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
        "control_contract_source_sha256": pins.control_contract_source_sha256,
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
    rendered_supervisor_path = (tmp_path / "rendered-formal-supervisor.py").resolve()
    rendered_supervisor_path.write_bytes(supervisor._render_supervisor_with_test_pins(pins))
    completed = subprocess.run(
        [
            pins.python_executable_path,
            "-B",
            str(rendered_supervisor_path),
            str(launch_path),
        ],
        check=False,
        capture_output=True,
        env=environment,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    frame = json.loads(completed.stdout)
    assert frame["schema"] == contract.WORKER_TERMINAL_SCHEMA
    assert frame["result"] == {
        "run_root": execution_payload["run_root"],
        "run_spec_path": execution_payload["run_spec_path"],
        "source": "be3-crossline-lightweight-runner",
        "status": "verified",
    }
