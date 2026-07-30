from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_jiaoch_trade_cal_legacy_artifact.py"
SYNTHETIC_PUBLICATION_CAPABILITY = "00000000-0000-4000-8000-000000000001"


def _attacker_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    attacker_root = tmp_path / "attacker"
    attacker_app = attacker_root / "app"
    attacker_app.mkdir(parents=True)
    (attacker_app / "__init__.py").write_text("", encoding="utf-8")
    (attacker_app / "jiaoch_trade_cal_authority.py").write_text(
        "def verify_jiaoch_trade_cal_authority(**_kwargs):\n"
        "    return {'forged_success': True}\n",
        encoding="utf-8",
    )
    marker = tmp_path / "sitecustomize-loaded"
    (attacker_root / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['JIAOCH_TRADE_CAL_TEST_SITE_MARKER']).write_text(\n"
        "    'loaded', encoding='utf-8'\n"
        ")\n"
        "import app.jiaoch_trade_cal_authority\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(attacker_root), str(REPO_ROOT)))
    environment["JIAOCH_TRADE_CAL_TEST_CAPABILITY"] = SYNTHETIC_PUBLICATION_CAPABILITY
    environment["JIAOCH_TRADE_CAL_TEST_SITE_MARKER"] = str(marker)
    return environment, marker


def _run_probe(
    tmp_path: Path,
    environment: dict[str, str],
    *python_flags: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            *python_flags,
            str(SCRIPT),
            "--artifact-root",
            str(tmp_path),
            "--publication-capability-env",
            "JIAOCH_TRADE_CAL_TEST_CAPABILITY",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_legacy_artifact_probe_rejects_nonisolated_sitecustomize_preload(
    tmp_path: Path,
) -> None:
    environment, marker = _attacker_environment(tmp_path)

    completed = _run_probe(tmp_path, environment, "-B")

    assert marker.read_text(encoding="utf-8") == "loaded"
    assert completed.returncode != 0
    assert "requires Python -I" in completed.stderr
    assert "forged_success" not in completed.stdout


def test_legacy_artifact_probe_ignores_attacker_pythonpath_and_sitecustomize(
    tmp_path: Path,
) -> None:
    environment, marker = _attacker_environment(tmp_path)

    completed = _run_probe(tmp_path, environment, "-I", "-B")

    assert not marker.exists()
    assert completed.returncode != 0
    assert "forged_success" not in completed.stdout
    assert "Jiaoch trade calendar authority manifest" in completed.stderr
