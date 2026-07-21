import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import jobs
from app.research_validation import read_experiment_ledger


_TEMPORAL_CONTRACT_PATH = Path("data/research_partitions/frozen-v1.json")
_TEMPORAL_CONTRACT_SHA256 = json.loads(
    _TEMPORAL_CONTRACT_PATH.read_text(encoding="utf-8")
)["contract_sha256"]
_TEST_SETTINGS = jobs.get_settings()
_EXPECTED_GENERATION_SETTINGS_FIELDS = (
    "scan_min_amount",
    "scan_min_price",
    "scan_max_price",
    "max_entry_gap_up_pct",
    "max_entry_gap_down_pct",
    "locked_limit_gap_pct",
    "max_entry_intraday_range_pct",
    "min_backtest_trades",
    "min_backtest_win_rate",
    "min_backtest_avg_return",
    "max_backtest_avg_adverse",
)


def _canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_controlled_completion_recording_error_seals_completed_as_invalid_decision(
    monkeypatch,
):
    args = SimpleNamespace(
        ledger_path="E:/workspace/ledger.jsonl",
        experiment_id="new-038",
        registered_record_hash="a" * 64,
    )
    validation_started = {"record_hash": "b" * 64}
    completed = {
        "event_type": "completed",
        "experiment_id": "new-038",
        "record_hash": "c" * 64,
        "registered_record_hash": "a" * 64,
        "validation_started_record_hash": "b" * 64,
    }
    calls = []
    monkeypatch.setattr(
        jobs,
        "read_experiment_ledger",
        lambda *_args, **_kwargs: [completed],
    )
    monkeypatch.setattr(
        jobs,
        "decide_completed_validation_if_current",
        lambda *_args, **kwargs: calls.append(kwargs)
        or {"event_type": "decision", "decision_code": "PURGED_RESULT_INVALID"},
    )

    terminal = jobs._seal_controlled_validation_completion_failure(
        args,
        validation_started,
        error_type="RuntimeError",
    )

    assert terminal["decision_code"] == "PURGED_RESULT_INVALID"
    assert calls[0]["completed_record_hash"] == completed["record_hash"]
    assert calls[0]["all_gates_pass"] is False


def _treatment_plan_payload(output: Path) -> dict:
    parameters = {
        "max_deep": 80,
        "top_n": 3,
        "hold_days": 3,
        "lookback_days": 620,
        "max_universe_symbols": 0,
        "live_snapshot": False,
        "buy_only": False,
        "min_score": None,
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "trailing_stop_pct": None,
        "symbol_cooldown_days": 5,
        "max_active_positions": 3,
        "announcement_context": False,
        "announcement_lookback_days": None,
        "require_announcement_event": None,
        "require_all_announcement_events": False,
        "exclude_announcement_event": None,
        "require_market_level": None,
        "require_signal_tag": None,
        "require_all_signal_tags": False,
        "exclude_signal_tag": None,
        "min_prior_win_rate": None,
        "min_prior_avg_return": None,
        "max_prior_avg_adverse": None,
        "margin_eligibility_context": False,
    }
    baseline_parameters = dict(parameters)
    baseline_parameters["hold_days"] = 5
    payload = {
        "schema_version": "research-treatment-input-plan/v2",
        "experiment_id": "h1-hold-days-3",
        "producer": "research-historical-universe",
        "producer_source_sha256": jobs.historical_treatment_producer_source_sha256(),
        "output_basename": output.name,
        "temporal_role": "development",
        "start_date": "2020-01-01",
        "end_date": "2023-12-31",
        "authority": {
            "kind": "single_audited_artifact",
            "artifact_root_sha256": "b" * 64,
            "coverage_audit_sha256": "a" * 64,
            "composite_root_sha256": None,
            "temporal_contract_sha256": _TEMPORAL_CONTRACT_SHA256,
        },
        "settings_fingerprint": jobs.research_generation_settings_fingerprint(
            _TEST_SETTINGS
        ),
        "treatment": {
            "parameter": "hold_days",
            "baseline": 5,
            "candidate": 3,
        },
        "baseline_parameters": baseline_parameters,
        "parameters": parameters,
    }
    payload["plan_sha256"] = _canonical_sha256(payload)
    return payload


def _write_treatment_plan(path: Path, output: Path) -> dict:
    payload = _treatment_plan_payload(output)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return payload


def _historical_args(output: Path, plan: Path | None = None) -> list[str]:
    args = [
        "research-historical-universe",
        "--start-date",
        "2020-01-01",
        "--end-date",
        "2023-12-31",
        "--hold-days",
        "3",
        "--top-n",
        "3",
        "--symbol-cooldown-days",
        "5",
        "--max-active-positions",
        "3",
        "--audited-pit-universe-path",
        "/frozen/audited.sqlite3",
        "--expected-coverage-audit-sha256",
        "a" * 64,
        "--expected-artifact-root-sha256",
        "b" * 64,
        "--temporal-contract-path",
        str(_TEMPORAL_CONTRACT_PATH),
        "--expected-temporal-contract-sha256",
        _TEMPORAL_CONTRACT_SHA256,
        "--expected-temporal-role",
        "development",
        "--qualified-trades-output",
        str(output),
    ]
    if plan is not None:
        args.extend(["--input-plan-path", str(plan)])
    return args


def _generated_payload() -> dict:
    return {
        "summary": {
            "hold_days": 3,
            "top_n": 3,
            "symbol_cooldown_days": 5,
            "max_active_positions": 3,
            "selected_trade_count": 1,
        },
        "qualified_trades": [
            {
                "symbol": "600001",
                "signal_date": "2023-01-03",
                "planned_holding_sessions": 3,
            }
        ],
    }


def test_historical_treatment_plan_binds_generated_summary(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    plan = _write_treatment_plan(plan_path, output)
    monkeypatch.setattr(jobs, "get_settings", lambda: _TEST_SETTINGS)

    def run_backtest(**kwargs):
        assert kwargs["settings"] is _TEST_SETTINGS
        return _generated_payload()

    monkeypatch.setattr(
        jobs, "run_historical_universe_research_backtest", run_backtest
    )

    assert jobs.main(_historical_args(output, plan_path)) == 0

    written = json.loads(output.read_text(encoding="utf-8"))
    binding = written["summary"]["treatment_input_plan"]
    assert binding["schema_version"] == "research-treatment-input-plan-binding/v2"
    assert binding["input_plan_sha256"] == plan["plan_sha256"]
    assert binding["experiment_id"] == plan["experiment_id"]
    assert binding["producer"] == "research-historical-universe"
    assert binding["settings_fingerprint"] == plan["settings_fingerprint"]
    assert json.loads(capsys.readouterr().out)["summary"]["treatment_input_plan"] == binding


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan["parameters"].__setitem__("hold_days", 5),
        lambda plan: plan.__setitem__("start_date", "2020-01-02"),
        lambda plan: plan.__setitem__("end_date", "2023-12-30"),
        lambda plan: plan["authority"].__setitem__(
            "artifact_root_sha256", "c" * 64
        ),
        lambda plan: plan.__setitem__("output_basename", "other.json"),
    ],
    ids=["hold-days", "start-date", "end-date", "authority", "output-basename"],
)
def test_historical_treatment_plan_mismatch_fails_before_backtest_io(
    tmp_path, monkeypatch, mutation
):
    output = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    plan = _treatment_plan_payload(output)
    mutation(plan)
    plan["plan_sha256"] = _canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(jobs, "get_settings", lambda: _TEST_SETTINGS)
    monkeypatch.setattr(
        jobs,
        "run_historical_universe_research_backtest",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("backtest/provider I/O")),
    )

    with pytest.raises(ValueError, match="plan|treatment|parameter|authority|output|date"):
        jobs.main(_historical_args(output, plan_path))

    assert output.exists() is False


def test_historical_treatment_plan_rejects_tampered_plan_bytes_before_backtest_io(
    tmp_path, monkeypatch
):
    output = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    plan = _write_treatment_plan(plan_path, output)
    plan["producer_source_sha256"] = "e" * 64
    # Deliberately retain the old self-hash: the bytes no longer match the plan claim.
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(jobs, "get_settings", lambda: _TEST_SETTINGS)
    monkeypatch.setattr(
        jobs,
        "run_historical_universe_research_backtest",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("backtest/provider I/O")),
    )

    with pytest.raises(ValueError, match="plan|hash|sha256"):
        jobs.main(_historical_args(output, plan_path))

    assert output.exists() is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan["treatment"].__setitem__("baseline", {"invalid": 5}),
        lambda plan: plan["baseline_parameters"].__setitem__("top_n", 4),
    ],
    ids=["non-scalar-baseline", "multiple-changed-parameters"],
)
def test_historical_treatment_plan_requires_scalar_single_change_baseline(
    tmp_path, monkeypatch, mutation
):
    output = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    plan = _treatment_plan_payload(output)
    mutation(plan)
    plan["plan_sha256"] = _canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(
        jobs,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("settings/provider I/O")),
    )

    with pytest.raises(ValueError, match="baseline|scalar|exactly one"):
        jobs.main(_historical_args(output, plan_path))

    assert output.exists() is False


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--industry-rotation-context"],
        ["--industry-rotation-max-boards", "41"],
        ["--dragon-tiger-context"],
    ],
    ids=["industry-context", "industry-board-count", "dragon-tiger-context"],
)
def test_historical_treatment_plan_rejects_ignored_context_flags_before_settings_io(
    tmp_path, monkeypatch, extra_args
):
    output = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    _write_treatment_plan(plan_path, output)
    monkeypatch.setattr(
        jobs,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("settings/provider I/O")),
    )

    with pytest.raises(ValueError, match="industry|dragon|ignored"):
        jobs.main([*_historical_args(output, plan_path), *extra_args])

    assert output.exists() is False


def test_historical_treatment_producer_bundle_changes_with_dependency_bytes(monkeypatch):
    original_reader = jobs._read_bounded_regular_file_snapshot
    manifest = jobs.historical_treatment_producer_source_manifest()
    assert manifest["schema_version"] == "research-historical-producer-source-bundle/v2"
    assert {
        "app/config.py",
        "app/artifact_outcome_evidence.py",
        "app/research_market_data.py",
    }.issubset(manifest["files"])
    baseline = jobs.historical_treatment_producer_source_sha256()

    def mutated_reader(path, *, max_bytes):
        raw = original_reader(path, max_bytes=max_bytes)
        return raw + b"\n# adversarial dependency mutation" if path.name == "signals.py" else raw

    monkeypatch.setattr(jobs, "_read_bounded_regular_file_snapshot", mutated_reader)

    assert jobs.historical_treatment_producer_source_sha256() != baseline


def test_research_generation_settings_fingerprint_has_versioned_exact_fields():
    fingerprint = jobs.research_generation_settings_fingerprint(_TEST_SETTINGS)

    assert fingerprint["schema_version"] == "research-generation-settings/v2"
    assert tuple(fingerprint["values"]) == _EXPECTED_GENERATION_SETTINGS_FIELDS
    assert fingerprint["settings_sha256"] == _canonical_sha256(
        {
            "schema_version": fingerprint["schema_version"],
            "values": fingerprint["values"],
        }
    )


@pytest.mark.parametrize(
    "field",
    _EXPECTED_GENERATION_SETTINGS_FIELDS,
)
def test_historical_treatment_plan_rejects_each_generation_settings_change_before_backtest(
    tmp_path, monkeypatch, field
):
    output = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    _write_treatment_plan(plan_path, output)
    expected_fields = set(_EXPECTED_GENERATION_SETTINGS_FIELDS)
    assert tuple(jobs._RESEARCH_GENERATION_SETTINGS_FIELDS) == (
        _EXPECTED_GENERATION_SETTINGS_FIELDS
    )
    mutated_values = {
        setting_field: getattr(_TEST_SETTINGS, setting_field)
        for setting_field in expected_fields
    }
    mutated_values[field] = float(mutated_values[field]) + 1.0
    mutated_settings = SimpleNamespace(**mutated_values)
    monkeypatch.setattr(jobs, "get_settings", lambda: mutated_settings)
    monkeypatch.setattr(
        jobs,
        "run_historical_universe_research_backtest",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("backtest/provider I/O")),
    )

    with pytest.raises(ValueError, match="generation settings mismatch"):
        jobs.main(_historical_args(output, plan_path))

    assert output.exists() is False


def test_historical_legacy_cli_output_is_unchanged_without_treatment_plan(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "legacy-qualified.json"
    expected = _generated_payload()
    monkeypatch.setattr(jobs, "get_settings", lambda: _TEST_SETTINGS)
    monkeypatch.setattr(
        jobs,
        "run_historical_universe_research_backtest",
        lambda **kwargs: _generated_payload(),
    )

    assert jobs.main(_historical_args(output)) == 0

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == expected
    assert "treatment_input_plan" not in written["summary"]
    assert "treatment_input_plan" not in json.loads(capsys.readouterr().out)["summary"]


def test_historical_treatment_plan_does_not_overwrite_different_existing_output(
    tmp_path, monkeypatch
):
    output = tmp_path / "h1-qualified.json"
    original = b'{"owner":"pre-existing"}\n'
    output.write_bytes(original)
    plan_path = tmp_path / "h1-plan.json"
    _write_treatment_plan(plan_path, output)
    monkeypatch.setattr(jobs, "get_settings", lambda: _TEST_SETTINGS)
    monkeypatch.setattr(
        jobs, "run_historical_universe_research_backtest", lambda **kwargs: _generated_payload()
    )

    with pytest.raises(ValueError, match="exist|overwrite|output"):
        jobs.main(_historical_args(output, plan_path))

    assert output.read_bytes() == original


def _validation_args(
    *, qualified: Path, plan: Path, ledger: Path, registered_record_hash: str | None = None
) -> list[str]:
    args = [
        "research-validate-file",
        "--qualified-trades-path",
        str(qualified),
        "--audited-pit-universe-path",
        "/frozen/audited.sqlite3",
        "--experiment-id",
        "h1-hold-days-3",
        "--hypothesis",
        "Three-session exits improve the frozen strategy",
        "--expected-mechanism",
        "Shorter exposure reduces reversal loss",
        "--falsification-criterion",
        "Primary validation gates fail",
        "--exit-criterion",
        "Reject H1 when any primary gate fails",
        "--final-oos-start",
        "2026-07-13",
        "--start-date",
        "2020-01-01",
        "--end-date",
        "2023-12-31",
        "--temporal-contract-path",
        str(_TEMPORAL_CONTRACT_PATH),
        "--expected-coverage-audit-sha256",
        "a" * 64,
        "--expected-artifact-root-sha256",
        "b" * 64,
        "--expected-temporal-contract-sha256",
        _TEMPORAL_CONTRACT_SHA256,
        "--expected-temporal-role",
        "development",
        "--hold-days",
        "3",
        "--top-n",
        "3",
        "--symbol-cooldown-days",
        "5",
        "--max-active-positions",
        "3",
        "--ledger-path",
        str(ledger),
        "--artifact-dir",
        str(ledger.parent / "artifacts"),
        "--input-plan-path",
        str(plan),
    ]
    if registered_record_hash is None:
        args.append("--register-only")
    else:
        args.extend(["--registered-record-hash", registered_record_hash])
    return args


@pytest.mark.parametrize("binding", [None, "wrong"], ids=["missing", "wrong-hash"])
def test_registered_validation_rejects_unbound_treatment_artifact_before_authority_io(
    tmp_path, monkeypatch, capsys, binding
):
    qualified = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    ledger = tmp_path / "ledger.jsonl"
    plan = _write_treatment_plan(plan_path, qualified)

    assert jobs.main(
        _validation_args(qualified=qualified, plan=plan_path, ledger=ledger)
    ) == 0
    registered = json.loads(capsys.readouterr().out)
    assert registered["single_change"] == "hold_days"
    assert registered["registration_contract"]["intent"]["single_change"] == (
        "hold_days"
    )

    summary = {
        "artifact_root_sha256": "b" * 64,
        "coverage_audit_sha256": "a" * 64,
    }
    if binding is not None:
        summary["treatment_input_plan"] = {
            "schema_version": "research-treatment-input-plan-binding/v2",
            "input_plan_sha256": "0" * 64
            if binding == "wrong"
            else plan["plan_sha256"],
            "experiment_id": plan["experiment_id"],
            "producer": plan["producer"],
        }
    qualified.write_text(
        json.dumps({"summary": summary, "qualified_trades": []}), encoding="utf-8"
    )
    monkeypatch.setattr(
        jobs,
        "_open_audited_authority",
        lambda args: (_ for _ in ()).throw(AssertionError("authority I/O")),
    )

    with pytest.raises(ValueError, match="plan|binding|input"):
        jobs.main(
            _validation_args(
                qualified=qualified,
                plan=plan_path,
                ledger=ledger,
                registered_record_hash=registered["record_hash"],
            )
        )

    assert [event["event_type"] for event in read_experiment_ledger(str(ledger))] == [
        "registered",
    ]


def test_registration_freezes_50_return_win_band_profit_factor_and_calmar(
    tmp_path, capsys
):
    qualified = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    ledger = tmp_path / "ledger.jsonl"
    _write_treatment_plan(plan_path, qualified)

    assert jobs.main(
        _validation_args(qualified=qualified, plan=plan_path, ledger=ledger)
    ) == 0
    registered = json.loads(capsys.readouterr().out)
    strategy = registered["registration_contract"]["strategy"]
    assert strategy["target_one_year_return_pct"] == 50.0
    assert strategy["target_win_rate_pct"] == 52.0
    assert strategy["target_win_rate_max_pct"] == 60.0
    assert strategy["target_drawdown_pct"] == 15.0
    assert strategy["target_profit_factor"] == 1.3
    assert strategy["target_calmar"] == 1.5


@pytest.mark.parametrize(
    "tamper_after_claim",
    [False, True],
    ids=["stable", "compiled-tamper"],
)
def test_registered_validation_native_evidence_uses_first_qualified_snapshot(
    tmp_path, monkeypatch, capsys, tamper_after_claim
):
    qualified = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    ledger = tmp_path / "ledger.jsonl"
    plan = _write_treatment_plan(plan_path, qualified)

    assert jobs.main(
        _validation_args(qualified=qualified, plan=plan_path, ledger=ledger)
    ) == 0
    registered = json.loads(capsys.readouterr().out)
    plan_raw = plan_path.read_bytes()
    plan_artifact = {
        "basename": plan_path.name,
        "sha256": hashlib.sha256(plan_raw).hexdigest(),
        "bytes": len(plan_raw),
    }
    original_payload = {
        "summary": {
            "artifact_root_sha256": "b" * 64,
            "coverage_audit_sha256": "a" * 64,
            "hold_days": 3,
            "top_n": 3,
            "symbol_cooldown_days": 5,
            "max_active_positions": 3,
            "treatment_input_plan": jobs._treatment_input_plan_binding(
                plan, plan_artifact
            ),
        },
        "qualified_trades": [
            {
                "symbol": "600001",
                "signal_date": "2023-01-03",
                "planned_holding_sessions": 3,
            }
        ],
    }
    original_raw = json.dumps(original_payload, sort_keys=True).encode("utf-8")
    original_sha256 = hashlib.sha256(original_raw).hexdigest()
    qualified.write_bytes(original_raw)

    class FakeUniverse:
        artifact_root_sha256 = "b" * 64
        coverage_audit_sha256 = "a" * 64

        def close(self):
            return None

    verified_authority = {
        "artifact_root_sha256": "b" * 64,
        "coverage_audit_sha256": "a" * 64,
        "temporal_contract_sha256": _TEMPORAL_CONTRACT_SHA256,
        "temporal_role": "development",
        "artifact_manifest_sha256": "c" * 64,
        "market_generation_root_sha256": "d" * 64,
        "stock_generation_lineage_sha256": "e" * 64,
    }
    monkeypatch.setattr(jobs, "_open_audited_authority", lambda args: FakeUniverse())
    captured = {}

    def fake_run_frozen_validation(*args, **kwargs):
        captured["run_calls"] = captured.get("run_calls", 0) + 1
        return {
            "dataset_sha256": "1" * 64,
            "strategy_sha256": "2" * 64,
            "validation_sha256": "3" * 64,
            "strategy_selection_replay": {},
            "qualification": {"all_pass": False},
            "aggregate_validation": {},
            "final_oos": {"status": "not_loaded"},
        }

    monkeypatch.setattr(
        jobs,
        "run_frozen_strategy_validation",
        fake_run_frozen_validation,
    )

    def fake_native_builder(**kwargs):
        assert [
            event["event_type"] for event in read_experiment_ledger(str(ledger))
        ] == ["registered"]
        snapshot_path = Path(kwargs["qualified_trades_path"])
        assert Path(kwargs["artifact_root"]).resolve() == (
            tmp_path / "artifacts"
        ).resolve()
        captured["builder_path"] = snapshot_path
        captured["builder_raw"] = snapshot_path.read_bytes()
        captured["builder_trades"] = kwargs["qualified_trades"]
        assert qualified.read_bytes() == original_raw
        assert snapshot_path.resolve() != qualified.resolve()
        return {"eligibility": {"qualified_trade_lineage_bound": True}}

    def fake_native_writer(output_dir, payload):
        path = Path(output_dir) / "native-evidence.json"
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        path.write_bytes(raw)
        return {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    def fake_native_verifier(path, **kwargs):
        captured["native_verify_calls"] = captured.get("native_verify_calls", 0) + 1
        assert Path(kwargs["artifact_root"]).resolve() == (
            tmp_path / "artifacts"
        ).resolve()
        captured["verifier_original_raw"] = qualified.read_bytes()
        captured["verifier_snapshot_raw"] = captured["builder_path"].read_bytes()
        return {
            "eligibility": {
                "eligible_for_development_validation": True,
                "eligible_for_final_validation": False,
                "final_oos_eligible": False,
                "qualified_trade_lineage_bound": True,
                "reasons": [],
            }
        }

    def fake_strict_writer(output_dir, **kwargs):
        assert [
            event["event_type"] for event in read_experiment_ledger(str(ledger))
        ] == ["registered"]
        path = Path(output_dir) / "strict-evidence.json"
        raw = b'{"strict":"verified-before-claim"}'
        path.write_bytes(raw)
        return {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "evidence_bundle_sha256": "9" * 64,
        }

    def fake_strict_verifier(path, **kwargs):
        captured["strict_verify_calls"] = captured.get("strict_verify_calls", 0) + 1
        assert Path(path).name == "strict-evidence.json"
        ledger_types = [
            event["event_type"] for event in read_experiment_ledger(str(ledger))
        ]
        assert ledger_types in (
            ["registered"],
            ["registered", "validation_started"],
        )
        return {
            "evidence_bundle_sha256": "9" * 64,
            "artifact_native_evidence": {
                "qualified_trades": {
                    "path": str(captured["builder_path"].relative_to(tmp_path / "artifacts"))
                }
            },
            "eligibility": {
                "eligible_for_development_validation": True,
                "eligible_for_final_validation": False,
                "final_oos_eligible": False,
                "reasons": [],
            },
        }

    def fake_compiled_writer(directory, **kwargs):
        assert Path(kwargs["source_payload_path"]).resolve() == captured[
            "builder_path"
        ].resolve()
        compiled = json.loads(captured["builder_path"].read_text(encoding="utf-8"))
        compiled["summary"]["research_data_contract"] = {
            "schema_version": "research_data_contract/v2",
            "strict_preclaim_test": True,
        }
        path = Path(directory) / "strict-qualified-test.json"
        raw = json.dumps(compiled, sort_keys=True).encode("utf-8")
        path.write_bytes(raw)
        captured["compiled_path"] = path
        captured["compiled_raw"] = raw
        return {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "validation_verified": True,
        }

    def fake_contract_validator(summary, trades, **kwargs):
        captured["contract_verify_calls"] = captured.get(
            "contract_verify_calls", 0
        ) + 1
        assert summary["research_data_contract"]["strict_preclaim_test"] is True
        assert trades == original_payload["qualified_trades"]
        assert Path(kwargs["artifact_base_dir"]).resolve() == (
            tmp_path / "artifacts"
        ).resolve()
        ledger_types = [
            event["event_type"] for event in read_experiment_ledger(str(ledger))
        ]
        assert ledger_types in (
            ["registered"],
            ["registered", "validation_started"],
        )
        captured["contract_preflight"] = True
        return {
            "verified_authority": verified_authority,
            "eligible_for_development_validation": True,
            "eligible_for_final_validation": False,
            "final_oos_eligible": False,
        }

    original_claim = jobs.claim_registered_experiment

    def checked_claim(*args, **kwargs):
        assert captured.get("contract_preflight") is True
        assert [
            event["event_type"] for event in read_experiment_ledger(str(ledger))
        ] == ["registered"]
        claimed = original_claim(*args, **kwargs)
        assert [
            event["event_type"] for event in read_experiment_ledger(str(ledger))
        ] == ["registered", "validation_started"]
        if tamper_after_claim:
            captured["compiled_path"].write_bytes(b'{"post_claim":"tamper"}')
        return claimed

    monkeypatch.setattr(jobs, "build_artifact_native_evidence", fake_native_builder)
    monkeypatch.setattr(jobs, "write_artifact_native_evidence", fake_native_writer)
    monkeypatch.setattr(jobs, "verify_artifact_native_evidence", fake_native_verifier)
    monkeypatch.setattr(
        jobs, "write_strict_research_evidence_bundle", fake_strict_writer
    )
    monkeypatch.setattr(jobs, "verify_research_evidence_bundle", fake_strict_verifier)
    monkeypatch.setattr(jobs, "write_strict_qualified_trades_payload", fake_compiled_writer)
    monkeypatch.setattr(jobs, "validate_point_in_time_contract", fake_contract_validator)
    monkeypatch.setattr(jobs, "claim_registered_experiment", checked_claim)
    monkeypatch.setattr(
        jobs,
        "build_profile_evidence_receipt",
        lambda **kwargs: captured.setdefault("profile", kwargs) or {},
    )
    monkeypatch.setattr(
        jobs,
        "verify_profile_evidence_receipt",
        lambda receipt: {"ok": True, "errors": []},
    )

    run_args = _validation_args(
        qualified=qualified,
        plan=plan_path,
        ledger=ledger,
        registered_record_hash=registered["record_hash"],
    )
    if tamper_after_claim:
        with pytest.raises(ValueError, match="descriptor mismatch|changed") as exc_info:
            jobs.main(run_args)
        assert "strict qualified trades" in str(exc_info.value)
        events = read_experiment_ledger(str(ledger))
        assert [event["event_type"] for event in events] == [
            "registered",
            "validation_started",
            "failed",
        ]
        assert events[-1]["error_code"] == "VALIDATION_INPUT_REJECTED"
        assert captured.get("run_calls", 0) == 0
        assert "profile" not in captured
        return

    assert jobs.main(run_args) == 0
    json.loads(capsys.readouterr().out)
    completed = read_experiment_ledger(str(ledger))[-1]
    validation_started = read_experiment_ledger(str(ledger))[1]

    assert captured["builder_path"].name == f"qualified-{original_sha256}.json"
    assert captured["builder_raw"] == original_raw
    assert captured["builder_trades"] == original_payload["qualified_trades"]
    assert captured["verifier_original_raw"] == original_raw
    assert captured["verifier_snapshot_raw"] == original_raw
    assert Path(captured["profile"]["source_artifact"]["path"]) == captured[
        "compiled_path"
    ].resolve()
    assert captured["profile"]["source_artifact"]["sha256"] == hashlib.sha256(
        captured["compiled_raw"]
    ).hexdigest()
    assert captured["profile"]["source_artifact"]["bytes"] == len(
        captured["compiled_raw"]
    )
    upstream = captured["profile"]["evidence"]["upstream_treatment_output"]
    assert upstream["sha256"] == original_sha256
    assert upstream["bytes"] == len(original_raw)
    snapshot_artifact = captured["profile"]["evidence"]["qualified_input_snapshot"]
    assert Path(snapshot_artifact["path"]) == captured["builder_path"].resolve()
    assert snapshot_artifact["sha256"] == original_sha256
    assert snapshot_artifact["bytes"] == len(original_raw)
    assert captured["contract_preflight"] is True
    assert captured["run_calls"] == 1
    assert captured["native_verify_calls"] == 2
    assert captured["strict_verify_calls"] == 2
    assert captured["contract_verify_calls"] == 2
    assert validation_started["claimed_input_artifacts"][
        "qualified_input_snapshot"
    ]["sha256"] == original_sha256
    assert validation_started["claimed_input_artifacts_sha256"] == (
        jobs._canonical_payload_sha256(
            validation_started["claimed_input_artifacts"]
        )
    )
    assert completed["qualified_trades_artifact"]["sha256"] == original_sha256
    assert completed["qualified_trades_artifact"]["bytes"] == len(original_raw)


@pytest.mark.parametrize(
    "target",
    ["raw", "snapshot", "native", "strict", "compiled"],
)
def test_claimed_treatment_artifact_reverification_rejects_tamper(
    tmp_path, monkeypatch, target
):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    trades = [{"symbol": "600001", "signal_date": "2023-01-03"}]
    source_payload = {"summary": {"contract": "test"}, "qualified_trades": trades}
    qualified_raw = json.dumps(source_payload, sort_keys=True).encode("utf-8")
    paths = {
        "raw": tmp_path / "qualified.json",
        "snapshot": artifact_root / "snapshot.json",
        "native": artifact_root / "native.json",
        "strict": artifact_root / "strict.json",
        "compiled": artifact_root / "compiled.json",
    }
    paths["raw"].write_bytes(qualified_raw)
    paths["snapshot"].write_bytes(qualified_raw)
    paths["native"].write_bytes(b'{"native":true}')
    paths["strict"].write_bytes(b'{"strict":true}')
    paths["compiled"].write_bytes(qualified_raw)

    def descriptor(name):
        raw = paths[name].read_bytes()
        return {
            "path": str(paths[name]),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    native_evidence = {"eligibility": {"reasons": []}}
    data_contract = {
        "eligible_for_development_validation": True,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
    }
    strict_descriptor = {
        **descriptor("strict"),
        "evidence_bundle_sha256": "9" * 64,
    }
    preflight = {
        "payload": source_payload,
        "qualified_trades": trades,
        "data_contract": data_contract,
        "qualified_input_snapshot": descriptor("snapshot"),
        "artifact_native_evidence": native_evidence,
        "artifact_native_evidence_artifact": descriptor("native"),
        "strict_evidence_artifact": strict_descriptor,
        "strict_qualified_artifact": descriptor("compiled"),
    }
    claimed_input_artifacts = {
        "qualified_treatment_output": descriptor("raw"),
    }
    paths[target].write_bytes(b'{"tampered":true}')
    monkeypatch.setattr(
        jobs,
        "verify_artifact_native_evidence",
        lambda *args, **kwargs: native_evidence,
    )
    monkeypatch.setattr(
        jobs,
        "verify_research_evidence_bundle",
        lambda *args, **kwargs: {"evidence_bundle_sha256": "9" * 64},
    )
    monkeypatch.setattr(jobs, "_verify_treatment_bound_payload", lambda *args: None)
    monkeypatch.setattr(
        jobs,
        "validate_point_in_time_contract",
        lambda *args, **kwargs: data_contract,
    )

    with pytest.raises(ValueError, match="descriptor mismatch|changed"):
        jobs._reverify_treatment_validation_artifacts_after_claim(
            SimpleNamespace(
                artifact_dir=str(artifact_root),
                start_date="2023-01-01",
                end_date="2023-12-31",
            ),
            qualified_raw=qualified_raw,
            treatment_input_plan={},
            input_plan_artifact={},
            audited_universe=object(),
            preflight=preflight,
            claimed_input_artifacts=claimed_input_artifacts,
        )


def test_registered_validation_native_preflight_failure_does_not_consume_claim(
    tmp_path, monkeypatch, capsys
):
    qualified = tmp_path / "h1-qualified.json"
    plan_path = tmp_path / "h1-plan.json"
    ledger = tmp_path / "ledger.jsonl"
    plan = _write_treatment_plan(plan_path, qualified)

    assert jobs.main(
        _validation_args(qualified=qualified, plan=plan_path, ledger=ledger)
    ) == 0
    registered = json.loads(capsys.readouterr().out)
    plan_raw = plan_path.read_bytes()
    plan_artifact = {
        "basename": plan_path.name,
        "sha256": hashlib.sha256(plan_raw).hexdigest(),
        "bytes": len(plan_raw),
    }
    payload = {
        "summary": {
            "artifact_root_sha256": "b" * 64,
            "coverage_audit_sha256": "a" * 64,
            "hold_days": 3,
            "top_n": 3,
            "symbol_cooldown_days": 5,
            "max_active_positions": 3,
            "treatment_input_plan": jobs._treatment_input_plan_binding(
                plan, plan_artifact
            ),
        },
        "qualified_trades": [
            {
                "symbol": "600001",
                "signal_date": "2023-01-03",
                "planned_holding_sessions": 3,
            }
        ],
    }
    qualified_raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    qualified.write_bytes(qualified_raw)

    class FakeUniverse:
        artifact_root_sha256 = "b" * 64
        coverage_audit_sha256 = "a" * 64

        def close(self):
            return None

    monkeypatch.setattr(jobs, "_open_audited_authority", lambda args: FakeUniverse())
    monkeypatch.setattr(
        jobs,
        "claim_registered_experiment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("claim must occur after native and strict preflight")
        ),
    )
    monkeypatch.setattr(
        jobs,
        "validate_point_in_time_contract",
        lambda *args, **kwargs: {
            "verified_authority": {
                "artifact_root_sha256": "b" * 64,
                "coverage_audit_sha256": "a" * 64,
            }
        },
    )
    monkeypatch.setattr(
        jobs,
        "build_artifact_native_evidence",
        lambda **kwargs: {
            "eligibility": {
                "eligible_for_development_validation": True,
                "eligible_for_final_validation": False,
                "final_oos_eligible": False,
            }
        },
    )

    def fake_native_writer(output_dir, payload):
        path = Path(output_dir) / "native-evidence.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        raw = path.read_bytes()
        return {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    monkeypatch.setattr(jobs, "write_artifact_native_evidence", fake_native_writer)
    monkeypatch.setattr(
        jobs,
        "verify_artifact_native_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("native evidence tamper detected")
        ),
    )
    monkeypatch.setattr(
        jobs,
        "run_frozen_strategy_validation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("validation must not run after failed preflight")
        ),
    )

    with pytest.raises(ValueError, match="native evidence tamper detected"):
        jobs.main(
            _validation_args(
                qualified=qualified,
                plan=plan_path,
                ledger=ledger,
                registered_record_hash=registered["record_hash"],
            )
        )

    assert [event["event_type"] for event in read_experiment_ledger(str(ledger))] == [
        "registered"
    ]
    assert qualified.read_bytes() == qualified_raw
    assert plan_path.read_bytes() == plan_raw
