from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import inspect
import math
import os
from pathlib import Path
import stat
from typing import Any

import pytest

from app import audited_pit_factor_v3_formal_materializer_v2 as producer
from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract
from tests._factor_v3_formal_materializer_v2_disposable import (
    canonical_bytes,
    create_disposable_materialization_fixture,
    sha_bytes,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _calendar_projection() -> dict[str, Any]:
    all_sessions = [
        (date(2024, 1, 1) + timedelta(days=index)).isoformat()
        for index in range(733)
    ]
    prewindow = all_sessions[:250]
    development = all_sessions[250:]
    source_dates = all_sessions[:-1]
    return {
        "authority_scope": contract.DISPOSABLE_AUTHORITY_SCOPE,
        "all_market_session_count": len(all_sessions),
        "all_market_sessions": all_sessions,
        "all_market_sessions_sha256": _sha(all_sessions),
        "prewindow_session_count": len(prewindow),
        "prewindow_sessions": prewindow,
        "prewindow_sessions_sha256": _sha(prewindow),
        "development_session_count": len(development),
        "development_sessions": development,
        "development_sessions_sha256": _sha(development),
        "source_date_count": len(source_dates),
        "source_dates": source_dates,
        "source_dates_sha256": _sha(source_dates),
    }


def _board_fixture() -> dict[str, Any]:
    calendar = _calendar_projection()
    all_sessions = calendar["all_market_sessions"]
    source_dates = calendar["source_dates"]
    rows = [
        {
            "trade_date": trade_date,
            "segment": segment,
            "segment_row_count": 1,
            "segment_rows_sha256": _sha([trade_date, segment, 1]),
        }
        for trade_date in all_sessions
        for segment in contract.UPSTREAM_SOURCE_SEGMENTS
    ]
    roots = {
        trade_date: _sha(
            [row for row in rows if row["trade_date"] == trade_date]
        )
        for trade_date in all_sessions
    }
    codes = {
        "BSE": "430047.BJ",
        "SSE_MAIN": "600000.SH",
        "SSE_STAR": "688001.SH",
        "SZSE_CHINEXT": "300001.SZ",
        "SZSE_MAIN": "000001.SZ",
    }
    parent_rows = [
        {
            "candidate_key": f"cn-a-share:{codes[segment]}|{trade_date}",
            "signal_date": trade_date,
            "segment": segment,
            "ts_code": codes[segment],
        }
        for trade_date in calendar["development_sessions"]
        for segment in contract.UPSTREAM_SOURCE_SEGMENTS
    ]
    return {
        "all_market_sessions": all_sessions,
        "source_dates": source_dates,
        "upstream_board_rows": rows,
        "expected_per_date_prefilter_roots": roots,
        "parent_rows": parent_rows,
    }


def _parent_projection() -> tuple[dict[str, Any], dict[str, Any]]:
    evaluation = {
        field: _sha(["factor-v2-evaluation", field])
        for field in contract.FACTOR_V2_EVALUATION_ROOT_FIELDS
    }
    projection = {
        **contract.PARENT_FROZEN_BINDINGS,
        "common_eligible_candidate_count": (
            contract.PARENT_COMMON_ELIGIBLE_CANDIDATE_COUNT
        ),
        "full_export_rows_sha256": _sha(
            ["common-eligible-full-export", 1_796_834]
        ),
        "feature_names": list(contract.BASE_FEATURE_NAMES),
        **evaluation,
    }
    activation_binding = {
        field: projection[field]
        for field in (
            *contract.PARENT_PROJECTION_ROOT_FIELDS,
            *contract.FACTOR_V2_EVALUATION_ROOT_FIELDS,
        )
    }
    return projection, activation_binding


def _matrix_fixture() -> dict[str, Any]:
    calendar = _calendar_projection()
    sessions = calendar["all_market_sessions"]
    development = calendar["development_sessions"]
    signal_dates = development[:2]
    previous = {
        signal_date: sessions[sessions.index(signal_date) - 1]
        for signal_date in signal_dates
    }
    codes = ("000001.SZ", "600000.SH")
    base_rows = []
    source_rows = []
    for signal_date in signal_dates:
        signal_position = sessions.index(signal_date)
        window = sessions[signal_position - 250 : signal_position]
        for code_index, ts_code in enumerate(codes):
            candidate_key = f"cn-a-share:{ts_code}|{signal_date}"
            rank = -1.0 / 3.0 if code_index == 0 else 1.0 / 3.0
            base_rows.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "source_input_date_label": previous[signal_date],
                    **{name: rank for name in contract.BASE_FEATURE_NAMES[:8]},
                    "cross_section_above_ma20_fraction": 0.5,
                    "cross_section_median_return_5d_pct": -2.5,
                }
            )
            for source_position, trade_date in enumerate(window):
                source_rows.append(
                    {
                        "candidate_key": candidate_key,
                        "signal_date": signal_date,
                        "trade_date": trade_date,
                        "turnover_rate_f": float(
                            1 + code_index + (source_position % 7) / 10
                        ),
                    }
                )
            source_rows.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "trade_date": signal_date,
                    "turnover_rate_f": 999_999.0,
                }
            )
    return {
        "development_sessions": development,
        "previous_session_by_signal_date": previous,
        "base_feature_rows": base_rows,
        "factor_source_rows": source_rows,
    }


@pytest.mark.parametrize(
    "case",
    (
        "activation-v1",
        "candidate-v3",
        "disposable-v2",
        "caller-dict-capability",
        "object-new-capability",
    ),
)
def test_rejects_unregistered_activation_and_unforgeable_capability(
    case: str,
) -> None:
    required_transitive = {
        "run_claim_raw_sha256",
        "run_receipt_raw_sha256",
        "verify_claim_raw_sha256",
        "terminal_receipt_raw_sha256",
        "parent_producer_raw_sha256",
        "parent_verifier_raw_sha256",
        "evaluator_producer_raw_sha256",
        "evaluator_verifier_raw_sha256",
        "compound_run_receipt_raw_sha256",
        "compound_terminal_receipt_raw_sha256",
        "native_run_completion_raw_sha256",
        "native_verify_completion_raw_sha256",
        "compound_native_terminal_raw_sha256",
        "compound_publication_raw_sha256",
        "activation_descriptor_raw_sha256",
        "activation_publication_raw_sha256",
        "activation_independent_receipt_raw_sha256",
    }
    assert required_transitive <= set(
        contract.REGISTERED_ACTIVATION_EXACT_RAW_CLOSURE_FIELDS
    )
    assert set(contract.CAS_DESCRIPTOR_FIELDS) == {
        "byte_size",
        "category",
        "file_id_commitment",
        "file_sha256",
        "payload_root_sha256",
        "registered_root_id",
        "relative_path",
        "role",
        "schema",
    }
    if case == "activation-v1":
        producer.reject_unregistered_activation_input_v2(
            untrusted_schema=(
                "factor-v3-formal-development-input-activation-independent-verifier-receipt/v1"
            ),
            untrusted_authority_scope=contract.FORMAL_AUTHORITY_SCOPE,
        )
    elif case == "candidate-v3":
        producer.reject_unregistered_activation_input_v2(
            untrusted_schema="factor-v3-development-input-authority-publication/v3",
            untrusted_authority_scope=contract.FORMAL_AUTHORITY_SCOPE,
        )
    elif case == "disposable-v2":
        producer.reject_unregistered_activation_input_v2(
            untrusted_schema=contract.FORMAL_ACTIVATION_INDEPENDENT_RECEIPT_SCHEMA,
            untrusted_authority_scope=contract.DISPOSABLE_AUTHORITY_SCOPE,
        )
    else:
        fake: object
        if case == "caller-dict-capability":
            fake = {field: True for field in contract.ACTIVATION_V2_REQUIRED_TRUE_FIELDS}
        else:
            try:
                fake = object.__new__(
                    authority.RegisteredMaterializationAttemptAuthorityV2
                )
            except TypeError:
                fake = object()
        assert fake is not None
        assert not inspect.signature(
            producer.produce_registered_factor_v3_formal_materialization_v2_once
        ).parameters
        io_calls: list[tuple[str, str]] = []

        def io_probe(operation: str, path: str) -> None:
            io_calls.append((operation, path))

        original_probe = producer._producer_io_probe
        producer._producer_io_probe = io_probe
        try:
            with pytest.raises(
                authority.FactorV3FormalMaterializerV2UnavailableError
            ):
                producer.produce_registered_factor_v3_formal_materialization_v2_once()
        finally:
            producer._producer_io_probe = original_probe
            assert io_calls == []


@pytest.mark.parametrize(
    "mutation",
    (
        "none",
        "reordered",
        "duplicate",
        "prewindow-splice",
        "development-splice",
        "source-not-all-minus-one",
        "list-hash-drift",
        "parent-outside-development",
    ),
)
def test_calendar_binds_strict_lists_hashes_slices_and_parent_dates(
    mutation: str,
) -> None:
    projection = _calendar_projection()
    parent_dates = projection["development_sessions"][:3]
    if mutation == "reordered":
        projection["all_market_sessions"][0:2] = reversed(
            projection["all_market_sessions"][0:2]
        )
    elif mutation == "duplicate":
        projection["all_market_sessions"][1] = projection["all_market_sessions"][0]
    elif mutation == "prewindow-splice":
        projection["prewindow_sessions"][0] = projection["development_sessions"][0]
    elif mutation == "development-splice":
        projection["development_sessions"][0] = projection["prewindow_sessions"][-1]
    elif mutation == "source-not-all-minus-one":
        projection["source_dates"][-1] = projection["all_market_sessions"][-1]
    elif mutation == "list-hash-drift":
        projection["source_dates_sha256"] = _sha("drifted-source-dates")
    elif mutation == "parent-outside-development":
        parent_dates = [projection["prewindow_sessions"][-1]]

    if mutation != "none":
        with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
            producer.validate_exact_calendar_projection_v2(
                calendar_projection=projection,
                parent_signal_dates=parent_dates,
            )
        return
    result = producer.validate_exact_calendar_projection_v2(
        calendar_projection=projection,
        parent_signal_dates=parent_dates,
    )
    assert result["all_market_session_count"] == 733
    assert result["source_date_count"] == 732
    assert result["prewindow_sessions"] == result["all_market_sessions"][:250]
    assert result["development_sessions"] == result["all_market_sessions"][250:]
    assert result["source_dates"] == result["all_market_sessions"][:-1]


@pytest.mark.parametrize(
    "mutation",
    ("none", "missing-segment", "wrong-date-root", "source-drift", "filter-too-early"),
)
def test_board_ledger_preserves_five_segments_for_733_days_before_filter(
    mutation: str,
) -> None:
    fixture = _board_fixture()
    if mutation == "missing-segment":
        fixture["upstream_board_rows"].pop(0)
    elif mutation == "wrong-date-root":
        first = fixture["all_market_sessions"][0]
        fixture["expected_per_date_prefilter_roots"][first] = _sha("drift")
    elif mutation == "source-drift":
        fixture["source_dates"][-1] = fixture["all_market_sessions"][-1]
    elif mutation == "filter-too-early":
        fixture["upstream_board_rows"] = [
            row
            for row in fixture["upstream_board_rows"]
            if row["segment"] not in {"BSE", "SSE_STAR"}
        ]

    if mutation != "none":
        with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
            producer.project_formal_market_scope_v2(**fixture)
        return
    result = producer.project_formal_market_scope_v2(**fixture)
    assert result["upstream_date_count"] == 733
    assert result["source_projection_date_count"] == 732
    assert result["upstream_segments"] == list(contract.UPSTREAM_SOURCE_SEGMENTS)
    assert result["eligible_parent_row_count"] == 483 * 3
    assert result["filtered_parent_counts"] == {"BSE": 483, "SSE_STAR": 483}


@pytest.mark.parametrize(
    "mutation",
    (
        "none",
        *tuple(contract.PARENT_FROZEN_BINDINGS),
        "full_export_rows_sha256",
        *contract.FACTOR_V2_EVALUATION_ROOT_FIELDS,
        "common_eligible_candidate_count",
        "feature_names",
    ),
)
def test_parent_binds_all_frozen_dynamic_and_terminal_evaluator_roots(
    mutation: str,
) -> None:
    projection, activation_binding = _parent_projection()
    assert (
        projection["full_export_rows_sha256"]
        != contract.PARENT_FROZEN_BINDINGS["original_parent_feature_rows_sha256"]
    )
    if mutation in projection and mutation != "none":
        value = projection[mutation]
        if isinstance(value, int):
            projection[mutation] = value - 1
        elif isinstance(value, list):
            projection[mutation] = list(reversed(value))
        else:
            projection[mutation] = _sha(["drift", mutation])

    if mutation != "none":
        with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
            producer.validate_parent_frozen_projection_v2(
                parent_projection=projection,
                activation_binding=activation_binding,
            )
        return
    result = producer.validate_parent_frozen_projection_v2(
        parent_projection=projection,
        activation_binding=activation_binding,
    )
    assert result["common_eligible_candidate_count"] == 1_796_834
    assert result["feature_names"] == list(contract.BASE_FEATURE_NAMES)
    assert all(field in result for field in contract.PARENT_PROJECTION_ROOT_FIELDS)
    assert all(
        field in result for field in contract.FACTOR_V2_EVALUATION_ROOT_FIELDS
    )


@pytest.mark.parametrize(
    ("case", "level", "observed_20", "observed_250", "reject"),
    (
        ("finite-zero-observed", 0.0, [0.0, *([1.0] * 19)], [0.0, *([1.0] * 249)], False),
        ("nan", math.nan, [1.0] * 20, [1.0] * 250, True),
        ("infinity", math.inf, [1.0] * 20, [1.0] * 250, True),
        ("zero-short-mean", 0.0, [0.0] * 20, [1.0] * 250, True),
        ("zero-long-mean", 0.0, [1.0] * 20, [0.0] * 250, True),
    ),
)
def test_turnover_zero_counts_as_observed_but_undefined_or_nonfinite_rejects(
    case: str,
    level: float,
    observed_20: list[float],
    observed_250: list[float],
    reject: bool,
) -> None:
    if reject:
        with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
            producer.derive_turnover_features_v2(
                t_minus_one_turnover_rate_f=level,
                observed_turnover_rate_f_20=observed_20,
                observed_turnover_rate_f_250=observed_250,
            )
        return
    result = producer.derive_turnover_features_v2(
        t_minus_one_turnover_rate_f=level,
        observed_turnover_rate_f_20=observed_20,
        observed_turnover_rate_f_250=observed_250,
    )
    assert case == "finite-zero-observed"
    assert result["turnover_rate_f"] == 0.0
    assert result["observed_count_20"] == 20
    assert result["observed_count_250"] == 250
    assert math.isfinite(result["abnormal_turnover_rate_f_20_to_250"])


@pytest.mark.parametrize(
    "mutation",
    ("none", "unregistered-reason", "missing-evidence", "duplicate", "unreconciled"),
)
def test_exclusion_ledger_is_exact_content_addressed_and_per_date_reconciled(
    mutation: str,
) -> None:
    parent = [
        {"candidate_key": f"candidate-{index}", "signal_date": "2026-01-05"}
        for index in range(3)
    ]
    eligible = [parent[0]]
    exclusions = [
        {
            **parent[1],
            "reason": contract.ALLOWED_EXCLUSION_REASONS[0],
            "authority_evidence_root_sha256": _sha("listing-authority"),
        },
        {
            **parent[2],
            "reason": contract.ALLOWED_EXCLUSION_REASONS[2],
            "authority_evidence_root_sha256": _sha("daily-basic-authority"),
        },
    ]
    if mutation == "unregistered-reason":
        exclusions[0]["reason"] = "silent-row-drop"
    elif mutation == "missing-evidence":
        exclusions[0].pop("authority_evidence_root_sha256")
    elif mutation == "duplicate":
        exclusions.append(deepcopy(exclusions[0]))
    elif mutation == "unreconciled":
        exclusions.pop()
    if mutation != "none":
        with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
            producer.reconcile_exclusion_ledger_v2(
                parent_identity_rows=parent,
                eligible_identity_rows=eligible,
                exclusion_rows=exclusions,
            )
        return
    result = producer.reconcile_exclusion_ledger_v2(
        parent_identity_rows=parent,
        eligible_identity_rows=eligible,
        exclusion_rows=exclusions,
    )
    assert result["parent_row_count"] == 3
    assert result["eligible_row_count"] == 1
    assert result["excluded_row_count"] == 2
    assert result["per_signal_ledger"][0]["reconciled"] is True


@pytest.mark.parametrize(
    "mutation",
    (
        "none",
        "rank-out-of-range",
        "breadth-out-of-range",
        "nonfinite-context",
        "nonfinite-source",
        "missing-t-minus-one",
        "identity-drift",
        "tied-cross-section",
        "future-only-drift",
    ),
)
def test_matrices_are_three_arm_finite_ranked_same_date_tminus1_and_no_lookahead(
    mutation: str,
) -> None:
    fixture = _matrix_fixture()
    if mutation == "rank-out-of-range":
        fixture["base_feature_rows"][0][contract.BASE_FEATURE_NAMES[0]] = 1.01
    elif mutation == "breadth-out-of-range":
        fixture["base_feature_rows"][0]["cross_section_above_ma20_fraction"] = 1.01
    elif mutation == "nonfinite-context":
        fixture["base_feature_rows"][0]["cross_section_median_return_5d_pct"] = math.inf
    elif mutation == "nonfinite-source":
        fixture["factor_source_rows"][0]["turnover_rate_f"] = math.nan
    elif mutation == "missing-t-minus-one":
        first = fixture["base_feature_rows"][0]
        previous = fixture["previous_session_by_signal_date"][first["signal_date"]]
        fixture["factor_source_rows"] = [
            row
            for row in fixture["factor_source_rows"]
            if not (
                row["candidate_key"] == first["candidate_key"]
                and row["trade_date"] == previous
            )
        ]
    elif mutation == "identity-drift":
        fixture["factor_source_rows"][0]["candidate_key"] = "drifted-candidate"
    elif mutation == "tied-cross-section":
        for row in fixture["factor_source_rows"]:
            if row["trade_date"] != row["signal_date"]:
                row["turnover_rate_f"] = 1.0
    elif mutation == "future-only-drift":
        baseline = deepcopy(fixture)
        for row in fixture["factor_source_rows"]:
            if row["trade_date"] == row["signal_date"]:
                row["turnover_rate_f"] *= 10.0
        expected = producer.build_factor_v3_arm_matrices_v2(**baseline)
        actual = producer.build_factor_v3_arm_matrices_v2(**fixture)
        assert actual == expected
        return

    if mutation in {
        "rank-out-of-range",
        "breadth-out-of-range",
        "nonfinite-context",
        "nonfinite-source",
        "missing-t-minus-one",
        "identity-drift",
    }:
        with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
            producer.build_factor_v3_arm_matrices_v2(**fixture)
        return
    matrices = producer.build_factor_v3_arm_matrices_v2(**fixture)
    assert tuple(matrices) == contract.ARM_ORDER
    assert {arm: len(value["feature_names"]) for arm, value in matrices.items()} == {
        "control": 10,
        "turnover_level": 11,
        "abnormal_turnover": 11,
    }
    assert "combined" not in matrices
    assert len({value["identity_root_sha256"] for value in matrices.values()}) == 1
    assert all(
        len(raw) == 8 * len(matrices[arm]["feature_names"])
        for arm in contract.ARM_ORDER
        for raw in matrices[arm]["feature_values_f64le"]
    )
    if mutation == "tied-cross-section":
        for arm in ("turnover_level", "abnormal_turnover"):
            assert matrices[arm]["new_feature_values"] == [0.0] * 4


@pytest.mark.parametrize(
    "tamper",
    (
        "reparse",
        "hardlink",
        "mid-read",
        "aba",
        "dacl",
        "ancestor",
        "extra-staging",
        "wal",
        "shm",
    ),
)
def test_low_level_valid_held_opener_rejects_every_physical_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    fixture = create_disposable_materialization_fixture(tmp_path)
    target = fixture.producer_receipt_path
    original = target.read_bytes()

    if tamper == "reparse":
        monkeypatch.setattr(
            producer,
            "_is_reparse_point",
            lambda path: Path(path) == target,
        )

    def tamper_after_read(
        stage: str,
        _held: producer.HeldDisposableMaterializationInputsV2,
    ) -> None:
        if stage != "after-first-read-before-postverify" or tamper == "reparse":
            return
        if tamper == "hardlink":
            os.link(target, target.with_suffix(".hardlink"))
        elif tamper == "mid-read":
            target.write_bytes(b"x" * len(original))
        elif tamper == "aba":
            target.unlink()
            target.write_bytes(original)
        elif tamper == "dacl":
            os.chmod(target, stat.S_IREAD)
        elif tamper == "ancestor":
            root = target.parent
            moved = root.with_name(f"{root.name}-moved")
            root.rename(moved)
            root.mkdir()
        elif tamper == "extra-staging":
            (fixture.output_root / ".unexpected-staging.tmp").write_bytes(b"x")
        elif tamper == "wal":
            Path(f"{fixture.sqlite_artifact_path}-wal").write_bytes(b"x")
        else:
            Path(f"{fixture.sqlite_artifact_path}-shm").write_bytes(b"x")

    monkeypatch.setattr(producer, "_held_input_probe", tamper_after_read)
    assert {
        "no_reparse_point",
        "hardlink_count_is_one",
        "file_id_unchanged",
        "owner_dacl_unchanged",
        "ancestor_file_ids_unchanged",
        "staging_namespace_empty",
    } <= set(contract.HELD_INPUT_POSTVERIFY_FIELDS)

    with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
        producer._open_disposable_held_input_set_v2(**fixture.producer_kwargs())


@pytest.mark.parametrize("case", ("invalid-predecessor", "full-closure-injected-failure"))
def test_publication_is_last_hook_zero_or_one_and_final_category_stays_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    fixture = create_disposable_materialization_fixture(tmp_path)
    publication_calls: list[str] = []
    stage_calls: list[str] = []
    assert contract.PUBLICATION_STAGE_ORDER == (
        "atomic-create-global-attempt-claim",
        "hold-and-preverify-all-inputs",
        "create-sqlite-artifact-cas",
        "create-producer-receipt-cas",
        "create-native-run-receipt-cas",
        "independent-verifier-replay",
        "create-independent-verifier-receipt-cas",
        "create-native-verify-receipt-cas",
        "create-native-terminal-receipt-cas",
        "postverify-all-held-inputs",
        "create-final-publication-cas",
    )
    assert contract.MATERIALIZATION_STAGE_PROBE_STAGES == (
        contract.PUBLICATION_STAGE_ORDER[:-1]
    )
    assert contract.FAILURE_STAGE_ORDER == ("create-failure-terminal-cas",)

    def stage_probe(
        stage: str,
        _held: producer.HeldDisposableMaterializationInputsV2,
    ) -> None:
        stage_calls.append(stage)

    def probe(
        stage: str,
        _held: producer.HeldDisposableMaterializationInputsV2,
    ) -> None:
        assert tuple(stage_calls) == contract.MATERIALIZATION_STAGE_PROBE_STAGES
        publication_calls.append(stage)
        raise producer.FactorV3FormalMaterializerV2PublicationFailure(
            "injected before final publication"
        )

    monkeypatch.setattr(producer, "_materialization_stage_probe", stage_probe)
    monkeypatch.setattr(producer, "_publication_probe", probe)
    kwargs = fixture.producer_kwargs()
    if case == "invalid-predecessor":
        invalid = fixture.producer_receipt_path.with_name("invalid-receipt.json")
        invalid.write_bytes(b"{}")
        kwargs["producer_receipt_path"] = str(invalid)
        kwargs["expected_producer_receipt_file_sha256"] = sha_bytes(b"{}")
        try:
            with pytest.raises(producer.FactorV3FormalMaterializerV2Error):
                producer._publish_disposable_materialization_contract_v2(**kwargs)
        finally:
            assert stage_calls == []
            assert publication_calls == []
    else:
        with pytest.raises(producer.FactorV3FormalMaterializerV2PublicationFailure):
            producer._publish_disposable_materialization_contract_v2(**kwargs)
        assert tuple(stage_calls) == contract.MATERIALIZATION_STAGE_PROBE_STAGES
        assert publication_calls == [
            "after-all-postverification-before-final-publication"
        ]
    publication_root = fixture.output_root / contract.DISPOSABLE_PUBLICATION_CATEGORY
    assert not publication_root.exists() or not tuple(publication_root.rglob("*"))
    assert contract.PUBLICATION_STAGE_ORDER[-1] == "create-final-publication-cas"
    assert contract.PUBLICATION_PROBE_STAGES == (
        "after-all-postverification-before-final-publication",
    )
