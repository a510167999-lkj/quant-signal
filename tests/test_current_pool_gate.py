import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import current_pool_gate
from app.current_pool import POLICY_ID
from app.current_pool_gate import CurrentPoolGateError, load_current_pool_audit


NOW = datetime(2026, 7, 13, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _audit_payload(
    *,
    source_as_of: str = "2026-07-13",
    rows: list[tuple[dict, dict]] | None = None,
) -> dict:
    rows = rows or [
        (
            {
                "ts_code": "600001.SH",
                "name": "主板甲",
                "market": "主板",
                "exchange": "SSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            },
            {
                "symbol": "600001",
                "eligible": True,
                "bar_count": 120,
                "fetch_status": "success",
                "signal_ready": True,
                "signal_allowed_today": True,
            },
        )
    ]
    payload = {
        "schema": "current-pool-coverage-audit",
        "schema_version": "current-pool-coverage-audit/v1",
        "policy_id": POLICY_ID,
        "development_only": True,
        "evidence_scope": "development_only",
        "source_as_of": source_as_of,
        "source_ids": {
            "universe": "jiaoch",
            "history_summary": "jiaoch",
            "risk_snapshot": "jiaoch",
        },
        "input_descriptor_sha256": {
            "universe": "1" * 64,
            "history_summary": "2" * 64,
            "risk_snapshot": "3" * 64,
        },
        "risk_snapshot_complete": True,
        "risk_gate_passed": True,
        "production_recommendation_eligible": False,
        "min_signal_bars": 90,
        "universe_items": [row[0] for row in rows],
        "item_history_status": [row[1] for row in rows],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload["canonical_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_loader_accepts_only_policy_eligible_ready_and_today_allowed_symbols(tmp_path: Path):
    rows = [
        (
            {
                "ts_code": "600001.SH",
                "name": "主板甲",
                "market": "主板",
                "exchange": "SSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            },
            {"symbol": "600001", "eligible": True, "bar_count": 120, "fetch_status": "success", "signal_ready": True, "signal_allowed_today": True},
        ),
        (
            {
                "ts_code": "300001.SZ",
                "name": "创业板新股",
                "market": "创业板",
                "exchange": "SZSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            },
            {"symbol": "300001", "eligible": True, "bar_count": 89, "fetch_status": "success", "signal_ready": False, "signal_allowed_today": True},
        ),
        (
            {
                "ts_code": "000001.SZ",
                "name": "主板停牌",
                "market": "主板",
                "exchange": "SZSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": True},
            },
            {"symbol": "000001", "eligible": True, "bar_count": 500, "fetch_status": "success", "signal_ready": True, "signal_allowed_today": False},
        ),
        (
            {
                "ts_code": "002001.SZ",
                "name": "ST风险",
                "market": "主板",
                "exchange": "SZSE",
                "list_status": "L",
                "risk_flags": {"is_st": True, "is_suspended": False},
            },
            {"symbol": "002001", "eligible": False, "bar_count": None, "fetch_status": "not_applicable", "signal_ready": False, "signal_allowed_today": False},
        ),
        (
            {
                "ts_code": "688001.SH",
                "name": "科创板",
                "market": "科创板",
                "exchange": "SSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            },
            {"symbol": "688001", "eligible": False, "bar_count": None, "fetch_status": "not_applicable", "signal_ready": False, "signal_allowed_today": False},
        ),
        (
            {
                "ts_code": "430001.BJ",
                "name": "北交所",
                "market": "北交所",
                "exchange": "BSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            },
            {"symbol": "430001", "eligible": False, "bar_count": None, "fetch_status": "not_applicable", "signal_ready": False, "signal_allowed_today": False},
        ),
    ]
    path = _write(tmp_path / "audit.json", _audit_payload(rows=rows))

    result = load_current_pool_audit(path, now=NOW, max_age_hours=36)

    assert result["allowed_symbols"] == {"600001"}
    assert result["production_recommendation_eligible"] is False
    assert result["evidence_scope"] == "development_only"


@pytest.mark.parametrize("mutation", ["tamper", "policy", "source", "hash", "risk"])
def test_loader_rejects_tamper_and_semantically_invalid_audits(tmp_path: Path, mutation: str):
    payload = _audit_payload()
    if mutation == "tamper":
        payload["source_as_of"] = "2026-07-12"
    elif mutation == "policy":
        payload["policy_id"] = "wrong"
    elif mutation == "source":
        payload["source_ids"]["universe"] = "other"
    elif mutation == "hash":
        payload["input_descriptor_sha256"]["risk_snapshot"] = "not-a-hash"
    else:
        payload["risk_gate_passed"] = False
    path = _write(tmp_path / "audit.json", payload)

    with pytest.raises(CurrentPoolGateError):
        load_current_pool_audit(path, now=NOW, max_age_hours=36)


def test_loader_rejects_missing_and_stale_audit(tmp_path: Path):
    with pytest.raises(CurrentPoolGateError, match="missing"):
        load_current_pool_audit(tmp_path / "missing.json", now=NOW, max_age_hours=36)

    stale = _audit_payload(source_as_of=(NOW - timedelta(days=3)).date().isoformat())
    path = _write(tmp_path / "stale.json", stale)
    with pytest.raises(CurrentPoolGateError, match="stale"):
        load_current_pool_audit(path, now=NOW, max_age_hours=36)


@pytest.mark.parametrize(
    "source_as_of",
    ["2026-07-13T00:00:00", "2026-07-13T23:59:59+08:00"],
)
def test_loader_rejects_noncanonical_source_as_of_timestamp(
    tmp_path: Path, source_as_of: str
) -> None:
    path = _write(
        tmp_path / "timestamp.json",
        _audit_payload(source_as_of=source_as_of),
    )

    with pytest.raises(CurrentPoolGateError, match="source_as_of is invalid"):
        load_current_pool_audit(path, now=NOW, max_age_hours=36)


def test_loader_rejects_recomputed_hash_when_star_is_marked_eligible(tmp_path: Path):
    rows = [
        (
            {
                "ts_code": "688001.SH",
                "name": "科创板",
                "market": "科创板",
                "exchange": "SSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            },
            {"symbol": "688001", "eligible": True, "bar_count": 500, "fetch_status": "success", "signal_ready": True, "signal_allowed_today": True},
        )
    ]
    path = _write(tmp_path / "audit.json", _audit_payload(rows=rows))

    with pytest.raises(CurrentPoolGateError, match="classification"):
        load_current_pool_audit(path, now=NOW, max_age_hours=36)


@pytest.mark.parametrize("missing_field", ["schema", "schema_version"])
def test_loader_requires_exact_audit_schema(tmp_path: Path, missing_field: str):
    payload = _audit_payload()
    payload.pop(missing_field)
    unsigned = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()

    with pytest.raises(CurrentPoolGateError, match="schema"):
        load_current_pool_audit(_write(tmp_path / "audit.json", payload), now=NOW, max_age_hours=36)


def test_loader_never_trusts_a_reported_signal_window_below_90(tmp_path: Path):
    payload = _audit_payload()
    payload["min_signal_bars"] = 1
    payload["item_history_status"][0]["bar_count"] = 1
    payload["item_history_status"][0]["signal_ready"] = True
    unsigned = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()

    with pytest.raises(CurrentPoolGateError, match="90"):
        load_current_pool_audit(_write(tmp_path / "audit.json", payload), now=NOW, max_age_hours=36)


def test_loader_wraps_invalid_utf8_as_current_pool_gate_error(tmp_path: Path):
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(CurrentPoolGateError):
        load_current_pool_audit(path, now=NOW, max_age_hours=36)


@pytest.mark.parametrize(
    "risk_flags",
    [
        None,
        {"is_st": False},
        {"is_suspended": False},
        {"is_st": "false", "is_suspended": False},
        {"is_st": False, "is_suspended": 0},
        {"is_st": False, "is_suspended": False, "unknown": False},
    ],
)
def test_loader_rejects_re_signed_ambiguous_risk_flags(tmp_path: Path, risk_flags):
    payload = _audit_payload()
    if risk_flags is None:
        payload["universe_items"][0].pop("risk_flags")
    else:
        payload["universe_items"][0]["risk_flags"] = risk_flags
    unsigned = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()

    with pytest.raises(CurrentPoolGateError, match="risk flags"):
        load_current_pool_audit(_write(tmp_path / "audit.json", payload), now=NOW, max_age_hours=36)


@pytest.mark.parametrize("field", ["ts_code", "name", "market", "exchange", "list_status"])
def test_loader_rejects_re_signed_missing_structured_universe_fields(tmp_path: Path, field: str):
    payload = _audit_payload()
    payload["universe_items"][0].pop(field)
    unsigned = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()

    with pytest.raises(CurrentPoolGateError, match="structured fields"):
        load_current_pool_audit(_write(tmp_path / "audit.json", payload), now=NOW, max_age_hours=36)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ts_code", ""),
        ("name", ""),
        ("market", 1),
        ("exchange", []),
        ("list_status", None),
    ],
)
def test_loader_rejects_re_signed_empty_or_wrong_typed_structured_fields(
    tmp_path: Path, field: str, value
):
    payload = _audit_payload()
    payload["universe_items"][0][field] = value
    unsigned = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()

    with pytest.raises(CurrentPoolGateError, match="structured fields"):
        load_current_pool_audit(_write(tmp_path / "audit.json", payload), now=NOW, max_age_hours=36)


def test_loader_uses_expected_trade_data_dates_instead_of_midnight_age(tmp_path: Path):
    friday = _write(tmp_path / "audit.json", _audit_payload(source_as_of="2026-07-10"))

    accepted = load_current_pool_audit(
        friday,
        now=NOW,
        max_age_hours=36,
        expected_source_dates={"2026-07-10", "2026-07-13"},
    )
    assert accepted["source_as_of"] == "2026-07-10"

    with pytest.raises(CurrentPoolGateError, match="trade data date"):
        load_current_pool_audit(
            friday,
            now=NOW,
            max_age_hours=36,
            expected_source_dates={"2026-07-13"},
        )


def test_loader_caches_verified_inode_version_and_revalidates_after_change(
    tmp_path: Path, monkeypatch
):
    path = _write(tmp_path / "audit.json", _audit_payload())
    current_pool_gate._AUDIT_CACHE.clear()
    calls = 0
    original = current_pool_gate._verify_current_pool_audit_bytes

    def counted(raw):
        nonlocal calls
        calls += 1
        return original(raw)

    monkeypatch.setattr(current_pool_gate, "_verify_current_pool_audit_bytes", counted)
    for _ in range(3):
        load_current_pool_audit(path, now=NOW, max_age_hours=36)
    assert calls == 1

    changed = _audit_payload()
    changed["universe_items"][0]["name"] = "主板甲已更新"
    unsigned = {key: value for key, value in changed.items() if key != "canonical_sha256"}
    changed["canonical_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    _write(path, changed)
    load_current_pool_audit(path, now=NOW, max_age_hours=36)
    assert calls == 2
