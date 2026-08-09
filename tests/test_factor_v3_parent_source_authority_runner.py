from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

import pytest
import numpy
import pandas
import requests
import xgboost

from app import factor_v3_parent_source_authority_runner as runner


def _bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signed(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {**value, field: _sha(value)}


class _PopenFixture:
    stdout = b""
    stderr = b""
    exit_code = 0
    callback: Callable[[], None] | None = None
    calls: list[dict[str, Any]] = []
    job_assignments = 0
    events: list[str] = []

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        type(self).events.append("popen")
        self._call = {"command": command, **kwargs}
        type(self).calls.append(self._call)
        self._kwargs = kwargs
        self.returncode: int | None = None

    def communicate(self, input: bytes, timeout: int | None = None) -> tuple[None, None]:
        type(self).events.append("communicate")
        assert input
        assert timeout == runner.CHILD_TIMEOUT_SECONDS
        self._call["input"] = input
        if type(self).callback is not None:
            type(self).callback()
        self._kwargs["stdout"].write(type(self).stdout)
        self._kwargs["stdout"].flush()
        self._kwargs["stderr"].write(type(self).stderr)
        self._kwargs["stderr"].flush()
        self.returncode = type(self).exit_code
        return None, None

    def kill(self) -> None:
        type(self).events.append("kill")
        self.returncode = -9

    def wait(self, timeout: int | None = None) -> int:
        type(self).events.append("wait")
        assert timeout is not None
        if self.returncode is None:
            self.returncode = -9
        return self.returncode


@pytest.fixture(autouse=True)
def _reset_popen() -> None:
    _PopenFixture.stdout = b""
    _PopenFixture.stderr = b""
    _PopenFixture.exit_code = 0
    _PopenFixture.callback = None
    _PopenFixture.calls = []
    _PopenFixture.job_assignments = 0
    _PopenFixture.events = []


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, Path, dict[str, Any], dict[str, Path]]:
    frozen = tmp_path / "frozen-source"
    frozen.mkdir()
    blobs: dict[str, str] = {}
    for relative in runner.FROZEN_SOURCE_BLOB_SHA256:
        path = frozen / Path(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        logical = f"fixture:{relative}\nline-two\n".encode()
        path.write_bytes(logical.replace(b"\n", b"\r\n"))
        blobs[relative] = hashlib.sha256(logical).hexdigest()
    monkeypatch.setattr(runner, "FROZEN_SOURCE_BLOB_SHA256", blobs)
    app_sources = {
        relative: {
            "git_blob_sha256": digest,
            "physical_sha256": _file_sha(frozen / Path(*relative.split("/"))),
        }
        for relative, digest in blobs.items()
    }
    monkeypatch.setattr(
        runner,
        "_tracked_app_source_blobs",
        lambda _root: deepcopy(app_sources),
    )

    parent_artifact = _sha("parent-artifact")
    parent_root = tmp_path / "parent"
    parent_root.mkdir()
    parent_manifest = parent_root / f"{parent_artifact}.json"
    parent_manifest.write_bytes(_bytes({"schema": "parent-fixture"}))
    for name in runner.PARENT_DATABASE_FILENAMES:
        (parent_root / name).write_bytes(f"fixture:{name}".encode("utf-8"))

    overlay_artifact = _sha("overlay-artifact")
    overlay_root = tmp_path / "overlay"
    overlay_root.mkdir()
    overlay_manifest = overlay_root / f"{overlay_artifact}.json"
    overlay_manifest.write_bytes(_bytes({"schema": "overlay-fixture"}))
    overlay_database = overlay_root / runner.OVERLAY_DATABASE_FILENAME
    overlay_database.write_bytes(b"overlay-database")

    suspension_bundle = _sha("suspension-bundle")
    suspension_root = tmp_path / "suspension" / suspension_bundle
    suspension_root.mkdir(parents=True)
    suspension_manifest = suspension_root / "manifest.json"
    suspension_manifest.write_bytes(_bytes({"schema": "suspension-fixture"}))
    suspension_database = suspension_root / "metadata.sqlite3"
    suspension_database.write_bytes(b"suspension-database")

    runtime_root = tmp_path / "runtime" / "site-packages"
    runtime_root.mkdir(parents=True)
    for module_name in runner._REQUIRED_RUNTIME_MODULES:
        package = runtime_root / module_name
        package.mkdir()
        (package / "__init__.py").write_text(
            f"__version__ = 'fixture-{module_name}'\n",
            encoding="utf-8",
        )
    runtime_manifest = runner.build_factor_v3_runtime_dependency_manifest(
        runtime_root.resolve()
    )
    runtime_manifest_path = tmp_path / "runtime-dependency-manifest.json"
    runtime_manifest_path.write_bytes(_bytes(runtime_manifest))

    run_root = tmp_path / "run"
    verification_root = tmp_path / "verification"
    external_claim_root = tmp_path / "external-claims"
    external_claim_root.mkdir()

    monkeypatch.setattr(runner, "PARENT_ARTIFACT_SHA256", parent_artifact)
    monkeypatch.setattr(runner, "PARENT_MANIFEST_FILE_SHA256", _file_sha(parent_manifest))
    monkeypatch.setattr(runner, "OVERLAY_ARTIFACT_SHA256", overlay_artifact)
    monkeypatch.setattr(runner, "OVERLAY_MANIFEST_FILE_SHA256", _file_sha(overlay_manifest))
    monkeypatch.setattr(runner, "SUSPENSION_BUNDLE_SHA256", suspension_bundle)
    monkeypatch.setattr(runner, "SUSPENSION_METADATA_SHA256", _file_sha(suspension_database))
    monkeypatch.setattr(
        runner,
        "_git_identity",
        lambda root: {
            "clean": True,
            "commit": runner.FROZEN_SOURCE_COMMIT,
            "index_diff_clean": True,
            "index_flags_clean": True,
            "inside_work_tree": True,
            "repository_controls": {
                "common_directory_sha256": _sha("fixture-common-git-directory"),
                "object_alternates_absent": True,
                "replacement_refs_absent": True,
            },
            "top_level_sha256": hashlib.sha256(
                str(root.resolve(strict=True)).encode("utf-8")
            ).hexdigest(),
            "tree_oid": runner.FROZEN_SOURCE_TREE_OID,
            "worktree_diff_clean": True,
        },
    )
    monkeypatch.setattr(
        runner,
        "_git_blob_bytes",
        lambda root, relative: (root / Path(*relative.split("/"))).read_bytes().replace(
            b"\r\n",
            b"\n",
        ),
    )
    monkeypatch.setattr(
        runner,
        "_assign_windows_job",
        lambda _process: (
            _PopenFixture.events.append("assign"),
            setattr(
                _PopenFixture,
                "job_assignments",
                _PopenFixture.job_assignments + 1,
            ),
            object(),
        )[-1],
    )
    monkeypatch.setattr(
        runner,
        "_resume_suspended_process",
        lambda _process: _PopenFixture.events.append("resume"),
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_windows_job_tree",
        lambda _job, *, timeout_seconds: _PopenFixture.events.append("drain"),
    )
    monkeypatch.setattr(runner, "_close_windows_job", lambda _job: None)

    spec = runner.build_factor_v3_parent_source_candidate_run_spec(
        frozen_source_root=frozen.resolve(),
        python_executable=Path(sys.executable).resolve(),
        parent_materialization_manifest_path=parent_manifest.resolve(),
        overlay_manifest_path=overlay_manifest.resolve(),
        suspension_metadata_path=suspension_database.resolve(),
        run_root=run_root.resolve(),
        verification_root=verification_root.resolve(),
        external_run_claim_path=(external_claim_root / "run.claim.json").resolve(),
        external_verification_claim_path=(
            external_claim_root / "verification.claim.json"
        ).resolve(),
        runtime_dependency_manifest_path=runtime_manifest_path.resolve(),
        expected_runtime_dependency_manifest_file_sha256=_file_sha(runtime_manifest_path),
    )
    spec_path = tmp_path / "run-spec.json"
    raw = _bytes(spec)
    spec_path.write_bytes(raw)
    paths = {
        "frozen": frozen,
        "parent_manifest": parent_manifest,
        "overlay_manifest": overlay_manifest,
        "overlay_database": overlay_database,
        "suspension_database": suspension_database,
        "suspension_manifest": suspension_manifest,
        "runtime_root": runtime_root,
        "runtime_manifest": runtime_manifest_path,
        "verification_root": verification_root,
    }
    return spec_path, hashlib.sha256(raw).hexdigest(), run_root, spec, paths


def _child_result(spec: dict[str, Any]) -> dict[str, Any]:
    unsigned = {
        "development_only": True,
        "formal_materialization_eligible": False,
        "import_network_event_sequence": spec["runtime_dependencies"][
            "allowed_import_network_event_sequences"
        ][0],
        "isolated_runtime_verified": True,
        "loaded_app_module_origins_sha256": _sha("loaded-origins"),
        "loaded_runtime_dependency_origins_sha256": _sha(
            "loaded-runtime-dependency-origins"
        ),
        "loopback_import_probe_count": 1,
        "network_denied": True,
        "overlay_public_verification": {
            "artifact_sha256": spec["overlay"]["expected_artifact_sha256"],
            "eligible_candidate_count": 1_796_834,
            "excluded_candidate_count": 1,
            "feature_row_count": 1_796_834,
            "formal_preregistration_verified": True,
            "manifest_file_sha256": spec["overlay"]["expected_manifest_file_sha256"],
            "overlay_file_sha256": _sha("overlay-file"),
            "parent_artifact_sha256": spec["parent"]["expected_artifact_sha256"],
            "verified": True,
        },
        "parent_public_verification": {
            "artifact_sha256": spec["parent"]["expected_artifact_sha256"],
            "feature_row_count": runner.PARENT_FEATURE_ROW_COUNT,
            "fold_count": runner.PARENT_FOLD_COUNT,
            "folds_sha256": runner.PARENT_FOLDS_SHA256,
            "manifest_file_sha256": spec["parent"]["expected_manifest_file_sha256"],
            "outcome_row_count": runner.PARENT_OUTCOME_ROW_COUNT,
            "stage_bar_row_count": 10_000_000,
            "verified": True,
        },
        "public_verifier_replay_performed": True,
        "schema": runner.CHILD_RESULT_SCHEMA,
        **{field: False for field in runner.SAFETY_FALSE_FIELDS},
    }
    return _signed(unsigned, "result_sha256")


def _install_success_child(
    monkeypatch: pytest.MonkeyPatch,
    spec: dict[str, Any],
) -> bytes:
    raw = _bytes(_child_result(spec))
    _PopenFixture.stdout = raw
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    return raw


def _resign_spec(spec: dict[str, Any]) -> dict[str, Any]:
    unsigned = deepcopy(spec)
    unsigned.pop("run_spec_sha256")
    return {**unsigned, "run_spec_sha256": _sha(unsigned)}


def _resign_child(result: dict[str, Any]) -> dict[str, Any]:
    unsigned = deepcopy(result)
    unsigned.pop("result_sha256")
    return {**unsigned, "result_sha256": _sha(unsigned)}


def test_bootstrap_emits_the_exact_validator_field_contract() -> None:
    tree = ast.parse(runner._CHILD_BOOTSTRAP)
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "unsigned" for target in node.targets)
    ]
    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.Dict)
    observed = {ast.literal_eval(key) for key in value.keys}
    expected = {
        "development_only",
        "formal_materialization_eligible",
        "import_network_event_sequence",
        "isolated_runtime_verified",
        "loaded_app_module_origins_sha256",
        "loaded_runtime_dependency_origins_sha256",
        "loopback_import_probe_count",
        "network_denied",
        "overlay_public_verification",
        "parent_public_verification",
        "public_verifier_replay_performed",
        "schema",
        *runner.SAFETY_FALSE_FIELDS,
    }
    assert observed == expected


def test_child_imports_and_validates_runtime_before_source_root_becomes_visible() -> None:
    bootstrap = runner._CHILD_BOOTSTRAP
    runtime_insert = bootstrap.index('sys.path.insert(0, str(runtime_root))')
    guard_install = bootstrap.index(
        "manifest_import_guard = install_manifest_import_guard()"
    )
    runtime_import = bootstrap.index('for module_name in runtime_required_modules:')
    early_validation = bootstrap.index('"required runtime dependency prevalidation rejected"')
    app_import = bootstrap.index("from app.audited_pit_factor_v2_parent import")
    assert 'sys.path.insert(0, str(source_root))' not in bootstrap
    assert runtime_insert < guard_install < runtime_import < early_validation < app_import


def _run_manifest_guard_probe(
    *,
    source_root: Path,
    runtime_root: Path,
    source_files: dict[str, str],
    runtime_files: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    program = "\n".join(
        [
            "import hashlib",
            "import importlib",
            "import importlib.machinery",
            "import importlib.util",
            "from pathlib import Path",
            "import sys",
            f"source_root = Path({str(source_root)!r}).resolve(strict=True)",
            f"runtime_root = Path({str(runtime_root)!r}).resolve(strict=True)",
            f"app_source_sha256 = {source_files!r}",
            f"runtime_file_sha256 = {runtime_files!r}",
            runner._MANIFEST_IMPORT_GUARD_SOURCE,
            "sys.path.insert(0, str(runtime_root))",
            "install_manifest_import_guard()",
            "import app.probe",
            "print(Path(sys.modules['sqlite3'].__file__).resolve(strict=True))",
        ]
    )
    return subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", program],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_manifest_guard_never_executes_transient_non_app_source_module(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime"
    app_root = source_root / "app"
    app_root.mkdir(parents=True)
    runtime_root.mkdir()
    marker = tmp_path / "source-marker.txt"
    app_init = app_root / "__init__.py"
    probe = app_root / "probe.py"
    app_init.write_text("", encoding="utf-8")
    probe.write_text("import sqlite3\n", encoding="utf-8")
    (source_root / "sqlite3.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    source_files = {
        "app/__init__.py": _file_sha(app_init),
        "app/probe.py": _file_sha(probe),
    }

    completed = _run_manifest_guard_probe(
        source_root=source_root,
        runtime_root=runtime_root,
        source_files=source_files,
        runtime_files={},
    )

    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
    assert not Path(completed.stdout.strip()).is_relative_to(source_root)


def test_manifest_guard_rejects_unlisted_runtime_module_before_execution(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime"
    app_root = source_root / "app"
    app_root.mkdir(parents=True)
    runtime_root.mkdir()
    marker = tmp_path / "runtime-marker.txt"
    app_init = app_root / "__init__.py"
    probe = app_root / "probe.py"
    app_init.write_text("", encoding="utf-8")
    probe.write_text("import sqlite3\n", encoding="utf-8")
    (runtime_root / "sqlite3.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    source_files = {
        "app/__init__.py": _file_sha(app_init),
        "app/probe.py": _file_sha(probe),
    }

    completed = _run_manifest_guard_probe(
        source_root=source_root,
        runtime_root=runtime_root,
        source_files=source_files,
        runtime_files={},
    )

    assert completed.returncode != 0
    assert "runtime dependency pre-execution hash rejected" in completed.stderr
    assert not marker.exists()


def test_real_no_site_import_smoke_uses_explicit_root_and_never_processes_pth(
    tmp_path: Path,
) -> None:
    site_roots = {
        next(parent for parent in Path(module.__file__).resolve().parents if parent.name == "site-packages")
        for module in (numpy, pandas, requests, xgboost)
    }
    assert len(site_roots) == 1
    injected = tmp_path / "injected-dependency-root"
    injected.mkdir()
    marker = tmp_path / "pth-marker.txt"
    (injected / "must-not-run.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    report = runner._isolated_runtime_import_smoke_for_test(
        python_executable=Path(sys.executable).resolve(),
        dependency_roots=[injected.resolve(), *site_roots],
    )

    assert report["isolated"] is True
    assert report["no_site"] is True
    assert report["required_modules"] == ["numpy", "pandas", "requests", "xgboost"]
    assert not marker.exists()


def test_runtime_manifest_is_explicitly_candidate_only(tmp_path: Path) -> None:
    root = tmp_path / "site-packages"
    root.mkdir()
    (root / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = runner.build_factor_v3_runtime_dependency_manifest(root.resolve())
    assert manifest["candidate_file_closure_only"] is True
    assert manifest["runtime_dependency_authority_verified"] is False
    assert manifest["python_runtime_attested"] is False
    assert manifest["native_dependencies_attested"] is False
    assert manifest["venv_relationship_attested"] is False
    assert manifest["formal_execution_eligible"] is False


@pytest.mark.parametrize("bad_path", ["pkg/data.txt:stream", "Pkg/mod.py"])
def test_runtime_manifest_rejects_ads_and_casefold_collisions(
    tmp_path: Path,
    bad_path: str,
) -> None:
    root = tmp_path / "site-packages"
    root.mkdir()
    (root / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = runner.build_factor_v3_runtime_dependency_manifest(root.resolve())
    template = deepcopy(manifest["files"][0])
    if bad_path == "Pkg/mod.py":
        manifest["files"] = [
            {**template, "relative_path": "pkg/mod.py"},
            {**template, "relative_path": bad_path},
        ]
        manifest["files"].sort(key=lambda item: item["relative_path"])
    else:
        manifest["files"] = [{**template, "relative_path": bad_path}]
    manifest["file_count"] = len(manifest["files"])
    manifest["files_sha256"] = _sha(manifest["files"])
    unsigned = deepcopy(manifest)
    unsigned.pop("manifest_root_sha256")
    manifest["manifest_root_sha256"] = _sha(unsigned)
    with pytest.raises(ValueError, match="path|collision"):
        runner._validated_runtime_dependency_manifest(manifest)


def test_git_subprocess_disables_replace_config_and_starts_suspended(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    observed: dict[str, Any] = {}
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
        encoding="utf-8",
    )

    class GitPopen:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            events.append("popen")
            observed.update({"command": command, **kwargs})
            self.returncode: int | None = None

        def communicate(self, timeout: int | None = None) -> tuple[None, None]:
            events.append("communicate")
            assert timeout == 60
            observed["stdout"].write(b"fixture-output")
            observed["stdout"].flush()
            self.returncode = 0
            return None, None

    monkeypatch.setattr(runner.subprocess, "Popen", GitPopen)
    monkeypatch.setattr(
        runner,
        "_assign_windows_job",
        lambda _process: (events.append("assign"), object())[-1],
    )
    monkeypatch.setattr(
        runner,
        "_resume_suspended_process",
        lambda _process: events.append("resume"),
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_windows_job_tree",
        lambda _job, *, timeout_seconds: events.append("drain"),
    )
    monkeypatch.setattr(runner, "_close_windows_job", lambda _job: None)

    assert runner._git_command(tmp_path.resolve(), "rev-parse", "HEAD") == b"fixture-output"
    assert events == ["popen", "assign", "resume", "communicate", "drain"]
    assert "--no-replace-objects" in observed["command"]
    assert "core.fsmonitor=false" in observed["command"]
    assert f"core.attributesFile={runner.os.devnull}" in observed["command"]
    assert observed["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert observed["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert observed["env"]["GIT_ATTR_NOSYSTEM"] == "1"
    assert observed["close_fds"] is True
    if sys.platform == "win32":
        assert observed["creationflags"] & runner._CREATE_SUSPENDED


def test_git_local_filter_config_is_rejected_before_any_process_can_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    marker = tmp_path / "filter-marker.txt"
    (git_dir / "config").write_text(
        "\n".join(
            [
                "[core]",
                "\trepositoryformatversion = 0",
                '[filter "evil"]',
                f"\tclean = python -c \"from pathlib import Path; Path({str(marker)!r}).write_text('executed')\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[bool] = []

    def must_not_start(*_args: Any, **_kwargs: Any) -> Any:
        calls.append(True)
        marker.write_text("executed", encoding="utf-8")
        raise AssertionError("Git child started")

    monkeypatch.setattr(runner.subprocess, "Popen", must_not_start)
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="local config section",
    ):
        runner._git_command(tmp_path.resolve(), "status", "--porcelain=v1")
    assert calls == []
    assert not marker.exists()


def test_git_job_assignment_failure_kills_and_waits_without_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    monkeypatch.setattr(
        runner,
        "_assign_windows_job",
        lambda _process: (_ for _ in ()).throw(
            runner.FactorV3ParentSourceAuthorityRunnerError(
                "Windows Job Object assignment rejected"
            )
        ),
    )
    monkeypatch.setattr(
        runner,
        "_resume_suspended_process",
        lambda _process: _PopenFixture.events.append("resume"),
    )

    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="assignment",
    ):
        runner._git_command(tmp_path.resolve(), "rev-parse", "HEAD")

    assert _PopenFixture.events == ["popen", "kill", "wait"]


@pytest.mark.parametrize(
    "job_error",
    [
        "Windows Job Object creation rejected",
        "Windows Job Object policy rejected",
        "Windows Job Object assignment rejected",
    ],
)
def test_child_job_setup_failure_kills_waits_closes_pipes_and_never_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    job_error: str,
) -> None:
    spec_path, digest, run_root, _spec, _paths = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    monkeypatch.setattr(
        runner,
        "_assign_windows_job",
        lambda _process: (_ for _ in ()).throw(
            runner.FactorV3ParentSourceAuthorityRunnerError(job_error)
        ),
    )

    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="Job Object",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )

    assert _PopenFixture.events == ["popen", "kill", "wait"]
    failure = runner._load_failure_for_test(run_root)
    assert failure["child_exit_code"] == -9
    assert not list(run_root.glob(".child-*.partial"))


def test_child_job_drain_failure_still_waits_for_process_exit_and_closes_pipes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, _spec, _paths = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    monkeypatch.setattr(runner, "_assign_windows_job", lambda _process: object())
    monkeypatch.setattr(
        runner,
        "_resume_suspended_process",
        lambda _process: (_ for _ in ()).throw(
            runner.FactorV3ParentSourceAuthorityRunnerError("resume rejected")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_terminate_windows_job",
        lambda _job: _PopenFixture.events.append("terminate-job"),
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_windows_job_tree",
        lambda _job, *, timeout_seconds: (_ for _ in ()).throw(
            runner.FactorV3ParentSourceAuthorityRunnerError("drain rejected")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_close_windows_job",
        lambda _job: _PopenFixture.events.append("close-job"),
    )

    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="drain",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )

    assert _PopenFixture.events == [
        "popen",
        "terminate-job",
        "close-job",
        "wait",
    ]
    failure = runner._load_failure_for_test(run_root)
    assert failure["child_exit_code"] == -9
    assert not list(run_root.glob(".child-*.partial"))


@pytest.mark.parametrize(
    ("failure_kind", "failure_call"),
    [
        ("pipe", 2),
        ("fdopen", 1),
        ("fdopen", 2),
        ("fdopen", 3),
        ("fdopen", 4),
    ],
)
def test_child_pipe_setup_failure_closes_every_created_descriptor_before_failure_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    failure_call: int,
) -> None:
    spec_path, digest, run_root, _spec, _paths = _fixture(tmp_path, monkeypatch)
    original_pipe = runner.os.pipe
    original_fdopen = runner.os.fdopen
    original_persist = runner._persist_failure
    original_spawn = runner._spawn_child
    created_fds: list[int] = []
    pipe_calls = 0
    fdopen_calls = 0
    checked_before_publish: list[bool] = []

    def tracked_pipe() -> tuple[int, int]:
        nonlocal pipe_calls
        pipe_calls += 1
        if failure_kind == "pipe" and pipe_calls == failure_call:
            raise OSError("pipe fixture failure")
        pair = original_pipe()
        created_fds.extend(pair)
        return pair

    def tracked_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        nonlocal fdopen_calls
        fdopen_calls += 1
        if failure_kind == "fdopen" and fdopen_calls == failure_call:
            raise OSError("fdopen fixture failure")
        return original_fdopen(fd, *args, **kwargs)

    def injected_spawn(*args: Any, **kwargs: Any) -> int:
        monkeypatch.setattr(runner.os, "pipe", tracked_pipe)
        monkeypatch.setattr(runner.os, "fdopen", tracked_fdopen)
        try:
            return original_spawn(*args, **kwargs)
        finally:
            monkeypatch.setattr(runner.os, "pipe", original_pipe)
            monkeypatch.setattr(runner.os, "fdopen", original_fdopen)

    def checked_persist(**kwargs: Any) -> None:
        for descriptor in created_fds:
            with pytest.raises(OSError):
                runner.os.fstat(descriptor)
        checked_before_publish.append(True)
        original_persist(**kwargs)

    monkeypatch.setattr(runner, "_spawn_child", injected_spawn)
    monkeypatch.setattr(runner, "_persist_failure", checked_persist)

    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="child process launch",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )

    assert checked_before_publish == [True]
    assert _PopenFixture.calls == []
    assert "resume" not in _PopenFixture.events
    assert not list(run_root.glob(".child-*.partial"))


def test_run_and_independent_verify_publish_single_attempt_cas_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    child_stdout = _install_success_child(monkeypatch, spec)

    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )

    assert result["status"] == "completed"
    assert result["authority_status"] == "CANDIDATE_PARENT_REPLAY_ONLY"
    assert result["candidate_verified"] is True
    assert result["verified"] is False
    assert result["source_authority_verified"] is False
    assert result["formal_materialization_eligible"] is False
    assert all(result[field] is False for field in runner.SAFETY_FALSE_FIELDS)
    assert _PopenFixture.calls[0]["command"][:3] == [
        str(Path(sys.executable).resolve()),
        "-I",
        "-B",
    ]
    assert _PopenFixture.calls[0]["command"][3:5] == ["-S", "-X"]
    pycache_argument = _PopenFixture.calls[0]["command"][5]
    assert pycache_argument.startswith("pycache_prefix=")
    child_input = json.loads(_PopenFixture.calls[0]["input"])
    assert child_input["pycache_prefix"] == pycache_argument.removeprefix("pycache_prefix=")
    assert not Path(child_input["pycache_prefix"]).exists()
    assert _PopenFixture.calls[0]["shell"] is False
    assert _PopenFixture.calls[0]["close_fds"] is True
    assert _PopenFixture.calls[0]["stdin"] is subprocess.PIPE
    assert isinstance(_PopenFixture.calls[0]["stdout"].name, int)
    assert isinstance(_PopenFixture.calls[0]["stderr"].name, int)
    child_cwd = Path(_PopenFixture.calls[0]["cwd"])
    assert child_cwd.parent == run_root
    assert child_cwd.name.startswith(".child-cwd-")
    assert not child_cwd.exists()
    assert _PopenFixture.job_assignments == 1
    if sys.platform == "win32":
        assert _PopenFixture.calls[0]["creationflags"] & runner._CREATE_SUSPENDED
    assert _PopenFixture.events[:5] == [
        "popen",
        "assign",
        "resume",
        "communicate",
        "drain",
    ]

    status = json.loads((run_root / "status.json").read_text(encoding="utf-8"))
    assert status["child_stdout"] == {
        "sha256": hashlib.sha256(child_stdout).hexdigest(),
        "size_bytes": len(child_stdout),
    }
    assert status["child_stderr"] == {
        "sha256": hashlib.sha256(b"").hexdigest(),
        "size_bytes": 0,
    }
    assert not any("text" in key or "raw" in key for key in status)
    receipt_path = run_root / Path(*result["receipt_relative_path"].split("/"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["source_authority_verified"] is False
    assert receipt["authority_status"] == "CANDIDATE_PARENT_REPLAY_ONLY"
    assert receipt["candidate_verified"] is True
    assert receipt["verified"] is False
    assert receipt["formal_materialization_eligible"] is False
    assert receipt["public_verifier_replay_performed"] is True
    assert receipt["child_stdout"] == status["child_stdout"]
    assert receipt["child_stderr"] == status["child_stderr"]
    assert receipt["preflight_attestation"] == receipt["postflight_attestation"]
    assert (
        receipt["preflight_attestation"]["attestation_sha256"]
        == receipt["preflight_attestation_sha256"]
    )
    assert (
        receipt["postflight_attestation"]["attestation_sha256"]
        == receipt["postflight_attestation_sha256"]
    )
    assert all(receipt[field] is False for field in runner.SAFETY_FALSE_FIELDS)

    verified = runner.verify_factor_v3_parent_source_candidate_run(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    assert verified["schema"] == runner.VERIFICATION_RESULT_SCHEMA
    assert verified["status"] == "completed"
    assert verified["verified"] is False
    assert verified["source_authority_verified"] is False
    assert Path(spec["verification_root"]).exists()
    assert not any(path.name.startswith(".verify-child") for path in run_root.iterdir())
    assert len(_PopenFixture.calls) == 2
    assert _PopenFixture.job_assignments == 2
    verification_status_before = (
        Path(spec["verification_root"]) / "status.json"
    ).read_bytes()
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="verification.*claimed|single attempt",
    ):
        runner.verify_factor_v3_parent_source_candidate_run(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    assert (Path(spec["verification_root"]) / "status.json").read_bytes() == (
        verification_status_before
    )
    assert len(_PopenFixture.calls) == 2
    status_before = (run_root / "status.json").read_bytes()
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="claimed|attempt"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    assert (run_root / "status.json").read_bytes() == status_before


def test_run_refuses_candidate_result_when_terminal_namespace_has_extra_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    original = runner._create_owned_cas

    def polluted(root: Path, category: str, value: dict[str, Any]) -> Any:
        result = original(root, category, value)
        if category == "receipts":
            (root / "foreign.txt").write_text("foreign", encoding="utf-8")
        return result

    monkeypatch.setattr(runner, "_create_owned_cas", polluted)
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="terminal output tree",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )


def test_verify_refuses_candidate_result_when_terminal_namespace_has_extra_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    original = runner._create_owned_cas

    def polluted(root: Path, category: str, value: dict[str, Any]) -> Any:
        result = original(root, category, value)
        if category == "verification-receipts":
            extra = root / category / "sha256" / "ff" / f"{'f' * 64}.json"
            extra.parent.mkdir(parents=True)
            extra.write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(runner, "_create_owned_cas", polluted)
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="terminal output tree",
    ):
        runner.verify_factor_v3_parent_source_candidate_run(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory sharing contract")
def test_directory_chain_handle_blocks_parent_replacement_until_owned_file_closes(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "owned-parent"
    parent.mkdir()
    owned = runner._OwnedFile(parent / "status.json", label="fixture", raw=b"{}")
    moved = tmp_path / "moved-parent"
    try:
        with pytest.raises(OSError):
            parent.rename(moved)
        owned.verify()
    finally:
        owned.close()
    parent.rename(moved)
    assert (moved / "status.json").read_bytes() == b"{}"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda spec: spec["frozen_source"].update({"expected_commit": "0" * 40}),
        lambda spec: spec["frozen_source"].update({"expected_tree_oid": "0" * 40}),
        lambda spec: spec["frozen_source"].update({"checkout_policy": "lf"}),
        lambda spec: spec["frozen_source"]["expected_blob_sha256"].update(
            {"app/audited_pit_factor_v2.py": "0" * 64}
        ),
        lambda spec: spec["subprocess"].update({"python_flags": []}),
        lambda spec: spec.update({"formal_materialization_eligible": True}),
    ],
)
def test_run_spec_exact_source_subprocess_and_scope_contract_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    spec_path, _digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    mutation(spec)
    spec = _resign_spec(spec)
    raw = _bytes(spec)
    spec_path.write_bytes(raw)
    with pytest.raises(ValueError, match="source|subprocess|scope|materialization"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=hashlib.sha256(raw).hexdigest(),
            run_root=run_root,
        )
    assert not run_root.exists()


def test_blob_drift_and_sqlite_sidecar_are_failed_with_hash_only_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    blob_path = paths["frozen"] / "app" / "audited_pit_factor_v2.py"
    blob_path.write_bytes(b"drifted\r\n")
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="blob|attestation"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    failure = runner._load_failure_for_test(run_root)
    assert failure["source_authority_verified"] is False
    assert "error_message" not in failure
    assert all(failure[field] is False for field in runner.SAFETY_FALSE_FIELDS)

    second_root = tmp_path / "run-sidecar"
    blob_path.write_bytes(b"fixture:app/audited_pit_factor_v2.py\r\nline-two\r\n")
    (paths["overlay_database"].parent / f"{paths['overlay_database'].name}-wal").write_bytes(
        b"sidecar"
    )
    spec["run_root"] = str(second_root.resolve())
    spec["run_root_sha256"] = hashlib.sha256(spec["run_root"].encode("utf-8")).hexdigest()
    spec["external_run_claim_path"] = str(
        (tmp_path / "external-claims" / "sidecar.claim.json").resolve()
    )
    spec["external_claim_path_sha256"] = hashlib.sha256(
        spec["external_run_claim_path"].encode("utf-8")
    ).hexdigest()
    spec = _resign_spec(spec)
    raw = _bytes(spec)
    spec_path.write_bytes(raw)
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="sidecar"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=hashlib.sha256(raw).hexdigest(),
            run_root=second_root,
        )


def test_reparse_and_path_contract_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    target = paths["parent_manifest"]
    real = runner._is_reparse_point
    monkeypatch.setattr(runner, "_is_reparse_point", lambda path: Path(path) == target or real(path))
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="reparse"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )

    malformed = deepcopy(spec)
    malformed["parent"]["manifest_path"] = str((tmp_path / "wrong.json").resolve())
    malformed = _resign_spec(malformed)
    raw = _bytes(malformed)
    spec_path.write_bytes(raw)
    with pytest.raises(ValueError, match="manifest.*path|filename|overlap"):
        runner.load_factor_v3_parent_source_candidate_run_spec(
            spec_path,
            expected_file_sha256=hashlib.sha256(raw).hexdigest(),
        )

    overlapping = deepcopy(spec)
    overlapping["external_run_claim_path"] = str(
        (paths["parent_manifest"].parent / "claim.json").resolve()
    )
    overlapping["external_claim_path_sha256"] = hashlib.sha256(
        overlapping["external_run_claim_path"].encode("utf-8")
    ).hexdigest()
    overlapping = _resign_spec(overlapping)
    raw = _bytes(overlapping)
    spec_path.write_bytes(raw)
    with pytest.raises(ValueError, match="overlap"):
        runner.load_factor_v3_parent_source_candidate_run_spec(
            spec_path,
            expected_file_sha256=hashlib.sha256(raw).hexdigest(),
        )


def test_child_exit_records_only_stdout_stderr_hash_and_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, _spec, _paths = _fixture(tmp_path, monkeypatch)
    _PopenFixture.stdout = b"child raw output"
    _PopenFixture.stderr = b"child private diagnostic"
    _PopenFixture.exit_code = 7
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="child.*exit"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    failure = runner._load_failure_for_test(run_root)
    assert failure["child_exit_code"] == 7
    assert failure["child_stdout"] == {
        "sha256": hashlib.sha256(_PopenFixture.stdout).hexdigest(),
        "size_bytes": len(_PopenFixture.stdout),
    }
    assert failure["child_stderr"] == {
        "sha256": hashlib.sha256(_PopenFixture.stderr).hexdigest(),
        "size_bytes": len(_PopenFixture.stderr),
    }
    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in run_root.rglob("*.json")
    )
    assert "child raw output" not in persisted
    assert "child private diagnostic" not in persisted
    status = json.loads((run_root / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["source_authority_verified"] is False


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_child_output_limit_is_enforced_while_process_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream_name: str,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    limit = spec["subprocess"][f"max_{stream_name}_bytes"]
    oversized = b"x" * (limit + 1)
    setattr(_PopenFixture, stream_name, oversized)
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    terminated: list[object] = []
    monkeypatch.setattr(
        runner,
        "_terminate_windows_job",
        lambda job: terminated.append(job),
    )

    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="output size",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )

    assert len(terminated) == 1
    failure = runner._load_failure_for_test(run_root)
    assert failure[f"child_{stream_name}"] == {
        "sha256": hashlib.sha256(oversized).hexdigest(),
        "size_bytes": len(oversized),
    }
    assert not list(run_root.glob(".child-*.partial"))


@pytest.mark.parametrize(
    "sequence",
    [["gethostname", "new", "bind"], ["new", "bind", "gethostname"]],
)
def test_child_result_accepts_only_the_two_observed_import_network_sequences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sequence: list[str],
) -> None:
    _spec_path, _digest, _run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    child = _child_result(spec)
    child["import_network_event_sequence"] = sequence
    child = _resign_child(child)
    assert runner._validate_child_result(_bytes(child), spec)[
        "import_network_event_sequence"
    ] == sequence


@pytest.mark.parametrize(
    "sequence",
    [
        ["gethostname", "bind", "new"],
        ["gethostname", "new", "new", "bind"],
        ["new", "gethostname", "bind"],
    ],
)
def test_child_result_rejects_unobserved_import_network_sequences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sequence: list[str],
) -> None:
    _spec_path, _digest, _run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    child = _child_result(spec)
    child["import_network_event_sequence"] = sequence
    child = _resign_child(child)
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="isolation",
    ):
        runner._validate_child_result(_bytes(child), spec)


def test_input_toctou_postflight_drift_is_rejected_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    original = runner._InputClosure.postflight

    def drifted_postflight(self: Any) -> dict[str, Any]:
        value = original(self)
        return {**value, "attestation_sha256": "0" * 64}

    monkeypatch.setattr(runner._InputClosure, "postflight", drifted_postflight)
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="postflight|changed"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    assert not (run_root / "receipts").exists()
    failure = runner._load_failure_for_test(run_root)
    assert failure["source_authority_verified"] is False


def test_frozen_source_bytecode_cache_is_bypassed_by_isolated_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    pycache = paths["frozen"] / "app" / "__pycache__"
    pycache.mkdir()
    (pycache / "audited_pit_factor_v2.cpython-312.pyc").write_bytes(b"valid-looking-pyc")

    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    assert result["status"] == "completed"
    assert len(_PopenFixture.calls) == 1


def test_child_cleanup_failure_preserves_literal_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, _spec, _paths = _fixture(tmp_path, monkeypatch)
    _PopenFixture.exit_code = 7
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    monkeypatch.setattr(
        runner,
        "_remove_isolated_pycache_prefix",
        lambda _path: (_ for _ in ()).throw(
            runner.FactorV3ParentSourceAuthorityRunnerError("cleanup rejected")
        ),
    )
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="cleanup|terminal output tree",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    failure = runner._load_failure_for_test(run_root)
    assert failure["child_exit_code"] == 7


@pytest.mark.skipif(sys.platform != "win32", reason="Windows strong sharing contract")
def test_transitive_tracked_app_source_is_strongly_held_during_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, paths = _fixture(tmp_path, monkeypatch)
    transitive = paths["frozen"] / "app" / "transitive_dependency.py"
    logical = b"VALUE = 'trusted'\n"
    transitive.write_bytes(logical.replace(b"\n", b"\r\n"))
    app_sources = runner._tracked_app_source_blobs(paths["frozen"])
    app_sources["app/transitive_dependency.py"] = {
        "git_blob_sha256": hashlib.sha256(logical).hexdigest(),
        "physical_sha256": _file_sha(transitive),
    }
    monkeypatch.setattr(
        runner,
        "_tracked_app_source_blobs",
        lambda _root: deepcopy(app_sources),
    )
    _install_success_child(monkeypatch, spec)

    def attempt_replace() -> None:
        with pytest.raises(PermissionError):
            transitive.write_bytes(b"VALUE = 'replaced'\r\n")

    _PopenFixture.callback = attempt_replace
    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    assert result["status"] == "completed"


def test_held_input_closure_blocks_child_write_until_terminal_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)

    def blocked_write() -> None:
        with pytest.raises(OSError):
            paths["overlay_database"].write_bytes(b"blocked")

    _PopenFixture.callback = blocked_write
    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    assert result["status"] == "completed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("feature_row_count", 1_796_834),
        ("outcome_row_count", 1_782_861),
        ("fold_count", 5),
        ("folds_sha256", "0" * 64),
    ],
)
def test_child_parent_frozen_counts_and_folds_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    child = _child_result(spec)
    child["parent_public_verification"][field] = value
    child = _resign_child(child)
    _PopenFixture.stdout = _bytes(child)
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="child parent"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )


@pytest.mark.parametrize(
    "stdout",
    [
        b"{}",
        b'{"schema":"wrong"}',
        b'{"schema":"factor-v3-parent-source-public-verification/v1"}\n',
    ],
)
def test_child_stdout_requires_exact_canonical_verified_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
) -> None:
    spec_path, digest, run_root, _spec, _paths = _fixture(tmp_path, monkeypatch)
    _PopenFixture.stdout = stdout
    monkeypatch.setattr(runner.subprocess, "Popen", _PopenFixture)
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="child.*result"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )


def test_frozen_source_checkout_must_be_exact_crlf_derivation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    source = paths["frozen"] / "app" / "audited_pit_factor_v2.py"
    source.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="checkout"):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )


@pytest.mark.parametrize("target", ["claim", "status", "receipt"])
def test_verify_rejects_claim_status_and_receipt_tamper_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    path = {
        "claim": run_root / "claim.json",
        "status": run_root / "status.json",
        "receipt": run_root / Path(*result["receipt_relative_path"].split("/")),
    }[target]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["tampered"] = True
    path.write_bytes(_bytes(value))
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="claim|status|receipt"):
        runner.verify_factor_v3_parent_source_candidate_run(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    assert len(_PopenFixture.calls) == 1


def test_atomic_receipt_cas_never_overwrites_racing_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    rival = b'{"rival":true}'
    observed_target: list[Path] = []

    def racing_link(_source: Any, destination: Any) -> None:
        target = Path(destination)
        target.write_bytes(rival)
        observed_target.append(target)
        raise FileExistsError

    monkeypatch.setattr(runner.os, "link", racing_link)
    with pytest.raises(runner.FactorV3ParentSourceAuthorityRunnerError, match="collision"):
        runner._create_cas(root, "receipts", {"schema": "fixture"})
    assert observed_target[0].read_bytes() == rival


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ownership contract")
def test_claim_log_and_status_paths_are_exclusively_owned_until_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    blocked: list[str] = []

    def attempt_foreign_writes() -> None:
        for name in ("claim.json", "status.json", ".child-stdout.partial"):
            try:
                mode = "x+b" if name == "status.json" else "r+b"
                (run_root / name).open(mode).close()
            except (OSError, PermissionError):
                blocked.append(name)

    _PopenFixture.callback = attempt_foreign_writes
    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    assert result["status"] == "completed"
    assert blocked == ["claim.json", "status.json", ".child-stdout.partial"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ownership contract")
def test_verify_holds_all_sealed_run_evidence_during_independent_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    receipt_path = run_root / Path(*result["receipt_relative_path"].split("/"))
    targets = [
        run_root / "run-spec.json",
        run_root / "claim.json",
        run_root / "status.json",
        receipt_path,
        Path(spec["external_run_claim_path"]),
    ]
    blocked: list[Path] = []

    def attempt_mutation() -> None:
        for path in targets:
            try:
                with path.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"{}")
            except OSError:
                blocked.append(path)

    _PopenFixture.callback = attempt_mutation
    verified = runner.verify_factor_v3_parent_source_candidate_run(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    assert verified["status"] == "completed"
    assert blocked == targets


def test_independent_verify_failure_seals_hash_only_evidence_and_terminal_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    _PopenFixture.stdout = b"verification raw output"
    _PopenFixture.stderr = b"verification private diagnostic"
    _PopenFixture.exit_code = 9
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="independent child verifier exit",
    ):
        runner.verify_factor_v3_parent_source_candidate_run(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    verification_root = Path(spec["verification_root"])
    status = json.loads((verification_root / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    failure = runner._load_cas(
        verification_root,
        status["failure"],
        category="verification-failures",
    )
    assert failure["child_exit_code"] == 9
    assert failure["child_stdout"] == {
        "sha256": hashlib.sha256(_PopenFixture.stdout).hexdigest(),
        "size_bytes": len(_PopenFixture.stdout),
    }
    assert failure["child_stderr"] == {
        "sha256": hashlib.sha256(_PopenFixture.stderr).hexdigest(),
        "size_bytes": len(_PopenFixture.stderr),
    }
    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in verification_root.rglob("*.json")
    )
    assert "verification raw output" not in persisted
    assert "verification private diagnostic" not in persisted
    assert failure["source_authority_verified"] is False
    assert all(failure[field] is False for field in runner.SAFETY_FALSE_FIELDS)


def test_receipt_mutation_before_terminal_is_blocked_by_owned_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    original = runner._create_owned_cas
    blocked: list[bool] = []

    def mutate_after_publish(root: Path, category: str, value: dict[str, Any]):
        relative, file_sha, owned = original(root, category, value)
        if category == "receipts":
            with pytest.raises(OSError):
                (root / Path(*relative.split("/"))).write_bytes(b"{}")
            blocked.append(True)
        return relative, file_sha, owned

    monkeypatch.setattr(runner, "_create_owned_cas", mutate_after_publish)
    result = runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    assert result["status"] == "completed"
    assert blocked == [True]


def test_run_spec_freezes_canonical_run_root_and_external_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    assert spec["run_root"] == str(run_root.resolve())
    assert Path(spec["external_run_claim_path"]).is_absolute()

    other_root = tmp_path / "other-run"
    runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="run root|claim",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=other_root,
        )
    assert len(_PopenFixture.calls) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_external_run_and_verification_claims_cannot_overlap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
) -> None:
    spec_path, _digest, _run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    parent = tmp_path / "overlapping-claims" / "claim"
    child = parent / "nested.claim.json"
    first, second = (child, parent) if reverse else (parent, child)
    spec["external_run_claim_path"] = str(first.resolve())
    spec["external_verification_claim_path"] = str(second.resolve())
    spec["external_claim_path_sha256"] = hashlib.sha256(
        spec["external_run_claim_path"].encode("utf-8")
    ).hexdigest()
    spec = _resign_spec(spec)
    raw = _bytes(spec)
    spec_path.write_bytes(raw)
    with pytest.raises(ValueError, match="claim paths overlap"):
        runner.load_factor_v3_parent_source_candidate_run_spec(
            spec_path,
            expected_file_sha256=hashlib.sha256(raw).hexdigest(),
        )


def test_external_claim_collision_leaves_no_unowned_status_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    rival = b"rival-claim"
    Path(spec["external_run_claim_path"]).write_bytes(rival)
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="exclusive create|claimed|exists",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    assert list(run_root.iterdir()) == []
    assert Path(spec["external_run_claim_path"]).read_bytes() == rival
    assert _PopenFixture.calls == []


def test_post_claim_initialization_failure_seals_terminal_failure_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    original = runner._OwnedFile.__init__

    def fail_snapshot(self: Any, path: Path, *, label: str, raw: bytes) -> None:
        if label == "run spec snapshot":
            raise runner.FactorV3ParentSourceAuthorityRunnerError(
                "fixture initialization failure"
            )
        original(self, path, label=label, raw=raw)

    monkeypatch.setattr(runner._OwnedFile, "__init__", fail_snapshot)
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="initialization failure",
    ):
        runner.run_factor_v3_parent_source_candidate(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    status = json.loads((run_root / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["failure"] is not None
    assert Path(spec["external_run_claim_path"]).exists()
    assert _PopenFixture.calls == []


def test_post_claim_verification_initialization_failure_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, run_root, spec, _paths = _fixture(tmp_path, monkeypatch)
    _install_success_child(monkeypatch, spec)
    runner.run_factor_v3_parent_source_candidate(
        run_spec_path=spec_path,
        expected_run_spec_file_sha256=digest,
        run_root=run_root,
    )
    original = runner._OwnedFile.__init__

    def fail_claim(self: Any, path: Path, *, label: str, raw: bytes) -> None:
        if label == "verification claim":
            raise runner.FactorV3ParentSourceAuthorityRunnerError(
                "fixture verification initialization failure"
            )
        original(self, path, label=label, raw=raw)

    monkeypatch.setattr(runner._OwnedFile, "__init__", fail_claim)
    with pytest.raises(
        runner.FactorV3ParentSourceAuthorityRunnerError,
        match="verification initialization failure",
    ):
        runner.verify_factor_v3_parent_source_candidate_run(
            run_spec_path=spec_path,
            expected_run_spec_file_sha256=digest,
            run_root=run_root,
        )
    verification_root = Path(spec["verification_root"])
    status = json.loads((verification_root / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["failure"] is not None
    assert Path(spec["external_verification_claim_path"]).exists()
    assert len(_PopenFixture.calls) == 1
