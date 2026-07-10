import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "deploy" / "check-production-health.sh"
DEPLOY = SCRIPT.parent


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
    return subprocess.run(["bash", str(SCRIPT)], text=True, capture_output=True, env=env, check=False)


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
    assert "ExecStart=/home/ubuntu/quant-signal/deploy/check-production-health.sh" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "Unit=quant-signal-health.service" in timer
