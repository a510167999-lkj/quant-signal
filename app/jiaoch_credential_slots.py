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
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


__all__ = (
    "HISTORICAL_MINUTE_ENV",
    "JiaochCredentialGeneration",
    "JiaochCredentialSlotDescription",
    "POINTS_PRIMARY_ENV",
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


_ROUTE_CAPABILITY = object()


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
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("Jiaoch credential rejected")
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
