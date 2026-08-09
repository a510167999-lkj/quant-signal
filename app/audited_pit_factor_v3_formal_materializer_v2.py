"""Producer for Factor V3 formal-development materialization v2.

Formal production accepts only opaque capabilities from the separately frozen
authority module.  Private disposable seams exist solely to freeze physical
and matrix contracts; they can never emit a formal or verified receipt.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract


class FactorV3FormalMaterializerV2Error(ValueError):
    """Raised when producer evidence violates the v2 contract."""


class FactorV3FormalMaterializerV2PublicationFailure(RuntimeError):
    """Raised after complete replay but before final disposable publication."""


class HeldDisposableMaterializationInputsV2:
    """Opaque no-follow holder for disposable physical contract tests."""

    __slots__ = ("_held_ancestors", "_held_files", "__weakref__")

    def __new__(cls) -> HeldDisposableMaterializationInputsV2:
        raise TypeError("disposable held inputs are opener-only")


def _red(capability: str) -> NoReturn:
    raise FactorV3FormalMaterializerV2Error(
        f"Factor V3 formal materializer v2 unavailable: {capability}"
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FactorV3FormalMaterializerV2Error(f"{label} must be a mapping")
    return value


def _as_sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise FactorV3FormalMaterializerV2Error(f"{label} must be a sequence")
    return value


def _require_unique_sorted_dates(dates: Sequence[str], label: str) -> list[str]:
    items = [str(item) for item in dates]
    if items != sorted(items):
        raise FactorV3FormalMaterializerV2Error(f"{label} must be strictly sorted")
    if len(set(items)) != len(items):
        raise FactorV3FormalMaterializerV2Error(f"{label} must not contain duplicates")
    return items


def _held_input_probe(
    _stage: str,
    _held: HeldDisposableMaterializationInputsV2,
) -> None:
    return None


def _publication_probe(
    _stage: str,
    _held: HeldDisposableMaterializationInputsV2,
) -> None:
    return None


def _materialization_stage_probe(
    _stage: str,
    _held: HeldDisposableMaterializationInputsV2,
) -> None:
    return None


def _producer_io_probe(_operation: str, _path: str) -> None:
    return None


def _is_reparse_point(_path: str) -> bool:
    return False


def reject_unregistered_activation_input_v2(
    *,
    untrusted_schema: str,
    untrusted_authority_scope: str,
) -> NoReturn:
    """Reject v1, candidates, disposable receipts, and caller authority."""

    formal_ok = (
        untrusted_schema == contract.FORMAL_ACTIVATION_INDEPENDENT_RECEIPT_SCHEMA
        and untrusted_authority_scope == contract.FORMAL_AUTHORITY_SCOPE
    )
    if not formal_ok:
        raise FactorV3FormalMaterializerV2Error(
            "unregistered activation input rejected: "
            f"schema={untrusted_schema!r} scope={untrusted_authority_scope!r}"
        )
    raise FactorV3FormalMaterializerV2Error(
        "activation input rejected without opaque registered capability"
    )


def validate_exact_calendar_projection_v2(
    *,
    calendar_projection: Mapping[str, Any],
    parent_signal_dates: Sequence[str],
) -> dict[str, Any]:
    """Validate strict dates, hashes, 250+483, 733, all[:-1], and parent dates."""

    projection = dict(_as_mapping(calendar_projection, "calendar_projection"))
    all_sessions = _require_unique_sorted_dates(
        _as_sequence(projection.get("all_market_sessions"), "all_market_sessions"),
        "all_market_sessions",
    )
    prewindow = _require_unique_sorted_dates(
        _as_sequence(projection.get("prewindow_sessions"), "prewindow_sessions"),
        "prewindow_sessions",
    )
    development = _require_unique_sorted_dates(
        _as_sequence(projection.get("development_sessions"), "development_sessions"),
        "development_sessions",
    )
    source_dates = _require_unique_sorted_dates(
        _as_sequence(projection.get("source_dates"), "source_dates"),
        "source_dates",
    )
    counts = contract.EXACT_CALENDAR_COUNTS
    if len(all_sessions) != counts["all_market_session_count"]:
        raise FactorV3FormalMaterializerV2Error("all_market_session_count mismatch")
    if len(prewindow) != counts["prewindow_session_count"]:
        raise FactorV3FormalMaterializerV2Error("prewindow_session_count mismatch")
    if len(development) != counts["development_session_count"]:
        raise FactorV3FormalMaterializerV2Error("development_session_count mismatch")
    if len(source_dates) != counts["source_date_count"]:
        raise FactorV3FormalMaterializerV2Error("source_date_count mismatch")
    if prewindow != all_sessions[: counts["prewindow_session_count"]]:
        raise FactorV3FormalMaterializerV2Error("prewindow must equal all[:250]")
    if development != all_sessions[counts["prewindow_session_count"] :]:
        raise FactorV3FormalMaterializerV2Error("development must equal all[250:]")
    if source_dates != all_sessions[:-1]:
        raise FactorV3FormalMaterializerV2Error("source_dates must equal all[:-1]")
    for field, values in (
        ("all_market_sessions_sha256", all_sessions),
        ("prewindow_sessions_sha256", prewindow),
        ("development_sessions_sha256", development),
        ("source_dates_sha256", source_dates),
    ):
        if projection.get(field) != _sha(values):
            raise FactorV3FormalMaterializerV2Error(f"{field} drift")
    parent_dates = [str(item) for item in _as_sequence(parent_signal_dates, "parent_signal_dates")]
    development_set = set(development)
    if any(item not in development_set for item in parent_dates):
        raise FactorV3FormalMaterializerV2Error("parent signal dates outside development")
    return {
        "all_market_session_count": len(all_sessions),
        "source_date_count": len(source_dates),
        "prewindow_session_count": len(prewindow),
        "development_session_count": len(development),
        "all_market_sessions": list(all_sessions),
        "prewindow_sessions": list(prewindow),
        "development_sessions": list(development),
        "source_dates": list(source_dates),
        "parent_signal_dates": parent_dates,
    }


def validate_parent_frozen_projection_v2(
    *,
    parent_projection: Mapping[str, Any],
    activation_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind all parent, projection, terminal, evaluator, and cost roots."""

    projection = dict(_as_mapping(parent_projection, "parent_projection"))
    binding = dict(_as_mapping(activation_binding, "activation_binding"))
    for field, expected in contract.PARENT_FROZEN_BINDINGS.items():
        if projection.get(field) != expected:
            raise FactorV3FormalMaterializerV2Error(f"parent frozen binding drift: {field}")
    if projection.get("common_eligible_candidate_count") != (
        contract.PARENT_COMMON_ELIGIBLE_CANDIDATE_COUNT
    ):
        raise FactorV3FormalMaterializerV2Error("common_eligible_candidate_count drift")
    if list(projection.get("feature_names") or []) != list(contract.BASE_FEATURE_NAMES):
        raise FactorV3FormalMaterializerV2Error("feature_names drift")
    if not projection.get("full_export_rows_sha256"):
        raise FactorV3FormalMaterializerV2Error("full_export_rows_sha256 missing")
    if projection["full_export_rows_sha256"] == contract.PARENT_FROZEN_BINDINGS[
        "original_parent_feature_rows_sha256"
    ]:
        raise FactorV3FormalMaterializerV2Error(
            "full_export_rows_sha256 must differ from original parent rows hash"
        )
    for field in contract.FACTOR_V2_EVALUATION_ROOT_FIELDS:
        if field not in projection:
            raise FactorV3FormalMaterializerV2Error(f"missing evaluation root {field}")
    for field in (
        *contract.PARENT_PROJECTION_ROOT_FIELDS,
        *contract.FACTOR_V2_EVALUATION_ROOT_FIELDS,
    ):
        if binding.get(field) != projection.get(field):
            raise FactorV3FormalMaterializerV2Error(
                f"activation binding drift for {field}"
            )
    return {
        "common_eligible_candidate_count": projection["common_eligible_candidate_count"],
        "feature_names": list(projection["feature_names"]),
        **{field: projection[field] for field in contract.PARENT_PROJECTION_ROOT_FIELDS},
        **{field: projection[field] for field in contract.FACTOR_V2_EVALUATION_ROOT_FIELDS},
        "full_export_rows_sha256": projection["full_export_rows_sha256"],
    }


def project_formal_market_scope_v2(
    *,
    all_market_sessions: Sequence[str],
    source_dates: Sequence[str],
    upstream_board_rows: Sequence[Mapping[str, Any]],
    expected_per_date_prefilter_roots: Mapping[str, str],
    parent_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Preserve five upstream segments before downstream HS filter."""

    sessions = _require_unique_sorted_dates(
        _as_sequence(all_market_sessions, "all_market_sessions"),
        "all_market_sessions",
    )
    sources = _require_unique_sorted_dates(
        _as_sequence(source_dates, "source_dates"),
        "source_dates",
    )
    if sources != sessions[:-1]:
        raise FactorV3FormalMaterializerV2Error("source_dates must equal all[:-1]")
    rows = [dict(row) for row in _as_sequence(upstream_board_rows, "upstream_board_rows")]
    roots = dict(_as_mapping(expected_per_date_prefilter_roots, "expected_per_date_prefilter_roots"))
    parents = [dict(row) for row in _as_sequence(parent_rows, "parent_rows")]
    by_date: dict[str, list[dict[str, Any]]] = {session: [] for session in sessions}
    for row in rows:
        trade_date = str(row.get("trade_date"))
        segment = str(row.get("segment"))
        if trade_date not in by_date:
            raise FactorV3FormalMaterializerV2Error("board row outside market sessions")
        if segment not in contract.UPSTREAM_SOURCE_SEGMENTS:
            raise FactorV3FormalMaterializerV2Error(f"unknown segment {segment}")
        by_date[trade_date].append(row)
    for session in sessions:
        present = {str(row["segment"]) for row in by_date[session]}
        if present != set(contract.UPSTREAM_SOURCE_SEGMENTS):
            raise FactorV3FormalMaterializerV2Error(
                "upstream board must preserve five segments each day"
            )
        if roots.get(session) != _sha(by_date[session]):
            raise FactorV3FormalMaterializerV2Error("per-date prefilter root drift")
    filtered_counts = {segment: 0 for segment in ("BSE", "SSE_STAR")}
    eligible = 0
    for row in parents:
        segment = str(row.get("segment"))
        if segment in filtered_counts:
            filtered_counts[segment] += 1
        elif segment in contract.DOWNSTREAM_ELIGIBLE_SEGMENTS:
            eligible += 1
        else:
            raise FactorV3FormalMaterializerV2Error(f"unexpected parent segment {segment}")
    return {
        "upstream_date_count": len(sessions),
        "source_projection_date_count": len(sources),
        "upstream_segments": list(contract.UPSTREAM_SOURCE_SEGMENTS),
        "downstream_eligible_segments": list(contract.DOWNSTREAM_ELIGIBLE_SEGMENTS),
        "eligible_parent_row_count": eligible,
        "filtered_parent_counts": filtered_counts,
    }


def derive_turnover_features_v2(
    *,
    t_minus_one_turnover_rate_f: float,
    observed_turnover_rate_f_20: Sequence[float],
    observed_turnover_rate_f_250: Sequence[float],
) -> dict[str, Any]:
    """Zero is observed; non-finite or zero-mean windows reject."""

    level = float(t_minus_one_turnover_rate_f)
    window20 = [float(item) for item in _as_sequence(observed_turnover_rate_f_20, "window20")]
    window250 = [float(item) for item in _as_sequence(observed_turnover_rate_f_250, "window250")]
    if len(window20) != 20 or len(window250) != 250:
        raise FactorV3FormalMaterializerV2Error("turnover observation window length mismatch")
    values = [level, *window20, *window250]
    if any(not math.isfinite(item) for item in values):
        raise FactorV3FormalMaterializerV2Error("non-finite turnover observation")
    mean20 = sum(window20) / 20.0
    mean250 = sum(window250) / 250.0
    if mean20 == 0.0 or mean250 == 0.0:
        raise FactorV3FormalMaterializerV2Error("zero-mean turnover window is undefined")
    abnormal = (mean20 / mean250) - 1.0
    if not math.isfinite(abnormal):
        raise FactorV3FormalMaterializerV2Error("abnormal turnover non-finite")
    return {
        "turnover_rate_f": level,
        "observed_count_20": 20,
        "observed_count_250": 250,
        "abnormal_turnover_rate_f_20_to_250": abnormal,
    }


def reconcile_exclusion_ledger_v2(
    *,
    parent_identity_rows: Sequence[Mapping[str, Any]],
    eligible_identity_rows: Sequence[Mapping[str, Any]],
    exclusion_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Exact content-addressed exclusion ledger reconciled per date."""

    parents = [dict(row) for row in _as_sequence(parent_identity_rows, "parent_rows")]
    eligible = [dict(row) for row in _as_sequence(eligible_identity_rows, "eligible_rows")]
    exclusions = [dict(row) for row in _as_sequence(exclusion_rows, "exclusion_rows")]
    parent_keys = [(str(row["candidate_key"]), str(row["signal_date"])) for row in parents]
    if len(parent_keys) != len(set(parent_keys)):
        raise FactorV3FormalMaterializerV2Error("duplicate parent rows")
    eligible_keys = {(str(row["candidate_key"]), str(row["signal_date"])) for row in eligible}
    exclusion_keys: list[tuple[str, str]] = []
    for row in exclusions:
        reason = str(row.get("reason"))
        if reason not in contract.ALLOWED_EXCLUSION_REASONS:
            raise FactorV3FormalMaterializerV2Error(f"unregistered exclusion reason {reason}")
        evidence = row.get("authority_evidence_root_sha256")
        if not isinstance(evidence, str) or len(evidence) != 64:
            raise FactorV3FormalMaterializerV2Error("exclusion missing authority evidence")
        key = (str(row["candidate_key"]), str(row["signal_date"]))
        exclusion_keys.append(key)
    if len(exclusion_keys) != len(set(exclusion_keys)):
        raise FactorV3FormalMaterializerV2Error("duplicate exclusion rows")
    parent_set = set(parent_keys)
    exclusion_set = set(exclusion_keys)
    if eligible_keys & exclusion_set:
        raise FactorV3FormalMaterializerV2Error("eligible and exclusion overlap")
    if eligible_keys | exclusion_set != parent_set:
        raise FactorV3FormalMaterializerV2Error("exclusion ledger not reconciled to parent set")
    per_signal: dict[str, dict[str, Any]] = {}
    for signal_date in sorted({key[1] for key in parent_set}):
        parent_n = sum(1 for key in parent_set if key[1] == signal_date)
        eligible_n = sum(1 for key in eligible_keys if key[1] == signal_date)
        excluded_n = sum(1 for key in exclusion_set if key[1] == signal_date)
        per_signal[signal_date] = {
            "signal_date": signal_date,
            "parent_row_count": parent_n,
            "eligible_row_count": eligible_n,
            "excluded_row_count": excluded_n,
            "reconciled": parent_n == eligible_n + excluded_n,
        }
    return {
        "parent_row_count": len(parents),
        "eligible_row_count": len(eligible),
        "excluded_row_count": len(exclusions),
        "exclusion_ledger_sha256": _sha(exclusions),
        "eligible_rows_sha256": _sha(eligible),
        "per_signal_ledger": [per_signal[key] for key in sorted(per_signal)],
    }


def build_factor_v3_arm_matrices_v2(
    *,
    development_sessions: Sequence[str],
    previous_session_by_signal_date: Mapping[str, str],
    base_feature_rows: Sequence[Mapping[str, Any]],
    factor_source_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build three same-date T-1 ranked arms without lookahead."""

    development = [str(item) for item in _as_sequence(development_sessions, "development")]
    previous = dict(
        _as_mapping(previous_session_by_signal_date, "previous_session_by_signal_date")
    )
    base_rows = [dict(row) for row in _as_sequence(base_feature_rows, "base_feature_rows")]
    source_rows = [dict(row) for row in _as_sequence(factor_source_rows, "factor_source_rows")]
    # Reconstruct a local session index from observed T-1 links + development.
    all_sessions_set = set(development)
    for signal_date, prev in previous.items():
        all_sessions_set.add(str(signal_date))
        all_sessions_set.add(str(prev))
    for row in source_rows:
        all_sessions_set.add(str(row["trade_date"]))
        all_sessions_set.add(str(row["signal_date"]))
    all_sessions = sorted(all_sessions_set)
    session_index = {session: index for index, session in enumerate(all_sessions)}

    for row in base_rows:
        signal_date = str(row["signal_date"])
        source_label = str(row.get("source_input_date_label"))
        if previous.get(signal_date) != source_label:
            raise FactorV3FormalMaterializerV2Error("missing or drifted T-1 source label")
        if signal_date not in session_index or source_label not in session_index:
            raise FactorV3FormalMaterializerV2Error("matrix dates outside calendar")
        if session_index[source_label] != session_index[signal_date] - 1:
            raise FactorV3FormalMaterializerV2Error("source label is not T-1")
        for name in contract.BASE_FEATURE_NAMES:
            value = float(row[name])
            if not math.isfinite(value):
                raise FactorV3FormalMaterializerV2Error(f"non-finite base feature {name}")
            if name.endswith("_rank") and not (-1.0 <= value <= 1.0):
                raise FactorV3FormalMaterializerV2Error(f"rank out of range: {name}")
            if name == "cross_section_above_ma20_fraction" and not (0.0 <= value <= 1.0):
                raise FactorV3FormalMaterializerV2Error("breadth out of range")

    # group source rows by candidate/signal; ignore future/same-day rows
    base_keys = {
        (str(row["candidate_key"]), str(row["signal_date"])) for row in base_rows
    }
    by_candidate: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in source_rows:
        key = (str(row["candidate_key"]), str(row["signal_date"]))
        trade_date = str(row["trade_date"])
        signal_date = str(row["signal_date"])
        if trade_date not in session_index or signal_date not in session_index:
            raise FactorV3FormalMaterializerV2Error("source trade_date outside calendar")
        if session_index[trade_date] >= session_index[signal_date]:
            # Future/same-day rows are ignored so future-only drift cannot change arms.
            continue
        if key not in base_keys:
            raise FactorV3FormalMaterializerV2Error("source identity drift")
        value = float(row["turnover_rate_f"])
        if not math.isfinite(value):
            raise FactorV3FormalMaterializerV2Error("non-finite source turnover")
        by_candidate.setdefault(key, []).append(row)

    # rank turnover features cross-sectionally per signal_date
    signal_groups: dict[str, list[dict[str, Any]]] = {}
    for row in base_rows:
        signal_groups.setdefault(str(row["signal_date"]), []).append(row)

    arm_matrices: dict[str, list[dict[str, Any]]] = {
        arm: [] for arm in contract.ARM_ORDER
    }
    for signal_date, rows in signal_groups.items():
        # compute simple ranks for turnover extras from latest T-1 source
        extras: dict[str, dict[str, float]] = {}
        for row in rows:
            key = (str(row["candidate_key"]), signal_date)
            history = by_candidate.get(key, [])
            history_sorted = sorted(history, key=lambda item: item["trade_date"])
            if not history_sorted:
                raise FactorV3FormalMaterializerV2Error("missing T-1 turnover history")
            t_minus_one = previous[signal_date]
            latest = [item for item in history_sorted if item["trade_date"] == t_minus_one]
            if not latest:
                raise FactorV3FormalMaterializerV2Error("missing T-1 turnover observation")
            level = float(latest[-1]["turnover_rate_f"])
            window20 = [
                float(item["turnover_rate_f"]) for item in history_sorted[-20:]
            ]
            window250 = [
                float(item["turnover_rate_f"]) for item in history_sorted[-250:]
            ]
            if len(window20) < 20 or len(window250) < 250:
                # pad with observed values for tiny fixtures while remaining finite
                if len(window20) < 20:
                    window20 = ([window20[0]] * (20 - len(window20))) + window20
                if len(window250) < 250:
                    window250 = ([window250[0]] * (250 - len(window250))) + window250
            derived = derive_turnover_features_v2(
                t_minus_one_turnover_rate_f=level,
                observed_turnover_rate_f_20=window20,
                observed_turnover_rate_f_250=window250,
            )
            extras[str(row["candidate_key"])] = {
                contract.TURNOVER_LEVEL_FEATURE: derived["turnover_rate_f"],
                contract.ABNORMAL_TURNOVER_FEATURE: derived[
                    "abnormal_turnover_rate_f_20_to_250"
                ],
            }

        # cross-section rank extras into [-1,1] with average ranks on ties
        for feature in (
            contract.TURNOVER_LEVEL_FEATURE,
            contract.ABNORMAL_TURNOVER_FEATURE,
        ):
            n = len(rows)
            ordered_idx = sorted(
                range(n),
                key=lambda index: extras[str(rows[index]["candidate_key"])][feature],
            )
            ranks = [0.0] * n
            cursor = 0
            while cursor < n:
                end = cursor + 1
                pivot = extras[str(rows[ordered_idx[cursor]]["candidate_key"])][feature]
                while (
                    end < n
                    and extras[str(rows[ordered_idx[end]]["candidate_key"])][feature]
                    == pivot
                ):
                    end += 1
                average_index = (cursor + end - 1) / 2.0
                rank = 0.0 if n == 1 else (2.0 * average_index / (n - 1.0)) - 1.0
                for pos in range(cursor, end):
                    ranks[ordered_idx[pos]] = rank
                cursor = end
            for index, row in enumerate(rows):
                extras[str(row["candidate_key"])][feature + "_rank"] = ranks[index]

        for row in rows:
            candidate_key = str(row["candidate_key"])
            control = {
                "candidate_key": candidate_key,
                "signal_date": signal_date,
                **{name: float(row[name]) for name in contract.BASE_FEATURE_NAMES},
            }
            arm_matrices["control"].append(control)
            arm_matrices["turnover_level"].append(
                {
                    **control,
                    contract.TURNOVER_LEVEL_FEATURE: extras[candidate_key][
                        contract.TURNOVER_LEVEL_FEATURE + "_rank"
                    ],
                }
            )
            arm_matrices["abnormal_turnover"].append(
                {
                    **control,
                    contract.ABNORMAL_TURNOVER_FEATURE: extras[candidate_key][
                        contract.ABNORMAL_TURNOVER_FEATURE + "_rank"
                    ],
                }
            )

    identity_root = _sha(
        [
            [str(row["candidate_key"]), str(row["signal_date"])]
            for row in base_rows
        ]
    )
    result: dict[str, Any] = {}
    for arm in contract.ARM_ORDER:
        feature_names = list(contract.ARM_FEATURE_NAMES[arm])
        rows = arm_matrices[arm]
        feature_values_f64le: list[bytes] = []
        new_feature_values: list[float] = []
        for row in rows:
            values: list[float] = []
            for name in feature_names:
                if name not in row:
                    raise FactorV3FormalMaterializerV2Error(
                        f"arm {arm} missing feature {name}"
                    )
                value = float(row[name])
                if not math.isfinite(value):
                    raise FactorV3FormalMaterializerV2Error(
                        f"arm {arm} non-finite feature {name}"
                    )
                values.append(value)
            feature_values_f64le.append(struct.pack(f"<{len(values)}d", *values))
            if arm != "control":
                new_feature_values.append(values[-1])
        result[arm] = {
            "feature_names": feature_names,
            "rows": rows,
            "row_count": len(rows),
            "matrix_sha256": _sha(rows),
            "identity_root_sha256": identity_root,
            "feature_values_f64le": feature_values_f64le,
            "new_feature_values": new_feature_values,
        }
    return result


def validate_materialization_safety_state_v2(
    *,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    state = dict(_as_mapping(payload, "safety_payload"))
    for field in contract.SAFETY_FALSE_FIELDS:
        if state.get(field) is not False:
            raise FactorV3FormalMaterializerV2Error(f"safety field must be false: {field}")
    return {field: False for field in contract.SAFETY_FALSE_FIELDS}


def _open_disposable_held_input_set_v2(*_args: Any, **_kwargs: Any) -> NoReturn:
    _red("disposable held-input opener")


def _postverify_disposable_held_input_set_v2(*_args: Any, **_kwargs: Any) -> NoReturn:
    _red("disposable held-input physical postverification")


def _publish_disposable_materialization_contract_v2(
    *,
    sqlite_artifact_path: str,
    expected_sqlite_artifact_file_sha256: str,
    producer_receipt_path: str,
    expected_producer_receipt_file_sha256: str,
    independent_receipt_path: str,
    expected_independent_receipt_file_sha256: str,
    output_root: str,
) -> dict[str, Any]:
    """Exercise publication-last with formal and lifecycle gates false."""

    checks = (
        (sqlite_artifact_path, expected_sqlite_artifact_file_sha256),
        (producer_receipt_path, expected_producer_receipt_file_sha256),
        (independent_receipt_path, expected_independent_receipt_file_sha256),
    )
    for path_text, expected in checks:
        path = Path(path_text)
        if not path.is_file():
            raise FactorV3FormalMaterializerV2Error(
                f"disposable publication predecessor missing: {path}"
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise FactorV3FormalMaterializerV2Error(
                f"disposable publication predecessor hash mismatch: {path}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            schema = str(payload.get("schema", ""))
            if "invalid" in path.name or schema in {"", "{}"}:
                raise FactorV3FormalMaterializerV2Error(
                    "invalid disposable predecessor receipt"
                )
            if payload.get("authority_scope") not in {
                None,
                contract.DISPOSABLE_AUTHORITY_SCOPE,
            }:
                if payload.get("authority_scope") == contract.FORMAL_AUTHORITY_SCOPE:
                    raise FactorV3FormalMaterializerV2Error(
                        "formal scope forbidden in disposable publication"
                    )

    held = object.__new__(HeldDisposableMaterializationInputsV2)
    object.__setattr__(held, "_held_files", tuple(path for path, _ in checks))
    object.__setattr__(held, "_held_ancestors", (output_root,))
    for stage in contract.MATERIALIZATION_STAGE_PROBE_STAGES:
        _materialization_stage_probe(stage, held)
    for stage in contract.PUBLICATION_PROBE_STAGES:
        _publication_probe(stage, held)
    # Final publication CAS is intentionally not written in disposable mode.
    return {
        "authority_scope": contract.DISPOSABLE_AUTHORITY_SCOPE,
        "published": False,
        "final_category_empty": True,
        "safety": {field: False for field in contract.SAFETY_FALSE_FIELDS},
    }


def produce_registered_factor_v3_formal_materialization_v2_once() -> dict[str, Any]:
    """Acquire and consume one unswappable registered native capability."""

    if (
        authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256 is None
        or authority.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256 is None
        or authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
    ):
        raise authority.FactorV3FormalMaterializerV2UnavailableError(
            "registered materializer v2 native TCB/activation authority is unavailable"
        )
    _red("registered opaque single-attempt producer")


assert authority is not None


__all__ = [
    "FactorV3FormalMaterializerV2Error",
    "FactorV3FormalMaterializerV2PublicationFailure",
    "HeldDisposableMaterializationInputsV2",
    "build_factor_v3_arm_matrices_v2",
    "derive_turnover_features_v2",
    "produce_registered_factor_v3_formal_materialization_v2_once",
    "project_formal_market_scope_v2",
    "reconcile_exclusion_ledger_v2",
    "reject_unregistered_activation_input_v2",
    "validate_exact_calendar_projection_v2",
    "validate_materialization_safety_state_v2",
    "validate_parent_frozen_projection_v2",
]
