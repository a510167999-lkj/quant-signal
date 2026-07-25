import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "deploy" / "check-production-health.sh"
DEPLOY = SCRIPT.parent


def _require_regular_file(path, label):
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"{label} not found: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"{label} is not a regular file: {path}")


def _validated_posix_bash(candidate):
    _require_regular_file(candidate, "trusted POSIX Bash")
    resolved = candidate.resolve(strict=True)
    try:
        probe = subprocess.run(
            [str(resolved), "--version"], capture_output=True, check=False
        )
    except OSError as exc:
        raise RuntimeError("trusted POSIX Bash version probe failed") from exc
    if probe.returncode != 0:
        raise RuntimeError("trusted POSIX Bash version probe failed")
    if not isinstance(probe.stdout, bytes) or not isinstance(probe.stderr, bytes):
        raise RuntimeError("trusted POSIX Bash version output is not bytes")
    try:
        stdout = probe.stdout.decode("utf-8", errors="strict")
        probe.stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            "trusted POSIX Bash version output is not valid UTF-8"
        ) from exc
    if "GNU bash" not in stdout:
        raise RuntimeError("trusted POSIX Bash version output is not GNU Bash")
    return str(resolved)


def _posix_bash():
    if platform.system() != "Windows":
        candidate = shutil.which("bash")
        if not candidate:
            raise RuntimeError("trusted POSIX Bash executable not found")
        return _validated_posix_bash(Path(candidate))

    git = shutil.which("git")
    if not git:
        raise RuntimeError("trusted Git executable not found")
    git_path = Path(git)
    _require_regular_file(git_path, "trusted Git executable")
    git_path = git_path.resolve(strict=True)
    if git_path.parent.name.casefold() not in {"cmd", "bin"}:
        raise RuntimeError("trusted Git executable has an unrecognized layout")
    git_root = git_path.parent.parent.resolve(strict=True)
    # Git for Windows 2.x 把 git 放在 <install>/mingw64/bin/git.exe,bash 在
    # <install>/usr/bin/bash.exe(比 mingw64 高一级)。git_root 这里是 mingw64,
    # candidates 找不到时回退到 git_root.parent 覆盖 2.x 布局。
    bash_candidates = (
        git_root / "bin" / "bash.exe",
        git_root / "usr" / "bin" / "bash.exe",
        git_root.parent / "usr" / "bin" / "bash.exe",
    )
    trusted_roots = (git_root, git_root.parent)
    for candidate in bash_candidates:
        if not candidate.exists():
            continue
        _require_regular_file(candidate, "trusted Git Bash")
        resolved = candidate.resolve(strict=True)
        if not any(_path_within(resolved, root) for root in trusted_roots):
            raise RuntimeError("trusted Git Bash escapes the Git installation root")
        return _validated_posix_bash(resolved)
    raise RuntimeError("trusted Git Bash executable not found")


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def run_checker(tmp_path, disabled=False, app_exit=0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        "#!/bin/bash\n"
        + ("[[ \"$*\" == *quant-signal-monitor.timer* ]] && exit 1\n" if disabled else "")
        + "[[ \"$1\" == is-failed ]] && exit 1\nexit 0\n",
        encoding="utf-8",
    )
    python = tmp_path / "python"
    python.write_text(f"#!/bin/bash\necho '{{\"status\":\"test\"}}'\nexit {app_exit}\n", encoding="utf-8")
    systemctl.chmod(0o755)
    python.chmod(0o755)
    env = os.environ | {"PROJECT_DIR": str(tmp_path), "SYSTEMCTL_BIN": str(systemctl), "PYTHON_BIN": str(python)}
    return subprocess.run(
        [_posix_bash(), str(SCRIPT)],
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        env=env,
        check=False,
    )


def _fake_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test executable")
    return path


def _gnu_bash_probe(argv, **kwargs):
    assert argv[1:] == ["--version"]
    assert kwargs == {"capture_output": True, "check": False}
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout=b"GNU bash, version 5.2.37(1)-release (x86_64-pc-msys)\n",
        stderr=b"",
    )


def test_posix_bash_windows_derives_bin_bash_from_git_without_querying_path_bash(
    tmp_path, monkeypatch
):
    git = _fake_executable(tmp_path / "Git" / "cmd" / "git.exe")
    bash = _fake_executable(tmp_path / "Git" / "bin" / "bash.exe")
    queried = []

    def fake_which(name):
        queried.append(name)
        assert name == "git"
        return str(git)

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", _gnu_bash_probe)

    assert _posix_bash() == str(bash.resolve())
    assert queried == ["git"]


def test_posix_bash_windows_accepts_usr_bin_when_bin_is_absent(tmp_path, monkeypatch):
    git = _fake_executable(tmp_path / "Git" / "cmd" / "git.exe")
    bash = _fake_executable(tmp_path / "Git" / "usr" / "bin" / "bash.exe")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else None)
    monkeypatch.setattr(subprocess, "run", _gnu_bash_probe)

    assert _posix_bash() == str(bash.resolve())


def test_posix_bash_windows_fails_when_git_is_missing(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="trusted Git executable not found"):
        _posix_bash()


def test_posix_bash_windows_rejects_non_regular_git(tmp_path, monkeypatch):
    git = tmp_path / "Git" / "cmd" / "git.exe"
    git.mkdir(parents=True)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else None)

    with pytest.raises(RuntimeError, match="trusted Git executable is not a regular file"):
        _posix_bash()


def test_posix_bash_windows_fails_when_no_git_root_bash_exists(tmp_path, monkeypatch):
    git = _fake_executable(tmp_path / "Git" / "cmd" / "git.exe")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else None)

    with pytest.raises(RuntimeError, match="trusted Git Bash executable not found"):
        _posix_bash()


def test_posix_bash_windows_rejects_non_regular_candidate(tmp_path, monkeypatch):
    git = _fake_executable(tmp_path / "Git" / "cmd" / "git.exe")
    (tmp_path / "Git" / "bin" / "bash.exe").mkdir(parents=True)
    _fake_executable(tmp_path / "Git" / "usr" / "bin" / "bash.exe")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else None)

    with pytest.raises(RuntimeError, match="trusted Git Bash is not a regular file"):
        _posix_bash()


def test_posix_bash_rejects_failed_version_probe(tmp_path, monkeypatch):
    git = _fake_executable(tmp_path / "Git" / "cmd" / "git.exe")
    _fake_executable(tmp_path / "Git" / "bin" / "bash.exe")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2, stdout=b"", stderr=b"failed"),
    )

    with pytest.raises(RuntimeError, match="trusted POSIX Bash version probe failed"):
        _posix_bash()


def test_posix_bash_rejects_non_utf8_version_output(tmp_path, monkeypatch):
    git = _fake_executable(tmp_path / "Git" / "cmd" / "git.exe")
    _fake_executable(tmp_path / "Git" / "bin" / "bash.exe")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout=b"\xff", stderr=b""),
    )

    with pytest.raises(RuntimeError, match="version output is not valid UTF-8"):
        _posix_bash()


def test_posix_bash_rejects_non_gnu_bash_banner(tmp_path, monkeypatch):
    git = _fake_executable(tmp_path / "Git" / "cmd" / "git.exe")
    _fake_executable(tmp_path / "Git" / "bin" / "bash.exe")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: str(git) if name == "git" else None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, stdout=b"not GNU shell\n", stderr=b""
        ),
    )

    with pytest.raises(RuntimeError, match="version output is not GNU Bash"):
        _posix_bash()


def test_posix_bash_non_windows_uses_which_bash(tmp_path, monkeypatch):
    bash = _fake_executable(tmp_path / "bin" / "bash")
    queried = []

    def fake_which(name):
        queried.append(name)
        return str(bash) if name == "bash" else None

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", _gnu_bash_probe)

    assert _posix_bash() == str(bash.resolve())
    assert queried == ["bash"]


def test_run_checker_uses_resolved_bash_and_strict_utf8(tmp_path, monkeypatch):
    resolved = str(tmp_path / "trusted" / "bash.exe")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setitem(run_checker.__globals__, "_posix_bash", lambda: resolved)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert run_checker(tmp_path / "fixture").returncode == 0
    assert captured["argv"] == [resolved, str(SCRIPT)]
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "strict"


def test_checker_passes_when_systemd_and_application_are_healthy(tmp_path):
    assert run_checker(tmp_path).returncode == 0


def test_checker_fails_for_disabled_timer_or_application(tmp_path):
    disabled = run_checker(tmp_path / "disabled", disabled=True)
    assert disabled.returncode == 2
    assert "quant-signal-monitor.timer" in disabled.stdout
    assert run_checker(tmp_path / "app", app_exit=1).returncode == 1


def test_health_systemd_units_have_expected_contract():
    service = (DEPLOY / "quant-signal-health.service").read_text(encoding="utf-8")
    timer = (DEPLOY / "quant-signal-health.timer").read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "SuccessExitStatus=1" in service
    assert "User=ubuntu" in service
    assert "Group=ubuntu" in service
    assert "WorkingDirectory=/home/ubuntu/quant-signal" in service
    assert "EnvironmentFile=/home/ubuntu/quant-signal/.env" in service
    assert (
        "ExecStart=/usr/bin/env VPS_RUNTIME_ROLE=recommendation_only "
        "/home/ubuntu/quant-signal/deploy/check-production-health.sh"
        in service
    )
    assert "OnUnitActiveSec=5min" in timer
    assert "Unit=quant-signal-health.service" in timer


def test_all_calendar_timers_pin_the_china_timezone():
    for name in (
        "quant-signal-recommend.timer",
        "quant-signal-monitor.timer",
        "quant-signal-planned-exits.timer",
        "quant-signal-cache-warm.timer",
    ):
        timer = (DEPLOY / name).read_text(encoding="utf-8")
        assert "Timezone=Asia/Shanghai" not in timer
        calendar_lines = [line for line in timer.splitlines() if line.startswith("OnCalendar=")]
        assert calendar_lines
        assert all(line.endswith("Asia/Shanghai") for line in calendar_lines)


def test_checker_covers_health_timer_and_oneshot_failure_states():
    script = SCRIPT.read_text(encoding="utf-8")
    for unit in (
        "quant-signal-health.timer",
        "quant-signal-recommend.service",
        "quant-signal-monitor.service",
        "quant-signal-planned-exits.service",
        "quant-signal-cache-warm.service",
    ):
        assert unit in script
