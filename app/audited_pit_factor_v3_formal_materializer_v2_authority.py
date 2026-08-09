"""Registered opaque authority boundary for formal materialization v2 RED."""

from __future__ import annotations

from typing import Any, NoReturn


# These are immutable deployment slots consumed only by the native broker.
# Callers never supply a Mapping, path, namespace, root, or boolean substitute.
REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256: str | None = None
REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256: str | None = None
REGISTERED_MATERIALIZER_V2_PRODUCER_SOURCE_ROOT_SHA256: str | None = None
REGISTERED_MATERIALIZER_V2_INDEPENDENT_SOURCE_ROOT_SHA256: str | None = None
REGISTERED_MATERIALIZER_V2_RUNNER_SOURCE_ROOT_SHA256: str | None = None
REGISTERED_MATERIALIZER_V2_ATTEMPT_LOCATOR_RAW_SHA256: str | None = None
REGISTERED_MATERIALIZER_V2_NATIVE_BROKER: object | None = None
REGISTERED_SQLITE_RUNTIME_TCB_SHA256: str | None = None


class FactorV3FormalMaterializerV2AuthorityError(ValueError):
    """Raised when an opaque registered authority fails closed."""


class FactorV3FormalMaterializerV2UnavailableError(
    FactorV3FormalMaterializerV2AuthorityError
):
    """Raised before locator parsing, reads, directory creation, or claims."""


class RegisteredMaterializationAttemptAuthorityV2:
    """One unswappable activation+attempt+output native capability."""

    __slots__ = ("_native_session",)

    def __new__(cls) -> RegisteredMaterializationAttemptAuthorityV2:
        raise TypeError("materialization attempt authority is native-broker-only")


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(
        f"Factor V3 formal materializer v2 authority RED: {capability}"
    )


def open_registered_materialization_attempt_authority_v2_once(
) -> RegisteredMaterializationAttemptAuthorityV2:
    """Atomically load, claim, bind, and hold the one registered native session."""

    _red("single opaque activation-attempt-output authority")


def postverify_registered_materialization_attempt_authority_v2(
    *,
    attempt_authority: RegisteredMaterializationAttemptAuthorityV2,
) -> dict[str, Any]:
    """Recheck the same native session and every held ancestor before promote."""

    _red("unswappable authority held-through-publication postverification")


__all__ = [
    "FactorV3FormalMaterializerV2AuthorityError",
    "FactorV3FormalMaterializerV2UnavailableError",
    "RegisteredMaterializationAttemptAuthorityV2",
    "open_registered_materialization_attempt_authority_v2_once",
    "postverify_registered_materialization_attempt_authority_v2",
]
