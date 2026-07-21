from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import app.research_registration_v2 as registration_module
from app.research_registration_v2 import (
    RegistrationV2Error,
    load_frozen_registration,
    publish_registration_v2,
    run_frozen_registration_v2,
)
from app.research_trusted_roots import TrustedFixtureRootV1, TrustedSnapshotRootV1
from tests.test_research_trusted_roots import (
    _assert_fixture_identity_resigned,
    _assert_snapshot_identity_resigned,
    _canonical_bytes,
    _expected_from_resigned_roots,
    _fixture_expected,
    _reencoded_snapshot_with_fully_resigned_fixture,
)


def _sha(value) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _synthetic_sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _future_trusted_roots() -> tuple[dict, dict]:
    snapshot = _fixture_expected().snapshot.to_dict()
    for field, value in tuple(snapshot.items()):
        if field.endswith("sha256") or field == "directory_id":
            snapshot[field] = _synthetic_sha(f"future-snapshot:{field}")
    snapshot["created_at_utc"] = "2026-07-17T00:00:00+00:00"
    snapshot["root_canonical_sha256"] = _sha(
        {key: value for key, value in snapshot.items() if key != "root_canonical_sha256"}
    )
    fixture = _fixture_expected().to_dict()
    for field, value in tuple(fixture.items()):
        if field.endswith("sha256") or field in {"fixture_id", "invalid_run_id"}:
            fixture[field] = _synthetic_sha(f"future-fixture:{field}")
    fixture["evidence_use"] = "research_development_historical"
    fixture["snapshot"] = snapshot
    fixture["root_canonical_sha256"] = _sha(
        {key: value for key, value in fixture.items() if key != "root_canonical_sha256"}
    )
    return snapshot, fixture


def _body() -> dict:
    snapshot, fixture = _future_trusted_roots()
    return {
        "experiment_id": "auto-iter-038-h1-hold3-development",
        "data_cutoff": "2026-07-10",
        "development_window": {"start": "2022-01-04", "end": "2023-12-29"},
        "final_oos_start": "2026-07-13",
        "trusted_snapshot_root": snapshot,
        "trusted_fixture_root": fixture,
        "topology": {
            "logical_calls": 53,
            "physical_raw_files": 50,
            "segments": [14, 39],
            "zero_rows": [4, 20],
            "source_indices": list(range(53)),
        },
        "determinism": {
            "call_order": "source_index_ascending",
            "sort_keys": ["symbol", "date"],
            "random_seed": 0,
            "workers": 1,
        },
        "output_policy": {
            "fresh_nonexistent": True,
            "single_writer": True,
            "atomic_publish": True,
            "overwrite": False,
            "reuse": False,
            "hardlink": False,
        },
        "budgets": {"network": 0, "credential_access": 0, "purged": 1},
        "safety": {
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
            "embargo_allowed": False,
            "final_oos_allowed": False,
            "deployment_allowed": False,
            "orders_allowed": False,
            "push_allowed": False,
            "paper_trading_validated": False,
            "live_ready": False,
        },
    }


def _publish(parent: Path, *, body: dict | None = None) -> dict:
    payload = body or _body()
    return publish_registration_v2(
        parent,
        body=payload,
        expected_snapshot=TrustedSnapshotRootV1.from_dict(
            payload["trusted_snapshot_root"]
        ),
        expected_fixture=TrustedFixtureRootV1.from_dict(
            payload["trusted_fixture_root"]
        ),
    )


def test_registration_publish_stop_and_frozen_load(tmp_path: Path) -> None:
    parent = tmp_path / "registrations"
    result = _publish(parent)
    root = Path(result["registration_root"])
    assert sorted(path.name for path in root.iterdir()) == ["publish-receipt.json", "registration.json"]
    assert result["outputs_created"] == 0
    assert result["network_calls"] == 0
    assert result["purged_calls"] == 0
    loaded = load_frozen_registration(
        root,
        expected_file_sha256=result["registration_file_sha256"],
        expected_canonical_sha256=result["registration_canonical_sha256"],
    )
    assert loaded["body"]["data_cutoff"] == "2026-07-10"


def test_frozen_load_requires_both_parent_hashes(tmp_path: Path) -> None:
    result = _publish(tmp_path / "registrations")
    root = Path(result["registration_root"])
    with pytest.raises(TypeError):
        load_frozen_registration(root, expected_file_sha256=result["registration_file_sha256"])  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["topology"].update(physical_raw_files=30),
        lambda body: body["topology"].update(zero_rows=[4, 19]),
        lambda body: body["budgets"].update(network=1),
        lambda body: body["budgets"].update(purged=2),
        lambda body: body["safety"].update(production_recommendation_eligible=True),
        lambda body: body["determinism"].update(random_seed=1),
        lambda body: body.update(data_cutoff="2026-07-11"),
    ],
)
def test_registration_rejects_semantic_drift_before_publish(tmp_path: Path, mutation) -> None:
    body = _body()
    mutation(body)
    with pytest.raises(RegistrationV2Error):
        _publish(tmp_path / "registrations", body=body)
    assert not (tmp_path / "registrations").exists()


def test_contaminated_candidate_cannot_be_promoted_into_registration(
    tmp_path: Path,
) -> None:
    body = _body()
    parent_snapshot = _fixture_expected().snapshot
    parent_fixture = _fixture_expected()
    laundering = parent_fixture.to_dict()
    laundering["evidence_use"] = "research_development_historical"
    laundering["root_canonical_sha256"] = _sha(
        {key: value for key, value in laundering.items() if key != "root_canonical_sha256"}
    )
    body["trusted_snapshot_root"] = parent_snapshot.to_dict()
    body["trusted_fixture_root"] = laundering
    with pytest.raises(RegistrationV2Error, match="parent trusted root"):
        publish_registration_v2(
            tmp_path / "registrations",
            body=body,
            expected_snapshot=parent_snapshot,
            expected_fixture=parent_fixture,
        )
    assert not (tmp_path / "registrations").exists()


def test_parent_anchor_rejects_fully_resigned_registration_before_side_effects(
    tmp_path: Path,
) -> None:
    result = _publish(tmp_path / "registrations")
    original_root = Path(result["registration_root"])
    registration = json.loads((original_root / "registration.json").read_text(encoding="utf-8"))
    attacked_snapshot_root, attacked_fixture_root = (
        _reencoded_snapshot_with_fully_resigned_fixture(tmp_path / "resigned-inputs")
    )
    _assert_snapshot_identity_resigned(attacked_snapshot_root)
    _assert_fixture_identity_resigned(attacked_fixture_root)
    attacked_snapshot, attacked_fixture = _expected_from_resigned_roots(
        attacked_snapshot_root,
        attacked_fixture_root,
    )
    attacked_fixture_payload = attacked_fixture.to_dict()
    attacked_fixture_payload["evidence_use"] = "research_development_historical"
    attacked_fixture_payload["root_canonical_sha256"] = _sha(
        {
            key: value
            for key, value in attacked_fixture_payload.items()
            if key != "root_canonical_sha256"
        }
    )
    registration["body"]["trusted_snapshot_root"] = attacked_snapshot.to_dict()
    registration["body"]["trusted_fixture_root"] = attacked_fixture_payload
    registration["body_canonical_sha256"] = _sha(registration["body"])
    registration["registration_id"] = _sha(
        {
            "schema": "experiment-038-registration-identity/v2",
            "body_canonical_sha256": registration["body_canonical_sha256"],
        }
    )
    unsigned = dict(registration)
    unsigned.pop("registration_canonical_sha256", None)
    registration["registration_canonical_sha256"] = _sha(unsigned)
    attacked = tmp_path / "attacked" / registration["registration_id"]
    attacked.mkdir(parents=True)
    (attacked / "registration.json").write_bytes(_canonical_bytes(registration) + b"\n")
    counters = {"outputs": 0, "network": 0, "purged": 0}
    def side_effects(_registration):
        counters["outputs"] += 1
        counters["network"] += 1
        counters["purged"] += 1

    with pytest.raises(RegistrationV2Error):
        run_frozen_registration_v2(
            attacked,
            expected_file_sha256=result["registration_file_sha256"],
            expected_canonical_sha256=result["registration_canonical_sha256"],
            runner=side_effects,
        )
    assert counters == {"outputs": 0, "network": 0, "purged": 0}


def test_registration_writer_claim_refuses_second_writer_before_staging(
    tmp_path: Path,
) -> None:
    body = _body()
    body_sha = _sha(body)
    registration_id = _sha(
        {
            "schema": "experiment-038-registration-identity/v2",
            "body_canonical_sha256": body_sha,
        }
    )
    parent = tmp_path / "registrations"
    parent.mkdir()
    lock = parent / f".registration-v2-{registration_id}.lock"
    lock.write_text("held", encoding="ascii")
    with pytest.raises(RegistrationV2Error, match="writer claim"):
        publish_registration_v2(
            parent,
            body=body,
            expected_snapshot=TrustedSnapshotRootV1.from_dict(
                body["trusted_snapshot_root"]
            ),
            expected_fixture=TrustedFixtureRootV1.from_dict(
                body["trusted_fixture_root"]
            ),
        )
    assert sorted(path.name for path in parent.iterdir()) == [lock.name]


def test_registration_target_appearing_at_atomic_publish_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "registrations"
    real_no_replace = registration_module._rename_no_replace

    def competing_target(source, target):
        target_path = Path(target)
        target_path.mkdir()
        (target_path / "other-writer.txt").write_text("owned", encoding="utf-8")
        return real_no_replace(source, target)

    monkeypatch.setattr(
        registration_module,
        "_rename_no_replace",
        competing_target,
    )
    with pytest.raises(RegistrationV2Error, match="target already exists"):
        _publish(parent)
    targets = [path for path in parent.iterdir() if path.is_dir() and not path.name.startswith(".")]
    assert len(targets) == 1
    assert (targets[0] / "other-writer.txt").read_text(encoding="utf-8") == "owned"


def test_file_sha_rejects_encoding_only_change_with_same_canonical(tmp_path: Path) -> None:
    result = _publish(tmp_path / "registrations")
    root = Path(result["registration_root"])
    path = root / "registration.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(RegistrationV2Error):
        load_frozen_registration(
            root,
            expected_file_sha256=result["registration_file_sha256"],
            expected_canonical_sha256=result["registration_canonical_sha256"],
        )
