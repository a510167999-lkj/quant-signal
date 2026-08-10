from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_factor_v3_activation_development_dry_run.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "factor_v3_activation_development_dry_run",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_activation_dry_run_refuses_without_local_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(SystemExit):
        module.run_dry_run(work_root=tmp_path / "work", repo_root=tmp_path)


def test_activation_dry_run_ok_under_local_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    evidence = module.run_dry_run(work_root=tmp_path / "work", repo_root=tmp_path)
    assert evidence["ok"] is True
    assert evidence["stage_goal_id"] == module.STAGE_GOAL_ID
    assert evidence["development_only"] is True
    assert evidence["production_profile_registered"] is False
    assert evidence["automatic_trading_allowed"] is False
    assert evidence["steps"]["disposable_publish_and_verify"]["ok"] is True
    assert (
        evidence["steps"]["disposable_publish_and_verify"][
            "formal_materialization_eligible"
        ]
        is False
    )
    assert evidence["steps"]["formal_entrypoint_rejects_disposable"]["ok"] is True
