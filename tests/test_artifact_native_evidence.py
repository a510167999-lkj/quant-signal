import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from app import jobs
from app.artifact_outcome_evidence import replay_trade_outcome
from app.indicators import add_indicators
from app.artifact_native_evidence import (
    build_artifact_native_evidence,
    verify_artifact_native_evidence,
    write_artifact_native_evidence,
)
from app.signals import evaluate_signal
from app.strategy_signal_evidence import build_signal_snapshot


class _FakeAudited:
    artifact_root_sha256 = "a" * 64
    coverage_audit_sha256 = "b" * 64
    temporal_contract_sha256 = "c" * 64
    temporal_role = "development"
    manifest = {
        "manifest_sha256": "d" * 64,
        "coverage": {"start_date": "2020-01-01", "end_date": "2020-01-10"},
        "market_generations": {"root_sha256": "e" * 64},
        "stock_generation": {"lineage_sha256": "f" * 64},
    }

    def item_as_of(self, symbol, signal_date):
        return {"symbol": symbol, "signal_date": signal_date}

    def open_sessions(self, start_date, end_date):
        return ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]

    def causal_signal_bars(self, symbol, start_date, as_of_date):
        return [{"trade_date": as_of_date, "generation_proof": {"trade_date": as_of_date}}]

    def next_open_execution_evidence(self, symbol, trade_date, side):
        return {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": 10.0 if side == "buy" else 11.0,
            "generation_proof": {
                "trade_date": trade_date,
                "side": side,
                "generation_id": f"g-{trade_date}",
            },
        }

    def close(self):
        return None


class _ReplayFakeAudited(_FakeAudited):
    def causal_signal_bars(self, symbol, start_date, as_of_date):
        dates = pd.bdate_range(end="2020-01-01", periods=100)
        rows = []
        price = 10.0
        for index, date in enumerate(dates):
            price *= 1.0015 if index < 85 else 1.004
            rows.append(
                {
                    "trade_date": date.strftime("%Y-%m-%d"),
                    "signal_open": price * 0.995,
                    "signal_high": price * 1.01,
                    "signal_low": price * 0.99,
                    "signal_close": price,
                    "volume_shares": 1_000_000 + index * 1_000,
                }
            )
        return rows


class _OutcomeReplayFakeAudited(_FakeAudited):
    def causal_signal_bars(self, symbol, start_date, as_of_date):
        rows = [
            {"trade_date": "2020-01-01", "signal_open": 9.0, "signal_high": 9.5, "signal_low": 8.5, "signal_close": 9.0},
            {"trade_date": "2020-01-02", "signal_open": 10.0, "signal_high": 10.5, "signal_low": 9.5, "signal_close": 10.2},
            {"trade_date": "2020-01-03", "signal_open": 10.4, "signal_high": 10.8, "signal_low": 10.0, "signal_close": 10.6},
            {"trade_date": "2020-01-04", "signal_open": 11.0, "signal_high": 11.2, "signal_low": 10.7, "signal_close": 11.1},
        ]
        for row in rows:
            row["bar_adj_factor"] = 1.0
            for field in ("open", "high", "low", "close"):
                row[f"raw_{field}"] = row[f"signal_{field}"]
        return rows


class _StrictReplayAudited(_FakeAudited):
    def __init__(self, *, buy_signal=True):
        dates = [date.strftime("%Y-%m-%d") for date in pd.bdate_range(end="2020-01-01", periods=100)]
        dates.extend(["2020-01-02", "2020-01-03", "2020-01-04"])
        price = 8.0
        self._bars = []
        refs = []
        for index, trade_date in enumerate(dates):
            if buy_signal:
                price *= 1.0 if index < 80 else 1.001
                if index == 99:
                    price *= 1.01
            else:
                price *= 1.0015 if index < 85 else 1.004
            proof = {
                "trade_date": trade_date,
                "generation_id": f"g-{trade_date}",
                "manifest_sha256": hashlib.sha256(
                    f"manifest-{trade_date}".encode()
                ).hexdigest(),
                "lineage_sha256": hashlib.sha256(
                    f"lineage-{trade_date}".encode()
                ).hexdigest(),
                "vintage": "development_backfill",
            }
            refs.append(dict(proof))
            self._bars.append(
                {
                    "trade_date": trade_date,
                    "signal_open": price * 0.995,
                    "signal_high": price * 1.01,
                    "signal_low": price * 0.99,
                    "signal_close": price,
                    "volume_shares": (
                        (1_000_000 + index * 1_000) * 2
                        if buy_signal and index == 99
                        else 1_000_000 + index * 1_000
                    ),
                    "generation_proof": proof,
                }
            )
            bar = self._bars[-1]
            raw_open = (
                10.0
                if trade_date == "2020-01-02"
                else 11.0
                if trade_date == "2020-01-04"
                else bar["signal_open"]
            )
            bar.update(
                {
                    "bar_adj_factor": 1.0,
                    "raw_open": raw_open,
                    "raw_high": max(raw_open, bar["signal_high"]),
                    "raw_low": min(raw_open, bar["signal_low"]),
                    "raw_close": bar["signal_close"],
                }
            )
        self.start_date = dates[0]
        self.end_date = dates[-1]
        self.manifest = deepcopy(_FakeAudited.manifest)
        self.manifest["coverage"] = {
            "start_date": self.start_date,
            "end_date": self.end_date,
        }
        self.manifest["producer_code_sha256"] = "1" * 64
        self.manifest["market_generations"] = {
            "root_sha256": "e" * 64,
            "refs": refs,
        }

    def causal_signal_bars(self, symbol, start_date, as_of_date):
        return [
            dict(row)
            for row in self._bars
            if str(start_date)[:10] <= row["trade_date"] <= str(as_of_date)[:10]
        ]


def _trade():
    return {
        "symbol": "600001",
        "signal_date": "2020-01-01",
        "entry_date": "2020-01-02",
        "planned_exit_date": "2020-01-04",
        "exit_date": "2020-01-04",
        "holding_days": 2,
        "exit_reason": "time_exit_next_open",
        "price_basis": "raw_unadjusted_execution",
        "return_price_basis": "causal_total_return_open_to_open",
        "entry_executability": {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": 10.0,
            "generation_proof": {
                "trade_date": "2020-01-02",
                "side": "buy",
                "generation_id": "g-2020-01-02",
            },
        },
        "exit_execution_evidence": {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": 11.0,
            "generation_proof": {
                "trade_date": "2020-01-04",
                "side": "sell",
                "generation_id": "g-2020-01-04",
            },
        },
    }


def _write_qualified(root: Path, trade: dict) -> Path:
    path = root / "qualified.json"
    path.write_text(json.dumps({"summary": {}, "qualified_trades": [trade]}), encoding="utf-8")
    return path


def _complete_trade(audited: _StrictReplayAudited) -> dict:
    bars = audited.causal_signal_bars("600001", audited.start_date, "2020-01-01")
    frame = pd.DataFrame(
        [
            {
                "date": row["trade_date"],
                "open": row["signal_open"],
                "high": row["signal_high"],
                "low": row["signal_low"],
                "close": row["signal_close"],
                "volume": row["volume_shares"],
            }
            for row in bars
        ]
    )
    trade = _trade()
    trade["strategy_signal"] = build_signal_snapshot(evaluate_signal(add_indicators(frame)))
    trade["action"] = trade["strategy_signal"]["action"]
    trade.update(replay_trade_outcome(audited, trade, compare_claim=False)["claim"])
    return trade


def test_artifact_native_evidence_keeps_incomplete_legacy_trade_ineligible(tmp_path):
    qualified_path = _write_qualified(tmp_path, _trade())
    payload = build_artifact_native_evidence(
        audited_universe=_FakeAudited(),
        qualified_trades=[_trade()],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )

    assert payload["schema_version"] == "research_artifact_native_evidence/v4"
    assert payload["eligibility"]["eligible_for_development_validation"] is False
    assert "strategy_signal_replay_not_bound" in payload["eligibility"]["reasons"]
    assert "corporate_action_receipt_bound" not in payload["eligibility"]
    descriptor = write_artifact_native_evidence(str(tmp_path), payload)
    verified = verify_artifact_native_evidence(
        descriptor["path"], audited_universe=_FakeAudited(), artifact_root=str(tmp_path)
    )
    assert verified["trade_lineage_sha256"] == payload["trade_lineage_sha256"]


def test_artifact_native_evidence_derives_development_eligibility_from_all_bindings(
    tmp_path,
):
    audited = _StrictReplayAudited()
    trade = _complete_trade(audited)
    qualified_path = _write_qualified(tmp_path, trade)

    payload = build_artifact_native_evidence(
        audited_universe=audited,
        qualified_trades=[trade],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )

    assert payload["producer_code_binding"]["bound"] is True
    assert {
        "historical_artifact_backtest",
        "outcome_claim_builder",
        "outcome_contract_module",
        "artifact_outcome_producer",
        "artifact_causal_indicator_prefix",
        "research_payload_builder",
        "portfolio_selector",
        "frozen_fixed_sweep",
        "fixed_sweep_engine",
    }.issubset(payload["producer_code_binding"]["components"])
    assert (
        payload["producer_code_binding"]["schema_version"]
        == "artifact_native_producer_code_binding/v2"
    )
    assert payload["adjustment_factor_generation_binding"]["bound"] is True
    assert payload["eligibility"] == {
        "execution_proof_complete": True,
        "qualified_trade_lineage_bound": True,
        "strategy_signal_replay_bound": True,
        "strategy_entry_decision_bound": True,
        "producer_code_bound": True,
        "adjustment_factor_generation_bound": True,
        "cross_segment_identity_continuity_bound": True,
        "outcome_replay_bound": True,
        "eligible_for_development_validation": True,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
        "reasons": [],
    }
    descriptor = write_artifact_native_evidence(str(tmp_path), payload)
    verified = verify_artifact_native_evidence(
        descriptor["path"], audited_universe=audited, artifact_root=str(tmp_path)
    )
    assert verified["eligibility"]["eligible_for_development_validation"] is True


@pytest.mark.parametrize("claimed_action", [None, "HOLD"])
def test_artifact_native_evidence_rejects_missing_or_mismatched_entry_action(
    tmp_path, claimed_action
):
    audited = _StrictReplayAudited()
    trade = _complete_trade(audited)
    assert trade["strategy_signal"]["action"] == "BUY"
    if claimed_action is None:
        trade.pop("action")
    else:
        trade["action"] = claimed_action
    qualified_path = _write_qualified(tmp_path, trade)

    payload = build_artifact_native_evidence(
        audited_universe=audited,
        qualified_trades=[trade],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )

    assert payload["strategy_signal_replay"]["bound"] is True
    assert payload["strategy_entry_decision_binding"]["bound"] is False
    assert payload["eligibility"]["strategy_entry_decision_bound"] is False
    assert payload["eligibility"]["eligible_for_development_validation"] is False
    assert "strategy_entry_decision_not_bound" in payload["eligibility"]["reasons"]


def test_artifact_native_evidence_rejects_matched_non_buy_signal(tmp_path):
    audited = _StrictReplayAudited(buy_signal=False)
    trade = _complete_trade(audited)
    assert trade["action"] != "BUY"
    qualified_path = _write_qualified(tmp_path, trade)

    payload = build_artifact_native_evidence(
        audited_universe=audited,
        qualified_trades=[trade],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )

    assert payload["strategy_signal_replay"]["bound"] is True
    assert payload["strategy_entry_decision_binding"]["bound"] is False
    assert payload["eligibility"]["eligible_for_development_validation"] is False


@pytest.mark.parametrize(
    ("field", "mutation", "message"),
    [
        (
            "producer_code_binding",
            lambda value: value["components"]["trade_lineage_verifier"].update(
                {"source_sha256": "0" * 64}
            ),
            "producer code binding",
        ),
        (
            "adjustment_factor_generation_binding",
            lambda value: value.update({"generation_claims_sha256": "0" * 64}),
            "adjustment factor generation binding",
        ),
        (
            "strategy_entry_decision_binding",
            lambda value: value.update({"decision_claims_sha256": "0" * 64}),
            "strategy entry decision binding",
        ),
    ],
)
def test_artifact_native_evidence_recomputes_strict_bindings_after_resigned_tampering(
    tmp_path, field, mutation, message
):
    audited = _StrictReplayAudited()
    trade = _complete_trade(audited)
    qualified_path = _write_qualified(tmp_path, trade)
    payload = build_artifact_native_evidence(
        audited_universe=audited,
        qualified_trades=[trade],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )
    descriptor = write_artifact_native_evidence(str(tmp_path), payload)
    tampered = json.loads(Path(descriptor["path"]).read_text(encoding="utf-8"))
    mutation(tampered[field])
    semantic = dict(tampered)
    semantic.pop("evidence_sha256")
    tampered["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    Path(descriptor["path"]).write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        verify_artifact_native_evidence(
            descriptor["path"], audited_universe=audited, artifact_root=str(tmp_path)
        )


def test_artifact_native_evidence_rejects_qualified_trade_tampering(tmp_path):
    qualified_path = _write_qualified(tmp_path, _trade())
    payload = build_artifact_native_evidence(
        audited_universe=_FakeAudited(),
        qualified_trades=[_trade()],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )
    descriptor = write_artifact_native_evidence(str(tmp_path), payload)
    tampered = _trade()
    tampered["entry_executability"]["raw_price"] = 10.5
    qualified_path.write_text(
        json.dumps({"summary": {}, "qualified_trades": [tampered]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="trade lineage|qualified trades file"):
        verify_artifact_native_evidence(
            descriptor["path"], audited_universe=_FakeAudited(), artifact_root=str(tmp_path)
        )


def test_artifact_native_evidence_binds_signal_snapshot_when_present(tmp_path):
    audited = _ReplayFakeAudited()
    bars = audited.causal_signal_bars("600001", "2019-01-01", "2020-01-01")
    frame = pd.DataFrame(
        [
            {
                "date": row["trade_date"],
                "open": row["signal_open"],
                "high": row["signal_high"],
                "low": row["signal_low"],
                "close": row["signal_close"],
                "volume": row["volume_shares"],
            }
            for row in bars
        ]
    )
    trade = _trade()
    trade["strategy_signal"] = build_signal_snapshot(evaluate_signal(add_indicators(frame)))
    qualified_path = _write_qualified(tmp_path, trade)

    payload = build_artifact_native_evidence(
        audited_universe=audited,
        qualified_trades=[trade],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )

    assert payload["strategy_signal_replay"]["bound"] is True
    assert payload["eligibility"]["strategy_signal_replay_bound"] is True
    assert "strategy_signal_replay_not_bound" not in payload["eligibility"]["reasons"]


def test_artifact_native_evidence_binds_outcome_replay_when_claims_present(tmp_path):
    audited = _OutcomeReplayFakeAudited()
    trade = _trade()
    trade.update(replay_trade_outcome(audited, trade, compare_claim=False)["claim"])
    qualified_path = _write_qualified(tmp_path, trade)

    payload = build_artifact_native_evidence(
        audited_universe=audited,
        qualified_trades=[trade],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )

    assert payload["outcome_replay"]["bound"] is True
    assert payload["eligibility"]["outcome_replay_bound"] is True
    assert "outcome_replay_not_bound" not in payload["eligibility"]["reasons"]
    assert payload["proof_policy"]["blocked_sell_retry"] == "first_fillable_sell_replayed_in_v1"


def test_artifact_native_evidence_rejects_rewritten_sell_retry_policy(tmp_path):
    audited = _OutcomeReplayFakeAudited()
    trade = _trade()
    trade.update(replay_trade_outcome(audited, trade, compare_claim=False)["claim"])
    qualified_path = _write_qualified(tmp_path, trade)
    payload = build_artifact_native_evidence(
        audited_universe=audited,
        qualified_trades=[trade],
        qualified_trades_path=str(qualified_path),
        artifact_root=str(tmp_path),
    )
    descriptor = write_artifact_native_evidence(str(tmp_path), payload)
    tampered = json.loads(Path(descriptor["path"]).read_text(encoding="utf-8"))
    tampered["proof_policy"]["blocked_sell_retry"] = "not_replayed_in_v1"
    semantic = dict(tampered)
    semantic.pop("evidence_sha256")
    tampered["evidence_sha256"] = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    Path(descriptor["path"]).write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="sell retry policy"):
        verify_artifact_native_evidence(
            descriptor["path"], audited_universe=audited, artifact_root=str(tmp_path)
        )


def test_artifact_native_evidence_cli_is_fail_closed(tmp_path, monkeypatch, capsys):
    qualified_path = _write_qualified(tmp_path, _trade())
    monkeypatch.setattr(
        jobs.AuditedPointInTimeUniverse,
        "from_file",
        lambda *args, **kwargs: _FakeAudited(),
    )

    assert jobs.main(
        [
            "research-build-artifact-native-evidence",
            "--qualified-trades-path", str(qualified_path),
            "--audited-pit-universe-path", str(tmp_path / "audited.sqlite3"),
            "--expected-coverage-audit-sha256", "b" * 64,
            "--expected-artifact-root-sha256", "a" * 64,
            "--expected-temporal-contract-sha256", "c" * 64,
            "--expected-temporal-role", "development",
            "--output-dir", str(tmp_path / "evidence"),
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "development_execution_integrity_only"
    assert result["strict_validation_eligible"] is False
    assert Path(result["evidence"]["path"]).exists()
