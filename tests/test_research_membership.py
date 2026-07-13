from __future__ import annotations

import inspect
import json

import pytest

from app.research_membership import (
    MEMBERSHIP_GENERATION_SCHEMA_VERSION,
    MEMBERSHIP_POLICY_VERSION,
    MembershipContractError,
    build_membership_manifest,
    classify_membership_snapshot_body,
    derive_quarantined_membership,
    verify_membership_manifest,
)


BAK_BASIC_FIELDS = ("trade_date", "ts_code", "name", "industry", "list_date")
SHA_A = "a" * 64
SHA_B = "b" * 64


def _body(*, code: int = 0, fields: list[str] | None = None, items=None) -> bytes:
    payload = {
        "code": code,
        "data": {
            "fields": list(fields if fields is not None else BAK_BASIC_FIELDS),
            "items": [] if items is None else items,
        },
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def _known_member(ts_code: str = "600001.SH", **overrides):
    row = {
        "trade_date": "2016-09-30",
        "ts_code": ts_code,
        "exchange": "SSE",
        "name": "Known",
        "industry": "Industry",
        "list_date": "2000-01-01",
    }
    row.update(overrides)
    return row


def _projection_rows():
    return derive_quarantined_membership(
        trade_date="2016-10-10",
        anchor_trade_date="2016-09-30",
        anchor_rows=[_known_member()],
        daily_codes={"600001.SH", "600002.SH"},
    ).rows


def _manifest_kwargs():
    return {
        "trade_date": "2016-10-10",
        "source_kind": "causal_carry_forward",
        "anchor": {
            "trade_date": "2016-09-30",
            "receipt_dataset": "bak_basic",
            "receipt_partition": "2016-09-30",
            "receipt_raw_sha256": SHA_B,
            "normalized_sha256": SHA_A,
            "row_count": 1,
            "in_scope_row_count": 1,
            "temporal_role": "development",
            "temporal_contract_sha256": SHA_B,
        },
        "empty_evidence": [
            {
                "trade_date": "2016-10-10",
                "attempt_id": "empty-attempt-2",
                "raw_sha256": SHA_B,
                "request_semantics_sha256": SHA_A,
                "terminal_status": "invalid_json",
                "classification_kind": "semantic_empty",
                "temporal_role": "development",
                "temporal_contract_sha256": SHA_B,
            },
            {
                "trade_date": "2016-10-10",
                "attempt_id": "empty-attempt-1",
                "raw_sha256": SHA_A,
                "request_semantics_sha256": SHA_B,
                "terminal_status": "invalid_json",
                "classification_kind": "semantic_empty",
                "temporal_role": "development",
                "temporal_contract_sha256": SHA_B,
            },
        ],
        "market_generation": {
            "trade_date": "2016-10-10",
            "generation_id": "market-generation",
            "manifest_sha256": SHA_A,
            "lineage_sha256": SHA_B,
            "temporal_role": "development",
            "temporal_contract_sha256": SHA_B,
        },
        "temporal_authority": {
            "temporal_role": "development",
            "temporal_contract_sha256": SHA_B,
        },
        "rows": _projection_rows(),
    }


def _preanchor_manifest_kwargs():
    kwargs = _manifest_kwargs()
    kwargs["source_kind"] = "pre_anchor_quarantine"
    kwargs["anchor"] = None
    kwargs["rows"] = derive_quarantined_membership(
        trade_date="2016-10-10",
        anchor_trade_date=None,
        anchor_rows=[],
        daily_codes={"600002.SH"},
    ).rows
    return kwargs


def test_contract_versions_are_frozen():
    assert MEMBERSHIP_GENERATION_SCHEMA_VERSION == "membership-session-generations/v1"
    assert MEMBERSHIP_POLICY_VERSION == "strictly-past-anchor-full-session-buy-quarantine/v1"


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"\xff",
        b"[]",
        b'{"code":0,"data":[]}',
        b'{"code":0,"data":{"fields":[],"items":{}}}',
        b'{"code":true,"data":{"fields":[],"items":[]}}',
    ],
)
def test_classifier_rejects_malformed_or_wrong_shape(raw):
    with pytest.raises(MembershipContractError):
        classify_membership_snapshot_body(raw, required_fields=())


def test_classifier_accepts_bytes_only():
    with pytest.raises(MembershipContractError):
        classify_membership_snapshot_body("{}", required_fields=())  # type: ignore[arg-type]


def test_classifier_rejects_duplicate_json_keys():
    raw = b'{"code":0,"data":{"fields":[],"items":[]},"code":0}'
    with pytest.raises(MembershipContractError, match="duplicate JSON key"):
        classify_membership_snapshot_body(raw, required_fields=())


def test_classifier_rejects_nonzero_response_code():
    with pytest.raises(MembershipContractError, match="response code"):
        classify_membership_snapshot_body(_body(code=-1), required_fields=BAK_BASIC_FIELDS)


def test_classifier_rejects_missing_and_duplicate_fields():
    with pytest.raises(MembershipContractError, match="required fields"):
        classify_membership_snapshot_body(
            _body(fields=["trade_date", "ts_code"]),
            required_fields=BAK_BASIC_FIELDS,
        )
    with pytest.raises(MembershipContractError, match="duplicate field"):
        classify_membership_snapshot_body(
            _body(fields=[*BAK_BASIC_FIELDS, "ts_code"]),
            required_fields=BAK_BASIC_FIELDS,
        )


def test_classifier_distinguishes_semantic_empty_from_nonempty():
    empty = classify_membership_snapshot_body(_body(), required_fields=BAK_BASIC_FIELDS)
    assert empty.kind == "semantic_empty"
    assert empty.response_code == 0
    assert empty.items_count == 0

    nonempty = classify_membership_snapshot_body(
        _body(items=[["20161010", "600001.SH", "Known", "I", "20000101"]]),
        required_fields=BAK_BASIC_FIELDS,
    )
    assert nonempty.kind == "nonempty"
    assert nonempty.response_code == 0
    assert nonempty.items_count == 1


def test_derivation_carries_known_rows_and_quarantines_new_daily_codes():
    projection = derive_quarantined_membership(
        trade_date="2016-10-10",
        anchor_trade_date="2016-09-30",
        anchor_rows=[_known_member()],
        daily_codes={"600002.SH", "600001.SH"},
    )

    assert projection.trade_date == "2016-10-10"
    assert projection.source_kind == "causal_carry_forward"
    assert projection.metadata_anchor_date == "2016-09-30"
    assert projection.signal_session_eligible is False
    assert projection.unknown_codes == ("600002.SH",)
    assert [row["ts_code"] for row in projection.rows] == ["600001.SH", "600002.SH"]
    known, unknown = projection.rows
    assert dict(known) == {
        "trade_date": "2016-10-10",
        "ts_code": "600001.SH",
        "exchange": "SSE",
        "name": "Known",
        "industry": "Industry",
        "list_date": "2000-01-01",
        "membership_present": True,
        "metadata_stale_possible": True,
        "unknown_metadata": False,
        "observed_in_daily": True,
        "signal_session_eligible": False,
        "signal_eligible": False,
        "source_kind": "causal_carry_forward",
        "metadata_anchor_date": "2016-09-30",
    }
    assert unknown["exchange"] is None
    assert unknown["name"] is None
    assert unknown["industry"] is None
    assert unknown["list_date"] is None
    assert unknown["membership_present"] is False
    assert unknown["unknown_metadata"] is True
    assert unknown["metadata_stale_possible"] is False
    assert unknown["observed_in_daily"] is True
    assert unknown["signal_eligible"] is False


def test_pre_anchor_projection_contains_only_unknown_quarantined_daily_codes():
    projection = derive_quarantined_membership(
        trade_date="2016-01-04",
        anchor_trade_date=None,
        anchor_rows=[],
        daily_codes={"000001.SZ"},
    )
    assert projection.source_kind == "pre_anchor_quarantine"
    assert projection.metadata_anchor_date is None
    assert projection.unknown_codes == ("000001.SZ",)
    assert projection.rows[0]["source_kind"] == "pre_anchor_quarantine"
    assert projection.rows[0]["metadata_anchor_date"] is None


@pytest.mark.parametrize("anchor", ["2016-10-10", "2016-10-11"])
def test_derivation_rejects_equal_or_future_anchor(anchor):
    with pytest.raises(MembershipContractError, match="strictly earlier"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date=anchor,
            anchor_rows=[_known_member(trade_date=anchor)],
            daily_codes=set(),
        )


def test_derivation_rejects_noncanonical_dates_and_anchor_date_mismatch():
    with pytest.raises(MembershipContractError, match="canonical ISO"):
        derive_quarantined_membership(
            trade_date="20161010",
            anchor_trade_date=None,
            anchor_rows=[],
            daily_codes=set(),
        )
    with pytest.raises(MembershipContractError, match="anchor row trade_date"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date="2016-09-30",
            anchor_rows=[_known_member(trade_date="2016-09-29")],
            daily_codes=set(),
        )


def test_derivation_rejects_anchor_rows_without_anchor_and_duplicate_or_bad_codes():
    with pytest.raises(MembershipContractError, match="without an anchor"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date=None,
            anchor_rows=[_known_member()],
            daily_codes=set(),
        )
    with pytest.raises(MembershipContractError, match="duplicate anchor code"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date="2016-09-30",
            anchor_rows=[_known_member(), _known_member(name="Duplicate")],
            daily_codes=set(),
        )
    with pytest.raises(MembershipContractError, match="invalid ts_code"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date="2016-09-30",
            anchor_rows=[_known_member(ts_code="bad")],
            daily_codes=set(),
        )


def test_derivation_rejects_out_of_frozen_scope_bj_anchor_and_daily_codes():
    with pytest.raises(MembershipContractError, match="frozen exchange scope"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date="2016-09-30",
            anchor_rows=[_known_member(ts_code="430001.BJ", exchange="BSE")],
            daily_codes=set(),
        )
    with pytest.raises(MembershipContractError, match="frozen exchange scope"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date=None,
            anchor_rows=[],
            daily_codes={"430001.BJ"},
        )


@pytest.mark.parametrize(
    ("ts_code", "exchange"),
    [("900901.SH", "SSE"), ("200001.SZ", "SZSE")],
)
def test_derivation_rejects_b_share_anchor_and_daily_codes(ts_code, exchange):
    with pytest.raises(MembershipContractError, match="frozen A-share scope"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date="2016-09-30",
            anchor_rows=[_known_member(ts_code=ts_code, exchange=exchange)],
            daily_codes=set(),
        )
    with pytest.raises(MembershipContractError, match="frozen A-share scope"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date=None,
            anchor_rows=[],
            daily_codes={ts_code},
        )


@pytest.mark.parametrize("list_date", [None, "2016-10-01"])
def test_derivation_rejects_missing_or_post_anchor_list_date(list_date):
    with pytest.raises(MembershipContractError, match="anchor list_date"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date="2016-09-30",
            anchor_rows=[_known_member(list_date=list_date)],
            daily_codes=set(),
        )


def test_derivation_rejects_metadata_injection_and_has_no_poison_inputs():
    with pytest.raises(MembershipContractError, match="anchor row fields"):
        derive_quarantined_membership(
            trade_date="2016-10-10",
            anchor_trade_date="2016-09-30",
            anchor_rows=[_known_member(name_current_or_final="Future Name")],
            daily_codes=set(),
        )
    parameters = inspect.signature(derive_quarantined_membership).parameters
    assert "future_rows" not in parameters
    assert "current_master" not in parameters


def test_manifest_is_deterministic_and_copies_canonical_inputs():
    kwargs = _manifest_kwargs()
    first = build_membership_manifest(**kwargs)
    kwargs["empty_evidence"].reverse()
    kwargs["rows"] = list(reversed(kwargs["rows"]))
    second = build_membership_manifest(**kwargs)

    assert first == second
    assert first["schema_version"] == MEMBERSHIP_GENERATION_SCHEMA_VERSION
    assert first["policy_version"] == MEMBERSHIP_POLICY_VERSION
    assert first["trade_date"] == "2016-10-10"
    assert first["signal_session_eligible"] is False
    assert len(first["manifest_sha256"]) == 64
    assert first["summary"] == {
        "row_count": 2,
        "membership_present_count": 1,
        "observed_in_daily_count": 2,
        "unknown_metadata_count": 1,
        "metadata_stale_possible_count": 1,
        "signal_eligible_count": 0,
        "signal_session_eligible": False,
    }
    kwargs["anchor"]["receipt_dataset"] = "mutated-after-build"
    assert first["anchor"]["receipt_dataset"] == "bak_basic"


@pytest.mark.parametrize(
    "field",
    [
        "trade_date",
        "receipt_dataset",
        "receipt_partition",
        "receipt_raw_sha256",
        "normalized_sha256",
        "row_count",
        "in_scope_row_count",
        "temporal_role",
        "temporal_contract_sha256",
    ],
)
def test_manifest_anchor_requires_fixed_descriptor_fields(field):
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = dict(kwargs["anchor"])
    del kwargs["anchor"][field]
    with pytest.raises(MembershipContractError, match="anchor descriptor"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("receipt_dataset", "daily"),
        ("receipt_partition", "2016-09-29"),
        ("receipt_raw_sha256", "A" * 64),
        ("normalized_sha256", "a" * 63),
        ("row_count", 0),
        ("row_count", True),
        ("in_scope_row_count", 0),
        ("in_scope_row_count", True),
    ],
)
def test_manifest_rejects_invalid_anchor_identity(field, value):
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = {**kwargs["anchor"], field: value}
    with pytest.raises(MembershipContractError, match="anchor descriptor"):
        build_membership_manifest(**kwargs)


def test_manifest_anchor_in_scope_count_must_fit_receipt_and_projection():
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = {
        **kwargs["anchor"],
        "row_count": 1,
        "in_scope_row_count": 2,
    }
    with pytest.raises(MembershipContractError, match="in_scope_row_count"):
        build_membership_manifest(**kwargs)

    kwargs = _manifest_kwargs()
    kwargs["anchor"] = {
        **kwargs["anchor"],
        "row_count": 10,
        "in_scope_row_count": 2,
    }
    with pytest.raises(MembershipContractError, match="projection membership count"):
        build_membership_manifest(**kwargs)


def test_manifest_anchor_receipt_may_include_excluded_rows():
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = {
        **kwargs["anchor"],
        "row_count": 2,
        "in_scope_row_count": 1,
    }
    manifest = build_membership_manifest(**kwargs)
    assert manifest["anchor"]["row_count"] == 2
    assert manifest["anchor"]["in_scope_row_count"] == 1


@pytest.mark.parametrize(
    "field",
    [
        "trade_date",
        "attempt_id",
        "raw_sha256",
        "request_semantics_sha256",
        "terminal_status",
        "classification_kind",
        "temporal_role",
        "temporal_contract_sha256",
    ],
)
def test_manifest_empty_evidence_requires_fixed_descriptor_fields(field):
    kwargs = _manifest_kwargs()
    evidence = dict(kwargs["empty_evidence"][0])
    del evidence[field]
    kwargs["empty_evidence"] = [evidence]
    with pytest.raises(MembershipContractError, match="empty evidence descriptor"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_id", ""),
        ("raw_sha256", "A" * 64),
        ("request_semantics_sha256", "b" * 65),
        ("terminal_status", "stored"),
        ("classification_kind", "nonempty"),
    ],
)
def test_manifest_rejects_invalid_empty_evidence_identity(field, value):
    kwargs = _manifest_kwargs()
    kwargs["empty_evidence"] = [{**kwargs["empty_evidence"][0], field: value}]
    with pytest.raises(MembershipContractError, match="empty evidence descriptor"):
        build_membership_manifest(**kwargs)


def test_manifest_rejects_duplicate_or_conflicting_attempt_identity():
    kwargs = _manifest_kwargs()
    evidence = dict(kwargs["empty_evidence"][0])
    kwargs["empty_evidence"] = [evidence, dict(evidence)]
    with pytest.raises(MembershipContractError, match="duplicate empty evidence attempt_id"):
        build_membership_manifest(**kwargs)

    kwargs = _manifest_kwargs()
    conflicting = {
        **kwargs["empty_evidence"][0],
        "raw_sha256": SHA_A,
    }
    kwargs["empty_evidence"] = [kwargs["empty_evidence"][0], conflicting]
    with pytest.raises(MembershipContractError, match="duplicate empty evidence attempt_id"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    "field",
    [
        "trade_date",
        "generation_id",
        "manifest_sha256",
        "lineage_sha256",
        "temporal_role",
        "temporal_contract_sha256",
    ],
)
def test_manifest_market_requires_fixed_descriptor_fields(field):
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = dict(kwargs["market_generation"])
    del kwargs["market_generation"][field]
    with pytest.raises(MembershipContractError, match="market generation descriptor"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation_id", ""),
        ("manifest_sha256", "A" * 64),
        ("lineage_sha256", "b" * 63),
    ],
)
def test_manifest_rejects_invalid_market_identity(field, value):
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = {
        **kwargs["market_generation"],
        field: value,
    }
    with pytest.raises(MembershipContractError, match="market generation descriptor"):
        build_membership_manifest(**kwargs)


def test_manifest_rejects_inconsistent_target_anchor_market_temporal_and_rows():
    cases = []
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = {**kwargs["anchor"], "trade_date": "2016-10-10"}
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["empty_evidence"] = [{**kwargs["empty_evidence"][0], "trade_date": "2016-10-09"}]
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = {
        **kwargs["market_generation"],
        "trade_date": "2016-10-09",
    }
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = {
        **kwargs["market_generation"],
        "temporal_role": "contaminated_diagnostic",
    }
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["rows"] = [{**dict(kwargs["rows"][0]), "signal_eligible": True}]
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["rows"] = [{**dict(kwargs["rows"][0]), "trade_date": "2016-10-09"}]
    cases.append(kwargs)

    for invalid in cases:
        with pytest.raises(MembershipContractError):
            build_membership_manifest(**invalid)


def test_manifest_rejects_pre_anchor_with_anchor_and_carry_forward_without_anchor():
    kwargs = _manifest_kwargs()
    kwargs["source_kind"] = "pre_anchor_quarantine"
    with pytest.raises(MembershipContractError):
        build_membership_manifest(**kwargs)

    kwargs = _manifest_kwargs()
    kwargs["anchor"] = None
    with pytest.raises(MembershipContractError):
        build_membership_manifest(**kwargs)


def test_manifest_preanchor_accepts_only_unknown_empty_metadata_observed_rows():
    baseline = _preanchor_manifest_kwargs()
    manifest = build_membership_manifest(**baseline)
    assert manifest["summary"]["membership_present_count"] == 0
    assert manifest["summary"]["unknown_metadata_count"] == 1

    invalid_changes = (
        {"membership_present": True},
        {"unknown_metadata": False},
        {"metadata_stale_possible": True},
        {"observed_in_daily": False},
        {"name": "current-master poison"},
        {"exchange": "SSE"},
        {"metadata_anchor_date": "2016-09-30"},
        {"source_kind": "causal_carry_forward"},
    )
    for changes in invalid_changes:
        kwargs = _preanchor_manifest_kwargs()
        kwargs["rows"] = [{**dict(kwargs["rows"][0]), **changes}]
        with pytest.raises(MembershipContractError):
            build_membership_manifest(**kwargs)

    coherent_known_row = _preanchor_manifest_kwargs()
    coherent_known_row["rows"] = [
        {
            **dict(coherent_known_row["rows"][0]),
            "exchange": "SSE",
            "name": "Known but forbidden before anchor",
            "industry": "Industry",
            "list_date": "2000-01-01",
            "membership_present": True,
            "metadata_stale_possible": True,
            "unknown_metadata": False,
        }
    ]
    with pytest.raises(MembershipContractError, match="pre-anchor rows must be unknown"):
        build_membership_manifest(**coherent_known_row)


@pytest.mark.parametrize("list_date", [None, "2016-10-01"])
def test_manifest_rejects_missing_or_post_anchor_known_list_date(list_date):
    kwargs = _manifest_kwargs()
    kwargs["rows"] = [{**dict(kwargs["rows"][0]), "list_date": list_date}]
    with pytest.raises(MembershipContractError, match="list_date"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    ("ts_code", "exchange"),
    [("900901.SH", "SSE"), ("200001.SZ", "SZSE")],
)
def test_manifest_rejects_b_share_rows(ts_code, exchange):
    kwargs = _manifest_kwargs()
    kwargs["rows"] = [
        {
            **dict(kwargs["rows"][0]),
            "ts_code": ts_code,
            "exchange": exchange,
        }
    ]
    with pytest.raises(MembershipContractError, match="frozen A-share scope"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize("anchor_date", ["2016-10-10", "2016-10-11"])
def test_manifest_rejects_equal_or_future_anchor(anchor_date):
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = {**kwargs["anchor"], "trade_date": anchor_date}
    with pytest.raises(MembershipContractError, match="strictly earlier"):
        build_membership_manifest(**kwargs)


def test_manifest_requires_complete_authority_on_every_bound_input():
    cases = []
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = dict(kwargs["anchor"])
    del kwargs["anchor"]["temporal_contract_sha256"]
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["empty_evidence"] = [dict(kwargs["empty_evidence"][0])]
    del kwargs["empty_evidence"][0]["temporal_role"]
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = dict(kwargs["market_generation"])
    del kwargs["market_generation"]["temporal_contract_sha256"]
    cases.append(kwargs)

    for invalid in cases:
        with pytest.raises(MembershipContractError, match="complete temporal authority"):
            build_membership_manifest(**invalid)


def test_manifest_rejects_authority_aliases_and_nested_binding_even_when_equal():
    cases = []
    kwargs = _manifest_kwargs()
    kwargs["anchor"] = {
        **kwargs["anchor"],
        "role": "development",
    }
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["empty_evidence"] = [
        {
            **kwargs["empty_evidence"][0],
            "contract_sha256": SHA_B,
        }
    ]
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = {
        **kwargs["market_generation"],
        "temporal_authority": {
            "temporal_role": "development",
            "temporal_contract_sha256": SHA_B,
        },
    }
    cases.append(kwargs)
    kwargs = _manifest_kwargs()
    kwargs["temporal_authority"] = {
        **kwargs["temporal_authority"],
        "role": "development",
    }
    cases.append(kwargs)

    for invalid in cases:
        with pytest.raises(MembershipContractError, match="canonical top-level"):
            build_membership_manifest(**invalid)


def test_manifest_source_kind_type_fails_with_contract_error():
    kwargs = _manifest_kwargs()
    kwargs["source_kind"] = ["causal_carry_forward"]
    with pytest.raises(MembershipContractError, match="source_kind"):
        build_membership_manifest(**kwargs)


def test_manifest_rejects_circular_mapping_and_sequence_as_contract_errors():
    kwargs = _manifest_kwargs()
    circular_mapping = {}
    circular_mapping["loop"] = circular_mapping
    kwargs["market_generation"] = {
        **kwargs["market_generation"],
        "extra": circular_mapping,
    }
    with pytest.raises(MembershipContractError, match="circular"):
        build_membership_manifest(**kwargs)

    kwargs = _manifest_kwargs()
    circular_sequence = []
    circular_sequence.append(circular_sequence)
    kwargs["empty_evidence"] = [{**kwargs["empty_evidence"][0], "extra": circular_sequence}]
    with pytest.raises(MembershipContractError, match="circular"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize("secret_key", ["token", "api_key", "apikey", "secret"])
def test_manifest_rejects_secret_key_names_at_any_depth(secret_key):
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = {
        **kwargs["market_generation"],
        "nested": [{secret_key: "must-not-persist"}],
    }
    with pytest.raises(MembershipContractError, match="secret"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    "location",
    ["anchor", "empty_evidence", "temporal_authority", "rows"],
)
def test_manifest_rejects_recursive_secret_keys_outside_market(location):
    kwargs = _manifest_kwargs()
    if location == "anchor":
        kwargs["anchor"] = {**kwargs["anchor"], "nested": {"token": "forbidden"}}
    elif location == "empty_evidence":
        kwargs["empty_evidence"] = [
            {**kwargs["empty_evidence"][0], "nested": {"api_key": "forbidden"}}
        ]
    elif location == "temporal_authority":
        kwargs["temporal_authority"] = {
            **kwargs["temporal_authority"],
            "nested": [{"apikey": "forbidden"}],
        }
    else:
        kwargs["rows"] = [{**dict(kwargs["rows"][0]), "nested": {"secret": "forbidden"}}]
    with pytest.raises(MembershipContractError, match="secret"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    "secret_key",
    [
        "tushare_token",
        "access_token",
        "refresh-token",
        "token_value",
        "client_secret",
        "secret_value",
        "provider_api_key",
        "api_key_value",
        "provider_apikey",
        "apikey_value",
    ],
)
def test_manifest_rejects_secret_key_prefix_and_suffix_variants(secret_key):
    kwargs = _manifest_kwargs()
    kwargs["market_generation"] = {
        **kwargs["market_generation"],
        "extra": {secret_key: "forbidden"},
    }
    with pytest.raises(MembershipContractError, match="secret"):
        build_membership_manifest(**kwargs)


@pytest.mark.parametrize(
    "secret_key",
    ["accessToken", "clientSecret", "providerApiKey", "providerApiKeyValue"],
)
def test_manifest_rejects_camel_case_secret_key_variants_recursively(secret_key):
    kwargs = _manifest_kwargs()
    kwargs["empty_evidence"] = [
        {
            **kwargs["empty_evidence"][0],
            "extra": [{"nested": {secret_key: "forbidden"}}],
        }
    ]
    with pytest.raises(MembershipContractError, match="secret"):
        build_membership_manifest(**kwargs)


def test_verify_manifest_rejects_mutation_of_returned_dict():
    manifest = build_membership_manifest(**_manifest_kwargs())
    assert verify_membership_manifest(manifest) == manifest

    original_hash = manifest["manifest_sha256"]
    manifest["anchor"]["note"] = "tampered after build"
    with pytest.raises(MembershipContractError, match="hash mismatch"):
        verify_membership_manifest(manifest)

    rebuilt = build_membership_manifest(
        trade_date=manifest["trade_date"],
        source_kind=manifest["source_kind"],
        anchor=manifest["anchor"],
        empty_evidence=manifest["empty_evidence"],
        market_generation=manifest["market_generation"],
        temporal_authority=manifest["temporal_authority"],
        rows=manifest["rows"],
    )
    assert rebuilt["manifest_sha256"] != original_hash
