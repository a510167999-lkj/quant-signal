"""Development-only materializer v2 registration fixture surface.

This module does **not** publish production authority and does **not** enable
automatic trading. It documents and process-locally installs the opaque
registration slot surface that formal materializer v2 requires, so local
research can exercise readiness / dry-run gates under
``VPS_RUNTIME_ROLE=local_research`` without mutating durable production
registry defaults in source.

Permanent defaults in
``audited_pit_factor_v3_formal_materializer_v2_authority`` remain ``None``
(fail-closed). Real formal TCB/broker publication is a separate audited
two-commit authority path and is out of scope here.
"""

from __future__ import annotations

import hashlib
import os
from types import MappingProxyType
from typing import Any, Mapping

from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import audited_pit_factor_v3_formal_materializer_v2_contract as contract

FIXTURE_SCHEMA = (
    "factor-v3-materializer-v2-development-registration-fixture/v1"
)
REQUIRED_RUNTIME_ROLE = "local_research"

# Opaque slot names consumed by formal materializer v2 (source of truth).
REQUIRED_REGISTRATION_SLOT_NAMES: tuple[str, ...] = (
    "REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256",
    "REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256",
    "REGISTERED_MATERIALIZER_V2_PRODUCER_SOURCE_ROOT_SHA256",
    "REGISTERED_MATERIALIZER_V2_INDEPENDENT_SOURCE_ROOT_SHA256",
    "REGISTERED_MATERIALIZER_V2_RUNNER_SOURCE_ROOT_SHA256",
    "REGISTERED_MATERIALIZER_V2_ATTEMPT_LOCATOR_RAW_SHA256",
    "REGISTERED_MATERIALIZER_V2_NATIVE_BROKER",
    "REGISTERED_SQLITE_RUNTIME_TCB_SHA256",
)

# Hex-hash slots get synthetic development digests; broker is a sentinel object.
_HEX_SLOT_NAMES: tuple[str, ...] = tuple(
    name
    for name in REQUIRED_REGISTRATION_SLOT_NAMES
    if name != "REGISTERED_MATERIALIZER_V2_NATIVE_BROKER"
)

SAFETY_FALSE_FIELDS: tuple[str, ...] = tuple(contract.SAFETY_FALSE_FIELDS)

_HEX = frozenset("0123456789abcdef")


class DevelopmentMaterializerRegistrationError(ValueError):
    """Raised when development registration fixture install/validation fails."""


class _DevelopmentNativeBrokerSentinel:
    """Non-production broker placeholder; formal producer remains RED."""

    __slots__ = ("_label",)

    def __init__(self) -> None:
        self._label = "development-only-materializer-v2-broker-sentinel"

    def __repr__(self) -> str:
        return f"<DevelopmentNativeBrokerSentinel {self._label}>"


_SENTINEL_BROKER = _DevelopmentNativeBrokerSentinel()
_INSTALLED: dict[str, Any] | None = None
_PREVIOUS_SLOT_VALUES: dict[str, Any] | None = None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def development_registration_slot_values() -> Mapping[str, Any]:
    """Stable synthetic digests for local-research install only."""

    values: dict[str, Any] = {}
    for name in _HEX_SLOT_NAMES:
        values[name] = _sha256_text(
            f"{FIXTURE_SCHEMA}|development-only|{name}|no-production"
        )
    values["REGISTERED_MATERIALIZER_V2_NATIVE_BROKER"] = _SENTINEL_BROKER
    return MappingProxyType(values)


def fixture_descriptor() -> dict[str, Any]:
    slots = development_registration_slot_values()
    return {
        "schema": FIXTURE_SCHEMA,
        "development_only": True,
        "production_profile_registered": False,
        "automatic_trading_eligible": False,
        "formal_materialization_red_path_still_blocked": True,
        "required_runtime_role": REQUIRED_RUNTIME_ROLE,
        "required_slot_names": list(REQUIRED_REGISTRATION_SLOT_NAMES),
        "hex_slot_sha256_by_name": {
            name: slots[name] for name in _HEX_SLOT_NAMES
        },
        "safety": {field: False for field in SAFETY_FALSE_FIELDS},
        "notes": (
            "Install is process-local and reversible. It does not publish "
            "native TCB binaries or enable produce_registered_* beyond the "
            "existing RED NotImplementedError gate."
        ),
    }


def _assert_local_research_role() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str):
        raise DevelopmentMaterializerRegistrationError(
            "VPS_RUNTIME_ROLE must be a string"
        )
    normalized = role.strip().casefold()
    if normalized != REQUIRED_RUNTIME_ROLE:
        raise DevelopmentMaterializerRegistrationError(
            "development materializer registration fixture requires "
            f"VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} "
            f"(got {role!r})"
        )
    return normalized


def _validate_hex_digest(value: Any, *, label: str) -> str:
    if type(value) is not str or len(value) != 64 or set(value) - _HEX:
        raise DevelopmentMaterializerRegistrationError(
            f"{label} must be a 64-char lowercase hex digest"
        )
    return value


def validate_fixture_surface() -> dict[str, Any]:
    """Static readiness of the fixture design (no process mutation)."""

    missing = [
        name
        for name in REQUIRED_REGISTRATION_SLOT_NAMES
        if not hasattr(authority, name)
    ]
    if missing:
        raise DevelopmentMaterializerRegistrationError(
            f"authority module missing registration slots: {missing}"
        )
    slots = development_registration_slot_values()
    for name in _HEX_SLOT_NAMES:
        _validate_hex_digest(slots[name], label=name)
    if slots["REGISTERED_MATERIALIZER_V2_NATIVE_BROKER"] is not _SENTINEL_BROKER:
        raise DevelopmentMaterializerRegistrationError(
            "native broker sentinel drifted"
        )
    descriptor = fixture_descriptor()
    for field in SAFETY_FALSE_FIELDS:
        if descriptor["safety"].get(field) is not False:
            raise DevelopmentMaterializerRegistrationError(
                f"safety field {field} must remain False"
            )
    if descriptor["development_only"] is not True:
        raise DevelopmentMaterializerRegistrationError(
            "fixture must remain development_only"
        )
    if descriptor["production_profile_registered"] is not False:
        raise DevelopmentMaterializerRegistrationError(
            "fixture must not register production profile"
        )
    if descriptor["automatic_trading_eligible"] is not False:
        raise DevelopmentMaterializerRegistrationError(
            "fixture must not enable automatic trading"
        )
    return descriptor


def is_development_registration_fixture_ready() -> bool:
    try:
        validate_fixture_surface()
    except DevelopmentMaterializerRegistrationError:
        return False
    return True


def fixture_readiness_detail() -> str:
    try:
        descriptor = validate_fixture_surface()
    except DevelopmentMaterializerRegistrationError as exc:
        return f"fixture surface invalid: {exc}"
    installed = is_fixture_installed()
    production_closed = production_registration_slots_closed()
    return (
        f"schema={descriptor['schema']} "
        f"slots={len(REQUIRED_REGISTRATION_SLOT_NAMES)} "
        f"installed={installed} "
        f"production_slots_closed={production_closed} "
        f"red_path_blocked={descriptor['formal_materialization_red_path_still_blocked']}"
    )


def production_registration_slots_closed() -> bool:
    """True when durable module defaults (or current values) are fail-closed."""

    return (
        authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256 is None
        and authority.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256 is None
        and authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
    )


def is_fixture_installed() -> bool:
    return _INSTALLED is not None


def install_development_registration_fixture() -> dict[str, Any]:
    """Process-local install under local_research; reversible via uninstall."""

    global _INSTALLED, _PREVIOUS_SLOT_VALUES
    _assert_local_research_role()
    descriptor = validate_fixture_surface()
    if _INSTALLED is not None:
        raise DevelopmentMaterializerRegistrationError(
            "development materializer registration fixture already installed"
        )
    values = dict(development_registration_slot_values())
    previous: dict[str, Any] = {}
    for name in REQUIRED_REGISTRATION_SLOT_NAMES:
        previous[name] = getattr(authority, name)
        setattr(authority, name, values[name])
    _PREVIOUS_SLOT_VALUES = previous
    _INSTALLED = {
        "descriptor": descriptor,
        "slot_names": list(REQUIRED_REGISTRATION_SLOT_NAMES),
    }
    return {
        "installed": True,
        "development_only": True,
        "slot_count": len(REQUIRED_REGISTRATION_SLOT_NAMES),
        "schema": FIXTURE_SCHEMA,
        "production_profile_registered": False,
        "automatic_trading_eligible": False,
    }


def uninstall_development_registration_fixture() -> dict[str, Any]:
    """Restore authority slot values captured at install time."""

    global _INSTALLED, _PREVIOUS_SLOT_VALUES
    if _INSTALLED is None or _PREVIOUS_SLOT_VALUES is None:
        raise DevelopmentMaterializerRegistrationError(
            "development materializer registration fixture is not installed"
        )
    for name, value in _PREVIOUS_SLOT_VALUES.items():
        setattr(authority, name, value)
    _INSTALLED = None
    _PREVIOUS_SLOT_VALUES = None
    return {
        "installed": False,
        "production_slots_closed": production_registration_slots_closed(),
        "schema": FIXTURE_SCHEMA,
    }


__all__ = [
    "DevelopmentMaterializerRegistrationError",
    "FIXTURE_SCHEMA",
    "REQUIRED_REGISTRATION_SLOT_NAMES",
    "REQUIRED_RUNTIME_ROLE",
    "SAFETY_FALSE_FIELDS",
    "development_registration_slot_values",
    "fixture_descriptor",
    "fixture_readiness_detail",
    "install_development_registration_fixture",
    "is_development_registration_fixture_ready",
    "is_fixture_installed",
    "production_registration_slots_closed",
    "uninstall_development_registration_fixture",
    "validate_fixture_surface",
]
