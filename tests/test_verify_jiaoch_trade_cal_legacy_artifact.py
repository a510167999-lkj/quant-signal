from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_jiaoch_trade_cal_legacy_artifact.py"
SYNTHETIC_PUBLICATION_CAPABILITY = "00000000-0000-4000-8000-000000000001"


def test_legacy_artifact_probe_anchors_repo_verifier_ahead_of_attacker_pythonpath(
    tmp_path: Path,
) -> None:
    attacker_root = tmp_path / "attacker"
    attacker_app = attacker_root / "app"
    attacker_app.mkdir(parents=True)
    (attacker_app / "__init__.py").write_text("", encoding="utf-8")
    (attacker_app / "jiaoch_trade_cal_authority.py").write_text(
        "def verify_jiaoch_trade_cal_authority(**_kwargs):\n"
        "    return {'forged_success': True}\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(attacker_root), str(REPO_ROOT)))
    environment["JIAOCH_TRADE_CAL_TEST_CAPABILITY"] = SYNTHETIC_PUBLICATION_CAPABILITY

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
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

    assert completed.returncode != 0
    assert "forged_success" not in completed.stdout
    assert "Jiaoch trade calendar authority manifest" in completed.stderr
