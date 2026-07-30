from __future__ import annotations

import _frozen_importlib as _early_importlib
import _frozen_importlib_external as _early_external
import sys


_EARLY_STDLIB_ENTRIES: tuple[dict[str, object], ...] | None = None
_EXECUTED_SUPERVISOR_PATH: str | None = None
_EXECUTED_SUPERVISOR_SHA256: str | None = None
_EXECUTION_PUBLIC_KEY_N: int | None = None
_EXECUTION_PUBLIC_KEY_E: int | None = None


def _early_sha256(raw: bytes) -> str:
    constants = (
        0x428A2F98,
        0x71374491,
        0xB5C0FBCF,
        0xE9B5DBA5,
        0x3956C25B,
        0x59F111F1,
        0x923F82A4,
        0xAB1C5ED5,
        0xD807AA98,
        0x12835B01,
        0x243185BE,
        0x550C7DC3,
        0x72BE5D74,
        0x80DEB1FE,
        0x9BDC06A7,
        0xC19BF174,
        0xE49B69C1,
        0xEFBE4786,
        0x0FC19DC6,
        0x240CA1CC,
        0x2DE92C6F,
        0x4A7484AA,
        0x5CB0A9DC,
        0x76F988DA,
        0x983E5152,
        0xA831C66D,
        0xB00327C8,
        0xBF597FC7,
        0xC6E00BF3,
        0xD5A79147,
        0x06CA6351,
        0x14292967,
        0x27B70A85,
        0x2E1B2138,
        0x4D2C6DFC,
        0x53380D13,
        0x650A7354,
        0x766A0ABB,
        0x81C2C92E,
        0x92722C85,
        0xA2BFE8A1,
        0xA81A664B,
        0xC24B8B70,
        0xC76C51A3,
        0xD192E819,
        0xD6990624,
        0xF40E3585,
        0x106AA070,
        0x19A4C116,
        0x1E376C08,
        0x2748774C,
        0x34B0BCB5,
        0x391C0CB3,
        0x4ED8AA4A,
        0x5B9CCA4F,
        0x682E6FF3,
        0x748F82EE,
        0x78A5636F,
        0x84C87814,
        0x8CC70208,
        0x90BEFFFA,
        0xA4506CEB,
        0xBEF9A3F7,
        0xC67178F2,
    )
    state = [
        0x6A09E667,
        0xBB67AE85,
        0x3C6EF372,
        0xA54FF53A,
        0x510E527F,
        0x9B05688C,
        0x1F83D9AB,
        0x5BE0CD19,
    ]
    message = bytearray(raw)
    bit_length = len(message) * 8
    message.append(0x80)
    while len(message) % 64 != 56:
        message.append(0)
    message.extend(bit_length.to_bytes(8, "big"))
    mask = 0xFFFFFFFF
    for offset in range(0, len(message), 64):
        words = [
            int.from_bytes(message[index : index + 4], "big")
            for index in range(offset, offset + 64, 4)
        ]
        for index in range(16, 64):
            value_15 = words[index - 15]
            value_2 = words[index - 2]
            sigma_0 = (
                ((value_15 >> 7) | (value_15 << 25))
                ^ ((value_15 >> 18) | (value_15 << 14))
                ^ (value_15 >> 3)
            ) & mask
            sigma_1 = (
                ((value_2 >> 17) | (value_2 << 15))
                ^ ((value_2 >> 19) | (value_2 << 13))
                ^ (value_2 >> 10)
            ) & mask
            words.append((words[index - 16] + sigma_0 + words[index - 7] + sigma_1) & mask)
        a, b, c, d, e, f, g, h = state
        for index, constant in enumerate(constants):
            rotate_e = (
                ((e >> 6) | (e << 26)) ^ ((e >> 11) | (e << 21)) ^ ((e >> 25) | (e << 7))
            ) & mask
            choice = (e & f) ^ ((~e) & g)
            temporary_1 = (h + rotate_e + choice + constant + words[index]) & mask
            rotate_a = (
                ((a >> 2) | (a << 30)) ^ ((a >> 13) | (a << 19)) ^ ((a >> 22) | (a << 10))
            ) & mask
            majority = (a & b) ^ (a & c) ^ (b & c)
            temporary_2 = (rotate_a + majority) & mask
            h, g, f, e, d, c, b, a = (
                g,
                f,
                e,
                (d + temporary_1) & mask,
                c,
                b,
                a,
                (temporary_1 + temporary_2) & mask,
            )
        state = [(left + right) & mask for left, right in zip(state, (a, b, c, d, e, f, g, h))]
    return "".join(f"{value:08x}" for value in state)


def _early_read(entry: dict[str, object]) -> bytes:
    path = entry["path"]
    expected_bytes = entry["bytes"]
    expected_sha256 = entry["sha256"]
    if (
        type(path) is not str
        or type(expected_bytes) is not int
        or isinstance(expected_bytes, bool)
        or expected_bytes < 0
        or type(expected_sha256) is not str
    ):
        raise ImportError("early stdlib entry rejected")
    with open(path, "rb") as stream:
        raw = stream.read(expected_bytes + 1)
    if len(raw) != expected_bytes or _early_sha256(raw) != expected_sha256:
        raise ImportError("early stdlib entry identity rejected")
    return raw


class _EarlySourceLoader:
    def __init__(self, entry: dict[str, object]) -> None:
        self._entry = dict(entry)

    def create_module(self, spec: object) -> None:
        del spec
        return None

    def exec_module(self, module: object) -> None:
        raw = _early_read(self._entry)
        path = str(self._entry["path"])
        name = str(self._entry["module"])
        is_package = self._entry["is_package"] is True
        code = compile(raw, path, "exec", dont_inherit=True, optimize=0)
        module.__file__ = path
        module.__loader__ = self
        module.__package__ = name if is_package else name.rpartition(".")[0]
        if is_package:
            separator = max(path.rfind("\\"), path.rfind("/"))
            module.__path__ = [path[:separator]]
        exec(code, module.__dict__)


class _EarlyManifestFinder:
    def __init__(self, entries: tuple[dict[str, object], ...]) -> None:
        self._entries = {
            str(entry["module"]): dict(entry)
            for entry in entries
            if entry.get("module") is not None
        }

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> object:
        del path, target
        entry = self._entries.get(fullname)
        if entry is None:
            return None
        kind = entry.get("kind")
        if kind == "source":
            loader = _EarlySourceLoader(entry)
        elif kind == "extension":
            _early_read(entry)
            loader = _early_external.ExtensionFileLoader(fullname, str(entry["path"]))
        else:
            return None
        return _early_importlib.spec_from_loader(
            fullname,
            loader,
            origin=str(entry["path"]),
            is_package=entry.get("is_package") is True,
        )


def _install_early_exact_import_boundary() -> None:
    entries = _EARLY_STDLIB_ENTRIES
    if type(entries) is not tuple or not entries:
        raise ImportError("early stdlib inventory unavailable")
    finder = _EarlyManifestFinder(entries)
    if len(finder._entries) != len(entries):
        raise ImportError("early stdlib inventory rejected")
    sys.meta_path = [
        _early_importlib.BuiltinImporter,
        _early_importlib.FrozenImporter,
        finder,
    ]
    sys.path = []
    sys.path_hooks = []
    sys.path_importer_cache.clear()


if (
    sys.flags.isolated != 1
    or not sys.dont_write_bytecode
    or sys.flags.no_site != 1
    or not sys.flags.safe_path
    or sys.pycache_prefix is not None
    or not sys.argv[0]
    or len(sys.argv) != 2
):
    raise RuntimeError("trusted supervisor loader runtime rejected")
_loader_orig_argv = list(sys.orig_argv)
if (
    len(_loader_orig_argv) != 7
    or _loader_orig_argv[1:5] != ["-I", "-B", "-S", "-P"]
    or _loader_orig_argv[5] != sys.argv[0]
    or _loader_orig_argv[6] != sys.argv[1]
):
    raise RuntimeError("trusted supervisor loader argv rejected")
_TRUSTED_SUPERVISOR_LOADER_PATH = sys.argv[0]
_install_early_exact_import_boundary()
# EARLY_EXACT_IMPORT_BOUNDARY_COMPLETE

import base64  # noqa: E402
import json  # noqa: E402


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_json(raw: bytes) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in items:
            if key in output:
                raise RuntimeError("launch authorization rejected")
            output[key] = value
        return output

    value = json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise RuntimeError("launch authorization rejected")
    return value


def _verify_signature(payload: bytes, value: object) -> None:
    if (
        type(value) is not str
        or type(_EXECUTION_PUBLIC_KEY_N) is not int
        or type(_EXECUTION_PUBLIC_KEY_E) is not int
    ):
        raise RuntimeError("launch authorization signature rejected")
    signature = base64.b64decode(value.encode("ascii"), validate=True)
    if len(signature) != 384 or base64.b64encode(signature).decode("ascii") != value:
        raise RuntimeError("launch authorization signature rejected")
    encoded = pow(
        int.from_bytes(signature, "big"),
        _EXECUTION_PUBLIC_KEY_E,
        _EXECUTION_PUBLIC_KEY_N,
    ).to_bytes(384, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + bytes.fromhex(
        _early_sha256(payload)
    )
    expected = b"\x00\x01" + b"\xff" * (384 - len(digest_info) - 3) + b"\x00" + digest_info
    if encoded != expected:
        raise RuntimeError("launch authorization signature rejected")


_authorization_path = sys.argv[1]
with open(_authorization_path, "rb") as _stream:
    _authorization_raw = _stream.read(4 * 1024 * 1024 + 1)
if (
    not _authorization_raw
    or len(_authorization_raw) > 4 * 1024 * 1024
    or _early_sha256(_authorization_raw)
    != _authorization_path.rsplit("\\", 1)[-1].removesuffix(".json")
):
    raise RuntimeError("launch authorization identity rejected")
_authorization = _strict_json(_authorization_raw)
if set(_authorization) != {"payload", "signature_base64"}:
    raise RuntimeError("launch authorization rejected")
_payload = _authorization["payload"]
if (
    type(_payload) is not dict
    or _payload.get("schema") != "factor-v3-formal-supervisor-launch-authorization/v2"
    or _payload.get("executed_supervisor_path") != _EXECUTED_SUPERVISOR_PATH
    or _payload.get("executed_supervisor_sha256") != _EXECUTED_SUPERVISOR_SHA256
    or _payload.get("supervisor_loader_path") != _TRUSTED_SUPERVISOR_LOADER_PATH
):
    raise RuntimeError("launch authorization artifact binding rejected")
_verify_signature(_canonical_bytes(_payload), _authorization["signature_base64"])
with open(_TRUSTED_SUPERVISOR_LOADER_PATH, "rb") as _stream:
    _supervisor_loader_raw = _stream.read(8 * 1024 * 1024 + 1)
_TRUSTED_SUPERVISOR_LOADER_SHA256 = _early_sha256(_supervisor_loader_raw)
if (
    not _supervisor_loader_raw
    or len(_supervisor_loader_raw) > 8 * 1024 * 1024
    or _payload.get("supervisor_loader_bytes") != len(_supervisor_loader_raw)
    or _payload.get("supervisor_loader_sha256") != _TRUSTED_SUPERVISOR_LOADER_SHA256
):
    raise RuntimeError("supervisor loader identity rejected")
with open(_EXECUTED_SUPERVISOR_PATH, "rb") as _stream:
    _executed_supervisor_raw = _stream.read(8 * 1024 * 1024 + 1)
if (
    not _executed_supervisor_raw
    or len(_executed_supervisor_raw) > 8 * 1024 * 1024
    or _early_sha256(_executed_supervisor_raw) != _EXECUTED_SUPERVISOR_SHA256
):
    raise RuntimeError("executed supervisor identity rejected")
# EXECUTED_SUPERVISOR_IDENTITY_VERIFIED
_namespace = {
    "__builtins__": __builtins__,
    "__file__": _EXECUTED_SUPERVISOR_PATH,
    "__name__": "__main__",
    "_TRUSTED_EXECUTED_SUPERVISOR_PATH": _EXECUTED_SUPERVISOR_PATH,
    "_TRUSTED_EXECUTED_SUPERVISOR_SHA256": _EXECUTED_SUPERVISOR_SHA256,
    "_TRUSTED_SUPERVISOR_LOADER_PATH": _TRUSTED_SUPERVISOR_LOADER_PATH,
    "_TRUSTED_SUPERVISOR_LOADER_SHA256": _TRUSTED_SUPERVISOR_LOADER_SHA256,
}
sys.argv = [_EXECUTED_SUPERVISOR_PATH, _authorization_path]
exec(
    compile(
        _executed_supervisor_raw,
        _EXECUTED_SUPERVISOR_PATH,
        "exec",
        dont_inherit=True,
        optimize=0,
    ),
    _namespace,
)
# EXECUTED_SUPERVISOR_BYTES_EXECUTED
with open(_EXECUTED_SUPERVISOR_PATH, "rb") as _stream:
    _terminal_supervisor_raw = _stream.read(8 * 1024 * 1024 + 1)
if (
    _terminal_supervisor_raw != _executed_supervisor_raw
    or _early_sha256(_terminal_supervisor_raw) != _EXECUTED_SUPERVISOR_SHA256
):
    raise RuntimeError("executed supervisor terminal identity rejected")
with open(_TRUSTED_SUPERVISOR_LOADER_PATH, "rb") as _stream:
    _terminal_loader_raw = _stream.read(8 * 1024 * 1024 + 1)
if (
    _terminal_loader_raw != _supervisor_loader_raw
    or _early_sha256(_terminal_loader_raw) != _TRUSTED_SUPERVISOR_LOADER_SHA256
):
    raise RuntimeError("supervisor loader terminal identity rejected")
