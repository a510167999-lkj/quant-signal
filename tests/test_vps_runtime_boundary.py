from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import jobs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = PROJECT_ROOT / "deploy"
VPS_RUNTIME_ARTIFACTS = tuple(
    sorted(
        path
        for path in DEPLOY_DIR.iterdir()
        if path.is_file()
        and (
            path.suffix in {".service", ".timer", ".sh"}
            or "vps" in path.name
        )
    )
)
FORBIDDEN_VPS_COMMANDS = (
    re.compile(r"\bapp\.jobs\s+research[-_]", re.IGNORECASE),
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(r"\b(?:xgboost|lightgbm|catboost)\b", re.IGNORECASE),
)
APP_JOB_COMMAND = re.compile(
    r"-m\s+app\.jobs\s+([a-z0-9-]+)",
    re.IGNORECASE,
)
ALLOWED_VPS_APP_JOBS = {
    "generate-recommendations",
    "jiaoch-connectivity-check",
    "monitor-planned-exits",
    "monitor-recommendations",
    "mootdx-l1-check",
    "production-check",
    "warm-market-cache",
}
EXPECTED_SERVICE_EXEC = {
    "quant-signal-cache-warm.service": (
        "ExecStart=/usr/bin/env VPS_RUNTIME_ROLE=recommendation_only "
        "/home/ubuntu/quant-signal/.venv/bin/python "
        "-m app.jobs warm-market-cache --max-deep 500 --workers 4 "
        "--lookback-days 620"
    ),
    "quant-signal-health.service": (
        "ExecStart=/usr/bin/env VPS_RUNTIME_ROLE=recommendation_only "
        "/home/ubuntu/quant-signal/"
        "deploy/check-production-health.sh"
    ),
    "quant-signal-monitor.service": (
        "ExecStart=/usr/bin/env VPS_RUNTIME_ROLE=recommendation_only "
        "/home/ubuntu/quant-signal/.venv/bin/python "
        "-m app.jobs monitor-recommendations"
    ),
    "quant-signal-planned-exits.service": (
        "ExecStart=/usr/bin/env VPS_RUNTIME_ROLE=recommendation_only "
        "/home/ubuntu/quant-signal/.venv/bin/python "
        "-m app.jobs monitor-planned-exits"
    ),
    "quant-signal-recommend.service": (
        "ExecStart=/usr/bin/env VPS_RUNTIME_ROLE=recommendation_only "
        "/home/ubuntu/quant-signal/.venv/bin/python "
        "-m app.jobs generate-recommendations --run-slot auto"
    ),
    "quant-signal.service": (
        "ExecStart=/usr/bin/env VPS_RUNTIME_ROLE=recommendation_only "
        "/home/ubuntu/quant-signal/.venv/bin/uvicorn "
        "app.main:app --host 127.0.0.1 --port 8010"
    ),
}
EXPECTED_DEPLOY_SHELL_SCRIPTS = {
    "check-production-health.sh",
    "research-vps-tasks.sh",
}
EXPECTED_DEPLOY_SHELL_SHA256 = {
    "check-production-health.sh": (
        "f719fd023093ba8e285348d4a029b9cdb0e68b03aa0fc4257548a20d59c3e285"
    ),
    "research-vps-tasks.sh": (
        "6c68915251e0af7698486aca47db603612ae2e70cf7e5533a7dcfcf59ada0453"
    ),
}


def test_vps_runtime_artifacts_cannot_launch_research_or_training() -> None:
    assert VPS_RUNTIME_ARTIFACTS
    violations: list[str] = []
    for path in VPS_RUNTIME_ARTIFACTS:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_VPS_COMMANDS:
            if pattern.search(text):
                violations.append(f"{path.name}: {pattern.pattern}")
    assert violations == []


def test_vps_app_job_entrypoints_are_explicitly_allowlisted() -> None:
    observed: set[str] = set()
    for path in VPS_RUNTIME_ARTIFACTS:
        text = path.read_text(encoding="utf-8")
        observed.update(
            command.casefold()
            for command in APP_JOB_COMMAND.findall(text)
        )
    assert observed
    assert observed <= ALLOWED_VPS_APP_JOBS


def test_all_vps_services_pin_recommendation_only_role() -> None:
    services = sorted(DEPLOY_DIR.glob("*.service"))
    assert {path.name for path in services} == set(EXPECTED_SERVICE_EXEC)
    for path in services:
        text = path.read_text(encoding="utf-8")
        assert "\nEnvironment=VPS_RUNTIME_ROLE=" not in text
        execution_lines = [
            line
            for line in text.splitlines()
            if line.startswith("Exec")
        ]
        assert execution_lines == [EXPECTED_SERVICE_EXEC[path.name]]


def test_deploy_shell_entrypoints_are_closed_set() -> None:
    scripts = {
        path.name: path for path in DEPLOY_DIR.glob("*.sh")
    }
    assert set(scripts) == EXPECTED_DEPLOY_SHELL_SCRIPTS
    assert set(EXPECTED_DEPLOY_SHELL_SHA256) == EXPECTED_DEPLOY_SHELL_SCRIPTS
    for name, path in scripts.items():
        normalized = path.read_bytes().replace(b"\r\n", b"\n")
        assert (
            hashlib.sha256(normalized).hexdigest()
            == EXPECTED_DEPLOY_SHELL_SHA256[name]
        )


def test_local_research_script_is_not_part_of_deploy_tree() -> None:
    assert not (DEPLOY_DIR / "research-local-tasks.sh").exists()
    assert (PROJECT_ROOT / "scripts" / "research-local-tasks.sh").is_file()


def test_jobs_runtime_role_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        jobs.VPS_RECOMMENDATION_ONLY_COMMANDS
        == frozenset(ALLOWED_VPS_APP_JOBS)
    )
    for command in ALLOWED_VPS_APP_JOBS:
        jobs._assert_runtime_command_allowed(
            command,
            runtime_role="recommendation_only",
        )
    with pytest.raises(ValueError, match="VPS.*禁止"):
        jobs._assert_runtime_command_allowed(
            "research-audited-pit-shallow-gbdt-build-training-dataset",
            runtime_role="recommendation_only",
        )
    with pytest.raises(ValueError, match="未知"):
        jobs._assert_runtime_command_allowed(
            "generate-recommendations",
            runtime_role="unexpected",
        )
    jobs._assert_runtime_command_allowed(
        "research-audited-pit-shallow-gbdt-build-training-dataset",
        runtime_role="local_research",
    )
    jobs._assert_runtime_command_allowed(
        "research-audited-pit-shallow-gbdt-build-training-dataset",
        runtime_role="",
    )
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "recommendation_only")
    with pytest.raises(ValueError, match="VPS.*禁止"):
        jobs.main(["research-backtest"])


def _bash_executable() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parent.parent
        for candidate in (
            git_root / "bin" / "bash.exe",
            git_root / "usr" / "bin" / "bash.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def test_retired_vps_research_entrypoint_is_fail_closed(
    tmp_path: Path,
) -> None:
    script = DEPLOY_DIR / "research-vps-tasks.sh"
    text = script.read_text(
        encoding="utf-8"
    )
    assert "VPS_RUNTIME_ROLE=recommendation_only" in text
    assert re.search(r"(?m)^exit 64$", text)
    bash = _bash_executable()
    if bash is None:
        pytest.skip("bash is unavailable")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "COMSPEC",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "WINDIR",
        }
    }
    environment.update({"HOME": str(tmp_path), "PATH": ""})
    before = tuple(tmp_path.rglob("*"))
    completed = subprocess.run(
        [bash, str(script), "ignored"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 64
    assert completed.stdout == b""
    assert (
        "已停用：VPS 仅负责推荐推理与分发"
        .encode("utf-8") in completed.stderr
    )
    assert tuple(tmp_path.rglob("*")) == before
