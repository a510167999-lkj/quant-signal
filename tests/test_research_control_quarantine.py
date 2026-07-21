from __future__ import annotations

import importlib

import pytest


_LEGACY_OPEN = [
    {
        "experiment_id": "auto-iter-034-current-pool-coverage-expansion",
        "sequence": 117,
        "record_hash": "68adc32daae82e7a11fe76fef888d47c8e004b3df9cc18a5fcf4547bb256f840",
        "event_type": "registered",
    },
    {
        "experiment_id": "auto-iter-038-h1-hold3-development",
        "sequence": 124,
        "record_hash": "7050a197d237b340c9f98db2ad1d02de30f36d1f7c7c39377f6e3bdb9fe70cbb",
        "event_type": "registered",
    },
]


def _quarantine_module():
    return importlib.import_module("app.research_control_quarantine")


quarantine = _quarantine_module()


def test_quarantine_binding_requires_the_exact_sorted_sealed_tuple():
    binding = quarantine.build_quarantine_binding_v1(_LEGACY_OPEN)

    assert quarantine.validate_quarantine_binding_v1(
        binding,
        expected_records=_LEGACY_OPEN,
        maximum_sequence=126,
    ) == binding

    for mutation in (
        lambda value: value["records"].reverse(),
        lambda value: value["records"].append(dict(value["records"][0])),
        lambda value: value["records"][0].__setitem__("sequence", 118),
        lambda value: value["records"][1].__setitem__("event_type", "failed"),
        lambda value: value.__setitem__("records_sha256", "0" * 64),
    ):
        tampered = {
            **binding,
            "records": [dict(item) for item in binding["records"]],
        }
        mutation(tampered)
        with pytest.raises(ValueError, match="quarantine"):
            quarantine.validate_quarantine_binding_v1(
                tampered,
                expected_records=_LEGACY_OPEN,
                maximum_sequence=126,
            )


def test_quarantine_open_set_allows_only_sealed_records_plus_current_target():
    binding = quarantine.build_quarantine_binding_v1(_LEGACY_OPEN)
    target = {
        "experiment_id": "auto-iter-038-h1-hold3-development-control-v4",
        "sequence": 127,
        "record_hash": "a" * 64,
        "event_type": "registered",
    }

    assert quarantine.validate_exact_open_set_v1(
        [*_LEGACY_OPEN, target],
        binding=binding,
        target=target,
    ) == [*_LEGACY_OPEN, target]

    for records in (
        _LEGACY_OPEN,
        [*_LEGACY_OPEN, target, {**target, "experiment_id": "unexpected", "sequence": 128}],
        [
            _LEGACY_OPEN[0],
            {**_LEGACY_OPEN[1], "record_hash": "b" * 64},
            target,
        ],
    ):
        with pytest.raises(ValueError, match="quarantine"):
            quarantine.validate_exact_open_set_v1(
                records,
                binding=binding,
                target=target,
            )


def test_frozen_quarantine_binding_cannot_be_replaced_by_another_signed_tuple():
    binding = quarantine.frozen_quarantine_binding_v1()

    assert quarantine.validate_frozen_quarantine_binding_v1(binding) == binding
    assert quarantine.quarantine_binding_sha256_v1(binding) == quarantine.quarantine_binding_sha256_v1(
        binding
    )

    replacement = quarantine.build_quarantine_binding_v1(
        [
            {
                **binding["records"][0],
                "record_hash": "e" * 64,
            },
            binding["records"][1],
        ]
    )
    with pytest.raises(ValueError, match="quarantine"):
        quarantine.validate_frozen_quarantine_binding_v1(replacement)
