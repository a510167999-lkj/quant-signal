from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import build_factor_v3_daily_basic_formal_run_spec as formal


def _sessions(count: int = 733) -> list[str]:
    start = date(2023, 6, 26)
    sessions = [(start + timedelta(days=index)).isoformat() for index in range(count - 1)]
    sessions.append("2026-07-03")
    return sessions


def _candidate(*, sessions: list[str] | None = None) -> dict[str, object]:
    trade_dates = _sessions() if sessions is None else sessions
    return {
        "schema": "factor-v3-daily-basic-run-spec/v2",
        "run_spec_sha256": "a" * 64,
        "source_authority_root_sha256": "b" * 64,
        "session_count": len(trade_dates),
        "sessions": trade_dates,
        "sessions_sha256": "c" * 64,
        "collection_policy_descriptor": {
            "credential_proof_claimed": False,
            "document": {
                "routes": [
                    {
                        "api_name": "daily_basic",
                        "credential_slot_id": "points-primary",
                    }
                ]
            },
        },
        "collector": {
            "timeout_seconds": 30.0,
            "max_attempts": 3,
            "workers": 1,
        },
        "exact_set_authority_inputs": dict(formal.EXACT_SET_AUTHORITY_INPUTS),
    }


class _FakeRunner:
    def __init__(self, candidate: dict[str, object]) -> None:
        self.candidate = candidate
        self.build_calls: list[dict[str, object]] = []
        self.load_calls: list[Path] = []

    def build_factor_v3_daily_basic_run_spec(self, **kwargs: object) -> dict[str, object]:
        self.build_calls.append(kwargs)
        return json.loads(json.dumps(self.candidate))

    def load_factor_v3_daily_basic_run_spec(self, path: str | Path) -> dict[str, object]:
        candidate_path = Path(path)
        self.load_calls.append(candidate_path)
        assert not candidate_path.read_bytes().endswith(b"\n")
        return json.loads(candidate_path.read_text(encoding="utf-8"))


def test_formal_paths_and_authority_inputs_are_frozen_exactly() -> None:
    main = Path(r"E:\AI workspace\quant-signal-lkj")
    assert formal.SPEC_OUTPUT_ROOT == (
        main
        / "data/research_runs/audited_pit_factor_v3_daily_basic_run_spec_v2"
        / "run_specs/sha256"
    )
    assert formal.PLANNED_RUN_ROOT == (
        main
        / "data/research_runs"
        / "audited_pit_factor_v3_daily_basic_collection_v2_development_733"
    )
    assert formal.EXACT_SET_AUTHORITY_INPUTS == {
        "feature_history_frozen_source_attestation_path": str(
            main
            / "data/research_artifacts"
            / "factor_v3_feature_history_frozen_source_attestation_v3"
            / "factor_v3_feature_history_frozen_source_attestations"
            / "sha256/68"
            / "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675.json"
        ),
        "expected_feature_history_frozen_source_attestation_sha256": (
            "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675"
        ),
        "feature_history_frozen_source_root": (
            r"E:\AI workspace\quant-signal-lkj-factor-v3-feature-history-formal-run"
        ),
        "expected_feature_history_frozen_source_commit": (
            "b8057962f7a9754848994a6cfda9c9bf85e3db89"
        ),
        "feature_history_run_spec_path": str(
            main
            / "data/research_runs/audited_pit_factor_v3_feature_history_run_spec_v1"
            / "run_specs/sha256/1d"
            / "1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3.json"
        ),
        "feature_history_run_root": str(
            main
            / "data/research_runs"
            / "audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
        ),
        "audited_development_universe_sqlite_path": str(
            main
            / "data/research_artifacts/audited_pit_universe_v2"
            / "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4"
            / "metadata.sqlite3"
        ),
        "expected_development_coverage_audit_sha256": (
            "eb999a28591f43cca2111bd609ff71d77eaae2ad0b3471bb2f8da5c1e6b6ceed"
        ),
        "expected_development_artifact_root_sha256": (
            "505400a945973df54b943e22d195ddc3c93734eecbc6e464bb39005049b92380"
        ),
        "expected_development_temporal_contract_sha256": (
            "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934"
        ),
        "expected_development_temporal_role": "development_4",
        "security_code_transition_evidence_root": str(
            main
            / "data/research_artifacts/security_code_transition_evidence_v1"
        ),
        "expected_security_code_transition_contract_sha256": (
            "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
        ),
    }


def test_offline_candidate_uses_exact_inputs_builds_twice_and_loads_once() -> None:
    fake = _FakeRunner(_candidate())

    candidate, content = formal.build_and_verify_candidate(fake)

    assert candidate["session_count"] == 733
    assert candidate["sessions"][0] == "2023-06-26"
    assert candidate["sessions"][-1] == "2026-07-03"
    assert len(fake.build_calls) == 2
    assert fake.build_calls == [
        {
            "exact_set_authority_inputs": dict(formal.EXACT_SET_AUTHORITY_INPUTS),
            "timeout_seconds": 30,
            "max_attempts": 3,
        },
        {
            "exact_set_authority_inputs": dict(formal.EXACT_SET_AUTHORITY_INPUTS),
            "timeout_seconds": 30,
            "max_attempts": 3,
        },
    ]
    assert len(fake.load_calls) == 1
    assert content == json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert not content.endswith(b"\n")


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(session_count=732), "733"),
        (lambda value: value["sessions"].pop(), "733"),
        (lambda value: value["sessions"].__setitem__(0, "2023-06-25"), "2023-06-26"),
        (lambda value: value["sessions"].__setitem__(-1, "2026-07-02"), "2026-07-03"),
        (lambda value: value["collector"].update(workers=2), "collector"),
        (lambda value: value.update(token="forbidden"), "credential"),
        (lambda value: value.update(publication_capability="forbidden"), "credential"),
    ],
)
def test_offline_candidate_rejects_nonformal_shape(
    mutation: object, match: str
) -> None:
    candidate = _candidate()
    mutation(candidate)

    with pytest.raises(formal.FormalRunSpecError, match=match):
        formal.build_and_verify_candidate(_FakeRunner(candidate))


def test_content_addressed_publish_has_no_trailing_lf_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(formal, "SPEC_OUTPUT_ROOT", tmp_path / "specs" / "sha256")
    content = b'{"safe":true}'
    expected_sha256 = hashlib.sha256(content).hexdigest()

    target = formal.publish_candidate(content)

    assert target == (
        tmp_path / "specs" / "sha256" / expected_sha256[:2] / f"{expected_sha256}.json"
    )
    assert target.read_bytes() == content
    assert formal.publish_candidate(content) == target

    target.write_bytes(b'{"safe":false}')
    with pytest.raises(formal.FormalRunSpecError, match="different bytes"):
        formal.publish_candidate(content)


def test_planned_run_root_must_be_absent_or_strictly_empty_without_sidecars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_root = tmp_path / "formal-run"
    monkeypatch.setattr(formal, "PLANNED_RUN_ROOT", run_root)

    formal.verify_planned_run_root()
    run_root.mkdir()
    formal.verify_planned_run_root()

    (run_root / "state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(formal.FormalRunSpecError, match="not empty"):
        formal.verify_planned_run_root()
    (run_root / "state.json").unlink()

    sidecar = run_root.with_name(f"{run_root.name}.stdout.log")
    sidecar.write_text("", encoding="utf-8")
    with pytest.raises(formal.FormalRunSpecError, match="sidecar"):
        formal.verify_planned_run_root()


def test_formal_worktree_requires_exact_branch_and_clean_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_commit = "d" * 40
    responses = {
        ("rev-parse", "--show-toplevel"): str(formal.FORMAL_WORKTREE_ROOT),
        ("rev-parse", "HEAD"): reviewed_commit,
        ("branch", "--show-current"): formal.EXPECTED_BRANCH,
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
    }
    monkeypatch.setattr(formal, "_git_output", lambda *args: responses[args])
    monkeypatch.setattr(
        formal,
        "_script_worktree_root",
        lambda: formal.FORMAL_WORKTREE_ROOT,
    )
    formal.verify_formal_worktree(
        expected_reviewed_commit=reviewed_commit,
    )

    with pytest.raises(formal.FormalRunSpecError, match="commit"):
        formal.verify_formal_worktree(
            expected_reviewed_commit="e" * 40,
        )

    responses[("status", "--porcelain=v1", "--untracked-files=all")] = " M unsafe.py"
    with pytest.raises(formal.FormalRunSpecError, match="dirty"):
        formal.verify_formal_worktree(
            expected_reviewed_commit=reviewed_commit,
        )


def test_isolated_cli_shim_delegates_verify_without_import_path_leak(
    tmp_path: Path,
) -> None:
    shim = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_factor_v3_daily_basic_formal.py"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(shim),
            "verify",
            "--run-spec",
            str(tmp_path / "absent-spec.json"),
            "--run-root",
            str(tmp_path / "absent-run"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
    )

    assert completed.returncode == 2
    assert "No module named" not in completed.stderr
    assert completed.stderr in {
        "formal bootstrap requires fixed Python -I -B\n",
        "formal bootstrap claim unavailable; external trusted bootstrap context required\n",
    }
    assert completed.stdout == ""


def test_safe_summary_excludes_inputs_credentials_and_capabilities() -> None:
    summary = formal.safe_summary(
        _candidate(),
        b'{"safe":true}',
        published=False,
    )
    encoded = json.dumps(summary, sort_keys=True).lower()

    assert summary["status"] == "preflight-verified"
    assert summary["session_count"] == 733
    assert "token" not in encoded
    assert "credential" not in encoded
    assert "capability" not in encoded
    assert "exact_set_authority_inputs" not in encoded
