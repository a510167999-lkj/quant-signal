from __future__ import annotations

import dataclasses
import hashlib
import inspect
import re

import pytest

from app.jiaoch_credential_slots import (
    HISTORICAL_MINUTE_ENV,
    POINTS_PRIMARY_ENV,
    create_jiaoch_credential_generation,
)


POINTS_TOKEN = "points-secret-value"
MINUTE_TOKEN = "minute-secret-value"


def _assert_public_descriptions_are_secret_free(generation) -> None:
    descriptions = generation.describe_slots()
    assert tuple(item.credential_slot_id for item in descriptions) == (
        "points-primary",
        "historical-minute",
    )
    for item in descriptions:
        assert set(dataclasses.asdict(item)) == {
            "credential_slot_id",
            "generation_id",
            "policy_sha256",
        }
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            item.generation_id,
        )
        assert re.fullmatch(r"[0-9a-f]{64}", item.policy_sha256)
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.credential_slot_id = "other"

    rendered = repr(descriptions).encode()
    for token in (POINTS_TOKEN, MINUTE_TOKEN):
        assert token.encode() not in rendered
        assert hashlib.sha256(token.encode()).hexdigest().encode() not in rendered


def test_generation_resolves_each_slot_once_and_freezes_the_mapping() -> None:
    live = {
        "points-primary": POINTS_TOKEN,
        "historical-minute": MINUTE_TOKEN,
    }
    calls: list[str] = []

    def resolver(slot_id: str) -> str:
        calls.append(slot_id)
        return live[slot_id]

    generation = create_jiaoch_credential_generation(credential_resolver=resolver)
    live["points-primary"] = "rotated-points"
    live["historical-minute"] = "rotated-minute"

    assert calls == ["points-primary", "historical-minute"]
    assert generation.credential_for_points_api("daily_basic") == POINTS_TOKEN
    assert generation.credential_for_points_api("moneyflow") == POINTS_TOKEN
    assert generation.credential_for_historical_minute_api() == MINUTE_TOKEN
    assert generation.credential_for_calibration_daily() == MINUTE_TOKEN
    assert calls == ["points-primary", "historical-minute"]
    _assert_public_descriptions_are_secret_free(generation)


def test_environment_snapshot_is_copied_and_never_read_live() -> None:
    snapshot = {
        POINTS_PRIMARY_ENV: POINTS_TOKEN,
        HISTORICAL_MINUTE_ENV: MINUTE_TOKEN,
    }
    generation = create_jiaoch_credential_generation(environment_snapshot=snapshot)
    snapshot[POINTS_PRIMARY_ENV] = "rotated-points"
    snapshot[HISTORICAL_MINUTE_ENV] = "rotated-minute"

    assert generation.credential_for_points_api("daily_basic") == POINTS_TOKEN
    assert generation.credential_for_historical_minute_api() == MINUTE_TOKEN


@pytest.mark.parametrize("api_name", ["daily", "stk_mins", "adj_factor", "", None])
def test_points_slot_rejects_every_non_points_interface(api_name) -> None:
    generation = create_jiaoch_credential_generation(
        environment_snapshot={
            POINTS_PRIMARY_ENV: POINTS_TOKEN,
            HISTORICAL_MINUTE_ENV: MINUTE_TOKEN,
        }
    )
    with pytest.raises(ValueError, match="points-primary"):
        generation.credential_for_points_api(api_name)


def test_minute_slot_exposes_only_stk_mins_and_explicit_calibration_daily() -> None:
    generation = create_jiaoch_credential_generation(
        environment_snapshot={
            POINTS_PRIMARY_ENV: POINTS_TOKEN,
            HISTORICAL_MINUTE_ENV: MINUTE_TOKEN,
        }
    )

    assert inspect.signature(generation.credential_for_historical_minute_api).parameters == {}
    assert inspect.signature(generation.credential_for_calibration_daily).parameters == {}
    assert not hasattr(generation, "credential_for_slot")
    assert not hasattr(generation, "fallback")
    assert not hasattr(generation, "credentials")


def test_generation_id_and_policy_hash_do_not_depend_on_credentials() -> None:
    first = create_jiaoch_credential_generation(
        environment_snapshot={
            POINTS_PRIMARY_ENV: POINTS_TOKEN,
            HISTORICAL_MINUTE_ENV: MINUTE_TOKEN,
        }
    )
    second = create_jiaoch_credential_generation(
        environment_snapshot={
            POINTS_PRIMARY_ENV: "different-points",
            HISTORICAL_MINUTE_ENV: "different-minute",
        }
    )

    first_descriptions = first.describe_slots()
    second_descriptions = second.describe_slots()
    assert first_descriptions[0].generation_id != second_descriptions[0].generation_id
    assert first_descriptions[0].policy_sha256 == second_descriptions[0].policy_sha256
    assert {item.generation_id for item in first_descriptions} == {
        first_descriptions[0].generation_id
    }
    assert {item.policy_sha256 for item in first_descriptions} == {
        first_descriptions[0].policy_sha256
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {
            "credential_resolver": lambda slot_id: POINTS_TOKEN,
            "environment_snapshot": {
                POINTS_PRIMARY_ENV: POINTS_TOKEN,
                HISTORICAL_MINUTE_ENV: MINUTE_TOKEN,
            },
        },
        {"environment_snapshot": {}},
        {
            "environment_snapshot": {
                POINTS_PRIMARY_ENV: POINTS_TOKEN,
                HISTORICAL_MINUTE_ENV: "",
            }
        },
    ],
)
def test_generation_requires_exactly_one_complete_credential_source(kwargs) -> None:
    with pytest.raises(ValueError, match="credential"):
        create_jiaoch_credential_generation(**kwargs)
