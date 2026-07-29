from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MAIN_ROOT = Path(r"E:\AI workspace\quant-signal-lkj")
RUNS_ROOT = MAIN_ROOT / "data" / "research_runs"
FROZEN_SOURCE_ROOT = Path(r"E:\AI workspace\quant-signal-lkj-factor-v2-eval-fix")
EXPECTED_SOURCE_COMMIT = "b0f0a78ebef207434e5cadd85299ef529dd1ff46"
OUTPUT_BASENAME = "audited_pit_factor_v2_development_evaluation_v1_development_4_retry_1"
BASE_RUNNER_PATH = RUNS_ROOT / ".run_factor_v2_development_evaluation.py"
BASE_RUNNER_SHA256 = "997f89e4f38262def6f1d846fa6a47355573b76efdd65eb4a5dee651e8034a4e"
BASE_VERIFIER_PATH = MAIN_ROOT / "scripts" / "verify_factor_v2_development_evaluation_v2.py"
BASE_VERIFIER_SHA256 = "ffce06e2707809aa17592fdd032a896ca86337be684fbd792b921a4b9f89f976"
RUNNER_WRAPPER_PATH = ROOT / "scripts" / "run_factor_v2_development_evaluation_retry1.py"
VERIFIER_WRAPPER_PATH = ROOT / "scripts" / "verify_factor_v2_development_evaluation_retry1.py"
OLD_OUTPUT_DIR = RUNS_ROOT / "audited_pit_factor_v2_development_evaluation_v1_development_4"
NEW_OUTPUT_DIR = RUNS_ROOT / OUTPUT_BASENAME
SHARED_LOCK_PATH = RUNS_ROOT / ".factor_v2_development_evaluation.lock"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_unbound_base(path: Path, name: str) -> ModuleType:
    module = ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    source = path.read_bytes()
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def _uppercase_constants(module: ModuleType) -> dict[str, Any]:
    return {name: value for name, value in vars(module).items() if name.isupper()}


def test_retry_runner_binds_exact_source_and_isolated_paths() -> None:
    wrapper = _load_module(RUNNER_WRAPPER_PATH, "_retry1_runner_bindings")
    runner = wrapper._BASE_MODULE

    assert wrapper.BASE_MODULE_PATH == BASE_RUNNER_PATH
    assert wrapper.EXPECTED_BASE_MODULE_SHA256 == BASE_RUNNER_SHA256
    assert runner.SOURCE_ROOT == FROZEN_SOURCE_ROOT
    assert runner.EXPECTED_SOURCE_COMMIT == EXPECTED_SOURCE_COMMIT
    assert runner.OUTPUT_DIR == NEW_OUTPUT_DIR
    assert runner.STATUS_PATH == NEW_OUTPUT_DIR.with_name(f"{OUTPUT_BASENAME}.run.status.json")
    assert runner.VERIFY_STATUS_PATH == NEW_OUTPUT_DIR.with_name(
        f"{OUTPUT_BASENAME}.verify.status.json"
    )
    assert runner.STATUS_CLAIM_PATH == runner.STATUS_PATH.with_name(
        f"{runner.STATUS_PATH.name}.claim"
    )
    assert runner.EVALUATION_LOCK_PATH == SHARED_LOCK_PATH
    assert Path(runner.__file__).resolve() == RUNNER_WRAPPER_PATH.resolve()
    assert runner.OUTPUT_DIR != OLD_OUTPUT_DIR
    assert runner.STATUS_PATH != OLD_OUTPUT_DIR.with_name(f"{OLD_OUTPUT_DIR.name}.run.status.json")


def test_retry_verifier_binds_retry_runner_and_new_source() -> None:
    wrapper = _load_module(
        VERIFIER_WRAPPER_PATH,
        "_retry1_verifier_bindings",
    )
    verifier = wrapper._BASE_MODULE

    assert wrapper.BASE_MODULE_PATH == BASE_VERIFIER_PATH
    assert wrapper.EXPECTED_BASE_MODULE_SHA256 == BASE_VERIFIER_SHA256
    assert verifier.SOURCE_ROOT == FROZEN_SOURCE_ROOT
    assert verifier.EXPECTED_SOURCE_COMMIT == EXPECTED_SOURCE_COMMIT
    assert verifier.RUNNER_PATH == RUNNER_WRAPPER_PATH
    assert verifier.OUTPUT_DIR == NEW_OUTPUT_DIR
    assert verifier.BUILD_STATUS_PATH == NEW_OUTPUT_DIR.with_name(
        f"{OUTPUT_BASENAME}.run.status.json"
    )
    assert verifier.VERIFY_STATUS_PATH == NEW_OUTPUT_DIR.with_name(
        f"{OUTPUT_BASENAME}.verify.status.json"
    )
    assert verifier.VERIFY_CLAIM_PATH == verifier.VERIFY_STATUS_PATH.with_name(
        f"{verifier.VERIFY_STATUS_PATH.name}.claim"
    )
    assert verifier.EVALUATION_LOCK_PATH == SHARED_LOCK_PATH
    assert Path(verifier.__file__).resolve() == (VERIFIER_WRAPPER_PATH.resolve())

    runner = verifier._load_runner()
    assert runner.SOURCE_ROOT == FROZEN_SOURCE_ROOT
    assert runner.EXPECTED_SOURCE_COMMIT == EXPECTED_SOURCE_COMMIT
    assert runner.OUTPUT_DIR == NEW_OUTPUT_DIR
    assert Path(runner._BASE_MODULE.__file__).resolve() == (RUNNER_WRAPPER_PATH.resolve())


@pytest.mark.parametrize(
    ("wrapper_path", "module_name"),
    [
        (RUNNER_WRAPPER_PATH, "_retry1_runner_exact_overrides"),
        (VERIFIER_WRAPPER_PATH, "_retry1_verifier_exact_overrides"),
    ],
)
def test_retry_wrapper_changes_only_declared_base_bindings(
    wrapper_path: Path,
    module_name: str,
) -> None:
    wrapper = _load_module(wrapper_path, module_name)
    pristine = _load_unbound_base(
        wrapper.BASE_MODULE_PATH,
        f"{module_name}_pristine",
    )
    original = _uppercase_constants(pristine)
    bound = _uppercase_constants(wrapper._BASE_MODULE)
    changed = {name for name in original if original[name] != bound.get(name)}

    assert changed == set(wrapper.OVERRIDDEN_BASE_GLOBALS)
    assert wrapper._BASE_MODULE.__file__ == str(wrapper_path.resolve())


@pytest.mark.parametrize(
    ("wrapper_path", "module_name"),
    [
        (RUNNER_WRAPPER_PATH, "_retry1_runner_sha_drift"),
        (VERIFIER_WRAPPER_PATH, "_retry1_verifier_sha_drift"),
    ],
)
def test_retry_wrapper_rejects_base_sha_drift_before_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrapper_path: Path,
    module_name: str,
) -> None:
    wrapper = _load_module(wrapper_path, module_name)
    tampered = tmp_path / wrapper.BASE_MODULE_PATH.name
    tampered.write_bytes(wrapper.BASE_MODULE_PATH.read_bytes() + b"\n# drift\n")
    monkeypatch.setattr(wrapper, "BASE_MODULE_PATH", tampered)

    with pytest.raises(RuntimeError, match="base module SHA-256 drifted"):
        wrapper._load_base_module()


@pytest.mark.parametrize(
    ("wrapper_path", "module_name"),
    [
        (RUNNER_WRAPPER_PATH, "_retry1_runner_environment"),
        (VERIFIER_WRAPPER_PATH, "_retry1_verifier_environment"),
    ],
)
def test_retry_wrapper_ignores_environment_binding_overrides(
    monkeypatch: pytest.MonkeyPatch,
    wrapper_path: Path,
    module_name: str,
) -> None:
    monkeypatch.setenv("SOURCE_ROOT", r"C:\attacker\source")
    monkeypatch.setenv("EXPECTED_SOURCE_COMMIT", "0" * 40)
    monkeypatch.setenv("OUTPUT_DIR", r"C:\attacker\output")
    monkeypatch.setenv("RUNNER_PATH", r"C:\attacker\runner.py")

    wrapper = _load_module(wrapper_path, module_name)

    assert wrapper._BASE_MODULE.SOURCE_ROOT == FROZEN_SOURCE_ROOT
    assert wrapper._BASE_MODULE.EXPECTED_SOURCE_COMMIT == EXPECTED_SOURCE_COMMIT
    assert wrapper._BASE_MODULE.OUTPUT_DIR == NEW_OUTPUT_DIR


def test_retry_wrappers_reject_cli_binding_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_module(
        RUNNER_WRAPPER_PATH,
        "_retry1_runner_cli",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["retry1-runner", "--output-dir", r"C:\attacker\output"],
    )
    with pytest.raises(SystemExit):
        runner._BASE_MODULE._arguments()

    verifier = _load_module(
        VERIFIER_WRAPPER_PATH,
        "_retry1_verifier_cli",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["retry1-verifier", "--source-root", r"C:\attacker\source"],
    )
    with pytest.raises(SystemExit):
        verifier._BASE_MODULE._arguments()


def test_retry_wrappers_retain_isolated_no_bytecode_preflight() -> None:
    runner = _load_module(
        RUNNER_WRAPPER_PATH,
        "_retry1_runner_interpreter",
    )
    verifier = _load_module(
        VERIFIER_WRAPPER_PATH,
        "_retry1_verifier_interpreter",
    )

    with pytest.raises(RuntimeError, match="Python -I -B"):
        runner._BASE_MODULE._verify_interpreter()
    with pytest.raises(RuntimeError, match="Python -I -B"):
        verifier._BASE_MODULE._verify_interpreter_before_local_import()


def test_retry_wrappers_verify_frozen_source_commit_read_only() -> None:
    runner = _load_module(
        RUNNER_WRAPPER_PATH,
        "_retry1_runner_source",
    )
    verifier = _load_module(
        VERIFIER_WRAPPER_PATH,
        "_retry1_verifier_source",
    )

    runner._BASE_MODULE._verify_source()
    verifier._BASE_MODULE._verify_source_before_local_import()


@pytest.mark.parametrize(
    ("wrapper_path", "module_name"),
    [
        (RUNNER_WRAPPER_PATH, "_retry1_runner_delegate"),
        (VERIFIER_WRAPPER_PATH, "_retry1_verifier_delegate"),
    ],
)
def test_retry_wrapper_main_delegates_without_rewriting_arguments(
    monkeypatch: pytest.MonkeyPatch,
    wrapper_path: Path,
    module_name: str,
) -> None:
    wrapper = _load_module(wrapper_path, module_name)
    calls: list[str] = []

    def delegated_main() -> int:
        calls.append("main")
        return 17

    monkeypatch.setattr(wrapper._BASE_MODULE, "main", delegated_main)

    assert wrapper.main() == 17
    assert calls == ["main"]


def test_expected_base_files_remain_exact() -> None:
    assert hashlib.sha256(BASE_RUNNER_PATH.read_bytes()).hexdigest() == (BASE_RUNNER_SHA256)
    assert hashlib.sha256(BASE_VERIFIER_PATH.read_bytes()).hexdigest() == (BASE_VERIFIER_SHA256)
