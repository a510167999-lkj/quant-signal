from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import pickle
import re
import traceback
from collections.abc import Mapping

import pytest

from app import jiaoch_credential_slots
from app import factor_v3_feature_history_runner
from app.jiaoch_credential_slots import (
    HISTORICAL_MINUTE_ENV,
    POINTS_PRIMARY_ENV,
    JiaochCredentialGeneration,
    create_jiaoch_credential_generation,
)


POINTS_TOKEN = "points-secret-value"
MINUTE_TOKEN = "minute-secret-value"
POLICY_SHA256 = "de38737b3d44bc9730b5504a622ef88eb7fd517b6a7fb8c42255702d02445694"
ROUTE_MATRIX = (
    (
        "points-primary:daily_basic",
        "points-primary",
        "daily_basic",
        "points-interface",
    ),
    (
        "points-primary:moneyflow",
        "points-primary",
        "moneyflow",
        "points-interface",
    ),
    (
        "historical-minute:stk_mins",
        "historical-minute",
        "stk_mins",
        "historical-minute",
    ),
    (
        "historical-minute:calibration-daily",
        "historical-minute",
        "daily",
        "minute-calibration",
    ),
)


def _snapshot(
    *,
    points_token: str = POINTS_TOKEN,
    minute_token: str = MINUTE_TOKEN,
) -> dict[str, str]:
    return {
        POINTS_PRIMARY_ENV: points_token,
        HISTORICAL_MINUTE_ENV: minute_token,
    }


def _assert_text_is_secret_free(value: object) -> None:
    rendered = str(value).encode()
    represented = repr(value).encode()
    for token in (POINTS_TOKEN, MINUTE_TOKEN):
        assert token.encode() not in rendered
        assert token.encode() not in represented
        digest = hashlib.sha256(token.encode()).hexdigest().encode()
        assert digest not in rendered
        assert digest not in represented


def _assert_public_descriptions_are_secret_free(
    generation: JiaochCredentialGeneration,
) -> None:
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
        assert item.policy_sha256 == POLICY_SHA256
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.credential_slot_id = "other"
    _assert_text_is_secret_free(generation)
    _assert_text_is_secret_free(descriptions)


def test_policy_is_one_deeply_immutable_versioned_routing_source() -> None:
    policy = jiaoch_credential_slots._POLICY
    assert policy.schema == "jiaoch-credential-routing-policy/v1"
    assert (
        tuple(
            (
                route.route_id,
                route.credential_slot_id,
                route.api_name,
                route.purpose,
            )
            for route in policy.routes
        )
        == ROUTE_MATRIX
    )
    assert jiaoch_credential_slots._POLICY_SHA256 == POLICY_SHA256
    assert jiaoch_credential_slots._ROUTES_BY_ID == {
        route.route_id: route for route in policy.routes
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.schema = "changed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.routes[0].api_name = "daily"
    with pytest.raises(TypeError):
        policy.routes[0] = policy.routes[1]
    with pytest.raises(TypeError):
        jiaoch_credential_slots._ROUTES_BY_ID["other"] = policy.routes[0]


def test_factory_resolves_each_slot_once_and_public_surface_never_returns_credentials() -> None:
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
    assert {name for name in dir(generation) if not name.startswith("_")} == {"describe_slots"}
    assert not hasattr(generation, "credential_for_points_api")
    assert not hasattr(generation, "credential_for_historical_minute_api")
    assert not hasattr(generation, "credential_for_calibration_daily")
    assert not hasattr(generation, "credential_for_slot")
    assert not hasattr(generation, "fallback")
    assert not hasattr(generation, "credentials")
    assert inspect.signature(generation.describe_slots).parameters == {}
    _assert_public_descriptions_are_secret_free(generation)
    assert calls == ["points-primary", "historical-minute"]


def test_private_route_hook_is_not_exported_and_rejects_external_capability() -> None:
    assert "_credential_for_route" not in jiaoch_credential_slots.__all__
    signature = inspect.signature(jiaoch_credential_slots._credential_for_route)
    assert set(signature.parameters) == {"generation", "route_id", "capability"}
    generation = create_jiaoch_credential_generation(environment_snapshot=_snapshot())
    with pytest.raises(ValueError, match="private route capability"):
        jiaoch_credential_slots._credential_for_route(
            generation,
            route_id="points-primary:daily_basic",
            capability=object(),
        )


def test_private_route_hook_follows_the_exact_policy_matrix() -> None:
    generation = create_jiaoch_credential_generation(environment_snapshot=_snapshot())
    expected_by_route = {
        "points-primary:daily_basic": POINTS_TOKEN,
        "points-primary:moneyflow": POINTS_TOKEN,
        "historical-minute:stk_mins": MINUTE_TOKEN,
        "historical-minute:calibration-daily": MINUTE_TOKEN,
    }
    for route_id, expected in expected_by_route.items():
        assert (
            jiaoch_credential_slots._credential_for_route(
                generation,
                route_id=route_id,
                capability=jiaoch_credential_slots._ROUTE_CAPABILITY,
            )
            == expected
        )
    with pytest.raises(ValueError, match="route rejected"):
        jiaoch_credential_slots._credential_for_route(
            generation,
            route_id="historical-minute:daily",
            capability=jiaoch_credential_slots._ROUTE_CAPABILITY,
        )
    with pytest.raises(ValueError, match="generation rejected"):
        jiaoch_credential_slots._credential_for_route(
            object(),
            route_id="points-primary:daily_basic",
            capability=jiaoch_credential_slots._ROUTE_CAPABILITY,
        )


def test_feature_history_route_capability_is_private_and_binds_five_points_routes() -> None:
    assert "_credential_for_feature_history_route" not in jiaoch_credential_slots.__all__
    assert "_FEATURE_HISTORY_ROUTE_CAPABILITY" not in jiaoch_credential_slots.__all__
    generation = jiaoch_credential_slots._create_feature_history_generation(
        credential_resolver=lambda _slot_id: POINTS_TOKEN
    )
    descriptor = jiaoch_credential_slots._feature_history_policy_descriptor()

    assert descriptor == (
        factor_v3_feature_history_runner.FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR
    )
    assert [route.api_name for route in jiaoch_credential_slots._FEATURE_HISTORY_POLICY.routes] == [
        "bak_basic",
        "daily",
        "adj_factor",
        "stk_limit",
        "suspend_d",
    ]
    for route in jiaoch_credential_slots._FEATURE_HISTORY_POLICY.routes:
        assert route.credential_slot_id == "points-primary"
        assert route.purpose == "factor-v3-feature-history"
        assert (
            jiaoch_credential_slots._credential_for_feature_history_route(
                generation,
                route_id=route.route_id,
                capability=jiaoch_credential_slots._FEATURE_HISTORY_ROUTE_CAPABILITY,
            )
            == POINTS_TOKEN
        )
    with pytest.raises(ValueError, match="feature-history route capability"):
        jiaoch_credential_slots._credential_for_feature_history_route(
            generation,
            route_id="feature-history:points-primary:daily",
            capability=object(),
        )


def test_feature_history_collection_set_passes_one_sealed_points_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation = jiaoch_credential_slots._create_feature_history_generation(
        credential_resolver=lambda _slot_id: POINTS_TOKEN
    )
    calls: list[dict[str, object]] = []

    def run_closed(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"status": "verified", "token_exposed": False}

    monkeypatch.setattr(
        factor_v3_feature_history_runner,
        "_run_factor_v3_feature_history_collection_with_route_credential",
        run_closed,
    )
    result = jiaoch_credential_slots.collect_jiaoch_feature_history_collection_set(
        generation=generation,
        run_spec_path="C:/safe/run-spec.json",
        run_root="C:/safe/run",
    )

    assert result == {"status": "verified", "token_exposed": False}
    assert len(calls) == 1
    assert calls[0]["credential"] == POINTS_TOKEN
    assert calls[0]["feature_history_policy_descriptor"] == (
        factor_v3_feature_history_runner.FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR
    )
    assert calls[0]["source_generation_id"] == generation.describe_slots()[0].generation_id
    _assert_text_is_secret_free(result)


def test_feature_history_environment_entry_reads_only_points_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    environment_reads: list[str] = []

    def getenv(name: str) -> str:
        environment_reads.append(name)
        if name == POINTS_PRIMARY_ENV:
            return POINTS_TOKEN
        pytest.fail("feature-history must not read the minute credential")

    def run_closed(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"status": "verified"}

    monkeypatch.setattr(jiaoch_credential_slots.os, "getenv", getenv)
    monkeypatch.setattr(
        factor_v3_feature_history_runner,
        "_run_factor_v3_feature_history_collection_with_route_credential",
        run_closed,
    )

    result = jiaoch_credential_slots.collect_jiaoch_feature_history_from_environment(
        run_spec_path="C:/safe/run-spec.json",
        run_root="C:/safe/run",
    )

    assert result == {"status": "verified"}
    assert environment_reads == [POINTS_PRIMARY_ENV]
    assert calls[0]["credential"] == POINTS_TOKEN


def test_environment_snapshot_is_copied_without_live_environment_access() -> None:
    snapshot = _snapshot()
    generation = create_jiaoch_credential_generation(environment_snapshot=snapshot)
    before = generation.describe_slots()
    snapshot[POINTS_PRIMARY_ENV] = "rotated-points"
    snapshot[HISTORICAL_MINUTE_ENV] = "rotated-minute"

    assert generation.describe_slots() == before
    _assert_public_descriptions_are_secret_free(generation)


def test_public_constructor_cannot_inject_generation_id_or_credentials() -> None:
    with pytest.raises(TypeError, match="factory"):
        JiaochCredentialGeneration()
    with pytest.raises(TypeError, match="factory"):
        JiaochCredentialGeneration(
            generation_id="chosen",
            points_primary=POINTS_TOKEN,
            historical_minute=MINUTE_TOKEN,
        )


def test_generation_is_not_mutable_copyable_or_serializable() -> None:
    generation = create_jiaoch_credential_generation(environment_snapshot=_snapshot())
    with pytest.raises(AttributeError, match="immutable"):
        generation.extra = "value"
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError, match="cannot be copied or serialized"):
            operation(generation)


def test_generation_id_and_policy_hash_do_not_depend_on_credentials() -> None:
    first = create_jiaoch_credential_generation(environment_snapshot=_snapshot())
    second = create_jiaoch_credential_generation(
        environment_snapshot=_snapshot(
            points_token="different-points",
            minute_token="different-minute",
        )
    )

    first_descriptions = first.describe_slots()
    second_descriptions = second.describe_slots()
    assert first_descriptions[0].generation_id != second_descriptions[0].generation_id
    assert first_descriptions[0].policy_sha256 == second_descriptions[0].policy_sha256
    assert {item.generation_id for item in first_descriptions} == {
        first_descriptions[0].generation_id
    }
    assert {item.policy_sha256 for item in first_descriptions} == {POLICY_SHA256}


def test_identical_physical_credentials_are_rejected_without_secret_derivatives() -> None:
    with pytest.raises(ValueError, match="distinct") as caught:
        create_jiaoch_credential_generation(
            environment_snapshot=_snapshot(minute_token=POINTS_TOKEN)
        )
    _assert_text_is_secret_free(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


class _ExplodingSnapshot(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        raise RuntimeError(MINUTE_TOKEN)

    def __iter__(self):
        return iter((POINTS_PRIMARY_ENV, HISTORICAL_MINUTE_ENV))

    def __len__(self) -> int:
        return 2

    def get(self, key: str, default=None):
        raise RuntimeError(MINUTE_TOKEN)


@pytest.mark.parametrize("source_kind", ["resolver", "snapshot"])
def test_source_failures_are_normalized_without_secret_exception_context(
    source_kind: str,
) -> None:
    def exploding_resolver(slot_id: str) -> str:
        raise RuntimeError(POINTS_TOKEN)

    kwargs = (
        {"credential_resolver": exploding_resolver}
        if source_kind == "resolver"
        else {"environment_snapshot": _ExplodingSnapshot()}
    )
    with pytest.raises(ValueError, match="resolution rejected") as caught:
        create_jiaoch_credential_generation(**kwargs)

    error = caught.value
    assert error.__context__ is None
    assert error.__cause__ is None
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    _assert_text_is_secret_free(error)
    _assert_text_is_secret_free(formatted)


@pytest.mark.parametrize("source_kind", ["resolver", "snapshot"])
def test_surrogate_credentials_are_rejected_without_unicode_error_leak(
    source_kind: str,
) -> None:
    surrogate_secret = "bad\ud800token"

    def resolver(slot_id: str) -> str:
        return surrogate_secret if slot_id == "points-primary" else MINUTE_TOKEN

    kwargs = (
        {"credential_resolver": resolver}
        if source_kind == "resolver"
        else {
            "environment_snapshot": _snapshot(
                points_token=surrogate_secret,
            )
        }
    )
    with pytest.raises(ValueError, match="credential rejected") as caught:
        create_jiaoch_credential_generation(**kwargs)

    error = caught.value
    assert error.__context__ is None
    assert error.__cause__ is None
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert surrogate_secret not in str(error)
    assert surrogate_secret not in repr(error)
    assert surrogate_secret not in formatted


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {
            "credential_resolver": lambda slot_id: POINTS_TOKEN,
            "environment_snapshot": _snapshot(),
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
    with pytest.raises(ValueError, match="credential") as caught:
        create_jiaoch_credential_generation(**kwargs)
    _assert_text_is_secret_free(caught.value)
