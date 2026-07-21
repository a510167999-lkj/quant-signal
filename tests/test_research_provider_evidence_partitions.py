import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.research_partitions import (
    PartitionContractError,
    assert_range_allowed,
    load_temporal_partition_contract,
)
from app.research_provider_evidence_partitions import (
    PROVIDER_EVIDENCE_CONTRACT_PATH,
    ProviderEvidenceAuthorityError,
    assert_provider_evidence_range_allowed,
    load_provider_evidence_temporal_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FROZEN_V1 = REPO_ROOT / "data" / "research_partitions" / "frozen-v1.json"


def _canonical_sha256(payload):
    unsigned = {key: value for key, value in payload.items() if key != "contract_sha256"}
    return hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _load_raw():
    return json.loads(PROVIDER_EVIDENCE_CONTRACT_PATH.read_text(encoding="utf-8"))


def _write_resigned(tmp_path, payload, *, directory_name=None):
    payload["contract_sha256"] = _canonical_sha256(payload)
    target = tmp_path / (directory_name or payload["contract_sha256"])
    target.mkdir()
    path = target / "contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_frozen_v1_remains_immutable_and_rejects_provider_evidence_operations():
    frozen = load_temporal_partition_contract(FROZEN_V1)
    assert frozen["contract_sha256"] == (
        "cf70083e66f8706bf21e48b655c5b4342886e230c6a88dcfff04698d12b2e227"
    )
    for operation in ("provider_evidence_collect", "provider_evidence_publish"):
        with pytest.raises(PartitionContractError):
            assert_range_allowed(frozen, "embargo", "2026-07-04", "2026-07-10", operation)


def test_successor_loads_from_content_address_and_binds_frozen_parent():
    contract = load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=REPO_ROOT,
    )
    assert PROVIDER_EVIDENCE_CONTRACT_PATH.parent.name == contract["contract_sha256"]
    assert contract["schema_version"] == "research-temporal-provider-evidence/v1"
    assert contract["evidence_role"] == "research_development_provider_pit"
    assert contract["parent"]["path"] == "data/research_partitions/frozen-v1.json"
    assert contract["parent"]["file_sha256"] == (
        "c810e1d19f5296f8e7edb7b437e487130473aade359982409ca3c7038a055c2b"
    )
    assert contract["parent"]["contract_sha256"] == (
        "cf70083e66f8706bf21e48b655c5b4342886e230c6a88dcfff04698d12b2e227"
    )


def test_successor_allows_only_exact_provider_evidence_tail_operations():
    contract = load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=REPO_ROOT,
    )
    for operation in ("provider_evidence_collect", "provider_evidence_publish"):
        assert_provider_evidence_range_allowed(
            contract,
            "2026-07-04",
            "2026-07-10",
            operation,
        )


@pytest.mark.parametrize(
    "operation",
    [
        "collect",
        "publish",
        "diagnose",
        "train",
        "backtest",
        "validate",
        "purged_validation",
        "recommend",
        "eligible_pool",
        "paper_trading",
        "live",
        "final_oos",
    ],
)
def test_successor_rejects_every_non_evidence_operation(operation):
    contract = load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=REPO_ROOT,
    )
    with pytest.raises(ProviderEvidenceAuthorityError):
        assert_provider_evidence_range_allowed(
            contract,
            "2026-07-04",
            "2026-07-10",
            operation,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-07-03", "2026-07-10"),
        ("2026-07-04", "2026-07-11"),
        ("2026-07-10", "2026-07-04"),
        ("2026-07-13", "2026-07-13"),
        ("2026-07-04T00:00:00", "2026-07-10"),
    ],
)
def test_successor_rejects_out_of_scope_or_noncanonical_dates(start, end):
    contract = load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=REPO_ROOT,
    )
    with pytest.raises(ProviderEvidenceAuthorityError):
        assert_provider_evidence_range_allowed(
            contract,
            start,
            end,
            "provider_evidence_collect",
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: raw["parent"].update({"path": "data/research_partitions/other.json"}),
        lambda raw: raw["parent"].update({"file_sha256": "0" * 64}),
        lambda raw: raw["parent"].update({"contract_sha256": "0" * 64}),
        lambda raw: raw["authorization"].update({"source_kind": "artifact_self_report"}),
        lambda raw: raw["authorization"].update({"source_thread_id": "forged"}),
        lambda raw: raw["authorization"].update({"scope_sha256": "0" * 64}),
        lambda raw: raw["authorization"].pop("scope_sha256"),
        lambda raw: raw["authorization"].update(
            {"operations": ["provider_evidence_collect", "provider_evidence_publish", "validate"]}
        ),
        lambda raw: raw["authorization"].update({"end_date": "2026-07-12"}),
        lambda raw: raw.update({"official_exchange_pit": True}),
        lambda raw: raw["safety"].update({"eligible_pool_count": 1}),
        lambda raw: raw["safety"].update({"production_recommendation_eligible": True}),
        lambda raw: raw["safety"].update({"paper_trading_validated": True}),
        lambda raw: raw["safety"].update({"live_ready": True}),
        lambda raw: raw["safety"].update({"final_oos_authorized": True}),
    ],
)
def test_full_resign_cannot_expand_or_forge_successor_authority(tmp_path, mutator):
    raw = copy.deepcopy(_load_raw())
    mutator(raw)
    path = _write_resigned(tmp_path, raw)
    with pytest.raises(ProviderEvidenceAuthorityError):
        load_provider_evidence_temporal_contract(path, repo_root=REPO_ROOT)


def test_content_address_directory_cannot_be_resigned_or_renamed(tmp_path):
    raw = copy.deepcopy(_load_raw())
    path = _write_resigned(tmp_path, raw, directory_name="0" * 64)
    with pytest.raises(ProviderEvidenceAuthorityError, match="content-address"):
        load_provider_evidence_temporal_contract(path, repo_root=REPO_ROOT)


def test_parent_file_mutation_is_detected_even_when_successor_is_unchanged(tmp_path):
    raw = copy.deepcopy(_load_raw())
    repo = tmp_path / "repo"
    parent = repo / "data" / "research_partitions"
    parent.mkdir(parents=True)
    parent.joinpath("frozen-v1.json").write_bytes(FROZEN_V1.read_bytes() + b"\n")
    path = _write_resigned(tmp_path, raw)
    with pytest.raises(ProviderEvidenceAuthorityError, match="parent"):
        load_provider_evidence_temporal_contract(path, repo_root=repo)


def test_successor_contract_hardlink_is_rejected(tmp_path):
    raw = copy.deepcopy(_load_raw())
    directory = tmp_path / raw["contract_sha256"]
    directory.mkdir()
    source = tmp_path / "source.json"
    source.write_bytes(PROVIDER_EVIDENCE_CONTRACT_PATH.read_bytes())
    path = directory / "contract.json"
    os.link(source, path)
    with pytest.raises(ProviderEvidenceAuthorityError, match="hardlink"):
        load_provider_evidence_temporal_contract(path, repo_root=REPO_ROOT)


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":"first","schema_version":"second"}',
        '{"value":NaN}',
        '{"value":Infinity}',
    ],
)
def test_successor_strict_json_rejects_duplicates_and_nonfinite_values(tmp_path, text):
    target = tmp_path / ("0" * 64)
    target.mkdir()
    path = target / "contract.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ProviderEvidenceAuthorityError):
        load_provider_evidence_temporal_contract(path, repo_root=REPO_ROOT)
