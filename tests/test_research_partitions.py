import copy
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app import current_pool_gate
from app.research_partitions import (
    PartitionContractError,
    assert_final_oos_sealed,
    assert_range_allowed,
    classify_date,
    load_temporal_partition_contract,
)


CONTRACT_PATH = Path("data/research_partitions/frozen-v1.json")
V2_CONTRACT_PATH = Path("data/research_partitions/frozen-v2.json")


def _canonical_sha256(payload):
    unhashed = {key: value for key, value in payload.items() if key != "contract_sha256"}
    encoded = json.dumps(
        unhashed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _raw_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _write_contract(tmp_path, payload):
    payload["contract_sha256"] = _canonical_sha256(payload)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_frozen_contract_loads_and_has_a_valid_self_hash():
    raw = _raw_contract()
    contract = load_temporal_partition_contract(CONTRACT_PATH)
    assert contract["contract_sha256"] == _canonical_sha256(raw)
    assert contract["schema_version"] == "research-temporal-partitions/v1"
    assert contract["policy_version"] == "contamination-aware-forward-oos/v1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2016-01-01", "development"),
        (date(2020, 2, 29), "development"),
        ("2023-12-31", "development"),
        ("2024-01-01", "contaminated_diagnostic"),
        ("2026-07-03", "contaminated_diagnostic"),
        ("2026-07-04", "embargo"),
        ("2026-07-12", "embargo"),
        ("2026-07-13", "final_oos"),
        ("2099-12-31", "final_oos"),
    ],
)
def test_classify_date_inclusive_boundaries_and_open_end(value, expected):
    assert classify_date(load_temporal_partition_contract(CONTRACT_PATH), value) == expected


@pytest.mark.parametrize("value", ["2015-12-31", "2024-02-30", "01-01-2024", 20240101])
def test_classify_date_rejects_outside_or_non_iso_dates(value):
    with pytest.raises(PartitionContractError):
        classify_date(load_temporal_partition_contract(CONTRACT_PATH), value)


def test_tampering_without_rehashing_is_rejected(tmp_path):
    raw = _raw_contract()
    raw["roles"][0]["end_date"] = "2024-01-01"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PartitionContractError, match="hash"):
        load_temporal_partition_contract(path)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: raw.pop("policy_version"),
        lambda raw: raw.update({"surprise": True}),
        lambda raw: raw["roles"][0].pop("permitted_operations"),
        lambda raw: raw["roles"][0].update({"surprise": True}),
        lambda raw: raw["contamination_evidence"].pop("strategy_results"),
        lambda raw: raw["contamination_evidence"].update({"surprise": True}),
    ],
)
def test_unknown_and_missing_fields_fail_closed(tmp_path, mutator):
    raw = _raw_contract()
    mutator(raw)
    with pytest.raises(PartitionContractError):
        load_temporal_partition_contract(_write_contract(tmp_path, raw))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda roles: roles.__setitem__(1, {**roles[1], "start_date": "2024-01-02"}),
        lambda roles: roles.__setitem__(1, {**roles[1], "start_date": "2023-12-31"}),
        lambda roles: roles.__setitem__(0, {**roles[0], "end_date": None}),
        lambda roles: roles.__setitem__(3, {**roles[3], "end_date": "2026-12-31"}),
        lambda roles: roles.reverse(),
    ],
)
def test_roles_must_be_exact_ordered_contiguous_and_only_final_open(tmp_path, mutator):
    raw = _raw_contract()
    mutator(raw["roles"])
    with pytest.raises(PartitionContractError):
        load_temporal_partition_contract(_write_contract(tmp_path, raw))


def test_permitted_operations_are_strict_and_range_must_stay_in_role():
    contract = load_temporal_partition_contract(CONTRACT_PATH)
    for operation in ("collect", "publish", "train", "validate", "backtest"):
        assert_range_allowed(contract, "development", "2016-01-01", "2023-12-31", operation)
    for operation in ("collect", "publish", "diagnose"):
        assert_range_allowed(
            contract, "contaminated_diagnostic", "2024-01-01", "2026-07-03", operation
        )
    for operation in ("promotion", "train", "validate", "backtest"):
        with pytest.raises(PartitionContractError):
            assert_range_allowed(
                contract, "contaminated_diagnostic", "2024-01-01", "2026-07-03", operation
            )
    with pytest.raises(PartitionContractError):
        assert_range_allowed(contract, "development", "2023-12-31", "2024-01-01", "train")
    with pytest.raises(PartitionContractError):
        assert_range_allowed(contract, "development", "2020-01-01", "2019-12-31", "train")
    with pytest.raises(PartitionContractError):
        assert_range_allowed(contract, "development", "2020-01-01", "2020-01-02", "unknown")


@pytest.mark.parametrize("role", ["embargo", "final_oos"])
@pytest.mark.parametrize("operation", ["collect", "publish", "diagnose", "train", "validate", "backtest", "promotion"])
def test_embargo_and_final_oos_reject_all_operations(role, operation):
    contract = load_temporal_partition_contract(CONTRACT_PATH)
    start = "2026-07-04" if role == "embargo" else "2026-07-13"
    end = "2026-07-12" if role == "embargo" else "2026-07-13"
    with pytest.raises(PartitionContractError):
        assert_range_allowed(contract, role, start, end, operation)


def test_final_oos_seal_is_enforced():
    contract = load_temporal_partition_contract(CONTRACT_PATH)
    assert_final_oos_sealed(contract)
    raw = copy.deepcopy(_raw_contract())
    raw["roles"][-1]["sealed"] = False
    raw["contract_sha256"] = _canonical_sha256(raw)
    with pytest.raises(PartitionContractError, match="sealed"):
        assert_final_oos_sealed(raw)


@pytest.mark.parametrize(
    "entrypoint",
    [
        lambda contract: classify_date(contract, "2020-01-01"),
        lambda contract: assert_range_allowed(
            contract, "development", "2020-01-01", "2020-01-02", "train"
        ),
        assert_final_oos_sealed,
    ],
)
@pytest.mark.parametrize("rehash", [False, True])
def test_every_public_enforcement_entrypoint_revalidates_after_mutation(entrypoint, rehash):
    contract = load_temporal_partition_contract(CONTRACT_PATH)
    contract["roles"][-1]["permitted_operations"].append("collect")
    if rehash:
        contract["contract_sha256"] = _canonical_sha256(contract)
    with pytest.raises(PartitionContractError):
        entrypoint(contract)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda contract: contract["roles"][0].update({"start_date": "2015-01-01"}),
        lambda contract: contract["roles"][0].update({"sealed": 0}),
        lambda contract: contract.update({"schema_version": 1}),
        lambda contract: contract["contamination_evidence"]["strategy_results"][0].update(
            {"sha256": "0" * 64}
        ),
    ],
)
def test_rehashed_policy_or_type_mutation_cannot_change_classification(mutator):
    contract = load_temporal_partition_contract(CONTRACT_PATH)
    mutator(contract)
    contract["contract_sha256"] = _canonical_sha256(contract)
    with pytest.raises(PartitionContractError):
        classify_date(contract, "2020-01-01")


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":"first","schema_version":"second"}',
        '{"outer":{"sealed":true,"sealed":false}}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
        '{"value":"raw\u0001control"}',
    ],
)
def test_strict_json_parser_rejects_duplicates_constants_and_controls(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PartitionContractError):
        load_temporal_partition_contract(path)


@pytest.mark.parametrize(
    "duplicate",
    [
        ('"schema_version": "research-temporal-partitions/v1",',
         '"schema_version": "research-temporal-partitions/v1", '
         '"schema_version": "research-temporal-partitions/v1",'),
        ('"name": "development",',
         '"name": "development", "name": "development",'),
        ('"strategy_results": [',
         '"strategy_results": [], "strategy_results": ['),
    ],
)
def test_valid_contract_with_top_or_nested_duplicate_key_is_rejected(tmp_path, duplicate):
    original, replacement = duplicate
    text = CONTRACT_PATH.read_text(encoding="utf-8").replace(original, replacement, 1)
    path = tmp_path / "duplicate.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PartitionContractError, match="duplicate"):
        load_temporal_partition_contract(path)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2024, 1, 1),
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        "2024-01-01T00:00:00",
        "2024-01-01Z",
    ],
)
def test_datetime_objects_and_timestamp_strings_are_rejected(value):
    with pytest.raises(PartitionContractError):
        classify_date(load_temporal_partition_contract(CONTRACT_PATH), value)


def test_current_pool_v2_contract_loads_with_a_valid_self_hash():
    raw = json.loads(V2_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract = load_temporal_partition_contract(V2_CONTRACT_PATH)

    assert contract["contract_sha256"] == _canonical_sha256(raw)
    assert contract["policy_version"] == "current-pool-development-forward-oos/v2"
    assert [role["name"] for role in contract["roles"]] == [
        "development",
        "embargo",
        "final_oos",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-07-05", "development"),
        ("2026-07-03", "development"),
        ("2026-07-04", "embargo"),
        ("2026-07-12", "embargo"),
        ("2026-07-13", "final_oos"),
    ],
)
def test_current_pool_v2_classifies_frozen_boundaries(value, expected):
    assert classify_date(load_temporal_partition_contract(V2_CONTRACT_PATH), value) == expected


def test_current_pool_v2_permits_development_only_and_seals_final_oos(monkeypatch):
    contract = load_temporal_partition_contract(V2_CONTRACT_PATH)
    evidence = contract["development_evidence"]["current_pool_coverage_audit"]
    monkeypatch.setattr(
        current_pool_gate,
        "verify_current_pool_audit",
        lambda _path: {
            "canonical_sha256": evidence["canonical_sha256"],
            "source_as_of": evidence["source_as_of"],
        },
    )

    for operation in ("collect", "publish", "train", "validate", "backtest"):
        assert_range_allowed(contract, "development", "2024-07-05", "2026-07-03", operation)
    for role, start, end in (
        ("embargo", "2026-07-04", "2026-07-12"),
        ("final_oos", "2026-07-13", "2026-07-13"),
    ):
        with pytest.raises(PartitionContractError):
            assert_range_allowed(contract, role, start, end, "backtest")
    assert_final_oos_sealed(contract)


def test_current_pool_v2_rejects_rehashed_evidence_tampering(tmp_path):
    raw = json.loads(V2_CONTRACT_PATH.read_text(encoding="utf-8"))
    raw["development_evidence"]["history_end"] = "2026-07-02"

    with pytest.raises(PartitionContractError, match="development evidence"):
        load_temporal_partition_contract(_write_contract(tmp_path, raw))


def test_current_pool_v2_rejects_missing_audit_evidence(tmp_path, monkeypatch):
    contract = load_temporal_partition_contract(V2_CONTRACT_PATH)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PartitionContractError, match="evidence is unavailable"):
        assert_range_allowed(contract, "development", "2024-07-05", "2026-07-03", "collect")


def test_current_pool_v2_rejects_audit_evidence_hash_mismatch(monkeypatch):
    contract = load_temporal_partition_contract(V2_CONTRACT_PATH)
    monkeypatch.setattr(
        current_pool_gate,
        "verify_current_pool_audit",
        lambda _path: {"canonical_sha256": "0" * 64, "source_as_of": "2026-07-22"},
    )

    with pytest.raises(PartitionContractError, match="evidence does not match"):
        assert_range_allowed(contract, "development", "2024-07-05", "2026-07-03", "collect")
