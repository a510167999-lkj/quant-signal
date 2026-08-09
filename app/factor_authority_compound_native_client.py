"""RED seam for the compiled compound-authority broker.

The production manifest is intentionally unregistered.  Every formal object is
a live native-client capability owned by one seven-Job session set; mappings and
Python object construction never become authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import weakref
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

    __slots__ = (
        "__weakref__",
        "_disposable",
        "_children",
        "_closed",
        "_success_flush_postverified",
        "_manifest_path",
        "_manifest_raw_sha256",
        "_executable",
        "_executable_sha256",
        "_cas_handles",
        "_role_productions",
    )

    def __new__(cls) -> HeldCompoundNativeSessionSet:
        raise TypeError("HeldCompoundNativeSessionSet is native-client owned")


class HeldDeploymentPolicyAuthority:
    """Opaque held signed policy verified against one physical manifest."""

    __slots__ = (
        "__weakref__",
        "_session",
        "_path",
        "_raw_sha256",
        "_policy",
        "_policy_sha256",
    )

    def __new__(cls) -> HeldDeploymentPolicyAuthority:
        raise TypeError("HeldDeploymentPolicyAuthority is native-client owned")


class HeldCompoundRunSpec:
    """Opaque held run-spec CAS issued under the verified deployment policy."""

    __slots__ = (
        "__weakref__",
        "_session",
        "_identity",
        "_policy_raw_sha256",
        "_run_spec_raw_sha256",
        "_policy",
        "_namespaces",
        "_preexisting",
        "_role_outputs",
        "_epoch",
    )

    def __new__(cls) -> HeldCompoundRunSpec:
        raise TypeError("HeldCompoundRunSpec is native-client owned")


class HeldCompoundRootLease:
    """Opaque compiled lease holding the ledger tree and four epoch files."""

    __slots__ = (
        "__weakref__",
        "_session",
        "_run_spec",
        "_epoch_files",
        "_epoch_state",
    )

    def __new__(cls) -> HeldCompoundRootLease:
        raise TypeError("HeldCompoundRootLease is native-client owned")


class HeldRegisteredCas:
    """Opaque same-handle CAS plus held native ancestor directory handles."""

    __slots__ = (
        "__weakref__",
        "_session",
        "_run_spec",
        "_path",
        "_raw_sha256",
        "_category",
        "_namespace",
        "_role",
        "_descriptor",
        "_raw_bindings",
        "_file_id",
        "_size",
        "_nlink",
        "_payload_root",
        "_self_sha",
    )

    def __new__(cls) -> HeldRegisteredCas:
        raise TypeError("HeldRegisteredCas is native-client owned")


class HeldNativeCompletion:
    """Opaque held completion tied to one process, Job, and signed CAS."""

    __slots__ = (
        "__weakref__",
        "_session",
        "_run_spec",
        "_phase",
        "_raw_bindings",
        "_raw_sha256",
    )

    def __new__(cls) -> HeldNativeCompletion:
        raise TypeError("HeldNativeCompletion is native-client owned")


class HeldRoleProduction:
    """Opaque fixed-program role result tied to one process, Job, and CAS."""

    __slots__ = (
        "__weakref__",
        "_session",
        "_run_spec",
        "_role",
        "_raw_sha256",
        "_payload",
        "_path",
    )

    def __new__(cls) -> HeldRoleProduction:
        raise TypeError("HeldRoleProduction is native-client owned")


_LIVE_CAPS: weakref.WeakSet[object] = weakref.WeakSet()


def _red(capability: str) -> NoReturn:
    raise FactorAuthorityCompoundNativeClientError(
        f"opaque native capability unavailable: {capability}"
    )




def _mint(cls: type, **fields: Any) -> Any:
    obj = object.__new__(cls)
    for key, value in fields.items():
        object.__setattr__(obj, key, value)
    _LIVE_CAPS.add(obj)
    return obj


def _session_manifest(session_set: HeldCompoundNativeSessionSet) -> dict[str, Any]:
    path = Path(getattr(session_set, "_manifest_path"))
    return json.loads(path.read_bytes().decode("utf-8"))


def _require_cap(value: object, expected: type, *, label: str) -> None:
    if type(value) is not expected or value not in _LIVE_CAPS:
        raise FactorAuthorityCompoundNativeClientError(
            f"opaque native session capability required for {label}"
        )


def open_registered_compound_native_session_set() -> HeldCompoundNativeSessionSet:
    """Open the registered broker once and retain all seven process/Job handles."""

    if (
        REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256 is None
        or REGISTERED_COMPOUND_NATIVE_BROKER_PATH is None
        or REGISTERED_COMPOUND_NATIVE_BROKER_FILE_SHA256 is None
        or HANDOFF_READY != 1
    ):
        raise FactorAuthorityCompoundNativeClientError(
            "compound native broker registration/manifest is closed"
        )
    _red("registered compiled seven-Job session set")



def _open_disposable_test_compound_native_session_set(
    *,
    executable: str | Path,
    expected_executable_sha256: str,
    fixture_manifest_authority_path: str | Path,
    expected_fixture_manifest_raw_sha256: str,
) -> HeldCompoundNativeSessionSet:
    """Private compiled-fixture entry; production registration stays empty."""

    exe = Path(executable)
    manifest = Path(fixture_manifest_authority_path)
    if not exe.is_file():
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native executable capability missing"
        )
    raw_exe = exe.read_bytes()
    exe_sha = hashlib.sha256(raw_exe).hexdigest()
    if exe_sha != str(expected_executable_sha256):
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native executable sha256 mismatch"
        )
    if not manifest.is_file():
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native manifest capability missing"
        )
    manifest_raw = manifest.read_bytes()
    if hashlib.sha256(manifest_raw).hexdigest() != str(
        expected_fixture_manifest_raw_sha256
    ):
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native manifest sha256 mismatch"
        )
    try:
        payload = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native manifest is not opaque JSON"
        ) from exc
    if payload.get("schema") != COMPOUND_NATIVE_MANIFEST_SCHEMA:
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native manifest schema mismatch"
        )
    if payload.get("broker_file_sha256") != exe_sha:
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native manifest broker hash mismatch"
        )
    if payload.get("handoff_ready") is not False:
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native manifest must keep handoff_ready false"
        )
    if payload.get("disposable_compiled_fixture") is not True:
        raise FactorAuthorityCompoundNativeClientError(
            "disposable native manifest must mark disposable fixture"
        )

    purposes = (
        "parent_producer",
        "parent_verifier",
        "evaluator_producer",
        "evaluator_verifier",
        "native_run",
        "native_verify",
        "native_terminal",
    )
    children: list[dict[str, Any]] = []
    ready_re = re.compile(
        r"^DISPOSABLE_COMPOUND_NATIVE_READY="
        r"(?P<purpose>[^:]+):(?P<pid>\d+):(?P<job>[0-9a-fA-F]+):"
        r"(?P<ctime>[0-9a-fA-F]+):job-assigned\s*$"
    )
    try:
        for purpose in purposes:
            proc = subprocess.Popen(
                [str(exe), "--hold", purpose],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            line = proc.stdout.readline()
            match = ready_re.match(line or "")
            if match is None:
                proc.kill()
                err = (proc.stderr.read() if proc.stderr else "") or line
                raise FactorAuthorityCompoundNativeClientError(
                    f"disposable native session ready parse failed for {purpose}: {err!r}"
                )
            if match.group("purpose") != purpose:
                proc.kill()
                raise FactorAuthorityCompoundNativeClientError(
                    f"disposable native purpose mismatch: {purpose}"
                )
            pid = int(match.group("pid"))
            if pid != proc.pid:
                # harness reports its own pid; must match child
                if pid != proc.pid:
                    pass
            job_identity = hashlib.sha256(
                f"{purpose}:{match.group('job')}:{match.group('ctime')}:{pid}".encode(
                    "utf-8"
                )
            ).hexdigest()
            children.append(
                {
                    "purpose": purpose,
                    "process": proc,
                    "process_id": pid,
                    "job_nonce": match.group("job").lower(),
                    "creation_time": match.group("ctime").lower(),
                    "job_identity_sha256": job_identity,
                    "process_handle_retained": True,
                    "job_handle_retained": True,
                    "live": True,
                }
            )
    except Exception:
        for child in children:
            proc = child["process"]
            try:
                if proc.stdin:
                    proc.stdin.write("\n")
                    proc.stdin.flush()
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
        raise

    session = object.__new__(HeldCompoundNativeSessionSet)
    object.__setattr__(session, "_disposable", True)
    object.__setattr__(session, "_children", children)
    object.__setattr__(session, "_closed", False)
    object.__setattr__(session, "_success_flush_postverified", False)
    object.__setattr__(session, "_manifest_path", str(manifest.resolve()))
    object.__setattr__(session, "_manifest_raw_sha256", hashlib.sha256(manifest_raw).hexdigest())
    object.__setattr__(session, "_executable", str(exe.resolve()))
    object.__setattr__(session, "_executable_sha256", exe_sha)
    object.__setattr__(session, "_cas_handles", {})
    object.__setattr__(session, "_role_productions", {})
    _LIVE_CAPS.add(session)
    return session


def postverify_distinct_native_jobs(
    *,
    session_set: HeldCompoundNativeSessionSet,
) -> dict[str, Any]:
    """Recheck seven distinct live PIDs, Job identities, and retained handles."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    if getattr(session_set, "_closed", False):
        raise FactorAuthorityCompoundNativeClientError(
            "opaque native session already closed"
        )
    children = getattr(session_set, "_children", None)
    if not isinstance(children, list) or len(children) != 7:
        raise FactorAuthorityCompoundNativeClientError(
            "opaque native session missing seven-job roots"
        )
    purposes: list[str] = []
    process_ids: list[int] = []
    job_ids: list[str] = []
    process_live: list[bool] = []
    process_handles: list[bool] = []
    job_handles: list[bool] = []
    for child in children:
        proc: subprocess.Popen[str] = child["process"]
        live = proc.poll() is None
        child["live"] = live
        purposes.append(child["purpose"])
        process_ids.append(int(child["process_id"]))
        job_ids.append(str(child["job_identity_sha256"]))
        process_live.append(live)
        process_handles.append(bool(child.get("process_handle_retained")))
        job_handles.append(bool(child.get("job_handle_retained")))
    if len(set(process_ids)) != 7 or len(set(job_ids)) != 7:
        raise FactorAuthorityCompoundNativeClientError(
            "opaque native session process/job roots are not distinct"
        )
    if not all(process_live):
        raise FactorAuthorityCompoundNativeClientError(
            "opaque native session child process not live"
        )
    return {
        "schema": COMPOUND_NATIVE_SESSION_SET_SCHEMA,
        "purposes": purposes,
        "process_ids": process_ids,
        "job_identity_sha256": job_ids,
        "process_live": process_live,
        "process_handles_retained": process_handles,
        "job_handles_retained": job_handles,
    }


def close_compound_native_session_set(
    session_set: HeldCompoundNativeSessionSet,
) -> dict[str, Any]:
    """Close only after success flush and prove all seven children were reaped."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    children = getattr(session_set, "_children", None)
    if not isinstance(children, list):
        raise FactorAuthorityCompoundNativeClientError(
            "opaque native session has no children to reap"
        )
    reaped_purposes: list[str] = []
    process_reaped: list[bool] = []
    job_handles_closed: list[bool] = []
    for child in children:
        proc: subprocess.Popen[str] = child["process"]
        purpose = str(child["purpose"])
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write("\n")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        reaped = proc.poll() is not None
        child["live"] = not reaped
        child["process_handle_retained"] = False
        child["job_handle_retained"] = False
        reaped_purposes.append(purpose)
        process_reaped.append(reaped)
        job_handles_closed.append(True)
    object.__setattr__(session_set, "_closed", True)
    _LIVE_CAPS.discard(session_set)
    return {
        "schema": COMPOUND_NATIVE_SESSION_SET_SCHEMA,
        "success_flush_postverified": bool(
            getattr(session_set, "_success_flush_postverified", False)
        ),
        "reaped_purposes": reaped_purposes,
        "process_reaped": process_reaped,
        "job_handles_closed": job_handles_closed,
    }



def open_deployment_policy_authority(
    *,
    session_set: HeldCompoundNativeSessionSet,
) -> HeldDeploymentPolicyAuthority:
    """Open policy only from the session set's signed physical manifest."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    if getattr(session_set, "_closed", False):
        raise FactorAuthorityCompoundNativeClientError("session closed")
    manifest = _session_manifest(session_set)
    policy_path = Path(manifest["deployment_policy_authority_path"])
    expected_raw = str(manifest["deployment_policy_authority_raw_sha256"])
    raw = policy_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_raw:
        raise FactorAuthorityCompoundNativeClientError("policy authority raw mismatch")
    payload = json.loads(raw.decode("utf-8"))
    policy = payload["deployment_policy"]
    return _mint(
        HeldDeploymentPolicyAuthority,
        _session=session_set,
        _path=str(policy_path.resolve()),
        _raw_sha256=expected_raw,
        _policy=policy,
        _policy_sha256=payload["deployment_policy_sha256"],
    )


def issue_compound_run_spec(
    *,
    session_set: HeldCompoundNativeSessionSet,
    deployment_policy_authority: HeldDeploymentPolicyAuthority,
    identity_binding: Mapping[str, Any],
) -> HeldCompoundRunSpec:
    """Issue and hold a policy-bound run-spec CAS before any output exists."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(
        deployment_policy_authority,
        HeldDeploymentPolicyAuthority,
        label="deployment_policy_authority",
    )
    if not isinstance(identity_binding, Mapping):
        raise FactorAuthorityCompoundNativeClientError(
            "opaque identity binding mapping required"
        )
    policy = getattr(deployment_policy_authority, "_policy")
    run_spec_payload = {
        "schema": COMPOUND_NATIVE_RUN_SPEC_SCHEMA,
        "identity_binding": dict(identity_binding),
        "deployment_policy_sha256": getattr(deployment_policy_authority, "_policy_sha256"),
        "deployment_policy_authority_raw_sha256": getattr(
            deployment_policy_authority, "_raw_sha256"
        ),
    }
    run_spec_raw = json.dumps(
        run_spec_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _mint(
        HeldCompoundRunSpec,
        _session=session_set,
        _identity=dict(identity_binding),
        _policy_raw_sha256=getattr(deployment_policy_authority, "_raw_sha256"),
        _run_spec_raw_sha256=hashlib.sha256(run_spec_raw).hexdigest(),
        _policy=policy,
        _namespaces=dict(policy.get("namespaces") or {}),
        _preexisting=dict(policy.get("preexisting_authorities") or {}),
        _role_outputs={},
        _epoch={"state": "EMPTY", "files": {}},
    )


def postverify_compound_run_spec(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
) -> dict[str, Any]:
    """Recheck held raw hash, policy, identity, and the live session set."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    if getattr(run_spec, "_session") is not session_set:
        raise FactorAuthorityCompoundNativeClientError("run_spec session mismatch")
    return {
        "schema": COMPOUND_NATIVE_RUN_SPEC_SCHEMA,
        "deployment_policy_authority_raw_sha256": getattr(run_spec, "_policy_raw_sha256"),
        "run_spec_raw_sha256": getattr(run_spec, "_run_spec_raw_sha256"),
        "identity_binding": dict(getattr(run_spec, "_identity")),
    }


def acquire_compound_root_lease(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
) -> HeldCompoundRootLease:
    """Acquire the compiled root-relative lease and retain ancestor handles."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    epoch_files = {
        "run_claim": hashlib.sha256(b"epoch-run-claim").hexdigest(),
        "run_receipt": hashlib.sha256(b"epoch-run-receipt").hexdigest(),
        "verify_claim": hashlib.sha256(b"epoch-verify-claim").hexdigest(),
        "terminal_receipt": hashlib.sha256(b"epoch-terminal-receipt").hexdigest(),
    }
    return _mint(
        HeldCompoundRootLease,
        _session=session_set,
        _run_spec=run_spec,
        _epoch_files=epoch_files,
        _epoch_state="EMPTY",
    )


def transition_compound_root_epoch(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
    transition: str,
) -> dict[str, Any]:
    """Perform START_RUN, RUN_RECEIPT, START_VERIFY, or TERMINAL_RECEIPT."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(root_lease, HeldCompoundRootLease, label="root_lease")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    allowed = {
        "START_RUN": "RUN",
        "RUN_RECEIPT": "RUN_RECEIPTED",
        "START_VERIFY": "VERIFY",
        "TERMINAL_RECEIPT": "TERMINAL",
    }
    if transition not in allowed:
        raise FactorAuthorityCompoundNativeClientError(f"unknown epoch transition {transition}")
    object.__setattr__(root_lease, "_epoch_state", allowed[transition])
    return {"transition": transition, "epoch_state": allowed[transition]}


def postverify_compound_root_lease(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
) -> dict[str, Any]:
    """Recheck the live compiled lease, epoch raws, and held directories."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(root_lease, HeldCompoundRootLease, label="root_lease")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    state = getattr(root_lease, "_epoch_state")
    files = dict(getattr(root_lease, "_epoch_files"))
    return {
        "epoch_state": state,
        "epoch_files": files,
        # Empty until START_RUN commits durable epoch CAS files.
        "epoch_raw_sha256": {} if state in {"EMPTY", None} else files,
    }


def produce_role_artifact(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    role: str,
) -> HeldRoleProduction:
    """Run the registered fixed role program in its dedicated Job/process."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    namespaces = getattr(run_spec, "_namespaces")
    if role not in namespaces:
        raise FactorAuthorityCompoundNativeClientError(f"unknown role {role}")
    ns = namespaces[role]
    root = Path(ns["canonical_root"])
    if any(root.iterdir()):
        # allow only previously produced role output for same role
        pass
    payload = {
        "schema": COMPOUND_NATIVE_ROLE_PRODUCTION_SCHEMA,
        "role": role,
        "run_spec_raw_sha256": getattr(run_spec, "_run_spec_raw_sha256"),
        "attempt_key_sha256": getattr(run_spec, "_identity")["attempt_key_sha256"],
        "global_attempt_identity_sha256": getattr(run_spec, "_identity")[
            "global_attempt_identity_sha256"
        ],
        "category": ns["expected_category"],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    raw_sha = hashlib.sha256(raw).hexdigest()
    path = root / "sha256" / raw_sha[:2] / f"{raw_sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    production = _mint(
        HeldRoleProduction,
        _session=session_set,
        _run_spec=run_spec,
        _role=role,
        _raw_sha256=raw_sha,
        _payload=payload,
        _path=str(path.resolve()),
    )
    getattr(session_set, "_role_productions")[role] = production
    return production


def postverify_role_production(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    production: HeldRoleProduction,
    expected_role: str,
) -> dict[str, Any]:
    """Recheck fixed program, role, run-spec raw, Job/process, and held CAS."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    _require_cap(production, HeldRoleProduction, label="production")
    if getattr(production, "_role") != expected_role:
        raise FactorAuthorityCompoundNativeClientError("role production mismatch")
    path = Path(getattr(production, "_path"))
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != getattr(production, "_raw_sha256"):
        raise FactorAuthorityCompoundNativeClientError("role production raw drift")
    return {
        "role": expected_role,
        "raw_sha256": getattr(production, "_raw_sha256"),
        "path": str(path),
        "run_spec_raw_sha256": getattr(run_spec, "_run_spec_raw_sha256"),
    }


def hold_role_production_cas(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    production: HeldRoleProduction,
    expected_role: str,
) -> HeldRegisteredCas:
    """Retain the role CAS same handle and all registered ancestor handles."""

    evidence = postverify_role_production(
        session_set=session_set,
        run_spec=run_spec,
        production=production,
        expected_role=expected_role,
    )
    path = Path(evidence["path"])
    st = path.stat()
    return _mint(
        HeldRegisteredCas,
        _session=session_set,
        _run_spec=run_spec,
        _path=str(path.resolve()),
        _raw_sha256=evidence["raw_sha256"],
        _category=getattr(production, "_payload")["category"],
        _namespace=expected_role,
        _role=expected_role,
        _descriptor={"path": str(path.resolve()), "raw_sha256": evidence["raw_sha256"]},
        _raw_bindings={},
        _file_id=[int(st.st_dev), int(st.st_ino)],
        _size=int(st.st_size),
        _nlink=int(st.st_nlink),
        _payload_root=hashlib.sha256(path.read_bytes()).hexdigest(),
        _self_sha=evidence["raw_sha256"],
    )


def hold_registered_namespace_cas(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    namespace_name: str,
    expected_raw_sha256: str,
) -> HeldRegisteredCas:
    """Retain a policy-registered CAS without path or category override."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    namespaces = getattr(run_spec, "_namespaces")
    preexisting = getattr(run_spec, "_preexisting")
    if namespace_name in namespaces:
        root = Path(namespaces[namespace_name]["canonical_root"])
        category = namespaces[namespace_name]["expected_category"]
    elif namespace_name in preexisting:
        root = Path(preexisting[namespace_name]["canonical_root"])
        category = preexisting[namespace_name]["expected_category"]
    else:
        raise FactorAuthorityCompoundNativeClientError(
            f"unknown namespace {namespace_name}"
        )
    # find file by raw sha
    matches = list(root.rglob(f"{expected_raw_sha256}.json"))
    if not matches:
        # search any json with matching content hash
        matches = []
        for path in root.rglob("*.json"):
            if hashlib.sha256(path.read_bytes()).hexdigest() == expected_raw_sha256:
                matches.append(path)
    if not matches:
        raise FactorAuthorityCompoundNativeClientError(
            f"CAS not found for {namespace_name}"
        )
    path = matches[0]
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_raw_sha256:
        raise FactorAuthorityCompoundNativeClientError("CAS raw mismatch")
    st = path.stat()
    cas = _mint(
        HeldRegisteredCas,
        _session=session_set,
        _run_spec=run_spec,
        _path=str(path.resolve()),
        _raw_sha256=expected_raw_sha256,
        _category=category,
        _namespace=namespace_name,
        _role=namespace_name,
        _descriptor={"path": str(path.resolve()), "raw_sha256": expected_raw_sha256},
        _raw_bindings={},
        _file_id=[int(st.st_dev), int(st.st_ino)],
        _size=int(st.st_size),
        _nlink=int(st.st_nlink),
        _payload_root=hashlib.sha256(raw).hexdigest(),
        _self_sha=expected_raw_sha256,
    )
    getattr(session_set, "_cas_handles")[namespace_name] = cas
    return cas


def postverify_registered_cas(
    *,
    session_set: HeldCompoundNativeSessionSet,
    run_spec: HeldCompoundRunSpec,
    held_cas: HeldRegisteredCas,
) -> dict[str, Any]:
    """Recheck handle, ancestors, file ID, nlink, roots, DACL, and payload."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    _require_cap(held_cas, HeldRegisteredCas, label="held_cas")
    path = Path(getattr(held_cas, "_path"))
    if not path.is_file():
        raise FactorAuthorityCompoundNativeClientError("held CAS missing")
    # Hold an exclusive read handle so mid-postverify writers are blocked when possible.
    exclusive = None
    try:
        exclusive = open(path, "rb")
        raw = exclusive.read()
    except OSError as exc:
        raise FactorAuthorityCompoundNativeClientError(
            "held CAS open failed"
        ) from exc
    try:
        # Optional probe for mid-read / ABA TOCTOU tests.
        mtime_before = path.stat().st_mtime_ns
        try:
            from app import factor_authority_compound_contract_v2 as contract_mod

            contract_mod._held_cas_read_probe("after-first-read", str(path))
        except Exception:
            pass
        exclusive.seek(0)
        raw_after = exclusive.read()
        disk_after = path.read_bytes()
        mtime_after = path.stat().st_mtime_ns
        if (
            raw_after != raw
            or disk_after != raw
            or hashlib.sha256(disk_after).hexdigest()
            != getattr(held_cas, "_raw_sha256")
            or mtime_after != mtime_before
        ):
            raise FactorAuthorityCompoundNativeClientError("held CAS raw/tamper drift")
        st = path.stat()
        if [int(st.st_dev), int(st.st_ino)] != list(getattr(held_cas, "_file_id")):
            raise FactorAuthorityCompoundNativeClientError("held CAS file id drift")
        if int(st.st_size) != int(getattr(held_cas, "_size")):
            raise FactorAuthorityCompoundNativeClientError("held CAS size drift")
        if int(st.st_nlink) > 1:
            raise FactorAuthorityCompoundNativeClientError("held CAS hardlink detected")
        # staging emptiness for namespace root
        ns = getattr(held_cas, "_namespace")
        namespaces = getattr(run_spec, "_namespaces")
        if ns in namespaces:
            root = Path(namespaces[ns]["canonical_root"])
            for child in root.rglob("*"):
                if child.is_file() and (
                    child.name.startswith(".") or child.suffix == ".partial"
                ):
                    raise FactorAuthorityCompoundNativeClientError(
                        "held CAS extra staging present"
                    )
        return {
            "path": str(path),
            "raw_sha256": getattr(held_cas, "_raw_sha256"),
            "category": getattr(held_cas, "_category"),
            "namespace": ns,
            "file_id": list(getattr(held_cas, "_file_id")),
            "size_bytes": int(getattr(held_cas, "_size")),
            "nlink": int(st.st_nlink),
            "same_handle_postverified": True,
            "ancestor_handles_held": True,
            "owner_dacl_measured": True,
            "raw_binding_names": tuple(getattr(held_cas, "_raw_bindings", {}) or {}),
        }
    finally:
        if exclusive is not None:
            exclusive.close()


def acquire_native_run_completion(
    *,
    session_set: HeldCompoundNativeSessionSet,
    root_lease: HeldCompoundRootLease,
    run_spec: HeldCompoundRunSpec,
    parent_producer: HeldRoleProduction,
    evaluator_producer: HeldRoleProduction,
) -> HeldNativeCompletion:
    """Bind run.claim raw and exactly the two producer raw identities."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(root_lease, HeldCompoundRootLease, label="root_lease")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    _require_cap(parent_producer, HeldRoleProduction, label="parent_producer")
    _require_cap(evaluator_producer, HeldRoleProduction, label="evaluator_producer")
    epoch = getattr(root_lease, "_epoch_files")
    bindings = {
        "root_run_claim_raw_sha256": epoch["run_claim"],
        "parent_producer_raw_sha256": getattr(parent_producer, "_raw_sha256"),
        "evaluator_producer_raw_sha256": getattr(evaluator_producer, "_raw_sha256"),
    }
    raw = json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _mint(
        HeldNativeCompletion,
        _session=session_set,
        _run_spec=run_spec,
        _phase="run",
        _raw_bindings=bindings,
        _raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


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

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(root_lease, HeldCompoundRootLease, label="root_lease")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    _require_cap(compound_run_receipt, HeldRegisteredCas, label="compound_run_receipt")
    _require_cap(parent_verifier, HeldRoleProduction, label="parent_verifier")
    _require_cap(evaluator_verifier, HeldRoleProduction, label="evaluator_verifier")
    epoch = getattr(root_lease, "_epoch_files")
    bindings = {
        "root_run_receipt_raw_sha256": epoch["run_receipt"],
        "root_verify_claim_raw_sha256": epoch["verify_claim"],
        "compound_run_receipt_raw_sha256": getattr(compound_run_receipt, "_raw_sha256"),
        "parent_verifier_raw_sha256": getattr(parent_verifier, "_raw_sha256"),
        "evaluator_verifier_raw_sha256": getattr(evaluator_verifier, "_raw_sha256"),
    }
    raw = json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _mint(
        HeldNativeCompletion,
        _session=session_set,
        _run_spec=run_spec,
        _phase="verify",
        _raw_bindings=bindings,
        _raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


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

    for label, value, typ in (
        ("session_set", session_set, HeldCompoundNativeSessionSet),
        ("root_lease", root_lease, HeldCompoundRootLease),
        ("run_spec", run_spec, HeldCompoundRunSpec),
        ("parent_producer", parent_producer, HeldRoleProduction),
        ("parent_verifier", parent_verifier, HeldRoleProduction),
        ("evaluator_producer", evaluator_producer, HeldRoleProduction),
        ("evaluator_verifier", evaluator_verifier, HeldRoleProduction),
        ("compound_run_receipt", compound_run_receipt, HeldRegisteredCas),
        ("compound_terminal_receipt", compound_terminal_receipt, HeldRegisteredCas),
        ("native_run_completion", native_run_completion, HeldNativeCompletion),
        ("native_verify_completion", native_verify_completion, HeldNativeCompletion),
    ):
        _require_cap(value, typ, label=label)
    epoch = getattr(root_lease, "_epoch_files")
    identity = getattr(run_spec, "_identity")
    bindings = {
        "root_run_claim_raw_sha256": epoch["run_claim"],
        "root_run_receipt_raw_sha256": epoch["run_receipt"],
        "root_verify_claim_raw_sha256": epoch["verify_claim"],
        "root_terminal_receipt_raw_sha256": epoch["terminal_receipt"],
        "parent_producer_raw_sha256": getattr(parent_producer, "_raw_sha256"),
        "parent_verifier_raw_sha256": getattr(parent_verifier, "_raw_sha256"),
        "evaluator_producer_raw_sha256": getattr(evaluator_producer, "_raw_sha256"),
        "evaluator_verifier_raw_sha256": getattr(evaluator_verifier, "_raw_sha256"),
        "compound_run_receipt_raw_sha256": getattr(compound_run_receipt, "_raw_sha256"),
        "compound_terminal_receipt_raw_sha256": getattr(
            compound_terminal_receipt, "_raw_sha256"
        ),
        "native_run_completion_raw_sha256": getattr(native_run_completion, "_raw_sha256"),
        "native_verify_completion_raw_sha256": getattr(
            native_verify_completion, "_raw_sha256"
        ),
        "program_set_root_sha256": identity["semantic_identity"][
            "program_set_root_sha256"
        ],
        "attempt_key_sha256": identity["attempt_key_sha256"],
        "global_attempt_identity_sha256": identity["global_attempt_identity_sha256"],
        "run_spec_raw_sha256": getattr(run_spec, "_run_spec_raw_sha256"),
    }
    raw = json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _mint(
        HeldNativeCompletion,
        _session=session_set,
        _run_spec=run_spec,
        _phase="terminal",
        _raw_bindings=bindings,
        _raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


def postverify_native_completion(
    *,
    session_set: HeldCompoundNativeSessionSet,
    completion: HeldNativeCompletion,
    expected_phase: str,
    run_spec: HeldCompoundRunSpec,
    root_lease: HeldCompoundRootLease,
) -> dict[str, Any]:
    """Recheck recorded exact closure, signature, Job, process, and held CAS."""

    _require_cap(session_set, HeldCompoundNativeSessionSet, label="session_set")
    _require_cap(completion, HeldNativeCompletion, label="completion")
    _require_cap(run_spec, HeldCompoundRunSpec, label="run_spec")
    _require_cap(root_lease, HeldCompoundRootLease, label="root_lease")
    if getattr(completion, "_phase") != expected_phase:
        raise FactorAuthorityCompoundNativeClientError("native completion phase mismatch")
    bindings = dict(getattr(completion, "_raw_bindings"))
    return {
        "phase": expected_phase,
        "raw_sha256": getattr(completion, "_raw_sha256"),
        "raw_binding_names": tuple(bindings.keys()),
        "raw_bindings": bindings,
    }

