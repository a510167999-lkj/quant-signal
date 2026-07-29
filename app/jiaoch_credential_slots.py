"""Immutable runtime credential slots for closed Jiaoch interface families."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


POINTS_PRIMARY_ENV = "JIAOCH_TOKEN"
HISTORICAL_MINUTE_ENV = "JIAOCH_STK_MINS_TOKEN"

_POINTS_PRIMARY_SLOT = "points-primary"
_HISTORICAL_MINUTE_SLOT = "historical-minute"
_SLOT_ORDER = (_POINTS_PRIMARY_SLOT, _HISTORICAL_MINUTE_SLOT)
_POINTS_APIS = frozenset({"daily_basic", "moneyflow"})
_POLICY = {
    "historical-minute": {
        "calibration_daily": True,
        "interfaces": ["stk_mins"],
    },
    "points-primary": {
        "interfaces": ["daily_basic", "moneyflow"],
    },
}
_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        _POLICY,
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
    __slots__ = (
        "__generation_id",
        "__historical_minute",
        "__points_primary",
        "__policy_sha256",
    )

    def __init__(
        self,
        *,
        generation_id: str,
        points_primary: str,
        historical_minute: str,
    ) -> None:
        object.__setattr__(self, "_JiaochCredentialGeneration__generation_id", generation_id)
        object.__setattr__(
            self,
            "_JiaochCredentialGeneration__points_primary",
            points_primary,
        )
        object.__setattr__(
            self,
            "_JiaochCredentialGeneration__historical_minute",
            historical_minute,
        )
        object.__setattr__(
            self,
            "_JiaochCredentialGeneration__policy_sha256",
            _POLICY_SHA256,
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Jiaoch credential generation is immutable")

    def __repr__(self) -> str:
        return f"JiaochCredentialGeneration(slots={self.describe_slots()!r})"

    def describe_slots(self) -> tuple[JiaochCredentialSlotDescription, ...]:
        return tuple(
            JiaochCredentialSlotDescription(
                credential_slot_id=slot_id,
                generation_id=self.__generation_id,
                policy_sha256=self.__policy_sha256,
            )
            for slot_id in _SLOT_ORDER
        )

    def credential_for_points_api(self, api_name: str) -> str:
        if type(api_name) is not str or api_name not in _POINTS_APIS:
            raise ValueError("points-primary Jiaoch interface rejected")
        return self.__points_primary

    def credential_for_historical_minute_api(self) -> str:
        return self.__historical_minute

    def credential_for_calibration_daily(self) -> str:
        return self.__historical_minute


def _credential(value: Any, *, slot_id: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or value.strip() != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{slot_id} credential rejected")
    return value


def create_jiaoch_credential_generation(
    *,
    credential_resolver: Callable[[str], str] | None = None,
    environment_snapshot: Mapping[str, str] | None = None,
) -> JiaochCredentialGeneration:
    """Resolve both credentials once and freeze the runtime slot mapping."""

    if (credential_resolver is None) == (environment_snapshot is None):
        raise ValueError("exactly one Jiaoch credential source is required")

    if credential_resolver is not None:
        if not callable(credential_resolver):
            raise ValueError("Jiaoch credential resolver rejected")
        points_primary = _credential(
            credential_resolver(_POINTS_PRIMARY_SLOT),
            slot_id=_POINTS_PRIMARY_SLOT,
        )
        historical_minute = _credential(
            credential_resolver(_HISTORICAL_MINUTE_SLOT),
            slot_id=_HISTORICAL_MINUTE_SLOT,
        )
    else:
        if not isinstance(environment_snapshot, Mapping):
            raise ValueError("Jiaoch credential environment snapshot rejected")
        points_primary = _credential(
            environment_snapshot.get(POINTS_PRIMARY_ENV),
            slot_id=_POINTS_PRIMARY_SLOT,
        )
        historical_minute = _credential(
            environment_snapshot.get(HISTORICAL_MINUTE_ENV),
            slot_id=_HISTORICAL_MINUTE_SLOT,
        )

    return JiaochCredentialGeneration(
        generation_id=str(uuid.uuid4()),
        points_primary=points_primary,
        historical_minute=historical_minute,
    )
