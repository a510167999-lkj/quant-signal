from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.verify_shallow_gbdt_probability_budget_development_1 as verifier


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalized_probability_binding() -> dict[str, object]:
    body = {
        "schema_version": (
            "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1"
        ),
        "base_shallow_gbdt_producer_root_sha256": "a" * 64,
    }
    return {**body, "root_sha256": _sha256(_canonical_bytes(body))}


def _execution_snapshot() -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": verifier.SNAPSHOT_SCHEMA,
        "code": {
            "git_commit": verifier.EXPECTED_SOURCE_COMMIT,
            "git_tree_sha1": "a" * 40,
            "manifest_sha256": "b" * 64,
        },
        "runtime": {
            "python_executable_sha256": "c" * 64,
            "python_abi": {
                "implementation": "cpython",
                "cache_tag": "cpython-311",
                "version": [3, 11, 0],
            },
            "dependency_plan_sha256": "d" * 64,
            "manifest_sha256": "e" * 64,
            "xgboost_relocation": {
                "normalized_probability_binding": _normalized_probability_binding()
            },
        },
        "data": {"manifest_sha256": "f" * 64, "file_count": 1},
    }
    return {**body, "snapshot_sha256": _sha256(_canonical_bytes(body))}


class _FakeSnapshot:
    def __init__(self, tmp_path: Path, inputs: dict[str, object]):
        self.root = tmp_path
        self.code_root = tmp_path / "code"
        self.runtime_root = tmp_path / "runtime"
        self.data_root = tmp_path / "data"
        self.python_executable = tmp_path / "interpreter" / "python.exe"
        for path in (self.code_root, self.runtime_root, self.data_root):
            path.mkdir(exist_ok=True)
        self.python_executable.parent.mkdir()
        self.python_executable.write_bytes(b"snapshot-python")
        self.xgboost_relocation_descriptor_path = tmp_path / "xgboost-relocation.json"
        self.xgboost_relocation_descriptor_path.write_text("{}\n", encoding="utf-8")
        self.snapshot_inputs = inputs
        self.formal_raw_producer = {
            "probability_binding": {"root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256},
            "xgboost_build_info": {"libxgboost": "C:/formal/libxgboost.dll"},
        }
        self.snapshot_raw_producer = {
            "xgboost_build_info": {"libxgboost": "C:/snapshot/libxgboost.dll"},
        }
        self.xgboost_relocation_public = {
            "normalized_probability_binding": _normalized_probability_binding(),
            "normalized_shallow_binding": {
                "xgboost_build_info": {
                    "libxgboost": "<relocated-xgboost-library>"
                }
            },
        }
        self._binding = _execution_snapshot()
        self.verify_calls = 0

    def __enter__(self) -> "_FakeSnapshot":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def verify(self) -> None:
        self.verify_calls += 1

    def binding(self) -> dict[str, object]:
        self.verify()
        return dict(self._binding)


class _FakePublisher:
    def __init__(self, root: Path):
        self.root = root
        self.terminal = False

    def __enter__(self) -> "_FakePublisher":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def assert_bound(self) -> None:
        return None

    def mark_terminal_status_committed(self) -> None:
        self.terminal = True

    def root_file_exists(self, name: str) -> bool:
        return (self.root / name).is_file()

    def write_root_once(
        self,
        *,
        name: str,
        raw: bytes,
        label: str,
        tolerate_close_failure: bool = False,
    ) -> Path:
        del label, tolerate_close_failure
        path = self.root / name
        if path.exists() and path.read_bytes() != raw:
            raise verifier.IndependentVerificationError("fake publisher collision")
        if not path.exists():
            path.write_bytes(raw)
        return path


def _write_content_addressed(directory: Path, body: dict[str, object]) -> tuple[Path, str, str]:
    artifact_sha256 = _sha256(_canonical_bytes(body))
    document = {**body, "artifact_sha256": artifact_sha256}
    raw = _canonical_bytes(document) + b"\n"
    path = directory / f"{artifact_sha256}.json"
    path.write_bytes(raw)
    return path, artifact_sha256, _sha256(raw)


def _runtime_document(
    main_artifact_sha256: str,
    sidecar_artifact_sha256: dict[str, str],
    *,
    producer_root_sha256: str = verifier.EXPECTED_PRODUCER_ROOT_SHA256,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": verifier.EXPECTED_VERIFICATION_SCHEMA,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": producer_root_sha256,
        "main_artifact_sha256": main_artifact_sha256,
        "sidecar_artifact_sha256": sidecar_artifact_sha256,
        "checks": {name: True for name in verifier.REQUIRED_VERIFICATION_CHECKS},
        "verified": True,
    }
    body["receipt_sha256"] = _sha256(_canonical_bytes(body))
    return body


def _make_completed_inputs(tmp_path: Path) -> dict[str, object]:
    run_root = tmp_path / verifier.RUN_ROOT_RELATIVE
    run_root.mkdir(parents=True)
    producer_code = {"root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256}
    sidecar_dir = run_root / "sidecars"
    sidecar_dir.mkdir()
    sidecar_paths: dict[str, Path] = {}
    sidecar_hashes: dict[str, str] = {}
    for name in sorted(verifier.RESULT_BUNDLE_SIDECAR_NAMES):
        path, digest, _file_sha = _write_content_addressed(
            sidecar_dir,
            {
                "schema_version": "test-sidecar/v1",
                "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
                "producer_code": producer_code,
                "sidecar_name": name,
            },
        )
        sidecar_paths[name] = path
        sidecar_hashes[name] = digest
    main_path, main_sha, main_file_sha = _write_content_addressed(
        run_root,
        {
            "schema_version": "test-main/v1",
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "producer_code": producer_code,
            "sidecars": {
                name: {
                    "artifact_sha256": sidecar_hashes[name],
                    "relative_path": f"sidecars/{sidecar_hashes[name]}.json",
                }
                for name in sorted(verifier.RESULT_BUNDLE_SIDECAR_NAMES)
            },
            "scope": {
                "point_in_time": True,
                "development_only": True,
                "embargo_consumed": False,
                "final_oos_consumed": False,
                "eligible_for_profile_registration": False,
                "production_recommendation_eligible": False,
            },
        },
    )
    runtime_dir = run_root / "verifications"
    runtime_dir.mkdir()
    runtime_path, runtime_sha, runtime_file_sha = _write_content_addressed(
        runtime_dir,
        _runtime_document(main_sha, sidecar_hashes),
    )
    resource = {
        "schema_version": "research-unbounded-command-receipt/v1",
        "exit_code": 0,
        "child_reaped": True,
        "memory_limit_enforced": False,
        "process_tree_drained": None,
        "process_tree_drain_verification": "not_performed",
    }
    resource_raw = _canonical_bytes(resource) + b"\n"
    (run_root / verifier.RESOURCE_RECEIPT_NAME).write_bytes(resource_raw)
    preflight = {
        "git_commit": verifier.EXPECTED_SOURCE_COMMIT,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "run_spec_sha256": verifier.EXPECTED_RUN_SPEC_SHA256,
        "producer_binding": {"root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256},
    }
    completion = {
        "schema_version": verifier.EXPECTED_COMPLETION_SCHEMA,
        "classification": "completed_result_pending_independent_verification",
        "result_available": True,
        "independent_verification_complete": False,
        "statistical_interpretation_allowed": False,
        "immutable_inputs_unchanged": True,
        "artifact_content_addressed": True,
        "runtime_verification_content_addressed": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "preflight": preflight,
        "post_run_preflight": preflight,
        "resource_receipt": {
            "path": verifier.RESOURCE_RECEIPT_NAME,
            "sha256": _sha256(resource_raw),
            "exit_code": 0,
            "memory_limit_enforced": False,
            "process_tree_drained": None,
        },
        "progress": {
            "schema_version": verifier.EXPECTED_PROGRESS_SCHEMA,
            "stage": "completed",
            "artifact_sha256": main_sha,
        },
        "result_artifact": {
            "path": main_path.name,
            "canonical_artifact_sha256": main_sha,
            "file_sha256": main_file_sha,
            "file_name_matches_content_sha256": True,
        },
        "runtime_verification": {
            "path": runtime_path.name,
            "canonical_artifact_sha256": runtime_sha,
            "file_sha256": runtime_file_sha,
        },
    }
    completion_raw = _canonical_bytes(completion) + b"\n"
    (run_root / verifier.COMPLETION_NAME).write_bytes(completion_raw)
    return {
        "run_root": run_root,
        "completion_sha256": _sha256(completion_raw),
        "completion_path": run_root / verifier.COMPLETION_NAME,
        "resource_receipt_path": run_root / verifier.RESOURCE_RECEIPT_NAME,
        "main_artifact_path": main_path,
        "runtime_verification_path": runtime_path,
        "main_artifact_sha256": main_sha,
        "runtime_verification_artifact_sha256": runtime_sha,
        "runtime_verification": _runtime_document(main_sha, sidecar_hashes)
        | {"artifact_sha256": runtime_sha},
        "sidecar_paths": sidecar_paths,
        "sidecar_artifact_sha256": sidecar_hashes,
        "source": {
            "root": tmp_path,
            "git_commit": verifier.EXPECTED_SOURCE_COMMIT,
            "python_executable": tmp_path / "python.exe",
            "python_executable_sha256": "c" * 64,
            "base_python_executable": tmp_path / "base-python.exe",
            "base_python_executable_sha256": "b" * 64,
            "stdlib_root": tmp_path / "stdlib",
            "stdlib_manifest_sha256": "d" * 64,
            "venv_config_sha256": "e" * 64,
            "python_abi": {
                "implementation": sys.implementation.name,
                "cache_tag": str(sys.implementation.cache_tag or ""),
                "version": list(sys.version_info[:3]),
            },
            "code_tree_sha1": "d" * 40,
            "code_manifest_sha256": "e" * 64,
            "runtime_plan_sha256": "f" * 64,
            "runtime_manifest_sha256": "a" * 64,
            "base_runtime_manifest_sha256": "b" * 64,
            "interpreter_manifest_sha256": "c" * 64,
            "runtime_site_root": tmp_path / "site-packages",
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "producer_root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
        },
    }


def _replay_result(inputs: dict[str, object]) -> dict[str, object]:
    producer_root_sha256 = str(_normalized_probability_binding()["root_sha256"])
    verification = _runtime_document(
        str(inputs["main_artifact_sha256"]),
        dict(inputs["sidecar_artifact_sha256"]),
        producer_root_sha256=producer_root_sha256,
    )
    runtime_artifact_sha256 = _sha256(_canonical_bytes(verification))
    result = {
        "schema_version": verifier.REPLAY_RESULT_SCHEMA,
        "main_artifact_sha256": inputs["main_artifact_sha256"],
        "runtime_verification_artifact_sha256": runtime_artifact_sha256,
        "verification": verification,
        "scope": {
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        },
    }
    return result


def _replay_envelope(
    inputs: dict[str, object],
    execution_snapshot: dict[str, object],
) -> dict[str, object]:
    return {
        "result": _replay_result(inputs),
        "execution_snapshot": execution_snapshot,
        "formal_relocation_equivalence_sha256": "a" * 64,
        "replay_relocation_equivalence_sha256": "a" * 64,
    }


def test_replay_plan_is_frozen_and_development_only() -> None:
    verifier._assert_replay_plan()

    mutated = dict(verifier.REPLAY_PLAN)
    mutated["development_partition"] = {
        **verifier.REPLAY_PLAN["development_partition"],
        "temporal_role": "embargo",
    }
    original = verifier.REPLAY_PLAN
    try:
        verifier.REPLAY_PLAN = mutated
        with pytest.raises(verifier.IndependentVerificationError):
            verifier._assert_replay_plan()
    finally:
        verifier.REPLAY_PLAN = original


def test_verifier_identity_requires_clean_exact_committed_entrypoint(monkeypatch) -> None:
    expected_commit = "a" * 40

    def fake_git_output(root: Path, *arguments: str) -> str:
        assert root == verifier.PROJECT_ROOT
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(verifier.PROJECT_ROOT)
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return expected_commit
        raise AssertionError(arguments)

    def fake_run(command, **_kwargs):
        requested = str(command[-1])
        if requested == f"{expected_commit}:{verifier.SCRIPT_GIT_PATH}":
            payload = verifier._normalized_source_bytes(verifier.SCRIPT_PATH)
        elif requested == f"{expected_commit}:{verifier.RUN_ROOT_BINDER_GIT_PATH}":
            payload = verifier._normalized_source_bytes(
                verifier.PROJECT_ROOT / verifier.RUN_ROOT_BINDER_GIT_PATH
            )
        else:
            raise AssertionError(requested)

        class Completed:
            returncode = 0
            stdout = payload

        return Completed()

    monkeypatch.setattr(verifier, "_git_output", fake_git_output)
    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    identity = verifier._verifier_identity(expected_commit)

    assert identity["git_commit"] == expected_commit
    assert len(identity["script_sha256"]) == 64
    assert identity["script_git_blob_sha256"] == _sha256(
        verifier._normalized_source_bytes(verifier.SCRIPT_PATH)
    )
    assert len(identity["run_root_binder_git_blob_sha256"]) == 64


def test_source_base_identity_requires_expected_clean_source(monkeypatch, tmp_path: Path) -> None:
    python_executable = tmp_path / ".venv" / "Scripts" / "python.exe"
    base_python_executable = tmp_path / "base" / "python.exe"
    stdlib_root = tmp_path / "base" / "Lib"
    python_executable.parent.mkdir(parents=True)
    base_python_executable.parent.mkdir(parents=True)
    stdlib_root.mkdir()
    python_executable.write_bytes(b"python")
    base_python_executable.write_bytes(b"base-python")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")

    def fake_git_output(root: Path, *arguments: str) -> str:
        assert root == tmp_path
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return verifier.EXPECTED_SOURCE_COMMIT
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier, "_git_output", fake_git_output)
    monkeypatch.setattr(
        verifier,
        "_source_python_runtime_identity",
        lambda *_: {
            "base_python_executable": base_python_executable,
            "base_python_executable_sha256": "a" * 64,
            "stdlib_root": stdlib_root,
            "stdlib_manifest_sha256": "b" * 64,
            "runtime_site_root": tmp_path / "site-packages",
            "venv_config_sha256": "c" * 64,
            "python_abi": {
                "implementation": "cpython",
                "cache_tag": "cpython-311",
                "version": [3, 11, 0],
            },
        },
    )
    monkeypatch.setattr(
        verifier,
        "_expected_source_tree",
        lambda *_: ((Path("app/__init__.py"), "1" * 40),),
    )
    monkeypatch.setattr(
        verifier,
        "_tracked_source_manifest",
        lambda *_args, **_kwargs: {"sha256": "b" * 64},
    )
    identity = verifier._source_base_identity(str(tmp_path))

    assert identity["root"] == tmp_path
    assert identity["git_commit"] == verifier.EXPECTED_SOURCE_COMMIT
    assert identity["python_executable_sha256"] == _sha256(b"python")


def test_copy_source_snapshot_preserves_verified_worktree_bytes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "module.py").write_bytes(b"line1\r\nline2\r\n")
    (tmp_path / "pyproject.toml").write_bytes(b"[tool]\r\n")
    tracked_tree_entries = (
        (Path("app/module.py"), "1" * 40),
        (Path("pyproject.toml"), "2" * 40),
    )
    manifest = verifier._tracked_source_manifest(
        tmp_path,
        tracked_tree_entries,
        "source",
    )
    source = {
        "root": tmp_path,
        "tracked_tree_entries": tracked_tree_entries,
        "tracked_manifest_sha256": manifest["sha256"],
    }
    code_root = tmp_path / "code"
    (code_root / "app").mkdir(parents=True)
    (code_root / "app" / "module.py").write_bytes(b"old")
    (code_root / "pyproject.toml").write_bytes(b"old")
    original_manifest = verifier._tracked_source_manifest
    monkeypatch.setattr(
        verifier,
        "_tracked_source_manifest",
        lambda root, entries, label, **_kwargs: original_manifest(
            root,
            entries,
            label,
        ),
    )

    copied = verifier._copy_source_snapshot(source, code_root)

    assert copied["sha256"] == manifest["sha256"]
    assert (code_root / "app" / "module.py").read_bytes() == b"line1\r\nline2\r\n"
    assert (code_root / "pyproject.toml").read_bytes() == b"[tool]\r\n"


def test_source_python_runtime_identity_uses_the_target_interpreter(
    monkeypatch,
    tmp_path: Path,
) -> None:
    python_executable = tmp_path / ".venv" / "Scripts" / "python.exe"
    base_python_executable = tmp_path / "base" / "python.exe"
    stdlib_root = tmp_path / "stdlib"
    runtime_site_root = tmp_path / ".venv" / "Lib" / "site-packages"
    python_executable.parent.mkdir(parents=True)
    base_python_executable.parent.mkdir(parents=True)
    python_executable.write_bytes(b"venv-python")
    base_python_executable.write_bytes(b"base-python")
    stdlib_root.mkdir()
    runtime_site_root.mkdir(parents=True)
    venv_config = python_executable.parent.parent / "pyvenv.cfg"
    venv_config.write_text(
        "home = C:\\base\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = _canonical_bytes(
            {
                "schema_version": verifier.PYTHON_RUNTIME_PROBE_SCHEMA,
                "base_python_executable": str(base_python_executable),
                "stdlib_root": str(stdlib_root),
                "python_abi": {
                    "implementation": "cpython",
                    "cache_tag": "cpython-311",
                    "version": [3, 11, 5],
                },
            }
        )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return Completed()

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    monkeypatch.setattr(
        verifier,
        "_stdlib_manifest",
        lambda _root: {"sha256": "a" * 64},
    )

    identity = verifier._source_python_runtime_identity(python_executable)

    assert captured["command"][:4] == [str(python_executable), "-I", "-S", "-B"]
    assert captured["cwd"] == python_executable.parent
    assert identity["base_python_executable_sha256"] == _sha256(b"base-python")
    assert identity["runtime_site_root"] == runtime_site_root
    assert identity["venv_config_sha256"] == _sha256(venv_config.read_bytes())
    assert '"runtime_site_root"' not in verifier.PYTHON_RUNTIME_PROBE_DRIVER


def test_copy_interpreter_snapshot_excludes_base_site_packages(
    monkeypatch,
    tmp_path: Path,
) -> None:
    base_runtime_root = tmp_path / "base-runtime"
    python_executable = base_runtime_root / "python.exe"
    stdlib_root = base_runtime_root / "Lib"
    (stdlib_root / "site-packages").mkdir(parents=True)
    (stdlib_root / "__pycache__").mkdir()
    python_executable.write_bytes(b"base-python")
    (stdlib_root / "os.py").write_text("value = 1\n", encoding="utf-8")
    (stdlib_root / "site-packages" / "unrelated.pyd").write_bytes(b"unrelated")
    (stdlib_root / "__pycache__" / "os.cpython-311.pyc").write_bytes(b"cache")
    excluded = ["Lib/__pycache__", "Lib/site-packages"]
    source = {
        "base_runtime_root": base_runtime_root,
        "base_python_executable": python_executable,
        "base_python_executable_sha256": _sha256(b"base-python"),
        "stdlib_root": stdlib_root,
        "stdlib_manifest_sha256": "a" * 64,
        "python_abi": {"implementation": "cpython", "cache_tag": "cpython-311", "version": [3, 11, 0]},
        "base_runtime_excluded_relative_prefixes": excluded,
        "base_runtime_manifest_sha256": verifier._manifest_tree(
            base_runtime_root,
            "test base runtime",
            ignored_relative_prefixes=frozenset(excluded),
        )["sha256"],
    }
    monkeypatch.setattr(
        verifier,
        "_python_core_runtime_identity",
        lambda path, _label: {
            "base_python_executable": path,
            "base_python_executable_sha256": _sha256(b"base-python"),
            "stdlib_root": path.parent / "Lib",
            "stdlib_manifest_sha256": "a" * 64,
            "python_abi": source["python_abi"],
        },
    )

    copied_python, manifest = verifier._copy_interpreter_snapshot(
        source,
        tmp_path / "snapshot-runtime",
    )

    assert copied_python.is_file()
    assert (copied_python.parent / "Lib" / "os.py").is_file()
    assert not (copied_python.parent / "Lib" / "site-packages").exists()
    assert not (copied_python.parent / "Lib" / "__pycache__").exists()
    assert manifest["sha256"] == source["base_runtime_manifest_sha256"]


def test_copy_interpreter_snapshot_rejects_live_core_runtime_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    base_runtime_root = tmp_path / "base-runtime"
    python_executable = base_runtime_root / "python.exe"
    stdlib_root = base_runtime_root / "Lib"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_bytes(b"base-python")
    stdlib_root.mkdir()
    (stdlib_root / "os.py").write_text("value = 1\n", encoding="utf-8")
    live_runtime_root = tmp_path / "live-runtime"
    live_python = live_runtime_root / "python.exe"
    live_stdlib = live_runtime_root / "Lib"
    live_python.parent.mkdir(parents=True)
    live_python.write_bytes(b"base-python")
    live_stdlib.mkdir()
    (live_stdlib / "os.py").write_text("value = 1\n", encoding="utf-8")
    excluded = ["Lib/__pycache__", "Lib/site-packages"]
    source = {
        "base_runtime_root": base_runtime_root,
        "base_python_executable": python_executable,
        "base_python_executable_sha256": _sha256(b"base-python"),
        "stdlib_root": stdlib_root,
        "stdlib_manifest_sha256": "a" * 64,
        "python_abi": {
            "implementation": "cpython",
            "cache_tag": "cpython-311",
            "version": [3, 11, 0],
        },
        "base_runtime_excluded_relative_prefixes": excluded,
        "base_runtime_manifest_sha256": verifier._manifest_tree(
            base_runtime_root,
            "test base runtime",
            ignored_relative_prefixes=frozenset(excluded),
        )["sha256"],
    }
    monkeypatch.setattr(
        verifier,
        "_python_core_runtime_identity",
        lambda _path, _label: {
            "base_python_executable": live_python,
            "base_python_executable_sha256": _sha256(b"base-python"),
            "stdlib_root": live_stdlib,
            "stdlib_manifest_sha256": "a" * 64,
            "python_abi": source["python_abi"],
        },
    )

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._copy_interpreter_snapshot(source, tmp_path / "snapshot-runtime")


def test_binding_identity_uses_the_frozen_producer_hash_conventions() -> None:
    ridge_identity = {"artifact_version": 3}
    ridge = {
        "schema_version": "audited-pit-ranked-liquidity-producer/v3",
        **ridge_identity,
        "root_sha256": _sha256(_canonical_bytes(ridge_identity)),
    }
    shallow_body = {
        "schema_version": "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1",
        "base_ranked_liquidity_producer_root_sha256": ridge["root_sha256"],
    }
    shallow = {
        **shallow_body,
        "root_sha256": _sha256(_canonical_bytes(shallow_body)),
    }
    probability_body = {
        "schema_version": (
            "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1"
        ),
        "base_shallow_gbdt_producer_root_sha256": shallow["root_sha256"],
    }
    probability = {
        **probability_body,
        "root_sha256": _sha256(_canonical_bytes(probability_body)),
    }

    assert verifier._binding_identity(ridge, "ridge")[1] == ridge["root_sha256"]
    assert verifier._binding_identity(shallow, "shallow")[1] == shallow["root_sha256"]
    assert (
        verifier._binding_identity(probability, "probability")[1]
        == probability["root_sha256"]
    )


def test_xgboost_relocation_descriptor_binds_equal_physical_libraries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    strategy_sha256 = "1" * 64
    formal_runtime = tmp_path / "formal-runtime"
    snapshot_runtime = tmp_path / "snapshot-runtime"
    formal_library = formal_runtime / "xgboost" / "lib" / "libxgboost.dll"
    snapshot_library = snapshot_runtime / "xgboost" / "lib" / "libxgboost.dll"
    formal_library.parent.mkdir(parents=True)
    snapshot_library.parent.mkdir(parents=True)
    formal_library.write_bytes(b"identical-xgboost-dll")
    snapshot_library.write_bytes(b"identical-xgboost-dll")

    def binding(body: dict[str, object]) -> dict[str, object]:
        return {**body, "root_sha256": _sha256(_canonical_bytes(body))}

    ridge_identity = {"kind": "ridge"}
    ridge_binding = {
        "schema_version": "audited-pit-ranked-liquidity-producer/v3",
        **ridge_identity,
        "root_sha256": _sha256(_canonical_bytes(ridge_identity)),
    }
    formal_shallow_binding = binding(
        {
            "schema_version": "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1",
            "base_ranked_liquidity_producer_root_sha256": ridge_binding["root_sha256"],
            "xgboost_build_info": {"libxgboost": str(formal_library)},
        }
    )
    snapshot_shallow_binding = binding(
        {
            "schema_version": "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1",
            "base_ranked_liquidity_producer_root_sha256": ridge_binding["root_sha256"],
            "xgboost_build_info": {"libxgboost": str(snapshot_library)},
        }
    )
    formal_probability_binding = binding(
        {
            "schema_version": (
                "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1"
            ),
            "base_shallow_gbdt_producer_root_sha256": formal_shallow_binding[
                "root_sha256"
            ],
        }
    )
    snapshot_probability_binding = binding(
        {
            "schema_version": (
                "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1"
            ),
            "base_shallow_gbdt_producer_root_sha256": snapshot_shallow_binding[
                "root_sha256"
            ],
        }
    )
    monkeypatch.setattr(verifier, "EXPECTED_STRATEGY_SHA256", strategy_sha256)
    monkeypatch.setattr(
        verifier,
        "EXPECTED_PRODUCER_ROOT_SHA256",
        formal_probability_binding["root_sha256"],
    )

    def raw(
        probability_binding: dict[str, object],
        shallow_binding: dict[str, object],
    ) -> dict[str, object]:
        return {
            "strategy_sha256": strategy_sha256,
            "probability_binding": probability_binding,
            "shallow_binding": shallow_binding,
            "ridge_binding": ridge_binding,
            "xgboost_build_info": shallow_binding["xgboost_build_info"],
        }

    descriptor = verifier._build_xgboost_relocation_descriptor(
        raw(formal_probability_binding, formal_shallow_binding),
        raw(snapshot_probability_binding, snapshot_shallow_binding),
        formal_runtime,
        snapshot_runtime,
    )
    descriptor_path = tmp_path / "xgboost-relocation.json"
    descriptor_path.write_bytes(_canonical_bytes(descriptor) + b"\n")

    public, descriptor_sha256 = verifier._read_xgboost_relocation_descriptor(
        descriptor_path,
        "test relocation descriptor",
    )

    assert "formal_xgboost_library_value" not in _canonical_bytes(descriptor).decode("utf-8")
    assert str(formal_runtime) not in _canonical_bytes(descriptor).decode("utf-8")
    assert public["formal_xgboost_library_sha256"] == _sha256(
        b"identical-xgboost-dll"
    )
    assert descriptor_sha256 == descriptor["descriptor_sha256"]
    assert public["normalized_shallow_binding"]["xgboost_build_info"][
        "libxgboost"
    ] == "<relocated-xgboost-library>"
    assert public["normalized_probability_binding"][
        "base_shallow_gbdt_producer_root_sha256"
    ] == public["normalized_shallow_binding"]["root_sha256"]

    tampered = {
        **descriptor,
        "snapshot_xgboost_library_sha256": "0" * 64,
    }
    descriptor_path.write_bytes(_canonical_bytes(tampered) + b"\n")
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._read_xgboost_relocation_descriptor(
            descriptor_path,
            "tampered relocation descriptor",
        )

    snapshot_library.write_bytes(b"different-xgboost-dll")
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._build_xgboost_relocation_descriptor(
            raw(formal_probability_binding, formal_shallow_binding),
            raw(snapshot_probability_binding, snapshot_shallow_binding),
            formal_runtime,
            snapshot_runtime,
        )


def test_relocation_equivalence_normalizes_only_the_declared_producer_binding() -> None:
    def probability_binding(base_shallow_root: str) -> dict[str, object]:
        body = {
            "schema_version": (
                "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1"
            ),
            "base_shallow_gbdt_producer_root_sha256": base_shallow_root,
        }
        return {**body, "root_sha256": _sha256(_canonical_bytes(body))}

    formal = probability_binding("a" * 64)
    normalized = probability_binding("b" * 64)
    formal_runtime = {"libxgboost": "C:/formal/xgboost/lib/libxgboost.dll"}
    normalized_runtime = {"libxgboost": "<relocated-xgboost-library>"}
    sidecars = {
        name: {
            "artifact_sha256": "1" * 64,
            "producer_code": formal,
            "payload": {"name": name},
        }
        for name in verifier.RESULT_BUNDLE_SIDECAR_NAMES
    }
    bundle = {
        "main_document": {
            "artifact_sha256": "2" * 64,
            "producer_code": formal,
            "sidecars": {name: {"artifact_sha256": "3" * 64} for name in sidecars},
            "payload": {"stable": True},
        },
        "sidecar_documents": sidecars,
        "runtime_verification": {
            "artifact_sha256": "4" * 64,
            "receipt_sha256": "5" * 64,
            "producer_root_sha256": formal["root_sha256"],
            "main_artifact_sha256": "6" * 64,
            "sidecar_artifact_sha256": {name: "7" * 64 for name in sidecars},
            "checks": {"all": True},
        },
    }
    replay_bundle = json.loads(_canonical_bytes(bundle).decode("utf-8"))
    replay_bundle["main_document"]["producer_code"] = normalized
    for document in replay_bundle["sidecar_documents"].values():
        document["producer_code"] = normalized
    replay_bundle["runtime_verification"]["producer_root_sha256"] = normalized[
        "root_sha256"
    ]

    formal_digest = verifier._relocation_equivalence_sha256(
        bundle,
        expected_producer_code=formal,
        normalized_producer_code=normalized,
        expected_xgboost_build_info=formal_runtime,
        normalized_xgboost_build_info=normalized_runtime,
        label="formal bundle",
    )
    replay_digest = verifier._relocation_equivalence_sha256(
        replay_bundle,
        expected_producer_code=normalized,
        normalized_producer_code=normalized,
        expected_xgboost_build_info=formal_runtime,
        normalized_xgboost_build_info=normalized_runtime,
        label="replayed bundle",
    )

    assert replay_digest == formal_digest
    replay_bundle["sidecar_documents"]["models"]["payload"] = {"name": "changed"}
    assert (
        verifier._relocation_equivalence_sha256(
            replay_bundle,
            expected_producer_code=normalized,
            normalized_producer_code=normalized,
            expected_xgboost_build_info=formal_runtime,
            normalized_xgboost_build_info=normalized_runtime,
            label="changed replay bundle",
        )
        != formal_digest
    )


def test_relocation_equivalence_normalizes_nested_xgboost_runtime_receipts() -> None:
    def probability_binding(base_shallow_root: str) -> dict[str, object]:
        body = {
            "schema_version": (
                "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-producer/v1"
            ),
            "base_shallow_gbdt_producer_root_sha256": base_shallow_root,
        }
        return {**body, "root_sha256": _sha256(_canonical_bytes(body))}

    formal = probability_binding("a" * 64)
    normalized = probability_binding("b" * 64)
    formal_runtime = {
        "compiler": "msvc",
        "libxgboost": "C:/formal/xgboost/lib/libxgboost.dll",
    }
    snapshot_runtime = {
        "compiler": "msvc",
        "libxgboost": "C:/snapshot/xgboost/lib/libxgboost.dll",
    }
    normalized_runtime = {
        "compiler": "msvc",
        "libxgboost": "<relocated-xgboost-library>",
    }

    def models_fields(runtime: dict[str, str]) -> dict[str, object]:
        fold = {
            "fit_receipt": {"runtime": {"xgboost_build_info": runtime}},
            "predict_receipt": {"runtime": {"xgboost_build_info": runtime}},
        }
        fold["receipt_sha256"] = _sha256(_canonical_bytes(fold))
        oof_receipt = {"folds": [fold]}
        oof_receipt["folds_sha256"] = _sha256(_canonical_bytes(oof_receipt["folds"]))
        oof_receipt["receipt_sha256"] = _sha256(_canonical_bytes(oof_receipt))
        return {
            "oof_receipt": oof_receipt,
            "oof_replay_verification": {
                "verified": True,
                "receipt_sha256": oof_receipt["receipt_sha256"],
            },
        }

    sidecars = {
        name: (
            {
                "artifact_sha256": "1" * 64,
                "producer_code": formal,
                **models_fields(formal_runtime),
            }
            if name == "models"
            else {
                "artifact_sha256": "1" * 64,
                "producer_code": formal,
                "payload": {"name": name},
            }
        )
        for name in verifier.RESULT_BUNDLE_SIDECAR_NAMES
    }
    bundle = {
        "main_document": {
            "artifact_sha256": "2" * 64,
            "producer_code": formal,
            "sidecars": {name: {"artifact_sha256": "3" * 64} for name in sidecars},
            "payload": {"stable": True},
        },
        "sidecar_documents": sidecars,
        "runtime_verification": {
            "artifact_sha256": "4" * 64,
            "receipt_sha256": "5" * 64,
            "producer_root_sha256": formal["root_sha256"],
            "main_artifact_sha256": "6" * 64,
            "sidecar_artifact_sha256": {name: "7" * 64 for name in sidecars},
            "checks": {"all": True},
        },
    }
    replay_bundle = json.loads(_canonical_bytes(bundle).decode("utf-8"))
    replay_bundle["main_document"]["producer_code"] = normalized
    for document in replay_bundle["sidecar_documents"].values():
        document["producer_code"] = normalized
    replay_bundle["runtime_verification"]["producer_root_sha256"] = normalized[
        "root_sha256"
    ]
    replay_bundle["sidecar_documents"]["models"].update(
        models_fields(snapshot_runtime)
    )

    formal_digest = verifier._relocation_equivalence_sha256(
        bundle,
        expected_producer_code=formal,
        normalized_producer_code=normalized,
        expected_xgboost_build_info=formal_runtime,
        normalized_xgboost_build_info=normalized_runtime,
        label="formal bundle",
    )
    replay_digest = verifier._relocation_equivalence_sha256(
        replay_bundle,
        expected_producer_code=normalized,
        normalized_producer_code=normalized,
        expected_xgboost_build_info=snapshot_runtime,
        normalized_xgboost_build_info=normalized_runtime,
        label="replayed bundle",
    )

    assert replay_digest == formal_digest
    runtime = replay_bundle["sidecar_documents"]["models"]["oof_receipt"][
        "folds"
    ][0]
    runtime["fit_receipt"]["runtime"]["xgboost_build_info"] = {
        **snapshot_runtime,
        "compiler": "tampered",
    }
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._relocation_equivalence_sha256(
            replay_bundle,
            expected_producer_code=normalized,
            normalized_producer_code=normalized,
            expected_xgboost_build_info=snapshot_runtime,
            normalized_xgboost_build_info=normalized_runtime,
            label="tampered replay bundle",
        )
    runtime["fit_receipt"]["runtime"]["xgboost_build_info"] = snapshot_runtime
    replay_bundle["sidecar_documents"]["models"]["oof_replay_verification"][
        "receipt_sha256"
    ] = "0" * 64
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._relocation_equivalence_sha256(
            replay_bundle,
            expected_producer_code=normalized,
            normalized_producer_code=normalized,
            expected_xgboost_build_info=snapshot_runtime,
            normalized_xgboost_build_info=normalized_runtime,
            label="detached OOF replay receipt",
        )


def test_safe_child_rejects_reparse_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._safe_child(link, "result.json", "result")


def test_held_run_root_binds_direct_publication_files(tmp_path: Path) -> None:
    raw = b"{}\n"
    commit = subprocess.run(
        ["git", "-C", str(verifier.PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout.decode("ascii").strip()
    binder_blob = subprocess.run(
        [
            "git",
            "-C",
            str(verifier.PROJECT_ROOT),
            "show",
            f"{commit}:{verifier.RUN_ROOT_BINDER_GIT_PATH}",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout
    binder_type = verifier._frozen_run_root_binder(
        commit,
        _sha256(binder_blob),
    )

    with verifier._held_run_root(tmp_path, binder_type=binder_type) as publisher:
        publisher.assert_bound()
        assert publisher.root_file_exists("result.json") is False
        path = publisher.write_root_once(
            name="result.json",
            raw=raw,
            label="test publication",
        )
        assert publisher.read_root_bounded(
            name="result.json",
            label="test publication",
        ) == raw
        publisher.assert_bound()

    assert path.read_bytes() == raw
    assert binder_type.__module__ == "_frozen_probability_budget_run_root_binder"


def test_load_completed_run_accepts_only_pending_development_result(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)

    loaded = verifier._load_completed_run(inputs["source"])

    assert loaded["completion_sha256"] == inputs["completion_sha256"]
    assert loaded["main_artifact_sha256"] == inputs["main_artifact_sha256"]
    assert loaded["runtime_verification"] == inputs["runtime_verification"]


def test_load_completed_run_accepts_launcher_without_redundant_filename_flag(
    tmp_path: Path,
) -> None:
    inputs = _make_completed_inputs(tmp_path)
    completion_path = inputs["run_root"] / verifier.COMPLETION_NAME
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["result_artifact"].pop("file_name_matches_content_sha256")
    completion_path.write_bytes(_canonical_bytes(completion) + b"\n")

    loaded = verifier._load_completed_run(inputs["source"])

    assert loaded["main_artifact_sha256"] == inputs["main_artifact_sha256"]


def test_load_completed_run_rejects_explicit_filename_hash_mismatch(
    tmp_path: Path,
) -> None:
    inputs = _make_completed_inputs(tmp_path)
    completion_path = inputs["run_root"] / verifier.COMPLETION_NAME
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["result_artifact"]["file_name_matches_content_sha256"] = False
    completion_path.write_bytes(_canonical_bytes(completion) + b"\n")

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="main artifact binding is invalid",
    ):
        verifier._load_completed_run(inputs["source"])


def test_load_completed_run_rejects_non_boolean_filename_hash_claim(
    tmp_path: Path,
) -> None:
    inputs = _make_completed_inputs(tmp_path)
    completion_path = inputs["run_root"] / verifier.COMPLETION_NAME
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["result_artifact"]["file_name_matches_content_sha256"] = 1
    completion_path.write_bytes(_canonical_bytes(completion) + b"\n")

    with pytest.raises(
        verifier.IndependentVerificationError,
        match="main artifact binding is invalid",
    ):
        verifier._load_completed_run(inputs["source"])


def test_load_completed_run_rejects_open_statistical_interpretation(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    completion_path = inputs["run_root"] / verifier.COMPLETION_NAME
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["statistical_interpretation_allowed"] = True
    completion_path.write_bytes(_canonical_bytes(completion) + b"\n")

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._load_completed_run(inputs["source"])


def test_validate_replay_requires_relocation_equivalence_and_runtime_integrity(
    tmp_path: Path,
) -> None:
    inputs = _make_completed_inputs(tmp_path)
    execution_snapshot = _execution_snapshot()
    replay = _replay_envelope(inputs, execution_snapshot)

    verifier._validate_replay(inputs, replay, execution_snapshot)

    replay["result"] = {
        **replay["result"],
        "runtime_verification_artifact_sha256": "f" * 64,
    }
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._validate_replay(inputs, replay, execution_snapshot)


def test_validate_replay_rejects_embedded_runtime_artifact_sha256(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    execution_snapshot = _execution_snapshot()
    replay = _replay_envelope(inputs, execution_snapshot)
    replay["result"]["verification"] = {
        **replay["result"]["verification"],
        "artifact_sha256": inputs["runtime_verification_artifact_sha256"],
    }

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._validate_replay(inputs, replay, execution_snapshot)


def test_validate_replay_rejects_unsigned_runtime_body_tampering(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    execution_snapshot = _execution_snapshot()
    replay = _replay_envelope(inputs, execution_snapshot)
    replay["result"]["verification"] = {
        **replay["result"]["verification"],
        "verified": False,
    }

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._validate_replay(inputs, replay, execution_snapshot)


def test_validate_replay_rejects_execution_snapshot_tampering(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    execution_snapshot = _execution_snapshot()
    replay = {
        **_replay_envelope(inputs, execution_snapshot),
        "execution_snapshot": {
            **execution_snapshot,
            "data": {"manifest_sha256": "0" * 64, "file_count": 1},
        },
    }

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._validate_replay(inputs, replay, execution_snapshot)


def test_isolated_driver_uses_snapshot_roots_and_never_loads_dotenv() -> None:
    assert "get_settings" not in verifier.ISOLATED_REPLAY_DRIVER
    assert "settings=Settings()" in verifier.ISOLATED_REPLAY_DRIVER
    assert "code_root" in verifier.ISOLATED_REPLAY_DRIVER
    assert "data_root" in verifier.ISOLATED_REPLAY_DRIVER
    assert "runtime_root" in verifier.ISOLATED_REPLAY_DRIVER
    assert "relocation_descriptor_path" in verifier.ISOLATED_REPLAY_DRIVER
    assert "formal_xgboost_library_value" not in verifier.ISOLATED_REPLAY_DRIVER
    assert "formal_runtime_root" not in verifier.ISOLATED_REPLAY_DRIVER
    assert "binding_runtime_root" not in verifier.ISOLATED_REPLAY_DRIVER
    assert "relocated_xgboost_build_info" not in verifier.ISOLATED_REPLAY_DRIVER
    assert "continuous_ridge._shallow_gbdt_producer_binding" in verifier.ISOLATED_REPLAY_DRIVER
    assert "continuous_ridge._shallow_gbdt_probability_budget_producer_binding" in verifier.ISOLATED_REPLAY_DRIVER


def test_runtime_dependency_plan_uses_site_free_isolated_probe(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = _canonical_bytes(
            {
                "schema_version": verifier.RUNTIME_PROBE_SCHEMA,
                "top_levels": ["numpy", "_example"],
                "metadata_paths": ["numpy-1.0.dist-info", "example-1.0.dist-info"],
            }
        )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Completed()

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    plan = verifier._runtime_dependency_plan(
        tmp_path / "code",
        tmp_path / "python.exe",
        tmp_path / "site-packages",
    )

    assert plan["top_levels"] == ["_example", "numpy"]
    assert captured["command"][:4] == [str(tmp_path / "python.exe"), "-I", "-S", "-B"]
    assert "PYTHONPATH" not in captured["env"]


def test_runtime_probe_tracks_distribution_metadata_reads() -> None:
    assert "requested_distributions" in verifier.RUNTIME_PROBE_DRIVER
    assert "tracked_version" in verifier.RUNTIME_PROBE_DRIVER
    assert "continuous_ridge.package_version = tracked_version" in verifier.RUNTIME_PROBE_DRIVER
    assert "distributions.update(requested_distributions)" in verifier.RUNTIME_PROBE_DRIVER


def test_copy_runtime_snapshot_copies_only_manifested_dependencies(tmp_path: Path) -> None:
    site_root = tmp_path / "site-packages"
    (site_root / "numpy").mkdir(parents=True)
    (site_root / "numpy" / "__init__.py").write_text("", encoding="utf-8")
    (site_root / "numpy.libs").mkdir()
    (site_root / "numpy.libs" / "native.dll").write_bytes(b"native")
    (site_root / "numpy-1.0.dist-info").mkdir()
    (site_root / "numpy-1.0.dist-info" / "METADATA").write_text("Name: numpy\n", encoding="utf-8")
    (site_root / "unrelated.py").write_text("raise RuntimeError\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    plan = {
        "top_levels": ["numpy"],
        "metadata_paths": ["numpy-1.0.dist-info"],
        "sha256": "0" * 64,
    }

    manifest = verifier._copy_runtime_snapshot(site_root, runtime_root, plan)

    assert (runtime_root / "numpy" / "__init__.py").is_file()
    assert (runtime_root / "numpy.libs" / "native.dll").is_file()
    assert (runtime_root / "numpy-1.0.dist-info" / "METADATA").is_file()
    assert not (runtime_root / "unrelated.py").exists()
    assert len(manifest["sha256"]) == 64


def test_data_snapshot_copies_complete_pit_bundle_and_rejects_sqlite_sidecars(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    universe_path = tmp_path / Path(verifier.REPLAY_PLAN["inputs"]["audited_pit_universe_path"])
    universe_path.parent.mkdir(parents=True)
    universe_path.write_bytes(b"sqlite")
    (universe_path.parent / "manifest.json").write_text("{}", encoding="utf-8")
    (universe_path.parent / "raw").mkdir()
    (universe_path.parent / "raw" / "snapshot.bin").write_bytes(b"raw")
    temporal_path = tmp_path / Path(verifier.REPLAY_PLAN["inputs"]["temporal_contract_path"])
    temporal_path.parent.mkdir(parents=True, exist_ok=True)
    temporal_path.write_text("{}", encoding="utf-8")
    current_pool = tmp_path / "data" / "research_artifacts" / "current_pool_audits"
    current_pool.mkdir(parents=True)
    (current_pool / "audit.json").write_text("{}", encoding="utf-8")
    transition = tmp_path / Path(verifier.REPLAY_PLAN["inputs"]["security_code_transition_evidence_root"])
    transition.mkdir(parents=True)
    (transition / "evidence.pdf").write_bytes(b"pdf")

    snapshot_root = tmp_path / "snapshot-data"
    _snapshot_inputs, manifest = verifier._copy_data_snapshot(inputs, snapshot_root)

    assert (snapshot_root / universe_path.parent.relative_to(tmp_path) / "raw" / "snapshot.bin").is_file()
    assert (snapshot_root / current_pool.relative_to(tmp_path) / "audit.json").is_file()
    assert (snapshot_root / transition.relative_to(tmp_path) / "evidence.pdf").is_file()
    assert manifest["file_count"] >= 1

    universe_path.with_name(f"{universe_path.name}-wal").write_bytes(b"active")
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._copy_data_snapshot(inputs, tmp_path / "snapshot-data-with-wal")


def test_preregistration_is_idempotent_but_rejects_drift(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    verifier_identity = {
        "git_commit": "d" * 40,
        "script_sha256": "e" * 64,
        "script_git_blob_sha256": "f" * 64,
    }
    publisher = _FakePublisher(Path(inputs["run_root"]))

    first = verifier._preregistration(inputs, verifier_identity, publisher=publisher)
    second = verifier._preregistration(inputs, verifier_identity, publisher=publisher)

    assert first["sha256"] == second["sha256"]
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._preregistration(
            inputs,
            {**verifier_identity, "script_sha256": "a" * 64},
            publisher=publisher,
        )


def test_publish_receipt_does_not_change_formal_completion(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    completion_path = inputs["run_root"] / verifier.COMPLETION_NAME
    before = completion_path.read_bytes()
    replay_result_path = inputs["run_root"] / verifier.REPLAY_RESULT_NAME
    replay_result_path.write_bytes(_canonical_bytes(_replay_result(inputs)) + b"\n")
    replay = {
        "result_path": replay_result_path,
        "result_sha256": _sha256(replay_result_path.read_bytes()),
        "exit_code": 0,
        "stdout": {"path": "stdout", "bytes": 0, "sha256": "1" * 64},
        "stderr": {"path": "stderr", "bytes": 0, "sha256": "2" * 64},
        "execution_snapshot": _execution_snapshot(),
        "formal_relocation_equivalence_sha256": "a" * 64,
        "replay_relocation_equivalence_sha256": "a" * 64,
    }
    preregistration = {"sha256": "3" * 64}
    verifier_identity = {
        "git_commit": "4" * 40,
        "script_sha256": "5" * 64,
        "script_git_blob_sha256": "6" * 64,
    }

    receipt = verifier._publish_receipt(
        inputs,
        verifier_identity,
        preregistration,
        replay,
        publisher=_FakePublisher(Path(inputs["run_root"])),
    )

    assert completion_path.read_bytes() == before
    assert receipt["path"].is_file()
    payload = json.loads(receipt["path"].read_text(encoding="utf-8"))
    assert payload["scope"]["production_authority"] is False
    assert payload["resource_contract"]["memory_policy"] == "unbounded"
    assert payload["post_verification_authority"] == {
        "development_statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
    }
    assert payload["terminal_status_required"] is True
    assert payload["receipt_alone_authoritative"] is False


def test_run_records_failure_without_receipt(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    prepared = {
        "verifier": {
            "git_commit": "4" * 40,
            "script_sha256": "5" * 64,
            "script_git_blob_sha256": "6" * 64,
            "run_root_binder_git_blob_sha256": "7" * 64,
        },
        "inputs": inputs,
    }
    monkeypatch.setattr(verifier, "preflight", lambda *_: prepared)
    monkeypatch.setattr(verifier, "_frozen_run_root_binder", lambda *_: object)
    monkeypatch.setattr(
        verifier,
        "_held_run_root",
        lambda root, **_kwargs: _FakePublisher(root),
    )
    monkeypatch.setattr(
        verifier,
        "_IsolatedReplaySnapshot",
        lambda _source, live_inputs: _FakeSnapshot(tmp_path, live_inputs),
    )
    monkeypatch.setattr(
        verifier,
        "_run_isolated_replay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            verifier.IndependentVerificationError()
        ),
    )

    with pytest.raises(verifier.IndependentVerificationError):
        verifier.run("ignored", "4" * 40)

    status_path = inputs["run_root"] / verifier.STATUS_NAME
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["verified"] is False
    assert (inputs["run_root"] / verifier.CLAIM_NAME).is_file()
    assert not list(
        (inputs["run_root"] / verifier.RECEIPT_ROOT_NAME).rglob("*.json")
    )


def test_run_publishes_terminal_receipt_after_exact_replay(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    prepared = {
        "verifier": {
            "git_commit": "4" * 40,
            "script_sha256": "5" * 64,
            "script_git_blob_sha256": "6" * 64,
            "run_root_binder_git_blob_sha256": "7" * 64,
        },
        "inputs": inputs,
    }
    replay_result_path = inputs["run_root"] / verifier.REPLAY_RESULT_NAME
    replay_result_path.write_bytes(_canonical_bytes(_replay_result(inputs)) + b"\n")
    replay = {
        **_replay_envelope(inputs, _execution_snapshot()),
        "result_path": replay_result_path,
        "result_sha256": _sha256(replay_result_path.read_bytes()),
        "exit_code": 0,
        "stdout": {"path": "stdout", "bytes": 0, "sha256": "1" * 64},
        "stderr": {"path": "stderr", "bytes": 0, "sha256": "2" * 64},
    }
    monkeypatch.setattr(verifier, "preflight", lambda *_: prepared)
    monkeypatch.setattr(verifier, "_frozen_run_root_binder", lambda *_: object)
    monkeypatch.setattr(
        verifier,
        "_held_run_root",
        lambda root, **_kwargs: _FakePublisher(root),
    )
    monkeypatch.setattr(
        verifier,
        "_IsolatedReplaySnapshot",
        lambda _source, live_inputs: _FakeSnapshot(tmp_path, live_inputs),
    )
    monkeypatch.setattr(
        verifier,
        "_run_isolated_replay",
        lambda *_args, **_kwargs: replay,
    )

    result = verifier.run("ignored", "4" * 40)

    assert result["status"] == "completed"
    status = json.loads(
        (inputs["run_root"] / verifier.STATUS_NAME).read_text(encoding="utf-8")
    )
    assert status["verified"] is True
    assert status["development_statistical_interpretation_allowed"] is True
    assert status["profile_registration_authority"] is False
    assert status["receipt"]["sha256"] == result["receipt_sha256"]
    assert (inputs["run_root"] / verifier.CLAIM_NAME).is_file()


def test_isolated_replay_uses_hidden_unbounded_child(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    captured: dict[str, object] = {}

    class Process:
        def wait(self) -> int:
            Path(str(captured["command"][10])).write_bytes(
                _canonical_bytes(_replay_result(inputs)) + b"\n"
            )
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["creationflags"] = kwargs["creationflags"]
        return Process()

    monkeypatch.setattr(verifier.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        verifier,
        "_load_content_addressed_result_bundle",
        lambda *_args, **_kwargs: inputs,
    )
    monkeypatch.setattr(
        verifier,
        "_relocation_equivalence_sha256",
        lambda *_args, **_kwargs: "a" * 64,
    )

    snapshot = _FakeSnapshot(tmp_path, inputs)
    replay = verifier._run_isolated_replay(
        inputs,
        snapshot,
        publisher=_FakePublisher(Path(inputs["run_root"])),
    )

    assert replay["result"] == _replay_result(inputs)
    assert replay["formal_relocation_equivalence_sha256"] == "a" * 64
    assert replay["replay_relocation_equivalence_sha256"] == "a" * 64
    assert captured["command"][0] == str(snapshot.python_executable)
    assert str(inputs["source"]["python_executable"]) not in captured["command"]
    assert "-I" in captured["command"]
    assert "-S" in captured["command"]
    assert "-B" in captured["command"]
    assert captured["command"][6:9] == [str(snapshot.code_root), str(snapshot.data_root), str(snapshot.runtime_root)]
    assert captured["command"][11] == str(snapshot.xgboost_relocation_descriptor_path)
    assert str(inputs["source"]["runtime_site_root"]) not in captured["command"]
    assert str(inputs["run_root"]) not in captured["command"]
    assert (inputs["run_root"] / verifier.REPLAY_RESULT_NAME).is_file()
    assert (inputs["run_root"] / verifier.REPLAY_STDOUT_NAME).is_file()
    assert (inputs["run_root"] / verifier.REPLAY_STDERR_NAME).is_file()
    assert captured["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_isolated_replay_fails_closed_on_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)

    class Process:
        def wait(self) -> int:
            return 1

    monkeypatch.setattr(verifier.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    snapshot = _FakeSnapshot(tmp_path, inputs)

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._run_isolated_replay(
            inputs,
            snapshot,
            publisher=_FakePublisher(Path(inputs["run_root"])),
        )

    assert not (inputs["run_root"] / verifier.REPLAY_STDOUT_NAME).exists()
    assert not (inputs["run_root"] / verifier.REPLAY_STDERR_NAME).exists()
    assert not (inputs["run_root"] / verifier.REPLAY_RESULT_NAME).exists()


def test_main_never_prints_verification_details(monkeypatch, capsys) -> None:
    monkeypatch.setattr(verifier, "preflight", lambda *_: {"ignored": True})

    assert verifier.main(["--expected-verifier-commit", "a" * 40, "--preflight"]) == 0
    assert capsys.readouterr().out == "status=preflight_verified\n"

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_: {"status": "completed", "receipt_sha256": "b" * 64},
    )
    assert verifier.main(["--expected-verifier-commit", "a" * 40]) == 0
    assert capsys.readouterr().out == "status=independently_verified\n"

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_: (_ for _ in ()).throw(verifier.IndependentVerificationError()),
    )
    assert verifier.main(["--expected-verifier-commit", "a" * 40]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "status=failed\n"
