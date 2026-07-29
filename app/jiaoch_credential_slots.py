"""Sealed runtime credential generations for closed Jiaoch routes.

The public object exposes descriptions only. A future collection-set must be
implemented in this module and consume the module-private route capability
directly; no callback or public credential getter is provided.

This is an accidental-misuse boundary, not isolation from hostile Python code
in the same process. Python reflection can bypass private attributes, so
untrusted collection code requires a separate process boundary. Generation IDs
and policy hashes describe a frozen runtime mapping; neither is cryptographic
proof of credential identity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any


__all__ = (
    "HISTORICAL_MINUTE_ENV",
    "JiaochCredentialGeneration",
    "JiaochCredentialSlotDescription",
    "POINTS_PRIMARY_ENV",
    "collect_jiaoch_feature_history_collection_set",
    "collect_jiaoch_feature_history_from_environment",
    "collect_jiaoch_historical_minute_collection_set",
    "collect_jiaoch_points_collection_set",
    "collect_jiaoch_trade_cal_authority",
    "create_jiaoch_credential_generation",
)

POINTS_PRIMARY_ENV = "JIAOCH_TOKEN"
HISTORICAL_MINUTE_ENV = "JIAOCH_STK_MINS_TOKEN"

_POINTS_PRIMARY_SLOT = "points-primary"
_HISTORICAL_MINUTE_SLOT = "historical-minute"


@dataclass(frozen=True, slots=True)
class _RoutePolicy:
    route_id: str
    credential_slot_id: str
    api_name: str
    purpose: str


@dataclass(frozen=True, slots=True)
class _RoutingPolicy:
    schema: str
    routes: tuple[_RoutePolicy, ...]


_POLICY = _RoutingPolicy(
    schema="jiaoch-credential-routing-policy/v1",
    routes=(
        _RoutePolicy(
            route_id="points-primary:daily_basic",
            credential_slot_id=_POINTS_PRIMARY_SLOT,
            api_name="daily_basic",
            purpose="points-interface",
        ),
        _RoutePolicy(
            route_id="points-primary:moneyflow",
            credential_slot_id=_POINTS_PRIMARY_SLOT,
            api_name="moneyflow",
            purpose="points-interface",
        ),
        _RoutePolicy(
            route_id="historical-minute:stk_mins",
            credential_slot_id=_HISTORICAL_MINUTE_SLOT,
            api_name="stk_mins",
            purpose="historical-minute",
        ),
        _RoutePolicy(
            route_id="historical-minute:calibration-daily",
            credential_slot_id=_HISTORICAL_MINUTE_SLOT,
            api_name="daily",
            purpose="minute-calibration",
        ),
    ),
)
_SLOT_ORDER = tuple(dict.fromkeys(route.credential_slot_id for route in _POLICY.routes))
_ENV_BY_SLOT = MappingProxyType(
    {
        _POINTS_PRIMARY_SLOT: POINTS_PRIMARY_ENV,
        _HISTORICAL_MINUTE_SLOT: HISTORICAL_MINUTE_ENV,
    }
)
_ROUTES_BY_ID = MappingProxyType({route.route_id: route for route in _POLICY.routes})


def _policy_document(policy: _RoutingPolicy) -> dict[str, Any]:
    return {
        "routes": [
            {
                "api_name": route.api_name,
                "credential_slot_id": route.credential_slot_id,
                "purpose": route.purpose,
                "route_id": route.route_id,
            }
            for route in policy.routes
        ],
        "schema": policy.schema,
    }


_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        _policy_document(_POLICY),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()

_AUXILIARY_POLICY = _RoutingPolicy(
    schema="jiaoch-credential-auxiliary-routing-policy/v1",
    routes=(
        _RoutePolicy(
            route_id="auxiliary:points-primary:trade_cal",
            credential_slot_id=_POINTS_PRIMARY_SLOT,
            api_name="trade_cal",
            purpose="exchange-calendar",
        ),
    ),
)
_AUXILIARY_ROUTES_BY_ID = MappingProxyType(
    {route.route_id: route for route in _AUXILIARY_POLICY.routes}
)
_AUXILIARY_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        _policy_document(_AUXILIARY_POLICY),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()

_FEATURE_HISTORY_POLICY = _RoutingPolicy(
    schema="jiaoch-credential-feature-history-routing-policy/v1",
    routes=tuple(
        _RoutePolicy(
            route_id=f"feature-history:points-primary:{api_name}",
            credential_slot_id=_POINTS_PRIMARY_SLOT,
            api_name=api_name,
            purpose="factor-v3-feature-history",
        )
        for api_name in ("bak_basic", "daily", "adj_factor", "stk_limit", "suspend_d")
    ),
)
_FEATURE_HISTORY_ROUTES_BY_ID = MappingProxyType(
    {route.route_id: route for route in _FEATURE_HISTORY_POLICY.routes}
)
_FEATURE_HISTORY_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        _policy_document(_FEATURE_HISTORY_POLICY),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()


def _auxiliary_policy_descriptor() -> dict[str, Any]:
    return {
        "document": _policy_document(_AUXILIARY_POLICY),
        "schema": "jiaoch-credential-auxiliary-policy-descriptor/v1",
        "sha256": _AUXILIARY_POLICY_SHA256,
    }


def _feature_history_policy_descriptor() -> dict[str, Any]:
    return {
        "credential_proof_claimed": False,
        "document": _policy_document(_FEATURE_HISTORY_POLICY),
        "schema": "jiaoch-credential-feature-history-policy-descriptor/v1",
        "sha256": _FEATURE_HISTORY_POLICY_SHA256,
    }


@dataclass(frozen=True, slots=True)
class JiaochCredentialSlotDescription:
    credential_slot_id: str
    generation_id: str
    policy_sha256: str


class JiaochCredentialGeneration:
    __slots__ = ("__credentials", "__generation_id")

    def __new__(cls, *args: Any, **kwargs: Any) -> JiaochCredentialGeneration:
        raise TypeError("Jiaoch credential generation must be created by its factory")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Jiaoch credential generation is immutable")

    def __dir__(self) -> list[str]:
        return ["describe_slots"]

    def __repr__(self) -> str:
        return f"JiaochCredentialGeneration(slots={self.describe_slots()!r})"

    def __copy__(self) -> None:
        raise TypeError("Jiaoch credential generation cannot be copied or serialized")

    def __deepcopy__(self, memo: object) -> None:
        raise TypeError("Jiaoch credential generation cannot be copied or serialized")

    def __reduce__(self) -> None:
        raise TypeError("Jiaoch credential generation cannot be copied or serialized")

    def __reduce_ex__(self, protocol: int) -> None:
        raise TypeError("Jiaoch credential generation cannot be copied or serialized")

    def __getstate__(self) -> None:
        raise TypeError("Jiaoch credential generation cannot be copied or serialized")

    def describe_slots(self) -> tuple[JiaochCredentialSlotDescription, ...]:
        generation_id = object.__getattribute__(
            self,
            "_JiaochCredentialGeneration__generation_id",
        )
        return tuple(
            JiaochCredentialSlotDescription(
                credential_slot_id=slot_id,
                generation_id=generation_id,
                policy_sha256=_POLICY_SHA256,
            )
            for slot_id in _SLOT_ORDER
        )


class _FeatureHistoryCredentialGeneration:
    """One-slot sealed generation reserved for feature-history collection."""

    __slots__ = ("__credential", "__generation_id")

    def __new__(cls, *args: Any, **kwargs: Any) -> _FeatureHistoryCredentialGeneration:
        raise TypeError("Jiaoch feature-history generation must be created by its factory")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Jiaoch feature-history generation is immutable")

    def __dir__(self) -> list[str]:
        return ["describe_slots"]

    def __repr__(self) -> str:
        return f"JiaochFeatureHistoryCredentialGeneration(slots={self.describe_slots()!r})"

    def __copy__(self) -> None:
        raise TypeError("Jiaoch feature-history generation cannot be copied or serialized")

    def __deepcopy__(self, memo: object) -> None:
        raise TypeError("Jiaoch feature-history generation cannot be copied or serialized")

    def __reduce__(self) -> None:
        raise TypeError("Jiaoch feature-history generation cannot be copied or serialized")

    def __reduce_ex__(self, protocol: int) -> None:
        raise TypeError("Jiaoch feature-history generation cannot be copied or serialized")

    def __getstate__(self) -> None:
        raise TypeError("Jiaoch feature-history generation cannot be copied or serialized")

    def describe_slots(self) -> tuple[JiaochCredentialSlotDescription, ...]:
        generation_id = object.__getattribute__(
            self,
            "_FeatureHistoryCredentialGeneration__generation_id",
        )
        return (
            JiaochCredentialSlotDescription(
                credential_slot_id=_POINTS_PRIMARY_SLOT,
                generation_id=generation_id,
                policy_sha256=_FEATURE_HISTORY_POLICY_SHA256,
            ),
        )


_ROUTE_CAPABILITY = object()
_AUXILIARY_ROUTE_CAPABILITY = object()
_FEATURE_HISTORY_ROUTE_CAPABILITY = object()


def _credential_for_route(
    generation: JiaochCredentialGeneration,
    *,
    route_id: str,
    capability: object,
) -> str:
    """Private hook reserved for a future collection-set implemented here."""

    if capability is not _ROUTE_CAPABILITY:
        raise ValueError("Jiaoch private route capability rejected")
    if type(generation) is not JiaochCredentialGeneration:
        raise ValueError("Jiaoch credential generation rejected")
    if type(route_id) is not str or route_id not in _ROUTES_BY_ID:
        raise ValueError("Jiaoch credential route rejected")
    route = _ROUTES_BY_ID[route_id]
    credentials = object.__getattribute__(
        generation,
        "_JiaochCredentialGeneration__credentials",
    )
    return credentials[_SLOT_ORDER.index(route.credential_slot_id)]


def _credential_for_auxiliary_route(
    generation: JiaochCredentialGeneration,
    *,
    route_id: str,
    capability: object,
) -> str:
    if capability is not _AUXILIARY_ROUTE_CAPABILITY:
        raise ValueError("Jiaoch private auxiliary route capability rejected")
    if type(generation) is not JiaochCredentialGeneration:
        raise ValueError("Jiaoch credential generation rejected")
    if type(route_id) is not str or route_id not in _AUXILIARY_ROUTES_BY_ID:
        raise ValueError("Jiaoch credential auxiliary route rejected")
    route = _AUXILIARY_ROUTES_BY_ID[route_id]
    credentials = object.__getattribute__(
        generation,
        "_JiaochCredentialGeneration__credentials",
    )
    return credentials[_SLOT_ORDER.index(route.credential_slot_id)]


def _credential_for_feature_history_route(
    generation: _FeatureHistoryCredentialGeneration,
    *,
    route_id: str,
    capability: object,
) -> str:
    if capability is not _FEATURE_HISTORY_ROUTE_CAPABILITY:
        raise ValueError("Jiaoch private feature-history route capability rejected")
    if type(generation) is not _FeatureHistoryCredentialGeneration:
        raise ValueError("Jiaoch feature-history credential generation rejected")
    if type(route_id) is not str or route_id not in _FEATURE_HISTORY_ROUTES_BY_ID:
        raise ValueError("Jiaoch feature-history credential route rejected")
    route = _FEATURE_HISTORY_ROUTES_BY_ID[route_id]
    if route.credential_slot_id != _POINTS_PRIMARY_SLOT:
        raise ValueError("Jiaoch feature-history credential route rejected")
    return object.__getattribute__(
        generation,
        "_FeatureHistoryCredentialGeneration__credential",
    )


_RESOLUTION_FAILED = object()


def _resolved_from_callable(
    resolver: Callable[[str], str],
) -> tuple[Any, ...] | object:
    try:
        return tuple(resolver(slot_id) for slot_id in _SLOT_ORDER)
    except Exception:
        return _RESOLUTION_FAILED


def _resolved_from_snapshot(snapshot: Mapping[str, str]) -> tuple[Any, ...] | object:
    try:
        if not isinstance(snapshot, Mapping):
            return _RESOLUTION_FAILED
        return tuple(snapshot.get(_ENV_BY_SLOT[slot_id]) for slot_id in _SLOT_ORDER)
    except Exception:
        return _RESOLUTION_FAILED


def _credential(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or value.strip() != value
        or any(
            ord(character) < 32 or ord(character) == 127 or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    ):
        raise ValueError("Jiaoch credential rejected")
    return value


def _credential_generation_id(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("Jiaoch credential generation rejected")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Jiaoch credential generation rejected") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("Jiaoch credential generation rejected")
    return value


def _seal_generation(credentials: tuple[str, ...]) -> JiaochCredentialGeneration:
    generation = object.__new__(JiaochCredentialGeneration)
    object.__setattr__(
        generation,
        "_JiaochCredentialGeneration__credentials",
        credentials,
    )
    object.__setattr__(
        generation,
        "_JiaochCredentialGeneration__generation_id",
        str(uuid.uuid4()),
    )
    return generation


def _create_feature_history_generation(
    *,
    credential_resolver: Callable[[str], str],
    source_generation_id: str | None = None,
) -> _FeatureHistoryCredentialGeneration:
    if not callable(credential_resolver):
        raise ValueError("Jiaoch feature-history credential resolution rejected")
    try:
        credential = _credential(credential_resolver(_POINTS_PRIMARY_SLOT))
    except Exception as exc:
        raise ValueError("Jiaoch feature-history credential resolution rejected") from exc
    generation = object.__new__(_FeatureHistoryCredentialGeneration)
    object.__setattr__(
        generation,
        "_FeatureHistoryCredentialGeneration__credential",
        credential,
    )
    object.__setattr__(
        generation,
        "_FeatureHistoryCredentialGeneration__generation_id",
        (
            str(uuid.uuid4())
            if source_generation_id is None
            else _credential_generation_id(source_generation_id)
        ),
    )
    return generation


def create_jiaoch_credential_generation(
    *,
    credential_resolver: Callable[[str], str] | None = None,
    environment_snapshot: Mapping[str, str] | None = None,
) -> JiaochCredentialGeneration:
    """Resolve both credentials once and return a sealed descriptive handle."""

    if (credential_resolver is None) == (environment_snapshot is None):
        raise ValueError("exactly one Jiaoch credential source is required")

    if credential_resolver is not None:
        if not callable(credential_resolver):
            raise ValueError("Jiaoch credential resolution rejected")
        resolved = _resolved_from_callable(credential_resolver)
    else:
        resolved = _resolved_from_snapshot(environment_snapshot)
    if resolved is _RESOLUTION_FAILED:
        raise ValueError("Jiaoch credential resolution rejected")

    credentials = tuple(_credential(value) for value in resolved)
    if len(credentials) != len(_SLOT_ORDER):
        raise ValueError("Jiaoch credential resolution rejected")
    if hmac.compare_digest(credentials[0].encode(), credentials[1].encode()):
        raise ValueError("distinct Jiaoch credentials are required")
    return _seal_generation(credentials)


def collect_jiaoch_feature_history_collection_set(
    *,
    generation: _FeatureHistoryCredentialGeneration,
    run_spec_path: str | Path,
    run_root: str | Path,
) -> dict[str, Any]:
    """Run the closed five-route feature-history collector with one points slot."""

    credentials = [
        _credential_for_feature_history_route(
            generation,
            route_id=route.route_id,
            capability=_FEATURE_HISTORY_ROUTE_CAPABILITY,
        )
        for route in _FEATURE_HISTORY_POLICY.routes
    ]
    if not credentials or any(
        not hmac.compare_digest(credentials[0].encode("utf-8"), credential.encode("utf-8"))
        for credential in credentials[1:]
    ):
        raise ValueError("Jiaoch feature-history route mapping rejected")
    descriptions = generation.describe_slots()
    if (
        len(descriptions) != 1
        or descriptions[0].credential_slot_id != _POINTS_PRIMARY_SLOT
        or descriptions[0].policy_sha256 != _FEATURE_HISTORY_POLICY_SHA256
    ):
        raise ValueError("Jiaoch feature-history points slot rejected")
    from app.factor_v3_feature_history_runner import (
        _run_factor_v3_feature_history_collection_with_route_credential,
    )

    return _run_factor_v3_feature_history_collection_with_route_credential(
        run_spec_path=run_spec_path,
        run_root=run_root,
        credential=credentials[0],
        source_generation_id=descriptions[0].generation_id,
        feature_history_policy_descriptor=_feature_history_policy_descriptor(),
    )


def _collect_jiaoch_feature_history_from_environment_for_run(
    *,
    run_spec_path: str | Path,
    run_root: str | Path,
    source_generation_id: str,
) -> dict[str, Any]:
    """Private runner bridge for a run-scoped generation identifier."""

    generation = _create_feature_history_generation(
        credential_resolver=lambda slot_id: str(os.getenv(_ENV_BY_SLOT[slot_id]) or ""),
        source_generation_id=source_generation_id,
    )
    return collect_jiaoch_feature_history_collection_set(
        generation=generation,
        run_spec_path=run_spec_path,
        run_root=run_root,
    )


def collect_jiaoch_feature_history_from_environment(
    *, run_spec_path: str | Path, run_root: str | Path
) -> dict[str, Any]:
    """Resolve the points slot only, without exposing credential provenance control."""

    from app.factor_v3_feature_history_runner import _run_credential_generation_id

    return _collect_jiaoch_feature_history_from_environment_for_run(
        run_spec_path=run_spec_path,
        run_root=run_root,
        source_generation_id=_run_credential_generation_id(
            run_spec_path=run_spec_path,
            run_root=run_root,
        ),
    )


def collect_jiaoch_trade_cal_authority(
    *,
    generation: JiaochCredentialGeneration,
    output_root: str | Path,
    start_date: date,
    end_date: date,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Collect one sealed SSE trade-calendar window with the points credential."""

    failed = object()
    try:
        credential = _credential_for_auxiliary_route(
            generation,
            route_id="auxiliary:points-primary:trade_cal",
            capability=_AUXILIARY_ROUTE_CAPABILITY,
        )
        generation_id = object.__getattribute__(
            generation,
            "_JiaochCredentialGeneration__generation_id",
        )
        from app.jiaoch_trade_cal_authority import (
            _collect_jiaoch_trade_cal_with_route_credential,
        )

        result = _collect_jiaoch_trade_cal_with_route_credential(
            credential=credential,
            generation_id=generation_id,
            auxiliary_policy_descriptor=_auxiliary_policy_descriptor(),
            output_root=output_root,
            start_date=start_date,
            end_date=end_date,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        result = failed
    if result is failed:
        raise ValueError("Jiaoch trade calendar collection failed") from None
    return result


def collect_jiaoch_points_collection_set(
    *,
    generation: JiaochCredentialGeneration,
    output_root: str | Path,
    trade_date: date,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Issue the closed daily-basic and moneyflow collection with one sealed slot."""

    failed = object()
    try:
        daily_basic_credential = _credential_for_route(
            generation,
            route_id="points-primary:daily_basic",
            capability=_ROUTE_CAPABILITY,
        )
        moneyflow_credential = _credential_for_route(
            generation,
            route_id="points-primary:moneyflow",
            capability=_ROUTE_CAPABILITY,
        )
        if not hmac.compare_digest(
            daily_basic_credential.encode("utf-8"),
            moneyflow_credential.encode("utf-8"),
        ):
            raise ValueError("Jiaoch points route mapping rejected")
        descriptions = {item.credential_slot_id: item for item in generation.describe_slots()}
        descriptor = descriptions[_POINTS_PRIMARY_SLOT]
        from app.jiaoch_points_collection_set import (
            _collect_jiaoch_points_collection_set_with_route_credential,
        )

        result = _collect_jiaoch_points_collection_set_with_route_credential(
            credential=daily_basic_credential,
            generation_id=descriptor.generation_id,
            policy_sha256=descriptor.policy_sha256,
            output_root=output_root,
            trade_date=trade_date,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        result = failed
    if result is failed:
        raise ValueError("Jiaoch points collection failed") from None
    return result


def collect_jiaoch_historical_minute_collection_set(
    *,
    generation: JiaochCredentialGeneration,
    output_root: str | Path,
    requested_ts_code: str,
    execution_session: date,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Issue the closed 1m, 5m and daily collection with one sealed slot."""

    failed = object()
    try:
        minute_credential = _credential_for_route(
            generation,
            route_id="historical-minute:stk_mins",
            capability=_ROUTE_CAPABILITY,
        )
        daily_credential = _credential_for_route(
            generation,
            route_id="historical-minute:calibration-daily",
            capability=_ROUTE_CAPABILITY,
        )
        if not hmac.compare_digest(
            minute_credential.encode("utf-8"),
            daily_credential.encode("utf-8"),
        ):
            raise ValueError("Jiaoch historical-minute route mapping rejected")
        descriptions = {item.credential_slot_id: item for item in generation.describe_slots()}
        descriptor = descriptions[_HISTORICAL_MINUTE_SLOT]
        from app.jiaoch_minute_collection_set import (
            _collect_jiaoch_minute_collection_set_with_route_credential,
        )

        result = _collect_jiaoch_minute_collection_set_with_route_credential(
            credential=minute_credential,
            generation_id=descriptor.generation_id,
            policy_sha256=descriptor.policy_sha256,
            output_root=output_root,
            requested_ts_code=requested_ts_code,
            execution_session=execution_session,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        result = failed
    if result is failed:
        raise ValueError("Jiaoch historical-minute collection failed") from None
    return result
