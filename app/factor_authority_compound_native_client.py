"""RED seam for the compiled compound-authority broker.

The production manifest is intentionally unregistered.  Every formal object is
a live native-client capability owned by one seven-Job session set; mappings and
Python object construction never become authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn


COMPOUND_NATIVE_MANIFEST_SCHEMA = "factor-authority-compound-native-manifest/v2"
COMPOUND_NATIVE_SESSION_SET_SCHEMA = "factor-authority-compound-session-set/v2"
COMPOUND_NATIVE_RUN_SPEC_SCHEMA = "factor-authority-compound-held-run-spec/v2"
COMPOUND_NATIVE_COMPLETION_SCHEMA = "factor-authority-compound-native-completion/v2"
COMPOUND_NATIVE_ROLE_PRODUCTION_SCHEMA = (
    "factor-authority-compound-native-role-production/v2"
)
COMPOUND_NATIVE_REGISTERED_CAS_SCHEMA = (
    "factor-authority-compound-native-held-registered-cas/v2"
)
COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_SCHEMA = (
    "factor-authority-compound-deployment-policy-authority/v2"
)

REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256: str | None = None
REGISTERED_COMPOUND_NATIVE_BROKER_PATH: str | None = None
REGISTERED_COMPOUND_NATIVE_BROKER_FILE_SHA256: str | None = None
HANDOFF_READY = 0


class FactorAuthorityCompoundNativeClientError(ValueError):
    """Raised when the compiled broker cannot prove native authority."""


class HeldCompoundNativeSessionSet:
    """Opaque owner of four role Jobs, three phase Jobs, and all live handles."""

    __slots__ = ()

    def __new__(cls) -> HeldCompoundNativeSessionSet:
        raise TypeError("HeldCompoundNativeSessionSet is native-client owned")


class HeldDeploymentPolicyAuthority:
    """Opaque held signed policy verified against one physical manifest."""

    __slots__ = ()

    def __new__(cls) -> HeldDeploymentPolicyAuthority:
        raise TypeError("HeldDeploymentPolicyAuthority is native-client owned")


class HeldCompoundRunSpec:
    """Opaque held run-spec CAS issued under the verified deployment policy."""

    __slots__ = ()

    def __new__(cls) -> HeldCompoundRunSpec:
        raise TypeError("HeldCompoundRunSpec is native-client owned")


class HeldCompoundRootLease:
    """Opaque compiled lease holding the ledger tree and four epoch files."""

    __slots__ = ()

    def __new__(cls) -> HeldCompoundRootLease:
        raise TypeError("HeldCompoundRootLease is native-client owned")


class HeldRegisteredCas:
    """Opaque same-handle CAS plus held native ancestor directory handles."""

    __slots__ = ()

    def __new__(cls) -> HeldRegisteredCas:
        raise TypeError("HeldRegisteredCas is native-client owned")


class HeldNativeCompletion:
    """Opaque held completion tied to one process, Job, and signed CAS."""

    __slots__ = ()

    def __new__(cls) -> HeldNativeCompletion:
        raise TypeError("HeldNativeCompletion is native-client owned")


class HeldRoleProduction:
    """Opaque fixed-program role result tied to one process, Job, and CAS."""

    __slots__ = ()

    def __new__(cls) -> HeldRoleProduction:
        raise TypeError("HeldRoleProduction is native-client owned")


def _red(capability: str) -> NoReturn:
    raise NotImplementedError(f"factor authority compound native RED: {capability}")


def open_registered_compound_native_session_set() -> HeldCompoundNativeSessionSet:
    """Open the registered broker once and retain all seven process/Job handles."""

    _red("registered compiled seven-Job session set")


def _open_disposable_test_compound_native_session_set(
    *,
    executable: str | Path,
    expected_executable_sha256: str,
    fixture_manifest_authority_path: str | Path,
    expected_fixture_manifest_raw_sha256: str,
) -> HeldCompoundNativeSessionSet:
    """Private compiled-fixture entry; production registration stays empty."""

    _red("disposable compiled seven-Job session set")


def postverify_distinct_native_jobs(
    *,
    session_set: HeldCompoundNativeSessionSet,
) -> dict[str, Any]:
    """Recheck seven distinct live PIDs, Job identities, and retained handles."""

    _red("seven distinct native process/job roots")


def open_deployment_policy_authority(
    *,
    session_set: HeldCompoundNativeSessionSet,
) -> HeldDeploymentPolicyAuthority:
    """Open policy only from the session set's signed physical manifest."""

    _red("signed physical deployment policy capability")


def issue_compound_run_spec(
    *,
    session_set: HeldCompoundNativeSessionSet,
    deployment_policy_authority: HeldDeploymentPolicyAuthority,
    identity_binding: Mapping[str, Any],
) -> HeldCompoundRunSpec:
    """Issue and hold a policy-bound run-spec CAS before any output exists."""

    _red("opaque policy-bound compound run spec")


def postverify_compound_run_spec(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
) -> dict[str, Any]:
    """Recheck held raw hash, policy, identity, and the live session set."""

    _red("held compound run spec postverification")


def acquire_compound_root_lease(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
) -> HeldCompoundRootLease:
    """Acquire the compiled root-relative lease and retain ancestor handles."""

    _red("opaque compiled compound root lease")


def transition_compound_root_epoch(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
    transition: str,
) -> dict[str, Any]:
    """Perform START_RUN, RUN_RECEIPT, START_VERIFY, or TERMINAL_RECEIPT."""

    _red("compiled four-file root epoch transition")


def postverify_compound_root_lease(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
) -> dict[str, Any]:
    """Recheck the live compiled lease, epoch raws, and held directories."""

    _red("compiled root lease postverification")


def produce_role_artifact(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    role: str,
) -> HeldRoleProduction:
    """Run the registered fixed role program in its dedicated Job/process."""

    _red("fixed-program distinct native role production")


def postverify_role_production(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    production: HeldRoleProduction,
    expected_role: str,
) -> dict[str, Any]:
    """Recheck fixed program, role, run-spec raw, Job/process, and held CAS."""

    _red("fixed-program native role postverification")


def hold_role_production_cas(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    production: HeldRoleProduction,
    expected_role: str,
) -> HeldRegisteredCas:
    """Retain the role CAS same handle and all registered ancestor handles."""

    _red("native-held role production CAS")


def hold_registered_namespace_cas(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    namespace_name: str,
    expected_raw_sha256: str,
) -> HeldRegisteredCas:
    """Retain a policy-registered CAS without path or category override."""

    _red("native-held registered namespace CAS")


def postverify_registered_cas(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    held_cas: HeldRegisteredCas,
) -> dict[str, Any]:
    """Recheck handle, ancestors, file ID, nlink, roots, DACL, and payload."""

    _red("native-held registered CAS postverification")


def acquire_native_run_completion(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
    parent_producer: HeldRoleProduction,
    evaluator_producer: HeldRoleProduction,
) -> HeldNativeCompletion:
    """Bind run.claim raw and exactly the two producer raw identities."""

    _red("broker-signed native run exact closure")


def acquire_native_verify_completion(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
    compound_run_receipt: HeldRegisteredCas,
    parent_verifier: HeldRoleProduction,
    evaluator_verifier: HeldRoleProduction,
) -> HeldNativeCompletion:
    """Bind run/verify epochs, compound run, and two verifier raw identities."""

    _red("broker-signed native verify exact closure")


def acquire_native_terminal_authority(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
    parent_producer: HeldRoleProduction,
    parent_verifier: HeldRoleProduction,
    evaluator_producer: HeldRoleProduction,
    evaluator_verifier: HeldRoleProduction,
    compound_run_receipt: HeldRegisteredCas,
    compound_terminal_receipt: HeldRegisteredCas,
    native_run_completion: HeldNativeCompletion,
    native_verify_completion: HeldNativeCompletion,
) -> HeldNativeCompletion:
    """Bind the complete terminal v2 closure from held native capabilities."""

    _red("broker-signed native terminal exact closure")


def postverify_native_completion(
    *,
    session_set: HeldCompoundNativeSessionSet,
    completion: HeldNativeCompletion,
    expected_phase: str,
    run_spec: HeldCompoundRunSpec,
    root_lease: HeldCompoundRootLease,
) -> dict[str, Any]:
    """Recheck recorded exact closure, signature, Job, process, and held CAS."""

    _red("native completion postverification")


def close_compound_native_session_set(
    session_set: HeldCompoundNativeSessionSet,
) -> dict[str, Any]:
    """Close only after success flush and prove all seven children were reaped."""

    _red("native session set close and seven-child reap")
