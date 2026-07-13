"""Content-addressed evidence for audited artifact-native development trades.

Development eligibility is derived only from bindings that can be recomputed
from a verified PIT artifact.  Event-level corporate-action receipts are not
claimed here: the narrower proof is that every causal adjusted bar is bound to
the audited daily/adjustment-factor market generation used by the replay.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping

from app.artifact_outcome_evidence import (
    replay_trade_outcome,
    verify_artifact_trade_outcome,
)
from app.indicators import add_indicators
from app.research_market_data import causal_adjusted_bars
from app.research_pit_store import AuditedPointInTimeUniverse
from app.research_validation import (
    _canonical_json,
    audited_authority_from_universe,
    qualified_trades_sha256,
    verify_artifact_trade_lineage,
)
from app.signal_tags import build_signal_tags
from app.strategy_signal_evidence import (
    build_signal_snapshot,
    replay_signal_snapshot,
    signal_evaluator_identity,
    verify_strategy_signal_replay,
)


SCHEMA_VERSION = "research_artifact_native_evidence/v2"
_REASONS = [
    "qualified_trade_lineage_not_bound",
    "strategy_signal_replay_not_bound",
    "strategy_entry_decision_not_bound",
    "producer_code_not_bound",
    "adjustment_factor_generation_not_bound",
    "outcome_replay_not_bound",
]
_GENERATION_PROOF_FIELDS = (
    "trade_date",
    "generation_id",
    "manifest_sha256",
    "lineage_sha256",
    "vintage",
)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_relative(root: Path, value: Any) -> Path:
    relative = Path(str(value or ""))
    if not str(value or "").strip() or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("qualified trades path is unsafe")
    current = root.resolve()
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("qualified trades path may not contain symlinks")
    candidate = current.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("qualified trades path escapes artifact root") from exc
    if not candidate.is_file():
        raise ValueError("qualified trades file is missing")
    return candidate


def _descriptor(root: Path, path: Path) -> Dict[str, Any]:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("qualified trades file must be inside artifact root") from exc
    content = resolved_path.read_bytes()
    return {
        "path": relative.as_posix(),
        "bytes": len(content),
        "file_sha256": _sha256_bytes(content),
    }


def _trade_key(trade: Mapping[str, Any]) -> str:
    values = tuple(str(trade.get(key) or "")[:10] if key != "symbol" else str(trade.get(key) or "") for key in ("symbol", "signal_date", "entry_date", "exit_date"))
    if not all(values):
        raise ValueError("qualified trade key is incomplete")
    return "|".join(values)


def _trade_keys_sha256(trades: List[Dict[str, Any]]) -> str:
    keys = [_trade_key(trade) for trade in trades]
    if len(keys) != len(set(keys)):
        raise ValueError("qualified trades contain duplicate trade keys")
    return _sha256_value(sorted(keys))


def _load_trade_rows(path: Path) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("qualified trades file is not valid JSON") from exc
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("qualified_trades"), list):
        rows = payload["qualified_trades"]
    else:
        raise ValueError("qualified trades file lacks qualified_trades rows")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("qualified trades rows are invalid")
    return [dict(row) for row in rows]


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _source_identity(target: Any) -> Dict[str, str]:
    try:
        source = inspect.getsource(target).encode("utf-8")
    except (OSError, TypeError) as exc:
        raise ValueError("producer code source is unavailable") from exc
    module = str(getattr(target, "__module__", ""))
    qualname = str(getattr(target, "__qualname__", ""))
    if not module or not qualname:
        raise ValueError("producer code identity is incomplete")
    return {
        "module": module,
        "qualname": qualname,
        "source_sha256": _sha256_bytes(source),
    }


def _producer_code_binding(audited_universe: Any) -> Dict[str, Any]:
    # Imported at call time to avoid the research_pit -> validation -> native
    # module cycle while still binding the candidate-pool and portfolio paths
    # that produced the qualified trade set.
    from app.research_backtest import (
        _research_payload_from_trades,
        _run_historical_universe_research_backtest_resolved,
    )
    from app.research_portfolio import _select_with_portfolio_controls
    from app.research_sweep import sweep_qualified_trades
    from app.research_validation import _fixed_sweep

    manifest = getattr(audited_universe, "manifest", None)
    artifact_producer_code_sha256 = (
        manifest.get("producer_code_sha256") if isinstance(manifest, Mapping) else None
    )
    components = {
        "artifact_native_builder": _source_identity(build_artifact_native_evidence),
        "artifact_native_verifier": _source_identity(verify_artifact_native_evidence),
        "producer_binding_builder": _source_identity(_producer_code_binding),
        "adjustment_generation_verifier": _source_identity(
            _adjustment_factor_generation_binding
        ),
        "strategy_entry_decision_verifier": _source_identity(
            _strategy_entry_decision_binding
        ),
        "trade_lineage_verifier": _source_identity(verify_artifact_trade_lineage),
        "strategy_signal_snapshot_builder": _source_identity(build_signal_snapshot),
        "strategy_signal_replay": _source_identity(replay_signal_snapshot),
        "strategy_signal_replay_verifier": _source_identity(
            verify_strategy_signal_replay
        ),
        "signal_evaluator": signal_evaluator_identity(),
        "indicator_builder": _source_identity(add_indicators),
        "signal_tags_builder": _source_identity(build_signal_tags),
        "outcome_replay": _source_identity(replay_trade_outcome),
        "outcome_replay_verifier": _source_identity(verify_artifact_trade_outcome),
        "causal_adjustment": _source_identity(causal_adjusted_bars),
        "audited_causal_signal_bars": _source_identity(
            AuditedPointInTimeUniverse.causal_signal_bars
        ),
        "audited_execution_evidence": _source_identity(
            AuditedPointInTimeUniverse.next_open_execution_evidence
        ),
        "historical_artifact_backtest": _source_identity(
            _run_historical_universe_research_backtest_resolved
        ),
        "research_payload_builder": _source_identity(_research_payload_from_trades),
        "portfolio_selector": _source_identity(_select_with_portfolio_controls),
        "frozen_fixed_sweep": _source_identity(_fixed_sweep),
        "fixed_sweep_engine": _source_identity(sweep_qualified_trades),
    }
    identity = {
        "schema_version": "artifact_native_producer_code_binding/v1",
        "artifact_producer_code_sha256": artifact_producer_code_sha256,
        "components": components,
    }
    bound = _valid_sha256(artifact_producer_code_sha256) and all(
        _valid_sha256(component.get("source_sha256"))
        for component in components.values()
    )
    return {
        **identity,
        "producer_code_root_sha256": _sha256_value(identity),
        "bound": bound,
        "reasons": [] if bound else ["producer_code_not_bound"],
    }


def _generation_ref_map(audited_universe: Any) -> Dict[str, Dict[str, Any]]:
    manifest = getattr(audited_universe, "manifest", None)
    market_generations = (
        manifest.get("market_generations") if isinstance(manifest, Mapping) else None
    )
    refs = market_generations.get("refs") if isinstance(market_generations, Mapping) else None
    if not isinstance(refs, list) or not refs:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for ref in refs:
        if not isinstance(ref, Mapping) or any(
            ref.get(field) is None for field in _GENERATION_PROOF_FIELDS
        ):
            return {}
        projection = {field: ref[field] for field in _GENERATION_PROOF_FIELDS}
        trade_date = str(projection["trade_date"])[:10]
        projection["trade_date"] = trade_date
        if trade_date in result:
            raise ValueError("market generation refs contain duplicate trade dates")
        result[trade_date] = projection
    return result


def _adjustment_factor_generation_binding(
    audited_universe: Any, trades: List[Dict[str, Any]]
) -> Dict[str, Any]:
    refs = _generation_ref_map(audited_universe)
    coverage = getattr(audited_universe, "manifest", {}).get("coverage") or {}
    start_date = str(
        getattr(audited_universe, "start_date", None)
        or coverage.get("start_date")
        or ""
    )[:10]
    claims: List[Dict[str, Any]] = []
    missing = 0
    if not refs or not start_date:
        missing = len(trades)
    else:
        for trade in trades:
            trade_key = _trade_key(trade)
            symbol = str(trade.get("symbol") or "")
            signal_date = str(trade.get("signal_date") or "")[:10]
            entry_date = str(trade.get("entry_date") or "")[:10]
            planned_exit_date = str(trade.get("planned_exit_date") or "")[:10]
            exit_date = str(trade.get("exit_date") or "")[:10]
            if not all((symbol, signal_date, entry_date, planned_exit_date, exit_date)):
                raise ValueError("adjustment factor generation trade is incomplete")
            bars = audited_universe.causal_signal_bars(symbol, start_date, exit_date)
            if not isinstance(bars, list) or not bars:
                missing += 1
                continue
            proofs = []
            seen_dates = set()
            incomplete = False
            for bar in bars:
                if not isinstance(bar, Mapping):
                    incomplete = True
                    break
                trade_date = str(bar.get("trade_date") or "")[:10]
                proof = bar.get("generation_proof")
                if (
                    not trade_date
                    or trade_date in seen_dates
                    or not isinstance(proof, Mapping)
                    or any(proof.get(field) is None for field in _GENERATION_PROOF_FIELDS)
                ):
                    incomplete = True
                    break
                seen_dates.add(trade_date)
                projection = {field: proof[field] for field in _GENERATION_PROOF_FIELDS}
                projection["trade_date"] = str(projection["trade_date"])[:10]
                if projection["trade_date"] != trade_date:
                    raise ValueError("adjustment factor generation proof date mismatch")
                expected = refs.get(trade_date)
                if expected is None or projection != expected:
                    raise ValueError("adjustment factor generation proof mismatch")
                proofs.append(projection)
            required_dates = {signal_date, entry_date, planned_exit_date, exit_date}
            if incomplete or not required_dates.issubset(seen_dates):
                missing += 1
                continue
            claims.append(
                {
                    "trade_key": trade_key,
                    "bar_count": len(proofs),
                    "first_trade_date": proofs[0]["trade_date"],
                    "last_trade_date": proofs[-1]["trade_date"],
                    "generation_proofs_sha256": _sha256_value(proofs),
                }
            )
    claims.sort(key=lambda item: item["trade_key"])
    bound = bool(trades) and missing == 0 and len(claims) == len(trades)
    return {
        "schema_version": "adjustment_factor_generation_binding/v1",
        "method": "audited_daily_and_adj_factor_generation_replay_v1",
        "causal_adjustment": _source_identity(causal_adjusted_bars),
        "causal_bar_adapter": _source_identity(
            AuditedPointInTimeUniverse.causal_signal_bars
        ),
        "trade_count": len(trades),
        "replayed_count": len(claims),
        "missing_count": missing,
        "market_generation_refs_sha256": _sha256_value(
            [refs[key] for key in sorted(refs)]
        ),
        "generation_claims_sha256": _sha256_value(claims),
        "bound": bound,
        "reasons": [] if bound else ["adjustment_factor_generation_not_bound"],
    }


def _strategy_entry_decision_binding(
    trades: List[Dict[str, Any]], strategy_signal_replay: Mapping[str, Any]
) -> Dict[str, Any]:
    claims = []
    missing = 0
    rejected = 0
    for trade in trades:
        trade_key = _trade_key(trade)
        snapshot = trade.get("strategy_signal")
        claimed_action = trade.get("action")
        if not isinstance(snapshot, Mapping) or claimed_action is None:
            missing += 1
            continue
        snapshot_action = snapshot.get("action")
        if claimed_action != "BUY" or snapshot_action != "BUY":
            rejected += 1
            continue
        claims.append(
            {
                "trade_key": trade_key,
                "action": "BUY",
                "signal_snapshot_sha256": _sha256_value(dict(snapshot)),
            }
        )
    claims.sort(key=lambda item: item["trade_key"])
    bound = (
        strategy_signal_replay.get("bound") is True
        and bool(trades)
        and missing == 0
        and rejected == 0
        and len(claims) == len(trades)
    )
    return {
        "schema_version": "strategy_entry_decision_binding/v1",
        "required_action": "BUY",
        "trade_count": len(trades),
        "bound_count": len(claims),
        "missing_count": missing,
        "rejected_count": rejected,
        "decision_claims_sha256": _sha256_value(claims),
        "bound": bound,
        "selection_receipt_bound": False,
        "limitations": [
            "candidate_filter_not_bound",
            "candidate_ranking_not_bound",
            "top_n_not_bound",
            "portfolio_selection_not_bound",
        ],
        "reasons": [] if bound else ["strategy_entry_decision_not_bound"],
    }


def _proof_policy(outcome_replay: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "entry_side": "buy",
        "exit_side": "sell",
        "decision_cutoff": "next_open",
        "exit_model": "time_exit_next_open",
        "blocked_sell_retry": (
            "first_fillable_sell_replayed_in_v1"
            if outcome_replay.get("bound") is True
            else "not_replayed_in_v1"
        ),
        "price_basis": "raw_unadjusted_execution",
        "return_price_basis": "causal_total_return_open_to_open",
        "intraday_exit_supported": False,
    }


def _eligibility(
    *,
    trade_count: int,
    strategy_signal_replay: Mapping[str, Any],
    strategy_entry_decision_binding: Mapping[str, Any],
    producer_code_binding: Mapping[str, Any],
    adjustment_factor_generation_binding: Mapping[str, Any],
    outcome_replay: Mapping[str, Any],
) -> Dict[str, Any]:
    bindings = {
        "qualified_trade_lineage_bound": trade_count > 0,
        "strategy_signal_replay_bound": strategy_signal_replay.get("bound") is True,
        "strategy_entry_decision_bound": (
            strategy_entry_decision_binding.get("bound") is True
        ),
        "producer_code_bound": producer_code_binding.get("bound") is True,
        "adjustment_factor_generation_bound": (
            adjustment_factor_generation_binding.get("bound") is True
        ),
        "outcome_replay_bound": outcome_replay.get("bound") is True,
    }
    reason_by_binding = {
        "qualified_trade_lineage_bound": "qualified_trade_lineage_not_bound",
        "strategy_signal_replay_bound": "strategy_signal_replay_not_bound",
        "strategy_entry_decision_bound": "strategy_entry_decision_not_bound",
        "producer_code_bound": "producer_code_not_bound",
        "adjustment_factor_generation_bound": (
            "adjustment_factor_generation_not_bound"
        ),
        "outcome_replay_bound": "outcome_replay_not_bound",
    }
    reasons = [
        reason_by_binding[key]
        for key, value in bindings.items()
        if value is not True
    ]
    if reasons != [reason for reason in _REASONS if reason in reasons]:
        raise ValueError("artifact-native eligibility reason order is invalid")
    eligible = not reasons
    return {
        "execution_proof_complete": bindings["qualified_trade_lineage_bound"],
        **bindings,
        "eligible_for_development_validation": eligible,
        "eligible_for_final_validation": False,
        "final_oos_eligible": False,
        "reasons": reasons,
    }


def build_artifact_native_evidence(
    *,
    audited_universe: Any,
    qualified_trades: List[Dict[str, Any]],
    qualified_trades_path: str,
    artifact_root: str,
) -> Dict[str, Any]:
    root = Path(artifact_root).resolve()
    qualified_path = Path(qualified_trades_path).resolve()
    descriptor = _descriptor(root, qualified_path)
    if not isinstance(qualified_trades, list):
        raise ValueError("qualified trades must be a list")
    # Freshly query the audited artifact before producing the evidence payload.
    lineage_sha256 = verify_artifact_trade_lineage(audited_universe, qualified_trades)
    strategy_signal_replay = verify_strategy_signal_replay(audited_universe, qualified_trades)
    strategy_entry_decision_binding = _strategy_entry_decision_binding(
        qualified_trades, strategy_signal_replay
    )
    outcome_replay = verify_artifact_trade_outcome(audited_universe, qualified_trades)
    producer_code_binding = _producer_code_binding(audited_universe)
    adjustment_factor_generation_binding = _adjustment_factor_generation_binding(
        audited_universe, qualified_trades
    )
    trade_keys_sha256 = _trade_keys_sha256(qualified_trades)
    authority = audited_authority_from_universe(audited_universe)
    eligibility = _eligibility(
        trade_count=len(qualified_trades),
        strategy_signal_replay=strategy_signal_replay,
        strategy_entry_decision_binding=strategy_entry_decision_binding,
        producer_code_binding=producer_code_binding,
        adjustment_factor_generation_binding=adjustment_factor_generation_binding,
        outcome_replay=outcome_replay,
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_role": "development_only",
        "evidence_scope": "artifact_native_development_replay_v2",
        "coverage": deepcopy(audited_universe.manifest["coverage"]),
        "authority": authority,
        "qualified_trades": {
            **descriptor,
            "count": len(qualified_trades),
            "qualified_trades_sha256": qualified_trades_sha256(qualified_trades),
            "trade_keys_sha256": trade_keys_sha256,
        },
        "proof_policy": _proof_policy(outcome_replay),
        "trade_lineage_sha256": lineage_sha256,
        "strategy_signal_replay": strategy_signal_replay,
        "strategy_entry_decision_binding": strategy_entry_decision_binding,
        "outcome_replay": outcome_replay,
        "producer_code_binding": producer_code_binding,
        "adjustment_factor_generation_binding": (
            adjustment_factor_generation_binding
        ),
        "eligibility": eligibility,
    }
    payload["evidence_sha256"] = _sha256_value(payload)
    return payload


def write_artifact_native_evidence(directory: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported artifact-native evidence schema")
    semantic = dict(payload)
    claimed = semantic.pop("evidence_sha256", None)
    if claimed != _sha256_value(semantic):
        raise ValueError("artifact-native evidence hash mismatch")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    digest = _sha256_bytes(content)
    filename = f"native-evidence-{digest}.json"
    destination = root / filename
    if destination.exists() and destination.read_bytes() != content:
        raise ValueError("content-addressed artifact-native evidence mismatch")
    if not destination.exists():
        fd, temporary = tempfile.mkstemp(prefix=f"{filename}.", suffix=".tmp", dir=str(root))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"path": str(destination), "filename": filename, "file_sha256": digest, "bytes": len(content)}


def verify_artifact_native_evidence(
    path: str,
    *,
    audited_universe: Any,
    artifact_root: str,
) -> Dict[str, Any]:
    evidence_path = Path(path).resolve()
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact-native evidence is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported artifact-native evidence schema")
    semantic = dict(payload)
    claimed = semantic.pop("evidence_sha256", None)
    if claimed != _sha256_value(semantic):
        raise ValueError("artifact-native evidence hash mismatch")
    if payload.get("authority") != audited_authority_from_universe(audited_universe):
        raise ValueError("artifact-native evidence authority mismatch")
    if payload.get("coverage") != audited_universe.manifest.get("coverage"):
        raise ValueError("artifact-native evidence coverage mismatch")
    if (
        payload.get("artifact_role") != "development_only"
        or payload.get("evidence_scope") != "artifact_native_development_replay_v2"
    ):
        raise ValueError("artifact-native evidence scope is invalid")
    qualified_descriptor = payload.get("qualified_trades") or {}
    root = Path(artifact_root).resolve()
    qualified_path = _safe_relative(root, qualified_descriptor.get("path"))
    descriptor = _descriptor(root, qualified_path)
    for key in ("path", "bytes", "file_sha256"):
        if descriptor[key] != qualified_descriptor.get(key):
            raise ValueError("qualified trades file descriptor mismatch")
    trades = _load_trade_rows(qualified_path)
    if len(trades) != qualified_descriptor.get("count"):
        raise ValueError("qualified trades file count mismatch")
    if qualified_trades_sha256(trades) != qualified_descriptor.get("qualified_trades_sha256"):
        raise ValueError("qualified trades file hash mismatch")
    if _trade_keys_sha256(trades) != qualified_descriptor.get("trade_keys_sha256"):
        raise ValueError("qualified trade keys hash mismatch")
    fresh_lineage = verify_artifact_trade_lineage(audited_universe, trades)
    if fresh_lineage != payload.get("trade_lineage_sha256"):
        raise ValueError("artifact-native trade lineage mismatch")
    fresh_strategy_signal_replay = verify_strategy_signal_replay(audited_universe, trades)
    if fresh_strategy_signal_replay != payload.get("strategy_signal_replay"):
        raise ValueError("artifact-native strategy signal replay mismatch")
    fresh_strategy_entry_decision_binding = _strategy_entry_decision_binding(
        trades, fresh_strategy_signal_replay
    )
    if fresh_strategy_entry_decision_binding != payload.get(
        "strategy_entry_decision_binding"
    ):
        raise ValueError("artifact-native strategy entry decision binding mismatch")
    fresh_outcome_replay = verify_artifact_trade_outcome(audited_universe, trades)
    if fresh_outcome_replay != payload.get("outcome_replay"):
        raise ValueError("artifact-native outcome replay mismatch")
    fresh_producer_code_binding = _producer_code_binding(audited_universe)
    if fresh_producer_code_binding != payload.get("producer_code_binding"):
        raise ValueError("artifact-native producer code binding mismatch")
    fresh_adjustment_factor_generation_binding = (
        _adjustment_factor_generation_binding(audited_universe, trades)
    )
    if fresh_adjustment_factor_generation_binding != payload.get(
        "adjustment_factor_generation_binding"
    ):
        raise ValueError("artifact-native adjustment factor generation binding mismatch")
    expected_proof_policy = _proof_policy(fresh_outcome_replay)
    if (payload.get("proof_policy") or {}).get(
        "blocked_sell_retry"
    ) != expected_proof_policy["blocked_sell_retry"]:
        raise ValueError("artifact-native sell retry policy mismatch")
    if payload.get("proof_policy") != expected_proof_policy:
        raise ValueError("artifact-native proof policy mismatch")
    expected_eligibility = _eligibility(
        trade_count=len(trades),
        strategy_signal_replay=fresh_strategy_signal_replay,
        strategy_entry_decision_binding=fresh_strategy_entry_decision_binding,
        producer_code_binding=fresh_producer_code_binding,
        adjustment_factor_generation_binding=(
            fresh_adjustment_factor_generation_binding
        ),
        outcome_replay=fresh_outcome_replay,
    )
    if payload.get("eligibility") != expected_eligibility:
        raise ValueError("artifact-native evidence eligibility is invalid")
    if (
        expected_eligibility["eligible_for_final_validation"] is not False
        or expected_eligibility["final_oos_eligible"] is not False
    ):
        raise ValueError("artifact-native final validation must remain sealed")
    return {**payload, "verified": True}
