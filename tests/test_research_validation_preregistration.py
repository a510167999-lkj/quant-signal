import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import jobs
from app.research_validation import claim_registered_experiment, read_experiment_ledger


_TEMPORAL_CONTRACT_PATH = Path("data/research_partitions/frozen-v1.json")
_TEMPORAL_CONTRACT_SHA256 = json.loads(
    _TEMPORAL_CONTRACT_PATH.read_text(encoding="utf-8")
)["contract_sha256"]
_REAL_LOAD_QUALIFIED_TRADES_PAYLOAD = jobs._load_qualified_trades_payload


class _SyntheticAuditedUniverse:
    artifact_root_sha256 = "e" * 64
    coverage_audit_sha256 = "d" * 64
    temporal_contract_sha256 = _TEMPORAL_CONTRACT_SHA256
    temporal_role = "development"
    manifest = {"coverage": {"start_date": "2016-01-01", "end_date": "2023-12-31"}}

    def item_as_of(self, symbol, signal_date):
        return {"symbol": symbol, "signal_date": signal_date}

    def close(self):
        return None


def _trade(signal_date: date, return_pct: float = 2.0) -> dict:
    return {
        "symbol": "600001",
        "signal_date": signal_date.isoformat(),
        "entry_date": (signal_date + timedelta(days=1)).isoformat(),
        "exit_date": (signal_date + timedelta(days=2)).isoformat(),
        "return_pct": return_pct,
        "max_adverse_pct": min(return_pct, -1.0),
        "rank_score": 5,
        "market_level": "favorable",
        "signal_tags": ["frozen_signal"],
    }


def _write_input_plan(path: Path, qualified: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "research-validation-input-plan/v1",
                "qualified_trades_basename": qualified.name,
                "authority_kind": "single_audited_artifact",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_qualified(path: Path) -> None:
    trades = [
        _trade(date(2020, 1, 1) + timedelta(days=offset), 3.0 if offset % 5 else -1.0)
        for offset in range(70)
    ]
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "artifact_root_sha256": "e" * 64,
                    "coverage_audit_sha256": "d" * 64,
                    "research_data_contract": {
                        "artifact_root_sha256": "e" * 64,
                        "coverage_audit_sha256": "d" * 64,
                    },
                },
                "qualified_trades": trades,
            }
        ),
        encoding="utf-8",
    )


def _args(
    *,
    qualified: Path,
    plan: Path,
    ledger: Path,
    artifacts: Path,
    experiment_id: str,
    register_only: bool = False,
    registered_record_hash: str | None = None,
    hold_days: int = 5,
) -> list[str]:
    args = [
        "research-validate-file",
        "--qualified-trades-path",
        str(qualified),
        "--audited-pit-universe-path",
        "synthetic-audited-artifact",
        "--experiment-id",
        experiment_id,
        "--hypothesis",
        "The frozen signal survives purged validation",
        "--expected-mechanism",
        "Trend continuation",
        "--falsification-criterion",
        "Primary validation gates fail",
        "--exit-criterion",
        "Reject when any primary gate fails",
        "--final-oos-start",
        "2026-07-13",
        "--start-date",
        "2020-01-01",
        "--end-date",
        "2023-12-31",
        "--temporal-contract-path",
        str(_TEMPORAL_CONTRACT_PATH),
        "--expected-coverage-audit-sha256",
        "d" * 64,
        "--expected-artifact-root-sha256",
        "e" * 64,
        "--expected-temporal-contract-sha256",
        _TEMPORAL_CONTRACT_SHA256,
        "--expected-temporal-role",
        "development",
        "--train-days",
        "21",
        "--validation-days",
        "7",
        "--step-days",
        "7",
        "--embargo-days",
        "2",
        "--minimum-oos-trades",
        "200",
        "--hold-days",
        str(hold_days),
        "--required-signal-tags",
        "frozen_signal",
        "--market-levels",
        "favorable",
        "--ledger-path",
        str(ledger),
        "--artifact-dir",
        str(artifacts),
        "--input-plan-path",
        str(plan),
    ]
    if register_only:
        args.append("--register-only")
    if registered_record_hash is not None:
        args.extend(["--registered-record-hash", registered_record_hash])
    return args


def _forbid_authority_and_payload_io(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs.AuditedPointInTimeUniverse,
        "from_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("authority I/O")),
    )
    monkeypatch.setattr(
        jobs,
        "_load_qualified_trades_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("payload I/O")),
    )


def _allow_validation_io(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "_load_qualified_trades_payload",
        _REAL_LOAD_QUALIFIED_TRADES_PAYLOAD,
    )
    monkeypatch.setattr(
        jobs.AuditedPointInTimeUniverse,
        "from_file",
        lambda *args, **kwargs: _SyntheticAuditedUniverse(),
    )
    monkeypatch.setattr(
        jobs,
        "validate_point_in_time_contract",
        lambda summary, rows, **kwargs: {
            **summary["research_data_contract"],
            "verified_authority": {
                "artifact_root_sha256": "e" * 64,
                "coverage_audit_sha256": "d" * 64,
            },
        },
    )


def _register(tmp_path, capsys, monkeypatch, experiment_id: str):
    qualified = tmp_path / "future-qualified.json"
    plan = tmp_path / "input-plan.json"
    ledger = tmp_path / "ledger.jsonl"
    artifacts = tmp_path / "artifacts"
    _write_input_plan(plan, qualified)
    _forbid_authority_and_payload_io(monkeypatch)

    assert jobs.main(
        _args(
            qualified=qualified,
            plan=plan,
            ledger=ledger,
            artifacts=artifacts,
            experiment_id=experiment_id,
            register_only=True,
        )
    ) == 0
    output = json.loads(capsys.readouterr().out)
    return qualified, plan, ledger, artifacts, output


def test_register_only_hashes_plan_without_qualified_or_authority_io(
    tmp_path, capsys, monkeypatch
):
    qualified, plan, ledger, _artifacts, output = _register(
        tmp_path, capsys, monkeypatch, "preregister-only"
    )

    assert qualified.exists() is False
    assert output["event_type"] == "registered"
    assert output["record_hash"]
    events = read_experiment_ledger(str(ledger))
    assert [event["event_type"] for event in events] == ["registered"]
    assert events[0]["record_hash"] == output["record_hash"]
    assert events[0]["input_plan_artifact"]["basename"] == plan.name
    assert len(events[0]["input_plan_artifact"]["sha256"]) == 64


def test_registered_hash_is_claimed_once_without_second_registration(
    tmp_path, capsys, monkeypatch
):
    qualified, plan, ledger, artifacts, registered = _register(
        tmp_path, capsys, monkeypatch, "preregister-run"
    )
    _write_qualified(qualified)
    _allow_validation_io(monkeypatch)

    assert jobs.main(
        _args(
            qualified=qualified,
            plan=plan,
            ledger=ledger,
            artifacts=artifacts,
            experiment_id="preregister-run",
            registered_record_hash=registered["record_hash"],
        )
    ) == 0
    capsys.readouterr()

    events = read_experiment_ledger(str(ledger))
    assert [event["event_type"] for event in events] == [
        "registered",
        "validation_started",
        "completed",
    ]
    assert sum(event["event_type"] == "registered" for event in events) == 1
    assert events[1]["registered_record_hash"] == registered["record_hash"]


@pytest.mark.parametrize("mutation", ["hold_days", "input_plan_bytes"])
def test_registered_claim_rejects_changed_inputs_before_authority_io_and_fails(
    tmp_path, capsys, monkeypatch, mutation
):
    qualified, plan, ledger, artifacts, registered = _register(
        tmp_path, capsys, monkeypatch, f"preregister-mismatch-{mutation}"
    )
    if mutation == "input_plan_bytes":
        plan.write_bytes(plan.read_bytes() + b"\n")
    _forbid_authority_and_payload_io(monkeypatch)

    with pytest.raises(ValueError, match="registered|plan|parameter|fingerprint"):
        jobs.main(
            _args(
                qualified=qualified,
                plan=plan,
                ledger=ledger,
                artifacts=artifacts,
                experiment_id=f"preregister-mismatch-{mutation}",
                registered_record_hash=registered["record_hash"],
                hold_days=6 if mutation == "hold_days" else 5,
            )
        )

    events = read_experiment_ledger(str(ledger))
    assert [event["event_type"] for event in events] == ["registered", "failed"]
    assert events[-1]["error_code"] == "VALIDATION_INPUT_REJECTED"


def test_registered_record_hash_cannot_be_claimed_twice(
    tmp_path, capsys, monkeypatch
):
    qualified, plan, ledger, artifacts, registered = _register(
        tmp_path, capsys, monkeypatch, "preregister-single-claim"
    )
    _write_qualified(qualified)
    _allow_validation_io(monkeypatch)
    run_args = _args(
        qualified=qualified,
        plan=plan,
        ledger=ledger,
        artifacts=artifacts,
        experiment_id="preregister-single-claim",
        registered_record_hash=registered["record_hash"],
    )
    assert jobs.main(run_args) == 0
    capsys.readouterr()

    with pytest.raises(ValueError, match="already.*(claimed|completed)|claim"):
        jobs.main(run_args)

    assert [event["event_type"] for event in read_experiment_ledger(str(ledger))] == [
        "registered",
        "validation_started",
        "completed",
    ]


def test_registered_record_hash_has_exactly_one_concurrent_claim(
    tmp_path, capsys, monkeypatch
):
    _qualified, _plan, ledger, _artifacts, registered = _register(
        tmp_path, capsys, monkeypatch, "preregister-concurrent-claim"
    )
    barrier = threading.Barrier(2)

    def claim_once():
        barrier.wait()
        return claim_registered_experiment(
            str(ledger),
            experiment_id="preregister-concurrent-claim",
            registered_record_hash=registered["record_hash"],
            expected_registration_contract=registered["registration_contract"],
            expected_input_plan_artifact=registered["input_plan_artifact"],
        )

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim_once) for _ in range(2)]
        for future in futures:
            try:
                outcomes.append(future.result())
            except ValueError as exc:
                outcomes.append(exc)

    assert sum(isinstance(item, dict) for item in outcomes) == 1
    failures = [item for item in outcomes if isinstance(item, ValueError)]
    assert len(failures) == 1
    assert "already claimed" in str(failures[0])
    events = read_experiment_ledger(str(ledger))
    assert [event["event_type"] for event in events] == [
        "registered",
        "validation_started",
    ]
    assert events[1]["registered_record_hash"] == registered["record_hash"]
