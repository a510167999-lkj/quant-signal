"""Controlled Tushare collection with request identity and attempt evidence.

The transport and clock are injected so tests and production callers can prove
which bytes were requested and when a complete response entity was received.
Credentials are used only to build the in-memory wire body and are never sent
to the receipt store.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from app.research_pit_contracts import STOCK_BASIC_FIELDS
from app.research_pit_transport import (
    HttpEntityResponse,
    PITCollectionError,
    TushareTransport,
    UrllibTushareTransport as _UrllibTushareTransport,
    _NoRedirectHandler,
)
from app.research_pit_store import (
    DEFAULT_MARKET_SESSION_VINTAGE,
    MAX_RAW_BYTES,
    NORMALIZED_FIELDS,
    OFFICIAL_ROW_CAPS,
    PITReceiptError,
    PITReceiptStore,
    REQUIRED_STOCK_PARTITIONS,
    _canonical_partition_key,
    _canonical_wire_params,
    _normalize_request_params,
    _parse_native_envelope,
)
from app.research_market_data import build_market_session_specs
from app.research_membership import MembershipContractError, classify_membership_snapshot_body
from app.research_partitions import PartitionContractError, assert_range_allowed


class _CollectionCancelled(PITCollectionError):
    """Internal control flow: no source request started, so no attempt exists."""


@dataclass(frozen=True)
class FetchSpec:
    dataset: str
    partition_key: str
    api_name: str
    wire_params: Mapping[str, str]
    receipt_params: Mapping[str, str]
    fields: tuple[str, ...]
    row_cap: int


class TrustedClock(Protocol):
    def assert_synchronized(self) -> Mapping[str, Any]: ...

    def now_utc(self) -> datetime: ...

    def monotonic_ns(self) -> int: ...


class UrllibTushareTransport(_UrllibTushareTransport):
    """Compatibility facade retaining collector-level monkeypatch hooks."""

    def __init__(self, opener: Any = None, *, proxy_url: str | None = None) -> None:
        super().__init__(
            opener,
            proxy_url=proxy_url,
            _opener_factory=build_opener,
            _proxy_handler_factory=ProxyHandler,
            _redirect_handler_factory=_NoRedirectHandler,
        )


# w32tm 同步时间窗口:Windows 系统默认每天同步数次,超过 24h 视为失同步。
_W32TM_MAX_SYNC_AGE = timedelta(hours=24)
# w32tm 输出里代表"未真正 NTP 同步"的源字符串(硬件时钟)。
_W32TM_UNSYNCHRONIZED_SOURCES = {"local cmos clock", "free-running system clock"}

# 中英双解析:w32tm /query /status 在不同 Windows 语言下输出的键名不同。
_W32TM_LAST_SYNC_KEYS = ("Last Successful Sync Time", "上次成功同步时间")
_W32TM_SOURCE_KEYS = ("Source", "源")
_W32TM_STRATUM_KEYS = ("Stratum", "层次")

# Windows 区域设置决定的日期格式多样,逐一尝试。
_W32TM_TIME_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",   # 英文: 7/21/2026 11:00:00 AM
    "%m/%d/%Y %H:%M:%S",       # 英文 24h
    "%Y/%m/%d %H:%M:%S",       # 中文: 2026/7/21 15:06:14
    "%Y-%m-%d %H:%M:%S",
    "%#m/%d/%Y %I:%M:%S %p",   # Windows Python 单数字月(非标准 directive,但能跑)
)


def _parse_w32tm_sync_time(value: str) -> datetime | None:
    """解析 w32tm 输出的"上次成功同步时间",返回 naive datetime(本地时区)。

    w32tm 输出的是本机本地时间;稍后与 now_utc 对比前要先假定本地时区,转成 aware。
    返回 None 表示无法解析。
    """
    raw = value.strip()
    if not raw:
        return None
    for fmt in _W32TM_TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _windows_w32tm_probe(scrubbed_env: Mapping[str, str], checked_at: str) -> Mapping[str, Any]:
    """Windows 平台的 NTP 同步证据收集;永远 fail-closed,绝不 crash。"""
    try:
        completed = subprocess.run(
            ["w32tm", "/query", "/status"],
            check=False,
            capture_output=True,
            timeout=5,
            env=scrubbed_env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {
            "source": "w32tm",
            "synchronized": False,
            "reason": "command_unavailable",
            "checked_at": checked_at,
        }

    # Windows 中文系统上 w32tm 默认输出 OEM 编码(GBK),英文系统输出 ASCII。
    # 先试 GBK(中文系统常见),失败再试 UTF-8;两路都失败用 replace 兜底。
    out_bytes = completed.stdout
    if isinstance(out_bytes, bytes):
        stdout = _decode_windows_output(out_bytes)
    else:
        stdout = str(out_bytes or "")
    if completed.returncode != 0 or not stdout:
        return {
            "source": "w32tm",
            "synchronized": False,
            "reason": "command_failed",
            "returncode": completed.returncode,
            "stderr": _safe_decode(completed.stderr),
            "checked_at": checked_at,
        }

    last_sync_raw = _extract_w32tm_field(stdout, _W32TM_LAST_SYNC_KEYS)
    source_raw = _extract_w32tm_field(stdout, _W32TM_SOURCE_KEYS)
    stratum_raw = _extract_w32tm_field(stdout, _W32TM_STRATUM_KEYS)

    if not last_sync_raw:
        return {
            "source": "w32tm",
            "synchronized": False,
            "reason": "last_sync_missing",
            "checked_at": checked_at,
        }
    parsed = _parse_w32tm_sync_time(last_sync_raw)
    if parsed is None:
        return {
            "source": "w32tm",
            "synchronized": False,
            "reason": "last_sync_unparseable",
            "raw_last_sync": last_sync_raw[:100],
            "checked_at": checked_at,
        }

    # 假定 w32tm 报告的是本机本地时间;转成 aware UTC 与 now_utc 对比。
    # 没有可靠的方式从 w32tm 输出里读时区,故假设本机 local;now_utc 是 UTC,
    # 两者对比用本地 now 减偏移。Python 3.9+ 用 astimezone 处理。
    try:
        local_aware = parsed.astimezone()
    except (OverflowError, OSError, ValueError):
        return {
            "source": "w32tm",
            "synchronized": False,
            "reason": "timezone_conversion_failed",
            "checked_at": checked_at,
        }
    now_utc = SystemTrustedClock.now_utc()
    age = now_utc - local_aware.astimezone(timezone.utc)
    if abs(age) > _W32TM_MAX_SYNC_AGE:
        return {
            "source": "w32tm",
            "synchronized": False,
            "reason": "stale_sync_over_24h",
            "sync_age_hours": round(abs(age).total_seconds() / 3600, 2),
            "last_sync_utc": local_aware.astimezone(timezone.utc).isoformat(),
            "checked_at": checked_at,
        }

    source_text = (source_raw or "").strip()
    # Windows 输出"Local CMOS Clock"或"Free-Running System Clock"表示硬件时钟未同步
    if source_text.lower() in _W32TM_UNSYNCHRONIZED_SOURCES:
        return {
            "source": "w32tm",
            "synchronized": False,
            "reason": "local_cmos_unsynchronized",
            "sync_source": source_text,
            "checked_at": checked_at,
        }

    stratum_value: int | None = None
    if stratum_raw:
        match = re.match(r"\s*(\d+)", stratum_raw)
        if match:
            stratum_value = int(match.group(1))

    return {
        "source": "w32tm",
        "synchronized": True,
        "sync_source": source_text,
        "stratum": stratum_value,
        "last_sync_utc": local_aware.astimezone(timezone.utc).isoformat(),
        "sync_age_seconds": round(abs(age).total_seconds(), 2),
        "maximum_sync_age_seconds": int(_W32TM_MAX_SYNC_AGE.total_seconds()),
        "checked_at": checked_at,
    }


def _extract_w32tm_field(stdout: str, keys: Sequence[str]) -> str | None:
    """从 w32tm 输出里抓字段值,中英双键尝试。

    w32tm 输出形如 `Source: ntp.aliyun.com,0x9`(英文)或 `源: ntp.aliyun.com,0x9`(中文)。
    行内第一个冒号后到行尾就是值;trim 空白。
    """
    for key in keys:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.*?)\s*$", re.MULTILINE)
        match = pattern.search(stdout)
        if match:
            return match.group(1)
    return None


def _safe_decode(value: Any) -> str:
    """subprocess 输出可能是 bytes 或 str(测试注入),统一返回前 200 字符 str。"""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    return text[:200]


def _decode_windows_output(data: bytes) -> str:
    """Windows w32tm / system 命令输出编码处理。

    中文 Windows 默认 OEM 编码是 GBK(code page 936),英文系统是 ASCII/UTF-8。
    策略:先试 GBK(中文键 `上次成功同步时间` 才能正确解析),失败 fallback UTF-8,
    再失败用 replace 兜底(至少英文键和数字还能解析)。
    """
    for encoding in ("gbk", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class SystemTrustedClock:
    """Fail-closed NTP attestation with injectable probe for managed hosts."""

    def __init__(self, probe: Callable[[], Mapping[str, Any]] = None) -> None:
        self._probe = probe or self._system_probe

    @staticmethod
    def _system_probe() -> Mapping[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        scrubbed_env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in {"TUSHARE_TOKEN", "TUSHARE_API_KEY", "JIAOCH_TOKEN"}
        }
        timedatectl = shutil.which("timedatectl")
        if timedatectl:
            completed = subprocess.run(
                [timedatectl, "show", "-p", "NTPSynchronized", "--value"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=scrubbed_env,
            )
            synchronized = completed.returncode == 0 and completed.stdout.strip().lower() == "yes"
            return {
                "source": "timedatectl",
                "synchronized": synchronized,
                "checked_at": checked_at,
            }
        if platform.system() == "Darwin":
            command = "/usr/sbin/systemsetup"
            network_time_enabled = False
            if Path(command).exists():
                completed = subprocess.run(
                    [command, "-getusingnetworktime"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=scrubbed_env,
                )
                network_time_enabled = (
                    completed.returncode == 0 and "on" in completed.stdout.lower()
                )
            sntp = shutil.which("sntp")
            if sntp:
                completed = subprocess.run(
                    [sntp, "-t", "3", "-d", "time.apple.com"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    env=scrubbed_env,
                )
                combined = completed.stdout + "\n" + completed.stderr
                selected_blocks = re.findall(
                    r"selected:\s*sntp_exchange\s*\{(.*?)\n\}",
                    combined,
                    flags=re.DOTALL,
                )
                selected_markers = re.findall(r"^\s*selected:\s*$", combined, flags=re.MULTILINE)
                if (
                    completed.returncode == 0
                    and len(selected_markers) == 1
                    and len(selected_blocks) == 1
                ):
                    selected = selected_blocks[0]
                    results = re.findall(r"^\s*result:\s*(.*?)\s*$", selected, flags=re.MULTILINE)
                    offsets = re.findall(
                        r"^\s*offset:.*?\((-?\d+(?:\.\d+)?)\)\s*$",
                        selected,
                        flags=re.MULTILINE,
                    )
                else:
                    results = []
                    offsets = []
                if results == ["0 (Success)"] and len(offsets) == 1:
                    selected_offset = abs(float(offsets[0]))
                    return {
                        "source": "sntp",
                        "server": "time.apple.com",
                        "network_time_enabled": network_time_enabled,
                        "synchronized": selected_offset <= 1.0,
                        "offset_seconds": selected_offset,
                        "maximum_offset_seconds": 1.0,
                        "checked_at": checked_at,
                    }
            if network_time_enabled:
                return {
                    "source": "systemsetup-unmeasured",
                    "network_time_enabled": True,
                    "synchronized": False,
                    "checked_at": checked_at,
                }
        if platform.system() == "Windows":
            # Windows 没有 timedatectl/sntp,用 w32tm /query /status 验证最近一次 NTP 同步。
            # 判定同步:命令成功 + 解析出"上次成功同步时间" + 距今 ≤ 24 小时 + 源不是
            # "Local CMOS Clock"(硬件时钟,未同步)。输出编码可能是 GBK / UTF-16 LE,
            # capture_output=True + text=True 在中文 Windows 上默认 GBK 解码;保险起见
            # 用 errors="replace" 让未知字节不致 raise。
            return _windows_w32tm_probe(scrubbed_env, checked_at)
        return {"source": "unavailable", "synchronized": False, "checked_at": checked_at}

    def assert_synchronized(self) -> Mapping[str, Any]:
        evidence = None
        for _attempt in range(3):
            try:
                evidence = dict(self._probe())
                break
            except Exception:
                continue
        if evidence is None:
            raise PITCollectionError("system clock synchronization could not be proven") from None
        if evidence.get("synchronized") is not True:
            raise PITCollectionError("system clock synchronization could not be proven")
        return evidence

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def monotonic_ns() -> int:
        return time.monotonic_ns()


TRADE_CAL_FIELDS = tuple(NORMALIZED_FIELDS["trade_cal"])
BAK_BASIC_FIELDS = tuple(NORMALIZED_FIELDS["bak_basic"])
RETRYABLE_HTTP_STATUSES = {408, 429, *range(500, 600)}
RESPONSE_HEADER_ALLOWLIST = {
    "content-encoding",
    "content-length",
    "content-type",
    "date",
    "retry-after",
    "x-request-id",
}


def _iso_date(value: date | str) -> str:
    if isinstance(value, datetime):
        raise PITCollectionError("datetime values are forbidden; provide a calendar date")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or len(value) != 10:
        raise PITCollectionError("date must be canonical ISO YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PITCollectionError("date must be canonical ISO YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise PITCollectionError("date must be canonical ISO YYYY-MM-DD")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_market_collection_progress(path: str | Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace a compact, credential-free market collection checkpoint."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def build_stock_basic_specs() -> list[FetchSpec]:
    return [
        FetchSpec(
            dataset="stock_basic",
            partition_key=f"{exchange}:{status}",
            api_name="stock_basic",
            wire_params={"exchange": exchange, "list_status": status},
            receipt_params={"exchange": exchange, "list_status": status},
            fields=STOCK_BASIC_FIELDS,
            row_cap=6000,
        )
        for exchange in ("SSE", "SZSE")
        for status in ("L", "D", "P", "G")
    ]


def build_trade_cal_specs(
    *,
    start_date: str,
    end_date: str,
    exchanges: Sequence[str] = ("SSE", "SZSE"),
) -> list[FetchSpec]:
    """Build trade_cal fetch specs.

    exchanges defaults to both SSE and SZSE per tushare API. Callers facing the
    jiaoch source (whose trade_cal only maintains SSE) should pass exchanges=("SSE",).
    """
    start = _iso_date(start_date)
    end = _iso_date(end_date)
    if end < start:
        raise ValueError("end_date precedes start_date")
    internal_guard = (date.fromisoformat(end) - date.fromisoformat(start)).days + 2
    return [
        FetchSpec(
            dataset="trade_cal",
            partition_key=f"{exchange}:{start}:{end}",
            api_name="trade_cal",
            wire_params={
                "exchange": exchange,
                "start_date": start.replace("-", ""),
                "end_date": end.replace("-", ""),
            },
            receipt_params={
                "exchange": exchange,
                "start_date": start,
                "end_date": end,
            },
            fields=TRADE_CAL_FIELDS,
            row_cap=internal_guard,
        )
        for exchange in exchanges
    ]


def build_bak_basic_specs(sessions: Sequence[str]) -> list[FetchSpec]:
    normalized = sorted({_iso_date(session) for session in sessions})
    return [
        FetchSpec(
            dataset="bak_basic",
            partition_key=session,
            api_name="bak_basic",
            wire_params={"trade_date": session.replace("-", "")},
            receipt_params={"trade_date": session},
            fields=BAK_BASIC_FIELDS,
            row_cap=7000,
        )
        for session in normalized
    ]


def build_market_session_fetch_specs(trade_date: str) -> list[FetchSpec]:
    """Convert canonical four-shard market session contracts into FetchSpecs."""

    session = _iso_date(trade_date)
    return [
        FetchSpec(
            dataset=spec.dataset,
            partition_key=session,
            api_name=spec.api_name,
            wire_params=dict(spec.wire_params),
            receipt_params={"trade_date": session},
            fields=tuple(spec.fields),
            row_cap=int(spec.row_cap),
        )
        for spec in build_market_session_specs(session)
    ]


@dataclass(frozen=True)
class _TemporalCollectionAuthority:
    contract_json: str
    contract_sha256: str
    role: str
    start_date: str
    end_date: str

    def contract_copy(self) -> dict[str, Any]:
        return json.loads(self.contract_json)


@dataclass(frozen=True)
class _SourceCollectionAuthority:
    api_url: str
    allowed_hosts: tuple[str, ...]
    source_profile: str
    request_protocol: str
    network_route: str
    proxy_endpoint: str | None
    allow_insecure_http: bool
    row_cap_overrides: tuple[tuple[str, int], ...]
    credential_slot_id: str | None
    credential_route_purpose: str | None
    credential_route_id_prefix: str | None
    credential_generation_id: str | None


class ControlledTushareCollector:
    def __setattr__(self, name: str, value: Any) -> None:
        if name in {
            "_temporal_authority",
            "_source_authority",
            "_workers",
            "_network_slots",
        } and hasattr(self, name):
            raise AttributeError("collection authority is immutable")
        super().__setattr__(name, value)

    def __init__(
        self,
        *,
        store: PITReceiptStore,
        token: str,
        api_url: str,
        transport: TushareTransport,
        clock: TrustedClock,
        max_attempts: int = 3,
        timeout_s: float = 30.0,
        sleeper: Callable[[float], None] = time.sleep,
        allow_insecure_http: bool = False,
        allowed_hosts: Sequence[str] = ("api.tushare.pro",),
        clock_attestation_ttl_s: float = 300.0,
        source_profile: str = "official",
        request_protocol: str = "tushare-root-post/v1",
        row_cap_overrides: Mapping[str, int] | None = None,
        credential_slot_id: str | None = None,
        credential_route_purpose: str | None = None,
        credential_route_id_prefix: str | None = None,
        credential_generation_id: str | None = None,
        network_route: str = "direct",
        proxy_endpoint: str | None = None,
        temporal_contract: Mapping[str, Any],
        temporal_role: str,
        temporal_contract_sha256: str,
        temporal_start_date: str,
        temporal_end_date: str,
        workers: int = 1,
    ) -> None:
        authorized_start = _iso_date(temporal_start_date)
        authorized_end = _iso_date(temporal_end_date)
        try:
            assert_range_allowed(
                temporal_contract,
                temporal_role,
                authorized_start,
                authorized_end,
                "collect",
            )
        except PartitionContractError as exc:
            raise PITCollectionError(str(exc)) from exc
        if str(temporal_contract.get("contract_sha256")) != str(temporal_contract_sha256):
            raise PITCollectionError("temporal contract hash does not match contract")
        if not str(token):
            raise PITCollectionError("Tushare credential is missing")
        parsed = urlparse(str(api_url))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise PITCollectionError("Tushare API URL is invalid")
        normalized_hosts = {str(host).strip().lower() for host in allowed_hosts}
        if str(parsed.hostname or "").lower() not in normalized_hosts:
            raise PITCollectionError("Tushare API host is not pinned")
        if parsed.scheme == "http" and not allow_insecure_http:
            # Jiaoch documents plain HTTP as the pinned gateway transport
            # (http://jiaoch.site); other sources still require explicit opt-in.
            jiaoch_documented_http = (
                str(source_profile).strip().lower() == "jiaoch"
                and str(parsed.hostname or "").lower() == "jiaoch.site"
            )
            if not jiaoch_documented_http:
                raise PITCollectionError("plain HTTP requires explicit allow_insecure_http")
        if int(max_attempts) <= 0:
            raise PITCollectionError("max_attempts must be positive")
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
            raise PITCollectionError("workers must be an integer in the range 1..8")
        if str(source_profile).strip().lower() == "jiaoch" and workers > 2:
            raise PITCollectionError(
                "jiaoch source workers must be an integer in the range 1..2"
            )
        if float(clock_attestation_ttl_s) <= 0:
            raise PITCollectionError("clock_attestation_ttl_s must be positive")
        if request_protocol not in {
            "tushare-root-post/v1",
            "tushare-path-per-interface/v1",
        }:
            raise PITCollectionError("Tushare request protocol is unsupported")
        if network_route not in {"direct", "loopback_http_proxy"}:
            raise PITCollectionError("Tushare network route is unsupported")
        if network_route == "direct" and proxy_endpoint is not None:
            raise PITCollectionError("direct network route forbids a proxy endpoint")
        if network_route == "loopback_http_proxy" and not proxy_endpoint:
            raise PITCollectionError("loopback HTTP proxy network route requires a proxy endpoint")
        if len(
            {
                credential_slot_id is None,
                credential_route_purpose is None,
                credential_route_id_prefix is None,
                credential_generation_id is None,
            }
        ) != 1:
            raise PITCollectionError("credential route identity is incomplete")
        if credential_slot_id is not None and (
            not isinstance(credential_slot_id, str)
            or not credential_slot_id
            or credential_slot_id != credential_slot_id.strip()
            or not isinstance(credential_route_purpose, str)
            or not credential_route_purpose
            or credential_route_purpose != credential_route_purpose.strip()
            or not isinstance(credential_route_id_prefix, str)
            or not credential_route_id_prefix
            or credential_route_id_prefix != credential_route_id_prefix.strip()
            or not isinstance(credential_generation_id, str)
            or not credential_generation_id
            or credential_generation_id != credential_generation_id.strip()
        ):
            raise PITCollectionError("credential route identity is invalid")
        if credential_generation_id is not None:
            try:
                parsed_generation_id = uuid.UUID(credential_generation_id)
            except (AttributeError, ValueError):
                raise PITCollectionError("credential route identity is invalid") from None
            if (
                parsed_generation_id.version != 4
                or str(parsed_generation_id) != credential_generation_id
            ):
                raise PITCollectionError("credential route identity is invalid")
        self.store = store
        self._token = str(token)
        frozen_overrides = tuple(
            sorted(
                (str(dataset), int(cap)) for dataset, cap in dict(row_cap_overrides or {}).items()
            )
        )
        self._source_authority = _SourceCollectionAuthority(
            api_url=str(api_url),
            allowed_hosts=tuple(sorted(normalized_hosts)),
            source_profile=str(source_profile),
            request_protocol=str(request_protocol),
            network_route=str(network_route),
            proxy_endpoint=proxy_endpoint,
            allow_insecure_http=bool(allow_insecure_http),
            row_cap_overrides=frozen_overrides,
            credential_slot_id=credential_slot_id,
            credential_route_purpose=credential_route_purpose,
            credential_route_id_prefix=credential_route_id_prefix,
            credential_generation_id=credential_generation_id,
        )
        # Freeze a private JSON-shaped copy so caller mutation cannot change the
        # collection authority after request semantics have been pinned.
        self._temporal_authority = _TemporalCollectionAuthority(
            contract_json=_canonical_json(temporal_contract),
            contract_sha256=str(temporal_contract_sha256),
            role=str(temporal_role),
            start_date=authorized_start,
            end_date=authorized_end,
        )
        self._workers = workers
        self._network_slots = threading.BoundedSemaphore(workers)
        self._active_cancellation: threading.Event | None = None
        self._membership_mode_local = threading.local()
        self.transport = transport
        self.clock = clock
        self.max_attempts = int(max_attempts)
        self.timeout_s = float(timeout_s)
        self.sleeper = sleeper
        self.clock_attestation_ttl_ns = int(float(clock_attestation_ttl_s) * 1_000_000_000)
        self._resume_index: dict[tuple[str, str, str], dict[str, Any]] | None = None
        self._clock_attestation: Mapping[str, Any] | None = None
        self._clock_attestation_monotonic_ns: int | None = None
        self._shared_state_lock = threading.RLock()
        self._shared_state_changed = threading.Condition(self._shared_state_lock)
        self._clock_attestation_loading = False
        self._resume_index_loading = False

    @property
    def temporal_contract(self) -> dict[str, Any]:
        return self._temporal_authority.contract_copy()

    @property
    def temporal_role(self) -> str:
        return self._temporal_authority.role

    @property
    def temporal_contract_sha256(self) -> str:
        return self._temporal_authority.contract_sha256

    @property
    def temporal_start_date(self) -> str:
        return self._temporal_authority.start_date

    @property
    def temporal_end_date(self) -> str:
        return self._temporal_authority.end_date

    @property
    def workers(self) -> int:
        return self._workers

    def _calendar_exchanges(self) -> tuple[str, ...]:
        return ("SSE",) if self.source_profile == "jiaoch" else ("SSE", "SZSE")

    @property
    def api_url(self) -> str:
        return self._source_authority.api_url

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return self._source_authority.allowed_hosts

    @property
    def source_profile(self) -> str:
        return self._source_authority.source_profile

    @property
    def request_protocol(self) -> str:
        return self._source_authority.request_protocol

    @property
    def network_route(self) -> str:
        return self._source_authority.network_route

    @property
    def proxy_endpoint(self) -> str | None:
        return self._source_authority.proxy_endpoint

    @property
    def allow_insecure_http(self) -> bool:
        return self._source_authority.allow_insecure_http

    @property
    def row_cap_overrides(self) -> dict[str, int]:
        return dict(self._source_authority.row_cap_overrides)

    @property
    def credential_slot_id(self) -> str | None:
        return self._source_authority.credential_slot_id

    @property
    def credential_route_purpose(self) -> str | None:
        return self._source_authority.credential_route_purpose

    @property
    def credential_route_id_prefix(self) -> str | None:
        return self._source_authority.credential_route_id_prefix

    @property
    def credential_generation_id(self) -> str | None:
        return self._source_authority.credential_generation_id

    def _authority_guard(self) -> None:
        authority = self._temporal_authority
        contract = authority.contract_copy()
        try:
            assert_range_allowed(
                contract,
                authority.role,
                authority.start_date,
                authority.end_date,
                "collect",
            )
        except PartitionContractError as exc:
            raise PITCollectionError(str(exc)) from exc
        if contract.get("contract_sha256") != authority.contract_sha256:
            raise PITCollectionError("temporal contract hash does not match authority")

    def _assert_date_authorized(self, value: str) -> None:
        authority = self._temporal_authority
        if not authority.start_date <= value <= authority.end_date:
            raise PITCollectionError("date exceeds collector authority")

    def _assert_spec_authorized(self, spec: FetchSpec) -> None:
        params = dict(spec.receipt_params)
        dates: list[str] = []
        if spec.dataset == "trade_cal":
            dates.extend([str(params.get("start_date")), str(params.get("end_date"))])
        elif "trade_date" in params:
            dates.append(str(params["trade_date"]))
        for value in dates:
            normalized = _iso_date(value)
            self._assert_date_authorized(normalized)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self._token, "[REDACTED]")
        if isinstance(value, Mapping):
            return {str(key): self._redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        return value

    def _response_contains_credential(self, body: bytes) -> bool:
        if self._token.encode("utf-8") in body:
            return True
        try:
            decoded = json.loads(body.decode("utf-8"), object_pairs_hook=lambda pairs: list(pairs))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False

        def walk(value: Any) -> bool:
            if isinstance(value, str):
                return self._token in value
            if isinstance(value, (list, tuple)):
                return any(walk(item) for item in value)
            return False

        return walk(decoded)

    def _freeze_spec(self, spec: FetchSpec) -> FetchSpec:
        row_cap_overrides = dict(self._source_authority.row_cap_overrides)
        try:
            return FetchSpec(
                dataset=str(spec.dataset),
                partition_key=str(spec.partition_key),
                api_name=str(spec.api_name),
                wire_params=dict(spec.wire_params),
                receipt_params=dict(spec.receipt_params),
                fields=tuple(spec.fields),
                row_cap=row_cap_overrides.get(str(spec.dataset), int(spec.row_cap)),
            )
        except (TypeError, ValueError) as exc:
            raise PITCollectionError("fetch specification could not be frozen") from exc

    def _attest_clock(self) -> Mapping[str, Any]:
        while True:
            with self._shared_state_changed:
                observed_ns = int(self.clock.monotonic_ns())
                checked_ns = self._clock_attestation_monotonic_ns
                if (
                    self._clock_attestation is not None
                    and checked_ns is not None
                    and observed_ns >= checked_ns
                    and observed_ns - checked_ns <= self.clock_attestation_ttl_ns
                ):
                    return {
                        **dict(self._clock_attestation),
                        "attestation_age_ns": observed_ns - checked_ns,
                        "maximum_attestation_age_ns": self.clock_attestation_ttl_ns,
                    }
                if not self._clock_attestation_loading:
                    self._clock_attestation_loading = True
                    break
                self._shared_state_changed.wait()
        loaded = False
        try:
            try:
                evidence = self._redact(dict(self.clock.assert_synchronized()))
                if evidence.get("synchronized") is not True:
                    raise PITCollectionError("system clock synchronization could not be proven")
                checked_ns = int(self.clock.monotonic_ns())
                loaded = True
            except KeyboardInterrupt:
                raise KeyboardInterrupt() from None
            except (SystemExit, GeneratorExit) as exc:
                message = self._redact(str(exc)) or type(exc).__name__
                raise PITCollectionError(message) from None
        finally:
            if not loaded:
                with self._shared_state_changed:
                    self._clock_attestation_loading = False
                    self._shared_state_changed.notify_all()
        with self._shared_state_changed:
            self._clock_attestation = evidence
            self._clock_attestation_monotonic_ns = checked_ns
            self._clock_attestation_loading = False
            self._shared_state_changed.notify_all()
            return {
                **dict(evidence),
                "attestation_age_ns": 0,
                "maximum_attestation_age_ns": self.clock_attestation_ttl_ns,
            }

    def _run_phase(
        self,
        indexed_tasks: Sequence[tuple[int, Callable[[], Any]]],
        *,
        workers: int,
        cancellation_event: threading.Event | None = None,
    ) -> list[Any]:
        """Run independent tasks concurrently while retaining planned order."""

        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
            raise PITCollectionError("workers must be an integer in the range 1..8")
        tasks = list(indexed_tasks)
        indexes = [index for index, _task in tasks]
        if any(not isinstance(index, int) or isinstance(index, bool) for index in indexes):
            raise PITCollectionError("phase task indexes must be integers")
        if len(set(indexes)) != len(indexes):
            raise PITCollectionError("phase task indexes must be unique")
        if any(not callable(task) for _index, task in tasks):
            raise PITCollectionError("phase tasks must be callable")
        tasks.sort(key=lambda item: item[0])

        cancelled = cancellation_event or self._active_cancellation or threading.Event()
        failure_lock = threading.Lock()
        failures: dict[int, BaseException] = {}
        first_failure_index: list[int] = []

        def invoke(index: int, task: Callable[[], Any]) -> Any:
            if cancelled.is_set():
                raise CancelledError()
            try:
                return task()
            except BaseException as exc:
                with failure_lock:
                    failures[index] = exc
                    if not first_failure_index:
                        first_failure_index.append(index)
                    cancelled.set()
                raise

        results: dict[int, Any] = {}
        if workers == 1:
            for index, task in tasks:
                try:
                    results[index] = invoke(index, task)
                except CancelledError:
                    break
                except BaseException:
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures: dict[Future[Any], int] = {
                    executor.submit(invoke, index, task): index for index, task in tasks
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except CancelledError:
                        continue
                    except BaseException:
                        for pending in futures:
                            if pending is not future:
                                pending.cancel()

        if failures:
            first_index = first_failure_index[0]
            first_error = failures[first_index]
            if isinstance(first_error, KeyboardInterrupt):
                raise KeyboardInterrupt() from None
            message = self._redact(str(first_error)) or type(first_error).__name__
            error = PITCollectionError(f"phase task {first_index} failed: {message}")
            for index in sorted(failures):
                if index == first_index:
                    continue
                exc = failures[index]
                detail = self._redact(str(exc)) or type(exc).__name__
                error.add_note(f"additional concurrent failure at task {index}: {detail}")
            raise error from None
        if cancelled.is_set() and len(results) != len(tasks):
            raise PITCollectionError("collection cancelled after concurrent failure")
        return [results[index] for index, _task in tasks]

    def _check_cancelled(self) -> None:
        token = self._active_cancellation
        if token is not None and token.is_set():
            raise _CollectionCancelled("collection cancelled after concurrent failure")

    def _validate_spec(self, spec: FetchSpec) -> None:
        if spec.dataset not in MAX_RAW_BYTES or spec.api_name != spec.dataset:
            raise PITCollectionError("fetch specification is unsupported")
        try:
            normalized_params = _normalize_request_params(spec.dataset, spec.receipt_params)
            expected_wire = _canonical_wire_params(spec.dataset, normalized_params)
            expected_partition = _canonical_partition_key(spec.dataset, normalized_params)
        except (KeyError, PITReceiptError) as exc:
            raise PITCollectionError("fetch specification params are not canonical") from exc
        if dict(spec.wire_params) != expected_wire:
            raise PITCollectionError("fetch specification wire params are not canonical")
        if spec.partition_key != expected_partition:
            raise PITCollectionError("fetch specification partition is not canonical")
        if tuple(spec.fields) != tuple(NORMALIZED_FIELDS[spec.dataset]):
            raise PITCollectionError("fetch specification fields are not canonical")
        if spec.dataset == "stock_basic" and (
            normalized_params["exchange"] not in {"SSE", "SZSE"}
            or normalized_params["list_status"] not in {"L", "D", "P", "G"}
        ):
            raise PITCollectionError("fetch specification stock shard is invalid")
        if spec.dataset == "trade_cal" and normalized_params["exchange"] not in {
            "SSE",
            "SZSE",
        }:
            raise PITCollectionError("fetch specification calendar shard is invalid")
        expected_cap = OFFICIAL_ROW_CAPS.get(spec.dataset)
        expected_cap = dict(self._source_authority.row_cap_overrides).get(
            spec.dataset, expected_cap
        )
        if spec.dataset == "trade_cal":
            start = date.fromisoformat(normalized_params["start_date"])
            end = date.fromisoformat(normalized_params["end_date"])
            if end < start:
                raise PITCollectionError("fetch specification dates are invalid")
            expected_cap = (end - start).days + 2
        if expected_cap is not None and int(spec.row_cap) != int(expected_cap):
            raise PITCollectionError("fetch specification row cap is not canonical")

    @staticmethod
    def _attempt_id(recorded: Any) -> str:
        if isinstance(recorded, Mapping):
            return str(recorded["attempt_id"])
        return str(recorded)

    def _record_attempt(
        self,
        spec: FetchSpec,
        *,
        attempt_no: int,
        request_body_sha256: str,
        request_semantics: Mapping[str, Any],
        request_semantics_sha256: str,
        started_at: str | None,
        retrieved_at: str | None,
        elapsed_ns: int,
        http_status: int | None,
        response_headers: Mapping[str, Any],
        raw_bytes: bytes | None,
        body_complete: bool,
        error_kind: str | None,
        error_message: str | None,
        clock_attestation: Mapping[str, Any],
    ) -> dict[str, Any]:
        metadata = {
            "dataset": spec.dataset,
            "partition_key": spec.partition_key,
            "endpoint": spec.api_name,
            "params": dict(spec.receipt_params),
            "fields": list(spec.fields),
            "wire_request_sha256": request_body_sha256,
            "request_body_sha256": request_body_sha256,
            "request_semantics_sha256": request_semantics_sha256,
            "request_semantics": self._redact(dict(request_semantics)),
            "raw_bytes": raw_bytes,
            "http_status": http_status,
            "started_at": started_at,
            "retrieved_at": retrieved_at,
            "elapsed_ns": int(elapsed_ns),
            "row_cap": int(spec.row_cap),
            "body_complete": bool(body_complete),
            "attempt_no": int(attempt_no),
            "response_headers": self._redact(dict(response_headers)),
            "clock_attestation": self._redact(dict(clock_attestation)),
            "error_kind": error_kind,
            "error_message": self._redact(error_message),
        }
        recorded = self.store.record_fetch_attempt(**metadata)
        attempt_id = self._attempt_id(recorded)
        return {
            "attempt_id": attempt_id,
            "attempt_no": attempt_no,
            "http_status": http_status,
            "error_kind": error_kind,
            "error_message": error_message,
            "request_body_sha256": request_body_sha256,
            "request_semantics_sha256": request_semantics_sha256,
        }

    def _request_semantics(self, spec: FetchSpec) -> dict[str, Any]:
        source = self._source_authority
        semantics = {
            "schema_version": "tushare-wire-request/v1",
            "source_profile": source.source_profile,
            "request_protocol": source.request_protocol,
            "network_route": source.network_route,
            "proxy_endpoint": source.proxy_endpoint,
            "dataset": spec.dataset,
            "partition_key": spec.partition_key,
            "api_name": spec.api_name,
            "method": "POST",
            "url": self._request_url(spec),
            "wire_params": dict(spec.wire_params),
            "receipt_params": dict(spec.receipt_params),
            "fields": list(spec.fields),
            "row_cap": int(spec.row_cap),
            "temporal_role": self._temporal_authority.role,
            "temporal_contract_sha256": self._temporal_authority.contract_sha256,
        }
        if source.credential_slot_id is not None:
            semantics["credential_slot_id"] = source.credential_slot_id
            semantics["credential_route_purpose"] = source.credential_route_purpose
            semantics["credential_route_id"] = (
                f"{source.credential_route_id_prefix}:"
                f"{source.credential_slot_id}:{spec.api_name}"
            )
            semantics["credential_generation_id"] = source.credential_generation_id
        return semantics

    def _request_url(self, spec: FetchSpec) -> str:
        source = self._source_authority
        if source.request_protocol == "tushare-path-per-interface/v1":
            return f"{source.api_url.rstrip('/')}/{spec.api_name}"
        return source.api_url

    def _generation_matches_request_semantics(
        self,
        verification: Mapping[str, Any],
        specs: Sequence[FetchSpec],
        *,
        shard_key: str,
    ) -> bool:
        shards = list((verification.get("manifest") or {}).get("shards") or [])
        actual = {
            str(shard.get(shard_key)): shard.get("request_semantics_sha256") for shard in shards
        }
        expected = {
            str(
                spec.partition_key
                if shard_key == "logical_partition_key"
                else getattr(spec, shard_key)
            ): _sha256_json(self._request_semantics(self._freeze_spec(spec)))
            for spec in specs
        }
        return len(shards) == len(actual) and actual == expected

    def _expected_request_semantics_manifest(
        self, specs: Sequence[FetchSpec], *, key: str
    ) -> tuple[dict[str, str], str]:
        manifest = {
            str(spec.partition_key if key == "partition_key" else spec.dataset): _sha256_json(
                self._request_semantics(self._freeze_spec(spec))
            )
            for spec in specs
        }
        canonical = {name: manifest[name] for name in sorted(manifest)}
        return canonical, _sha256_json(canonical)

    def _staged_generation_matches_request_semantics(
        self,
        generation: Mapping[str, Any],
        specs: Sequence[FetchSpec],
        *,
        staged_key: str,
        event_status: str,
    ) -> bool:
        staged = set(generation.get(staged_key) or [])
        if not staged:
            return True
        if not hasattr(self.store, "fetch_attempts"):
            return True
        expected = {
            (
                spec.partition_key if staged_key == "staged_partitions" else spec.dataset
            ): _sha256_json(self._request_semantics(self._freeze_spec(spec)))
            for spec in specs
        }
        actual: dict[str, str] = {}
        generation_id = str(generation["generation_id"])
        for attempt in self.store.fetch_attempts():
            for event in attempt.get("promotion_events") or []:
                details = event.get("details") or {}
                if (
                    event.get("status") == event_status
                    and str(details.get("generation_id")) == generation_id
                ):
                    key = (
                        str(attempt["partition_key"])
                        if staged_key == "staged_partitions"
                        else str(attempt["dataset"])
                    )
                    if key in actual:
                        return False
                    actual[key] = str(attempt["request_semantics_sha256"])
        return set(actual) == staged and all(actual[key] == expected.get(key) for key in staged)

    def fetch_membership_snapshot(self, spec: FetchSpec, *, resume: bool = False) -> dict[str, Any]:
        """Fetch one membership snapshot with a strict semantic-empty contract."""

        reusable = self._resumable_membership_snapshot(spec, resume=resume)
        if reusable is not None:
            return reusable
        previous = getattr(self._membership_mode_local, "enabled", False)
        self._membership_mode_local.enabled = True
        try:
            return self.fetch_partition(spec, resume=resume)
        finally:
            self._membership_mode_local.enabled = previous

    def _resumable_membership_snapshot(
        self, spec: FetchSpec, *, resume: bool
    ) -> dict[str, Any] | None:
        if not resume or not hasattr(self.store, "active_membership_generation"):
            return None
        self._authority_guard()
        frozen = self._freeze_spec(spec)
        self._validate_spec(frozen)
        self._assert_spec_authorized(frozen)
        request_semantics_sha256 = _sha256_json(self._request_semantics(frozen))
        active = self.store.active_membership_generation(frozen.partition_key)
        if active is None:
            return None
        evidence = [
            item
            for item in active.get("empty_evidence", [])
            if item.get("evidence_kind", "target_semantic_empty") == "target_semantic_empty"
        ]
        if (
            active.get("temporal_role") != self._temporal_authority.role
            or active.get("temporal_contract_sha256") != self._temporal_authority.contract_sha256
            or not evidence
            or any(
                item.get("request_semantics_sha256") != request_semantics_sha256
                for item in evidence
            )
        ):
            return None
        return {
            "status": "semantic_empty",
            "dataset": frozen.dataset,
            "partition_key": frozen.partition_key,
            "attempts": [],
            "receipt": None,
            "request_semantics_sha256": request_semantics_sha256,
            "semantic_empty": True,
            "reused": True,
        }

    def _membership_semantic_empty_result(
        self,
        spec: FetchSpec,
        attempts: Sequence[Mapping[str, Any]],
        raw_bodies: Sequence[bytes],
        request_semantics_sha256: str,
    ) -> dict[str, Any]:
        if len(attempts) != self.max_attempts or len(raw_bodies) != len(attempts):
            raise PITCollectionError("membership semantic-empty retry cohort is incomplete")
        for attempt, raw in zip(attempts, raw_bodies):
            if (
                attempt.get("error_kind") != "invalid_json"
                or attempt.get("error_message") != "planned response is empty"
                or not raw
            ):
                raise PITCollectionError("membership retry cohort contains conflicting outcomes")
            try:
                classification = classify_membership_snapshot_body(raw, required_fields=spec.fields)
            except MembershipContractError as exc:
                raise PITCollectionError(
                    "membership semantic-empty raw body failed classification"
                ) from exc
            if classification.kind != "semantic_empty":
                raise PITCollectionError("membership retry body is not semantic-empty")
            if hasattr(self.store, "fetch_attempts"):
                persisted = self.store.fetch_attempts(attempt_id=str(attempt["attempt_id"]))
                if len(persisted) != 1:
                    raise PITCollectionError("membership attempt persistence is incomplete")
                stored = persisted[0]
                events = stored.get("promotion_events") or []
                if len(events) != 1 or events[0].get("status") != "invalid_json":
                    raise PITCollectionError("membership attempt terminal evidence is incomplete")
                if stored.get("raw_sha256") != hashlib.sha256(raw).hexdigest():
                    raise PITCollectionError("membership attempt raw hash mismatch")
        return {
            "status": "semantic_empty",
            "dataset": spec.dataset,
            "partition_key": spec.partition_key,
            "attempts": list(attempts),
            "receipt": None,
            "request_semantics_sha256": request_semantics_sha256,
            "semantic_empty": True,
        }

    def fetch_partition(
        self,
        spec: FetchSpec,
        *,
        resume: bool = False,
        stock_generation_id: str | None = None,
        market_generation_id: str | None = None,
    ) -> dict[str, Any]:
        membership_mode = bool(getattr(self._membership_mode_local, "enabled", False))
        self._authority_guard()
        spec = self._freeze_spec(spec)
        self._validate_spec(spec)
        self._assert_spec_authorized(spec)
        request_semantics = self._request_semantics(spec)
        request_semantics_sha256 = _sha256_json(request_semantics)
        if resume and hasattr(self.store, "controlled_receipt_index"):
            should_load_resume_index = False
            while True:
                with self._shared_state_changed:
                    if self._resume_index is not None:
                        break
                    if not self._resume_index_loading:
                        self._resume_index_loading = True
                        should_load_resume_index = True
                        break
                    self._shared_state_changed.wait()
            if should_load_resume_index:
                loaded = False
                try:
                    try:
                        loaded_index = self.store.controlled_receipt_index()
                        loaded = True
                    except KeyboardInterrupt:
                        raise KeyboardInterrupt() from None
                    except (SystemExit, GeneratorExit) as exc:
                        message = self._redact(str(exc)) or type(exc).__name__
                        raise PITCollectionError(message) from None
                finally:
                    if not loaded:
                        with self._shared_state_changed:
                            self._resume_index_loading = False
                            self._shared_state_changed.notify_all()
                with self._shared_state_changed:
                    self._resume_index = loaded_index
                    self._resume_index_loading = False
                    self._shared_state_changed.notify_all()
            with self._shared_state_lock:
                resume_key = (
                    spec.dataset,
                    spec.partition_key,
                    request_semantics_sha256,
                )
                existing = self._resume_index.get(resume_key)
            if existing is not None:
                return {
                    "status": "skipped",
                    "dataset": spec.dataset,
                    "partition_key": spec.partition_key,
                    "attempts": [],
                    "receipt": {"status": "skipped", **existing},
                    "request_semantics_sha256": request_semantics_sha256,
                }
        request_payload = {
            "api_name": spec.api_name,
            "token": self._token,
            "params": dict(spec.wire_params),
            "fields": ",".join(spec.fields),
        }
        request_body = _canonical_json(request_payload).encode("utf-8")
        request_body_sha256 = hashlib.sha256(request_body).hexdigest()
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            # Jiaoch currently returns HTTP/1.1 JSON entities without a
            # Content-Length or chunked framing header.  Requesting connection
            # close gives the bounded reader an immediate, protocol-level EOF
            # instead of forcing every otherwise complete response to wait for
            # the socket timeout.
            "Connection": "close",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "quant-research-pit-collector/1",
        }
        attempts: list[dict[str, Any]] = []
        membership_raw_bodies: list[bytes] = []
        last_error = "fetch attempts exhausted"

        for attempt_no in range(1, self.max_attempts + 1):
            self._check_cancelled()
            attestation = self._attest_clock()
            started_monotonic = int(self.clock.monotonic_ns())
            try:
                self._network_slots.acquire()
                try:
                    self._check_cancelled()
                    try:
                        response = self.transport.post(
                            url=self._request_url(spec),
                            headers=headers,
                            body=request_body,
                            timeout_s=self.timeout_s,
                            max_body_bytes=MAX_RAW_BYTES[spec.dataset],
                        )
                    except BaseException:
                        if attempt_no >= self.max_attempts:
                            token = self._active_cancellation
                            if token is not None:
                                token.set()
                        raise
                finally:
                    self._network_slots.release()
            except _CollectionCancelled:
                raise
            except Exception as exc:
                elapsed = max(0, int(self.clock.monotonic_ns()) - started_monotonic)
                last_error = self._redact(str(exc)) or type(exc).__name__
                attempts.append(
                    self._record_attempt(
                        spec,
                        attempt_no=attempt_no,
                        request_body_sha256=request_body_sha256,
                        request_semantics=request_semantics,
                        request_semantics_sha256=request_semantics_sha256,
                        started_at=None,
                        retrieved_at=None,
                        elapsed_ns=elapsed,
                        http_status=None,
                        response_headers={},
                        raw_bytes=None,
                        body_complete=False,
                        error_kind="transport_error",
                        error_message=last_error,
                        clock_attestation=attestation,
                    )
                )
                if membership_mode:
                    membership_raw_bodies.append(b"")
                if attempt_no < self.max_attempts:
                    self._check_cancelled()
                    self.sleeper(min(2 ** (attempt_no - 1), 8))
                    continue
                raise PITCollectionError(
                    f"fetch attempts exhausted for {spec.dataset}/{spec.partition_key}: {last_error}"
                ) from None

            if not isinstance(response.body, bytes):
                response = HttpEntityResponse(
                    status=response.status,
                    headers=response.headers,
                    body=b"",
                    body_complete=False,
                )
            elif len(response.body) > MAX_RAW_BYTES[spec.dataset]:
                response = HttpEntityResponse(
                    status=response.status,
                    headers=response.headers,
                    body=response.body[: MAX_RAW_BYTES[spec.dataset]],
                    body_complete=False,
                )
            allowed_headers = {
                str(key).lower(): str(value)
                for key, value in response.headers.items()
                if str(key).lower() in RESPONSE_HEADER_ALLOWLIST
            }
            content_encoding = str(allowed_headers.get("content-encoding") or "identity").lower()
            elapsed = 0
            try:
                elapsed = max(0, int(self.clock.monotonic_ns()) - started_monotonic)
                attestation = self._attest_clock()
                retrieved = self.clock.now_utc()
                if not isinstance(retrieved, datetime) or retrieved.tzinfo is None:
                    raise PITCollectionError(
                        "trusted clock returned an invalid retrieval timestamp"
                    )
            except Exception:
                contains_credential = self._response_contains_credential(response.body)
                unsupported_encoding = content_encoding not in {"", "identity"}
                raw_after_clock_failure = (
                    None if contains_credential or unsupported_encoding else response.body
                )
                clock_failure_kind = (
                    "credential_echo"
                    if contains_credential
                    else "unsupported_content_encoding"
                    if unsupported_encoding
                    else "clock_attestation_failed"
                )
                clock_failure_message = (
                    "response contained a credential and was discarded"
                    if contains_credential
                    else "compressed response encoding is unsupported"
                    if unsupported_encoding
                    else "trusted clock attestation failed after response"
                )
                failed_attestation = {
                    **dict(attestation),
                    "synchronized": False,
                    "failure_phase": "after_response",
                }
                attempt = self._record_attempt(
                    spec,
                    attempt_no=attempt_no,
                    request_body_sha256=request_body_sha256,
                    request_semantics=request_semantics,
                    request_semantics_sha256=request_semantics_sha256,
                    started_at=None,
                    retrieved_at=None,
                    elapsed_ns=elapsed,
                    http_status=int(response.status),
                    response_headers=allowed_headers,
                    raw_bytes=raw_after_clock_failure,
                    body_complete=(
                        response.body_complete if raw_after_clock_failure is not None else False
                    ),
                    error_kind=clock_failure_kind,
                    error_message=clock_failure_message,
                    clock_attestation=failed_attestation,
                )
                attempts.append(attempt)
                if membership_mode:
                    membership_raw_bodies.append(b"")
                raise PITCollectionError(
                    f"clock synchronization could not be proven after response for "
                    f"{spec.dataset}/{spec.partition_key}"
                ) from None
            retrieved_at = retrieved.isoformat()
            started_at = (retrieved - timedelta(microseconds=elapsed / 1000)).isoformat()
            terminal = False
            retryable = False
            error_kind: str | None = None
            error_message: str | None = None
            persisted_body: bytes | None = response.body
            persisted_body_complete = response.body_complete
            if self._response_contains_credential(response.body):
                terminal = True
                error_kind = "credential_echo"
                error_message = "response contained a credential and was discarded"
                persisted_body = None
                persisted_body_complete = False
            elif not response.body_complete:
                retryable = True
                error_kind = "incomplete_response"
                error_message = "response body is incomplete"
            elif content_encoding not in {"", "identity"}:
                terminal = True
                error_kind = "unsupported_content_encoding"
                error_message = "compressed response encoding is unsupported"
                persisted_body = None
                persisted_body_complete = False
            elif int(response.status) in RETRYABLE_HTTP_STATUSES:
                retryable = True
                error_kind = "http_retryable"
                error_message = f"HTTP status {int(response.status)}"
            elif not 200 <= int(response.status) < 300:
                terminal = True
                error_kind = "http_error"
                error_message = f"HTTP status {int(response.status)}"
            else:
                try:
                    fields, items, _code, _message = _parse_native_envelope(response.body)
                    if any(field not in fields for field in spec.fields):
                        raise PITReceiptError("native response missing requested fields")
                    if spec.dataset in {"trade_cal", "bak_basic"} and not items:
                        raise PITReceiptError("planned response is empty")
                except PITReceiptError as exc:
                    message = self._redact(str(exc))
                    if message.startswith("native response code "):
                        terminal = True
                        error_kind = "api_error"
                    else:
                        retryable = True
                        error_kind = "invalid_json"
                    error_message = message

            attempt = self._record_attempt(
                spec,
                attempt_no=attempt_no,
                request_body_sha256=request_body_sha256,
                request_semantics=request_semantics,
                request_semantics_sha256=request_semantics_sha256,
                started_at=started_at,
                retrieved_at=retrieved_at,
                elapsed_ns=elapsed,
                http_status=int(response.status),
                response_headers=allowed_headers,
                raw_bytes=persisted_body,
                body_complete=persisted_body_complete,
                error_kind=error_kind,
                error_message=error_message,
                clock_attestation=attestation,
            )
            attempts.append(attempt)
            if membership_mode:
                membership_raw_bodies.append(persisted_body or b"")

            if not terminal and not retryable:
                if membership_mode and len(attempts) > 1:
                    receipt = self.store.promote_fetch_attempt(attempt["attempt_id"])
                    if receipt.get("status") not in {"stored", "reused"}:
                        raise PITCollectionError(
                            f"membership mixed retry terminal was not persisted for "
                            f"{spec.dataset}/{spec.partition_key}"
                        )
                    raise PITCollectionError(
                        f"membership retry cohort mixed for {spec.dataset}/{spec.partition_key}"
                    )
                if stock_generation_id is not None:
                    if spec.dataset != "stock_basic":
                        raise PITCollectionError("stock generation staging requires stock_basic")
                    receipt = self.store.stage_stock_basic_attempt(
                        str(stock_generation_id),
                        spec.partition_key,
                        attempt["attempt_id"],
                    )
                    accepted_statuses = {"staged", "reused"}
                elif market_generation_id is not None:
                    if spec.dataset not in {"daily", "adj_factor", "stk_limit", "suspend_d"}:
                        raise PITCollectionError(
                            "market generation staging requires a market session dataset"
                        )
                    receipt = self.store.stage_market_session_attempt(
                        str(market_generation_id),
                        spec.dataset,
                        attempt["attempt_id"],
                    )
                    accepted_statuses = {"staged", "reused"}
                else:
                    receipt = self.store.promote_fetch_attempt(attempt["attempt_id"])
                    accepted_statuses = {"stored", "reused"}
                if receipt.get("status") == "immutable_conflict":
                    raise PITCollectionError(
                        f"immutable conflict for {spec.dataset}/{spec.partition_key}"
                    )
                if receipt.get("status") not in accepted_statuses:
                    raise PITCollectionError(
                        f"attempt was not promoted for {spec.dataset}/{spec.partition_key}: "
                        f"{receipt.get('status')}"
                    )
                if stock_generation_id is None and receipt.get("status") in {"stored", "reused"}:
                    with self._shared_state_lock:
                        if self._resume_index is not None:
                            self._resume_index[
                                (
                                    spec.dataset,
                                    spec.partition_key,
                                    request_semantics_sha256,
                                )
                            ] = {
                                "dataset": spec.dataset,
                                "partition_key": spec.partition_key,
                                "request_semantics_sha256": request_semantics_sha256,
                                "receipt_raw_sha256": receipt.get("receipt_raw_sha256"),
                                "promotion_status": receipt.get("status"),
                            }
                return {
                    "status": str(receipt.get("status") or "stored"),
                    "dataset": spec.dataset,
                    "partition_key": spec.partition_key,
                    "attempts": attempts,
                    "receipt": receipt,
                    "request_semantics_sha256": request_semantics_sha256,
                }
            last_error = error_message or error_kind or "response rejected"
            if retryable and attempt_no < self.max_attempts:
                self._check_cancelled()
                self.sleeper(min(2 ** (attempt_no - 1), 8))
                continue
            if (
                membership_mode
                and retryable
                and error_kind == "invalid_json"
                and error_message == "planned response is empty"
            ):
                self._check_cancelled()
                return self._membership_semantic_empty_result(
                    spec,
                    attempts,
                    membership_raw_bodies,
                    request_semantics_sha256,
                )
            raise PITCollectionError(
                f"fetch failed for {spec.dataset}/{spec.partition_key}: {last_error}"
            )

        raise PITCollectionError(
            f"fetch attempts exhausted for {spec.dataset}/{spec.partition_key}"
        )

    def collect_stock_basic_generation(
        self,
        *,
        now: datetime | None = None,
        resume: bool = True,
        reuse_published: bool = False,
        workers: int = 1,
    ) -> dict[str, Any]:
        self._authority_guard()
        if resume and reuse_published:
            active = self.store.active_stock_basic_generation()
            if active is not None:
                verification = self.store.verify_stock_basic_generation(
                    str(active["generation_id"])
                )
                if self._generation_matches_request_semantics(
                    verification,
                    build_stock_basic_specs(),
                    shard_key="logical_partition_key",
                ):
                    return {
                        "status": "published",
                        "generation_id": str(active["generation_id"]),
                        "started_at": active.get("started_at"),
                        "staged_before": list(REQUIRED_STOCK_PARTITIONS),
                        "fetched_partition_count": 0,
                        "attempt_count": 0,
                        "published": active,
                        "active": active,
                        "reused_published": True,
                    }
        resolved_now = now
        if resolved_now is None:
            self._attest_clock()
            resolved_now = self.clock.now_utc()
        if not isinstance(resolved_now, datetime) or resolved_now.tzinfo is None:
            raise PITCollectionError("stock generation now must be timezone-aware")
        stock_specs = build_stock_basic_specs()
        expected_manifest, expected_manifest_sha256 = self._expected_request_semantics_manifest(
            stock_specs, key="partition_key"
        )
        pin_options = (
            {
                "expected_request_semantics": expected_manifest,
                "expected_request_semantics_sha256": expected_manifest_sha256,
            }
            if isinstance(self.store, PITReceiptStore)
            else {}
        )
        generation = self.store.begin_or_resume_stock_basic_generation(
            resolved_now, force_new=not resume, **pin_options
        )
        if resume and not self._staged_generation_matches_request_semantics(
            generation,
            stock_specs,
            staged_key="staged_partitions",
            event_status="generation_staged",
        ):
            generation = self.store.begin_or_resume_stock_basic_generation(
                resolved_now, force_new=True, **pin_options
            )
        generation_id = str(generation["generation_id"])
        staged = set(generation.get("staged_partitions") or [])
        missing_specs = [spec for spec in stock_specs if spec.partition_key not in staged]
        results = self._run_phase(
            [
                (
                    index,
                    lambda spec=spec: self.fetch_partition(
                        spec,
                        resume=False,
                        stock_generation_id=generation_id,
                    ),
                )
                for index, spec in enumerate(missing_specs)
            ],
            workers=workers,
        )
        self._check_cancelled()
        published = self.store.publish_stock_basic_generation(generation_id)
        active = self.store.active_stock_basic_generation()
        if active is None or str(active.get("generation_id")) != generation_id:
            raise PITCollectionError("published stock generation is not the active head")
        return {
            "status": "published",
            "generation_id": generation_id,
            "started_at": generation.get("started_at"),
            "staged_before": [key for key in REQUIRED_STOCK_PARTITIONS if key in staged],
            "fetched_partition_count": len(results),
            "attempt_count": sum(len(row.get("attempts") or []) for row in results),
            "published": published,
            "active": active,
            "reused_published": False,
        }

    def collect_market_session_generation(
        self,
        trade_date: str,
        *,
        now: datetime | None = None,
        resume: bool = True,
        vintage: str = DEFAULT_MARKET_SESSION_VINTAGE,
    ) -> dict[str, Any]:
        """Drive the four-shard atomic market session generation for one trade_date.

        The official path never emits legacy per-dataset receipts: each market
        dataset attempt is staged into the generation. A store without market
        generation capabilities is refused rather than silently degraded.
        """

        self._authority_guard()
        session = _iso_date(trade_date)
        self._assert_date_authorized(session)
        specs = [
            self._freeze_spec(spec) for spec in build_market_session_fetch_specs(session)
        ]
        market_methods = (
            "begin_or_resume_market_session_generation",
            "stage_market_session_attempt",
            "publish_market_session_generation",
            "active_market_session_generation",
            "verify_market_session_generation",
        )
        if not all(hasattr(self.store, method) for method in market_methods):
            raise PITCollectionError(
                "official market session collection requires generation capabilities"
            )
        if resume:
            active = self.store.active_market_session_generation(session)
            if active is not None and active.get("status") == "published":
                verification = self.store.verify_market_session_generation(trade_date=session)
                if self._generation_matches_request_semantics(
                    verification, specs, shard_key="dataset"
                ):
                    return {
                        "status": "published",
                        "trade_date": session,
                        "generation_id": str(active["generation_id"]),
                        "staged_datasets": list(active.get("staged_datasets") or []),
                        "fetched_dataset_count": 0,
                        "attempt_count": 0,
                        "published": active,
                        "active": active,
                        "manifest": verification["manifest"],
                        "manifest_sha256": verification["manifest_sha256"],
                        "lineage_sha256": verification["lineage_sha256"],
                        "reused_published": True,
                    }
        # WHY: only the collecting path needs a synchronized, timezone-aware
        # now to open the generation; the reuse short-circuit above returns
        # without touching the clock, so resume never depends on a fresh fetch.
        resolved_now = now
        if resolved_now is None:
            self._attest_clock()
            resolved_now = self.clock.now_utc()
        if not isinstance(resolved_now, datetime) or resolved_now.tzinfo is None:
            raise PITCollectionError("market generation now must be timezone-aware")
        expected_manifest, expected_manifest_sha256 = self._expected_request_semantics_manifest(
            specs, key="dataset"
        )
        pin_options = (
            {
                "expected_request_semantics": expected_manifest,
                "expected_request_semantics_sha256": expected_manifest_sha256,
            }
            if isinstance(self.store, PITReceiptStore)
            else {}
        )
        generation = self.store.begin_or_resume_market_session_generation(
            resolved_now,
            session,
            vintage=vintage,
            force_new=not resume,
            **pin_options,
        )
        if resume and not self._staged_generation_matches_request_semantics(
            generation,
            specs,
            staged_key="staged_datasets",
            event_status="market_session_staged",
        ):
            generation = self.store.begin_or_resume_market_session_generation(
                resolved_now,
                session,
                vintage=vintage,
                force_new=True,
                **pin_options,
            )
        generation_id = str(generation["generation_id"])
        staged = set(generation.get("staged_datasets") or [])
        results = []
        for spec in specs:
            self._check_cancelled()
            if spec.dataset in staged:
                continue
            results.append(
                self.fetch_partition(
                    spec,
                    resume=False,
                    market_generation_id=generation_id,
                )
            )
        self._check_cancelled()
        published = self.store.publish_market_session_generation(generation_id)
        active = self.store.active_market_session_generation(session)
        if active is None or str(active.get("generation_id")) != generation_id:
            raise PITCollectionError("published market generation is not the active head")
        return {
            "status": "published",
            "trade_date": session,
            "generation_id": generation_id,
            "staged_datasets": list(published.get("staged_datasets") or []),
            "fetched_dataset_count": len(results),
            "attempt_count": sum(len(row.get("attempts") or []) for row in results),
            "published": published,
            "active": active,
            "manifest": published["manifest"],
            "manifest_sha256": published["manifest_sha256"],
            "lineage_sha256": published["lineage_sha256"],
        }

    def collect(
        self,
        *,
        start_date: str,
        end_date: str,
        resume: bool = True,
        workers: int | None = None,
    ) -> dict[str, Any]:
        if self._active_cancellation is not None:
            raise PITCollectionError("collector already has an active collection run")
        token = threading.Event()
        self._active_cancellation = token
        try:
            return self._collect_once(
                start_date=start_date,
                end_date=end_date,
                resume=resume,
                workers=workers,
            )
        finally:
            self._active_cancellation = None

    def collect_trade_calendars(
        self,
        *,
        start_date: str,
        end_date: str,
        resume: bool = True,
    ) -> dict[str, Any]:
        """Collect only the two exchange calendar receipts for an authorized range."""

        if self._active_cancellation is not None:
            raise PITCollectionError("collector already has an active collection run")
        token = threading.Event()
        self._active_cancellation = token
        try:
            return self._collect_trade_calendars_once(
                start_date=start_date,
                end_date=end_date,
                resume=resume,
            )
        finally:
            self._active_cancellation = None

    def collect_current_pool_market(
        self,
        *,
        start_date: str,
        end_date: str,
        resume: bool = True,
        batch_size: int = 10,
        progress_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Collect only exchange calendars and four-shard market generations.

        This deliberately excludes stock masters, historical membership, and
        announcement/PDF evidence. Published session generations remain the
        resumable unit; the optional progress file is only an operator
        checkpoint and never contains request bodies or credentials.
        """

        if self._active_cancellation is not None:
            raise PITCollectionError("collector already has an active collection run")
        if self.workers not in {1, 2}:
            raise PITCollectionError("market-only workers must be in the range 1..2")
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 50
        ):
            raise PITCollectionError("batch_size must be an integer in the range 1..50")
        token = threading.Event()
        self._active_cancellation = token
        try:
            return self._collect_current_pool_market_once(
                start_date=start_date,
                end_date=end_date,
                resume=resume,
                batch_size=batch_size,
                progress_path=progress_path,
            )
        finally:
            self._active_cancellation = None

    def _collect_current_pool_market_once(
        self,
        *,
        start_date: str,
        end_date: str,
        resume: bool,
        batch_size: int,
        progress_path: str | Path | None,
    ) -> dict[str, Any]:
        start = _iso_date(start_date)
        end = _iso_date(end_date)
        sessions: list[str] = []
        market_plan_known = False
        calendar_report: Mapping[str, Any] = {}
        completed = 0
        last_session: str | None = None
        fetched_dataset_count = 0
        attempt_count = 0
        reused_session_count = 0

        def record_completed(
            entries: Sequence[tuple[str, Mapping[str, Any]]],
        ) -> None:
            nonlocal completed
            nonlocal last_session
            nonlocal fetched_dataset_count
            nonlocal attempt_count
            nonlocal reused_session_count
            if not entries:
                return
            completed += len(entries)
            last_session = entries[-1][0]
            fetched_dataset_count += sum(
                int(result.get("fetched_dataset_count") or 0) for _session, result in entries
            )
            attempt_count += sum(
                int(result.get("attempt_count") or 0) for _session, result in entries
            )
            reused_session_count += sum(
                result.get("reused_published") is True for _session, result in entries
            )

        def checkpoint(status: str, *, phase: str) -> dict[str, Any]:
            planned = len(sessions) if market_plan_known else None
            payload = {
                "schema_version": "current-pool-market-progress/v1",
                "status": status,
                "mode": "current_pool_market_only",
                "phase": phase,
                "source_profile": self.source_profile,
                "start_date": start,
                "end_date": end,
                "resume_requested": bool(resume),
                "workers": self.workers,
                "batch_size": batch_size,
                "planned": planned,
                "completed": completed,
                "remaining": None if planned is None else planned - completed,
                "last_session": last_session,
                "current_universe_bias": True,
                "development_only": True,
                "live_proof": False,
            }
            if progress_path is not None:
                _write_market_collection_progress(progress_path, payload)
            return payload

        checkpoint("running", phase="calendar")
        try:
            calendar_report = self._collect_trade_calendars_once(
                start_date=start,
                end_date=end,
                resume=resume,
            )
            if not hasattr(self.store, "common_open_sessions"):
                raise PITCollectionError(
                    "market-only collection requires verified common-open calendar sessions"
                )
            raw_sessions = self.store.common_open_sessions(
                start_date=start, end_date=end, exchanges=self._calendar_exchanges()
            )
            sessions = [_iso_date(session) for session in raw_sessions]
            if sessions != sorted(set(sessions)):
                raise PITCollectionError("common-open sessions must be unique and sorted")
            if any(session < start or session > end for session in sessions):
                raise PITCollectionError("common-open session is outside the requested range")
            market_plan_known = True
            attempt_count = int(calendar_report.get("attempt_count") or 0)
        except BaseException:
            checkpoint("failed", phase="calendar")
            raise

        checkpoint(
            "complete" if not sessions else "running",
            phase="complete" if not sessions else "market",
        )
        try:
            for offset in range(0, len(sessions), batch_size):
                batch = sessions[offset : offset + batch_size]
                batch_successes: dict[str, Mapping[str, Any]] = {}
                batch_successes_lock = threading.Lock()

                def collect_session(session: str) -> Mapping[str, Any]:
                    result = self.collect_market_session_generation(
                        session,
                        vintage="historical_backfill",
                        resume=resume,
                    )
                    with batch_successes_lock:
                        batch_successes[session] = result
                    return result

                try:
                    results = self._run_phase(
                        [
                            (
                                offset + index,
                                lambda session=session: collect_session(session),
                            )
                            for index, session in enumerate(batch)
                        ],
                        workers=self.workers,
                    )
                except BaseException:
                    record_completed(
                        [
                            (session, batch_successes[session])
                            for session in batch
                            if session in batch_successes
                        ]
                    )
                    raise
                self._check_cancelled()
                record_completed(list(zip(batch, results)))
                checkpoint(
                    "complete" if completed == len(sessions) else "running",
                    phase="complete" if completed == len(sessions) else "market",
                )
        except BaseException:
            checkpoint("failed", phase="market")
            raise

        if not hasattr(self.store, "bind_current_pool_market_collection"):
            raise PITCollectionError(
                "market-only collection requires durable temporal binding support"
            )
        temporal_binding = self.store.bind_current_pool_market_collection(
            start_date=start,
            end_date=end,
            sessions=sessions,
            temporal_contract_sha256=self._temporal_authority.contract_sha256,
            temporal_role=self._temporal_authority.role,
            source_profile=self.source_profile,
        )

        return {
            "status": "complete",
            "mode": "current_pool_market_only",
            "source_profile": self.source_profile,
            "start_date": start,
            "end_date": end,
            "resume_requested": bool(resume),
            "workers": self.workers,
            "batch_size": batch_size,
            "calendar_receipt_count": int(calendar_report.get("controlled_receipt_count") or 0),
            "planned": len(sessions),
            "completed": completed,
            "remaining": len(sessions) - completed,
            "last_session": last_session,
            "market_session_generation_count": completed,
            "fetched_dataset_count": fetched_dataset_count,
            "attempt_count": attempt_count,
            "reused_session_count": reused_session_count,
            "temporal_role": self._temporal_authority.role,
            "temporal_contract_sha256": self._temporal_authority.contract_sha256,
            "temporal_binding_sha256": temporal_binding["binding_sha256"],
            "current_universe_bias": True,
            "development_only": True,
            "live_proof": False,
        }

    def _collect_trade_calendars_once(
        self,
        *,
        start_date: str,
        end_date: str,
        resume: bool,
    ) -> dict[str, Any]:
        self._authority_guard()
        if not hasattr(self.store, "controlled_receipt_index"):
            raise PITCollectionError(
                "calendar-only collection requires controlled receipt capabilities"
            )
        start = _iso_date(start_date)
        end = _iso_date(end_date)
        if end < start:
            raise PITCollectionError("end_date precedes start_date")
        if start < self._temporal_authority.start_date or end > self._temporal_authority.end_date:
            raise PITCollectionError("requested range exceeds collector authority")
        try:
            assert_range_allowed(
                self.temporal_contract,
                self.temporal_role,
                start,
                end,
                "collect",
            )
        except PartitionContractError as exc:
            raise PITCollectionError(str(exc)) from exc

        # jiaoch trade_cal 只维护 SSE(SZSE/CFFEX/SHFE/DCE 全空,A 股两市日历同步);
        # 官方 tushare 等其他 source 仍要求两市完整 controlled receipt。
        specs = build_trade_cal_specs(
            start_date=start,
            end_date=end,
            exchanges=self._calendar_exchanges(),
        )
        results = self._run_phase(
            [
                (
                    index,
                    lambda spec=spec: self.fetch_partition(spec, resume=resume),
                )
                for index, spec in enumerate(specs)
            ],
            workers=self.workers,
        )
        self._check_cancelled()
        verified_index = self.store.controlled_receipt_index()
        calendar_receipts: list[dict[str, Any]] = []
        for spec, result in zip(specs, results):
            semantics_sha256 = _sha256_json(self._request_semantics(spec))
            key = (spec.dataset, spec.partition_key, semantics_sha256)
            verified = verified_index.get(key)
            if verified is None:
                raise PITCollectionError(
                    "controlled receipt lineage is incomplete after calendar collection"
                )
            if result.get("request_semantics_sha256") != semantics_sha256:
                raise PITCollectionError(
                    "calendar result request semantics do not match controlled receipt"
                )
            raw_sha256 = str(verified.get("receipt_raw_sha256") or "")
            if len(raw_sha256) != 64:
                raise PITCollectionError("controlled calendar receipt hash is invalid")
            calendar_receipts.append(
                {
                    "exchange": str(spec.receipt_params["exchange"]),
                    "partition_key": spec.partition_key,
                    "status": str(result.get("status") or ""),
                    "request_semantics_sha256": semantics_sha256,
                    "receipt_raw_sha256": raw_sha256,
                }
            )

        statuses = [str(result.get("status") or "") for result in results]
        return {
            "status": "complete",
            "mode": "trade_calendars_only",
            "start_date": start,
            "end_date": end,
            "resume_requested": bool(resume),
            "workers": self.workers,
            "network_workers": self.workers,
            "planned_receipt_count": len(specs),
            "controlled_receipt_count": len(calendar_receipts),
            "controlled_request_lineage_complete": True,
            "calendar_receipts": calendar_receipts,
            "attempt_count": sum(len(result.get("attempts") or []) for result in results),
            "stored_count": statuses.count("stored"),
            "reused_count": statuses.count("reused"),
            "skipped_count": statuses.count("skipped"),
            "error_count": 0,
            "errors": [],
            "temporal_role": self._temporal_authority.role,
            "temporal_contract_sha256": self._temporal_authority.contract_sha256,
            "final_oos_eligible": False,
            "promotion_eligible": self._temporal_authority.role == "development",
        }

    def _collect_once(
        self,
        *,
        start_date: str,
        end_date: str,
        resume: bool = True,
        workers: int | None = None,
    ) -> dict[str, Any]:
        self._authority_guard()
        effective_workers = self.workers if workers is None else workers
        if (
            isinstance(effective_workers, bool)
            or not isinstance(effective_workers, int)
            or not 1 <= effective_workers <= 8
        ):
            raise PITCollectionError("workers must be an integer in the range 1..8")
        if effective_workers != self.workers:
            raise PITCollectionError("collect workers must match frozen collector workers")
        start = _iso_date(start_date)
        end = _iso_date(end_date)
        if end < start:
            raise PITCollectionError("end_date precedes start_date")
        if start < self._temporal_authority.start_date or end > self._temporal_authority.end_date:
            raise PITCollectionError("requested range exceeds collector authority")
        try:
            assert_range_allowed(
                self.temporal_contract,
                self.temporal_role,
                start,
                end,
                "collect",
            )
        except PartitionContractError as exc:
            raise PITCollectionError(str(exc)) from exc
        results: list[dict[str, Any]] = []
        membership_results: list[dict[str, Any]] = []
        calendar_specs = build_trade_cal_specs(
            start_date=start,
            end_date=end,
            exchanges=self._calendar_exchanges(),
        )
        generation_methods = (
            "begin_or_resume_stock_basic_generation",
            "stage_stock_basic_attempt",
            "publish_stock_basic_generation",
            "active_stock_basic_generation",
            "verify_stock_basic_generation",
        )
        generation_supported = all(hasattr(self.store, method) for method in generation_methods)
        if not generation_supported:
            raise PITCollectionError("official collection requires stock generation capabilities")
        generation = None
        generation_verification = None
        generation_id = None
        generation_fetch_count = 0
        generation_attempt_count = 0
        generation_skipped_count = 0
        # Published stock generations are reused only after all receipt phases
        # are known complete.  Before sessions are derived, conservatively let
        # generation orchestration resume its own immutable shard manifest.
        reuse_published = False
        if effective_workers == 1:
            for spec in calendar_specs:
                results.append(self.fetch_partition(spec, resume=resume))
            self._check_cancelled()
            sessions = self.store.common_open_sessions(start_date=start, end_date=end)
            daily_specs = build_bak_basic_specs(sessions)
            if resume and hasattr(self.store, "controlled_receipt_index"):
                existing_index = self.store.controlled_receipt_index()
                calendar_keys = {
                    (
                        spec.dataset,
                        spec.partition_key,
                        _sha256_json(self._request_semantics(spec)),
                    )
                    for spec in calendar_specs
                }
                daily_authority_complete = all(
                    (
                        spec.dataset,
                        spec.partition_key,
                        _sha256_json(self._request_semantics(spec)),
                    )
                    in existing_index
                    or self._resumable_membership_snapshot(spec, resume=True) is not None
                    for spec in daily_specs
                )
                reuse_published = (
                    calendar_keys.issubset(existing_index) and daily_authority_complete
                )
            generation = self.collect_stock_basic_generation(
                resume=resume,
                reuse_published=reuse_published,
            )
        else:
            stock_workers = max(1, effective_workers - len(calendar_specs))
            stock_task_index = len(calendar_specs)
            phase_a = self._run_phase(
                [
                    (
                        index,
                        lambda spec=spec: self.fetch_partition(spec, resume=resume),
                    )
                    for index, spec in enumerate(calendar_specs)
                ]
                + [
                    (
                        stock_task_index,
                        lambda: self.collect_stock_basic_generation(
                            resume=resume,
                            reuse_published=resume,
                            workers=stock_workers,
                        ),
                    ),
                ],
                workers=min(len(calendar_specs) + 1, effective_workers),
            )
            results.extend(phase_a[:stock_task_index])
            generation = phase_a[stock_task_index]
            self._check_cancelled()
            sessions = self.store.common_open_sessions(start_date=start, end_date=end)
            daily_specs = build_bak_basic_specs(sessions)
        generation_id = str(generation["generation_id"])
        generation_verification = self.store.verify_stock_basic_generation(generation_id)
        generation_fetch_count = int(generation.get("fetched_partition_count") or 0)
        generation_attempt_count = int(generation.get("attempt_count") or 0)
        if generation.get("reused_published"):
            generation_skipped_count = len(REQUIRED_STOCK_PARTITIONS)
        self._check_cancelled()
        membership_results = self._run_phase(
            [
                (
                    index,
                    lambda spec=spec: self.fetch_membership_snapshot(spec, resume=resume),
                )
                for index, spec in enumerate(daily_specs)
            ],
            workers=effective_workers,
        )
        results.extend(membership_results)
        self._check_cancelled()
        # WHY: every common open session also needs a published four-shard
        # market session generation (daily / adj_factor / stk_limit /
        # suspend_d) staged into the generation store, never as legacy
        # per-dataset receipts. vintage is pinned to historical_backfill so
        # the backfilled evidence is never confused with live_forward heads.
        market_session_generations = self._run_phase(
            [
                (
                    index,
                    lambda session=session: self.collect_market_session_generation(
                        session, vintage="historical_backfill", resume=resume
                    ),
                )
                for index, session in enumerate(sessions)
            ],
            workers=effective_workers,
        )
        self._check_cancelled()
        membership_generations: list[dict[str, Any]] = []
        for session, membership_result in zip(sessions, membership_results):
            if membership_result.get("status") != "semantic_empty":
                continue
            self._check_cancelled()
            if membership_result.get("reused"):
                active = self.store.active_membership_generation(session)
                if active is None:
                    raise PITCollectionError("reused membership generation is not active")
                membership_generations.append(active)
                continue
            membership_generation = self.store.begin_or_resume_membership_generation(
                self.clock.now_utc(),
                session,
                temporal_role=self._temporal_authority.role,
                temporal_contract_sha256=self._temporal_authority.contract_sha256,
                force_new=False,
            )
            self._check_cancelled()
            self.store.stage_membership_generation(membership_generation["generation_id"])
            self._check_cancelled()
            _published = self.store.publish_membership_generation(
                membership_generation["generation_id"]
            )
            active = self.store.active_membership_generation(session)
            if active is None or active["generation_id"] != membership_generation["generation_id"]:
                raise PITCollectionError("published membership generation is not the active head")
            membership_generations.append(active)

        self._check_cancelled()
        controlled_count = None
        controlled_complete = None
        if hasattr(self.store, "controlled_receipt_index"):
            verified_index = self.store.controlled_receipt_index()
            exact_membership_specs = [
                spec
                for spec, result in zip(daily_specs, membership_results)
                if result.get("status") != "semantic_empty"
            ]
            expected_keys = {
                (
                    spec.dataset,
                    spec.partition_key,
                    _sha256_json(self._request_semantics(spec)),
                )
                for spec in [*calendar_specs, *exact_membership_specs]
            }
            controlled_count = len(expected_keys & set(verified_index))
            derived_complete = all(
                self.store.active_membership_generation(spec.partition_key) is not None
                for spec, result in zip(daily_specs, membership_results)
                if result.get("status") == "semantic_empty"
            )
            controlled_complete = expected_keys.issubset(verified_index) and derived_complete
            if not controlled_complete:
                raise PITCollectionError(
                    "controlled receipt lineage is incomplete after collection"
                )
        statuses = [
            str((result.get("receipt") or {}).get("status") or result.get("status"))
            for result in results
        ]
        exact_membership_count = sum(
            result.get("status") != "semantic_empty" for result in membership_results
        )
        semantic_empty_count = len(membership_results) - exact_membership_count
        pre_anchor_count = sum(
            generation.get("source_kind") == "pre_anchor_quarantine"
            for generation in membership_generations
        )
        membership_authority_count = exact_membership_count + len(membership_generations)
        if membership_authority_count != len(sessions):
            raise PITCollectionError("membership authority coverage is incomplete")
        report = {
            "status": "complete",
            "start_date": start,
            "end_date": end,
            "resume_requested": bool(resume),
            "workers": effective_workers,
            "network_workers": effective_workers,
            "open_sessions": list(sessions),
            "open_session_count": len(sessions),
            "exact_snapshot_session_count": exact_membership_count,
            "semantic_empty_session_count": semantic_empty_count,
            "pre_anchor_session_count": pre_anchor_count,
            "membership_authority_count": membership_authority_count,
            "derived_membership_generation_count": len(membership_generations),
            "membership_generations": membership_generations,
            "planned_receipt_count": (
                len(calendar_specs) + len(REQUIRED_STOCK_PARTITIONS) + len(daily_specs)
            ),
            "controlled_receipt_count": controlled_count,
            "controlled_request_lineage_complete": controlled_complete,
            "attempt_count": sum(len(result.get("attempts") or []) for result in results)
            + generation_attempt_count,
            "stored_count": statuses.count("stored") + generation_fetch_count,
            "reused_count": statuses.count("reused"),
            "skipped_count": statuses.count("skipped") + generation_skipped_count,
            "error_count": 0,
            "errors": [],
            "temporal_role": self._temporal_authority.role,
            "temporal_contract_sha256": self._temporal_authority.contract_sha256,
            "final_oos_eligible": False,
            "promotion_eligible": self._temporal_authority.role == "development",
            "market_session_generation_count": len(market_session_generations),
            "market_session_generations": market_session_generations,
        }
        if generation_verification is not None:
            report.update(
                {
                    "generation_id": generation_id,
                    "manifest": generation_verification["manifest"],
                    "manifest_sha256": generation_verification["manifest_sha256"],
                    "rows_sha256": generation_verification["rows_sha256"],
                    "audit_identity_sha256": generation_verification["audit_identity_sha256"],
                    "stock_basic_generation": generation,
                }
            )
        return report
