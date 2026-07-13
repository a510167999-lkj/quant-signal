from __future__ import annotations

import hashlib
import json
import re
import stat
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import jobs
from app.current_pool import build_current_pool_coverage, classify_current_pool_item


def _signed_universe(payload: dict) -> dict:
    items = payload.get("items")
    if isinstance(items, list):
        normalized_items = []
        for item in items:
            candidate = dict(item)
            ts_code = str(candidate.get("ts_code") or "")
            if ts_code:
                candidate.setdefault("symbol", ts_code.split(".")[0])
                candidate.setdefault("name", ts_code)
                candidate.setdefault("list_date", None)
                candidate.setdefault("delist_date", None)
                candidate.setdefault("market_evidence", "provider")
            normalized_items.append(candidate)
        normalized_items.sort(key=lambda item: str(item.get("ts_code") or ""))
        payload = {**payload, "items": normalized_items}
    counts = {(exchange, status): 0 for exchange in ("SSE", "SZSE") for status in ("L", "D", "P", "G")}
    for item in payload.get("items", []):
        key = (item.get("exchange"), item.get("list_status"))
        if key in counts:
            counts[key] += 1
    receipts = [
        {
            "api_name": "stock_basic",
            "params": {"exchange": exchange, "list_status": status},
            "raw_row_count": counts[(exchange, status)],
            "normalized_row_count": counts[(exchange, status)],
            "rows_sha256": hashlib.sha256(
                json.dumps(
                    sorted(
                        (
                            item
                            for item in payload.get("items", [])
                            if item.get("exchange") == exchange
                            and item.get("list_status") == status
                        ),
                        key=lambda item: item.get("ts_code", ""),
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "excluded_invalid_identity_count": 0,
            "excluded_invalid_identity_rows": [],
            "excluded_rows_sha256": hashlib.sha256(b"[]").hexdigest(),
        }
        for exchange in ("SSE", "SZSE")
        for status in ("L", "D", "P", "G")
    ]
    value = {
        "partition_coverage": {
            "exchanges": ["SSE", "SZSE"],
            "list_statuses": ["L", "D", "P", "G"],
            "partition_count": 8,
        },
        "risk_snapshot_complete": False,
        "production_recommendation_eligible": False,
        "risk_coverage": {
            "stock_st": "not_collected",
            "namechange": "not_collected",
            "suspend_d": "not_collected",
        },
        "partition_receipts": receipts,
        "excluded_invalid_identity_count": 0,
        **payload,
    }
    value.setdefault("retrieved_at", "2026-07-13T09:30:00+08:00")
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return {**value, "descriptor_sha256": hashlib.sha256(canonical).hexdigest()}


def _signed_risk(universe: dict, *, as_of: str = "2026-07-13", items: list[dict] | None = None) -> dict:
    risk_items = items
    if risk_items is None:
        risk_items = [
            {
                "ts_code": item["ts_code"],
                "is_st": False,
                "st_type": None,
                "is_suspended": False,
                "suspension_reason": None,
                "active_name": None,
            }
            for item in universe["items"]
        ]
    receipts = [
        {
            "api_name": "stock_st",
            "params": {"trade_date": as_of.replace("-", "")},
            "row_count": 0,
            "rows_sha256": "0" * 64,
        },
        {
            "api_name": "suspend_d",
            "params": {"trade_date": as_of.replace("-", "")},
            "row_count": 0,
            "rows_sha256": "0" * 64,
        },
    ]
    for year in range(1990, int(as_of[:4]) + 1):
        receipts.append(
            {
                "api_name": "namechange",
                "params": {
                    "start_date": f"{year}0101",
                    "end_date": min(f"{year}1231", as_of.replace("-", "")),
                },
                "row_count": 0,
                "rows_sha256": "0" * 64,
            }
        )
    value = {
        "schema": "current-pool-risk-input/v1",
        "source_id": "jiaoch",
        "as_of": as_of,
        "retrieved_at": f"{as_of}T10:00:00+08:00",
        "universe_descriptor_sha256": universe["descriptor_sha256"],
        "partition_coverage": {
            "stock_st": 1,
            "suspend_d": 1,
            "namechange": int(as_of[:4]) - 1989,
            "partition_count": int(as_of[:4]) - 1987,
        },
        "partition_receipts": receipts,
        "risk_snapshot_complete": True,
        "risk_gate_passed": True,
        "production_recommendation_eligible": False,
        "items": risk_items,
    }
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {**value, "descriptor_sha256": hashlib.sha256(canonical).hexdigest()}


def _write_risk(tmp_path: Path, universe: dict, **kwargs) -> Path:
    path = tmp_path / "risk.json"
    path.write_text(json.dumps(_signed_risk(universe, **kwargs), ensure_ascii=False), encoding="utf-8")
    return path


def _write_publishable_audit(path: Path, *, source_as_of: str | None = None) -> dict:
    source_as_of = source_as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    payload = {
        "schema": "current-pool-coverage-audit",
        "schema_version": "current-pool-coverage-audit/v1",
        "policy_id": "current-pool-mainboard-chinext-v1",
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
        "universe_items": [
            {
                "ts_code": "600001.SH",
                "name": "主板甲",
                "market": "主板",
                "exchange": "SSE",
                "list_status": "L",
                "risk_flags": {"is_st": False, "is_suspended": False},
            }
        ],
        "item_history_status": [
            {
                "symbol": "600001",
                "eligible": True,
                "bar_count": 120,
                "fetch_status": "success",
                "signal_ready": True,
                "signal_allowed_today": True,
            }
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    payload["canonical_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def test_classifies_supported_boards_and_rejects_other_markets() -> None:
    for symbol, market in [
        ("000001.SZ", "主板"),
        ("001001.SZ", "主板"),
        ("002001.SZ", "主板"),
        ("003001.SZ", "主板"),
        ("600001.SH", "主板"),
        ("601001.SH", "主板"),
        ("603001.SH", "主板"),
        ("605001.SH", "主板"),
        ("300001.SZ", "创业板"),
        ("301001.SZ", "创业板"),
        ("302132.SZ", "创业板"),
    ]:
        result = classify_current_pool_item(
            {"ts_code": symbol, "market": market, "list_status": "L"}
        )
        assert result["eligible"] is True
        assert result["reason_codes"] == []

    assert classify_current_pool_item(
        {"ts_code": "688001.SH", "market": "科创板", "list_status": "L"}
    )["reason_codes"] == ["excluded_market_science_technology"]
    assert classify_current_pool_item(
        {"ts_code": "830001.BJ", "exchange": "BSE", "list_status": "L"}
    )["reason_codes"] == ["excluded_exchange_beijing"]


def test_structured_market_exchange_and_status_take_precedence() -> None:
    assert classify_current_pool_item(
        {
            "ts_code": "600001.SH",
            "market": "科创板",
            "exchange": "SSE",
            "list_status": "L",
        }
    )["reason_codes"] == ["excluded_market_science_technology"]
    assert classify_current_pool_item(
        {
            "ts_code": "600001.SH",
            "market": "主板",
            "exchange": "BSE",
            "list_status": "L",
        }
    )["reason_codes"] == ["excluded_conflict_exchange_symbol"]
    assert classify_current_pool_item(
        {"ts_code": "300001.SZ", "market": "创业板", "list_status": "D"}
    )["reason_codes"] == ["excluded_list_status_delisted"]


def test_name_rules_are_explicit_and_do_not_misread_ordinary_english() -> None:
    assert (
        classify_current_pool_item(
            {"ts_code": "600001.SH", "name": "TEST INDUSTRIES", "list_status": "L"}
        )["eligible"]
        is True
    )
    assert classify_current_pool_item(
        {"ts_code": "600002.SH", "name": "ST测试", "list_status": "L"}
    )["reason_codes"] == ["excluded_name_st"]
    assert classify_current_pool_item(
        {"ts_code": "600003.SH", "name": "*ST测试", "list_status": "L"}
    )["reason_codes"] == ["excluded_name_st"]
    assert classify_current_pool_item(
        {"ts_code": "600004.SH", "name": "测试退市整理", "list_status": "L"}
    )["reason_codes"] == ["excluded_name_delisting_period"]


def test_structured_daily_risk_flags_exclude_with_stable_reason_codes() -> None:
    result = classify_current_pool_item(
        {
            "ts_code": "300001.SZ",
            "list_status": "L",
            "risk_flags": {"is_st": True, "is_delisting": True, "is_suspended": True},
        }
    )
    assert result == {
        "symbol": "300001",
        "eligible": False,
        "board": "chinext",
        "reason_codes": [
            "excluded_risk_st",
            "excluded_risk_delisting",
        ],
        "signal_allowed_today": False,
        "daily_entry_blocked": True,
        "daily_reason_codes": ["daily_entry_blocked_suspended"],
    }


def test_temporary_suspension_blocks_today_but_keeps_mother_pool_eligibility() -> None:
    result = classify_current_pool_item(
        {
            "ts_code": "300001.SZ",
            "market": "创业板",
            "list_status": "L",
            "risk_flags": {"is_suspended": True},
        }
    )
    assert result["eligible"] is True
    assert result["signal_allowed_today"] is False
    assert result["daily_entry_blocked"] is True
    assert result["reason_codes"] == []
    assert result["daily_reason_codes"] == ["daily_entry_blocked_suspended"]

    paused_listing = classify_current_pool_item(
        {"ts_code": "300001.SZ", "market": "创业板", "list_status": "P"}
    )
    assert paused_listing["eligible"] is False
    assert "excluded_list_status_paused" in paused_listing["reason_codes"]


def test_symbol_market_and_exchange_conflicts_fail_closed() -> None:
    disguised_star = classify_current_pool_item(
        {
            "ts_code": "688001.SH",
            "market": "主板",
            "exchange": "SSE",
            "list_status": "L",
        }
    )
    assert disguised_star["eligible"] is False
    assert disguised_star["reason_codes"] == ["excluded_conflict_market_symbol"]

    disguised_bse = classify_current_pool_item(
        {
            "ts_code": "830001.BJ",
            "market": "主板",
            "exchange": "SSE",
            "list_status": "L",
        }
    )
    assert disguised_bse["eligible"] is False
    assert disguised_bse["reason_codes"] == ["excluded_conflict_exchange_symbol"]


def test_coverage_keeps_new_stocks_in_mother_pool_and_reports_cohorts() -> None:
    items = [
        {"ts_code": "600001.SH", "list_status": "L"},
        {"ts_code": "000001.SZ", "list_status": "L"},
        {"ts_code": "300001.SZ", "list_status": "L"},
        {"ts_code": "301001.SZ", "list_status": "L"},
        {"ts_code": "002001.SZ", "list_status": "L"},
        {"ts_code": "003001.SZ", "list_status": "L"},
        {"ts_code": "688001.SH", "market": "科创板", "list_status": "L"},
    ]
    histories = {
        "600001.SH": [],
        "000001.SZ": [{}] * 60,
        "300001.SZ": [{}] * 90,
        "301001.SZ": [{}] * 250,
        "002001.SZ": [{}] * 500,
        # 003001.SZ deliberately failed collection.
    }

    audit = build_current_pool_coverage(items, histories, min_signal_bars=90)

    assert audit["counts"] == {
        "mother_pool": 7,
        "eligible": 6,
        "history_success": 5,
        "history_failure": 1,
        "signal_ready": 3,
    }
    assert audit["history_cohorts"] == {
        "0-59": 1,
        "60-89": 1,
        "90-249": 1,
        "250-499": 1,
        "500+": 1,
    }
    assert audit["percentages"] == {
        "eligible_of_mother_pool": 85.71,
        "history_success_of_eligible": 83.33,
        "history_failure_of_eligible": 16.67,
        "signal_ready_of_eligible": 50.0,
    }
    assert audit["min_signal_bars"] == 90
    assert audit["policy_id"] == "current-pool-mainboard-chinext-v1"
    assert audit["development_only"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", audit["canonical_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", audit["input_summary_sha256"])
    assert audit["item_history_status"] == [
        {
            "symbol": "000001",
            "eligible": True,
            "bar_count": 60,
            "fetch_status": "success",
            "signal_ready": False,
        },
        {
            "symbol": "002001",
            "eligible": True,
            "bar_count": 500,
            "fetch_status": "success",
            "signal_ready": True,
        },
        {
            "symbol": "003001",
            "eligible": True,
            "bar_count": None,
            "fetch_status": "failure",
            "signal_ready": False,
        },
        {
            "symbol": "300001",
            "eligible": True,
            "bar_count": 90,
            "fetch_status": "success",
            "signal_ready": True,
        },
        {
            "symbol": "301001",
            "eligible": True,
            "bar_count": 250,
            "fetch_status": "success",
            "signal_ready": True,
        },
        {
            "symbol": "600001",
            "eligible": True,
            "bar_count": 0,
            "fetch_status": "success",
            "signal_ready": False,
        },
        {
            "symbol": "688001",
            "eligible": False,
            "bar_count": None,
            "fetch_status": "not_applicable",
            "signal_ready": False,
        },
    ]


def test_coverage_hash_is_canonical_across_input_order() -> None:
    items = [
        {"ts_code": "600001.SH", "list_status": "L"},
        {"ts_code": "300001.SZ", "list_status": "L"},
    ]
    histories = {"600001.SH": [{}] * 100, "300001.SZ": [{}] * 10}
    first = build_current_pool_coverage(items, histories)
    second = build_current_pool_coverage(list(reversed(items)), histories)
    assert first == second

    changed = build_current_pool_coverage(items, {**histories, "300001.SZ": [{}] * 11})
    assert changed["input_summary_sha256"] != first["input_summary_sha256"]
    assert changed["canonical_sha256"] != first["canonical_sha256"]


def test_symbols_are_canonical_and_suffix_mismatches_fail_closed() -> None:
    assert (
        classify_current_pool_item({"ts_code": "600001.SH", "market": "主板", "list_status": "L"})[
            "symbol"
        ]
        == "600001"
    )
    assert (
        classify_current_pool_item({"symbol": "600001", "market": "主板", "list_status": "L"})[
            "symbol"
        ]
        == "600001"
    )
    assert classify_current_pool_item(
        {"ts_code": "600001.SZ", "market": "主板", "list_status": "L"}
    )["reason_codes"] == ["excluded_conflict_exchange_symbol"]
    assert classify_current_pool_item(
        {"ts_code": "300001.SH", "market": "创业板", "list_status": "L"}
    )["reason_codes"] == ["excluded_conflict_exchange_symbol"]
    assert classify_current_pool_item(
        {
            "symbol": "600001",
            "market": "主板",
            "exchange": "SZSE",
            "list_status": "L",
        }
    )["reason_codes"] == ["excluded_conflict_exchange_symbol"]
    assert classify_current_pool_item(
        {
            "symbol": "300001",
            "market": "创业板",
            "exchange": "SSE",
            "list_status": "L",
        }
    )["reason_codes"] == ["excluded_conflict_exchange_symbol"]

    with pytest.raises(ValueError, match="unique"):
        build_current_pool_coverage(
            [
                {"symbol": "600001", "list_status": "L"},
                {"ts_code": "600001.SH", "list_status": "L"},
            ],
            {},
        )


def test_missing_list_status_and_normalized_string_flags_fail_closed() -> None:
    missing = classify_current_pool_item({"ts_code": "600001.SH", "market": "主板"})
    assert missing["eligible"] is False
    assert missing["reason_codes"] == ["excluded_list_status_missing"]

    suspended = classify_current_pool_item(
        {
            "ts_code": "600001.SH",
            "market": "主板",
            "list_status": "L",
            "risk_flags": {"is_suspended": "  TrUe  "},
        }
    )
    assert suspended["eligible"] is True
    assert suspended["daily_entry_blocked"] is True


@pytest.mark.parametrize("bad_history", [True, -1])
def test_coverage_rejects_invalid_history_counts(bad_history: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        build_current_pool_coverage(
            [{"symbol": "600001", "list_status": "L"}],
            {"600001": bad_history},
        )


@pytest.mark.parametrize("bad_minimum", [True, 0, -1, 1.5])
def test_coverage_rejects_invalid_minimum(bad_minimum: object) -> None:
    with pytest.raises(ValueError):
        build_current_pool_coverage([], {}, min_signal_bars=bad_minimum)  # type: ignore[arg-type]


def test_empty_and_all_excluded_pool_percentages_are_zero() -> None:
    empty = build_current_pool_coverage([], {})
    assert empty["counts"] == {
        "mother_pool": 0,
        "eligible": 0,
        "history_success": 0,
        "history_failure": 0,
        "signal_ready": 0,
    }
    assert set(empty["percentages"].values()) == {0.0}

    excluded = build_current_pool_coverage(
        [{"ts_code": "688001.SH", "market": "科创板", "list_status": "L"}], {}
    )
    assert excluded["counts"]["eligible"] == 0
    assert set(excluded["percentages"].values()) == {0.0}


def test_current_pool_audit_cli_writes_idempotent_content_addressed_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    universe_path = tmp_path / "universe.json"
    history_path = tmp_path / "history.json"
    output_dir = tmp_path / "audits"
    universe_path.write_text(
        json.dumps(
            _signed_universe(
                {
                    "schema": "current-pool-universe-input/v1",
                    "source_id": "jiaoch",
                    "as_of": "2026-07-13T08:30:00+08:00",
                    "items": [
                        {
                            "ts_code": "600001.SH",
                            "market": "主板",
                            "exchange": "SSE",
                            "list_status": "L",
                        },
                        {
                            "ts_code": "300001.SZ",
                            "market": "创业板",
                            "exchange": "SZSE",
                            "list_status": "L",
                        },
                    ],
                }
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "schema": "current-pool-history-summary/v1",
                "source_id": "jiaoch",
                "as_of": "2026-07-13",
                "items": [{"ts_code": "600001", "bar_count": 120}],
            }
        ),
        encoding="utf-8",
    )
    argv = [
        "research-current-pool-audit",
        "--universe-path",
        str(universe_path),
        "--history-summary-path",
        str(history_path),
        "--risk-path",
        str(_write_risk(tmp_path, json.loads(universe_path.read_text()))),
        "--output-dir",
        str(output_dir),
    ]

    assert jobs.main(argv) == 0
    first_stdout = json.loads(capsys.readouterr().out)
    artifact = output_dir / f"{first_stdout['canonical_sha256']}.json"
    first_bytes = artifact.read_bytes()
    report = json.loads(first_bytes)
    assert report["counts"] == {
        "mother_pool": 2,
        "eligible": 2,
        "history_success": 1,
        "history_failure": 1,
        "signal_ready": 1,
    }
    assert report["evidence_scope"] == "development_only"
    assert report["source_as_of"] == "2026-07-13"
    assert report["source_ids"] == {
        "universe": "jiaoch",
        "history_summary": "jiaoch",
        "risk_snapshot": "jiaoch",
    }
    assert set(report["input_descriptor_sha256"]) == {
        "universe",
        "history_summary",
        "risk_snapshot",
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", digest)
        for digest in report["input_descriptor_sha256"].values()
    )
    assert first_stdout == {
        "canonical_sha256": artifact.stem,
        "path": str(artifact),
        "created": True,
    }

    assert jobs.main(argv) == 0
    second_stdout = json.loads(capsys.readouterr().out)
    assert second_stdout == {**first_stdout, "created": False}
    assert artifact.read_bytes() == first_bytes
    assert list(output_dir.glob("*.json")) == [artifact]


def test_current_pool_audit_cli_requires_structured_universe_fields(
    tmp_path: Path,
) -> None:
    universe_path = tmp_path / "universe.json"
    history_path = tmp_path / "history.json"
    universe_path.write_text(
        json.dumps(
            _signed_universe(
                {
                    "schema": "current-pool-universe-input/v1",
                    "source_id": "jiaoch",
                    "as_of": "2026-07-13",
                    "items": [
                        {
                            "ts_code": "600001.SH",
                            "market": "主板",
                            "list_status": "L",
                        }
                    ],
                }
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "schema": "current-pool-history-summary/v1",
                "source_id": "jiaoch",
                "as_of": "2026-07-13",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs.main(
            [
                "research-current-pool-audit",
                "--universe-path",
                str(universe_path),
                "--history-summary-path",
                str(history_path),
                "--risk-path",
                str(_write_risk(tmp_path, json.loads(universe_path.read_text()))),
                "--output-dir",
                str(tmp_path / "audits"),
            ]
        )


def test_current_pool_audit_cli_does_not_emit_input_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "do-not-leak-this-token"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    universe_path = tmp_path / "universe.json"
    history_path = tmp_path / "history.json"
    universe_path.write_text(
        json.dumps(
            _signed_universe(
                {
                    "schema": "current-pool-universe-input/v1",
                    "source_id": "jiaoch",
                    "as_of": "2026-07-13",
                    "items": [
                        {
                            "ts_code": "600001.SH",
                            "market": "主板",
                            "exchange": "SSE",
                            "list_status": "L",
                        }
                    ],
                }
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "schema": "current-pool-history-summary/v1",
                "source_id": "jiaoch",
                "as_of": "2026-07-13T23:00:00+08:00",
                "items": [{"ts_code": "600001.SH", "bar_count": 90}],
            }
        ),
        encoding="utf-8",
    )

    assert (
        jobs.main(
            [
                "research-current-pool-audit",
                "--universe-path",
                str(universe_path),
                "--history-summary-path",
                str(history_path),
                "--risk-path",
                str(_write_risk(tmp_path, json.loads(universe_path.read_text()))),
                "--output-dir",
                str(tmp_path / "audits"),
            ]
        )
        == 0
    )
    stdout = capsys.readouterr().out
    artifact_path = Path(json.loads(stdout)["path"])
    assert secret not in stdout
    assert secret.encode() not in artifact_path.read_bytes()


@pytest.mark.parametrize(
    ("universe_overrides", "history_payload"),
    [
        ({"schema": None}, None),
        ({"source_id": None}, None),
        ({"as_of": None}, None),
        ({"source_id": "other"}, None),
        ({}, {"600001.SH": 90}),
        (
            {},
            {
                "schema": "current-pool-history-summary/v1",
                "source_id": "jiaoch",
                "as_of": "2026-07-12",
                "items": [],
            },
        ),
    ],
)
def test_current_pool_audit_cli_rejects_untrusted_or_mismatched_descriptors(
    tmp_path: Path,
    universe_overrides: dict[str, object],
    history_payload: object,
) -> None:
    universe = _signed_universe(
        {
            "schema": "current-pool-universe-input/v1",
            "source_id": "jiaoch",
            "as_of": "2026-07-13",
            "items": [],
            **universe_overrides,
        }
    )
    history = history_payload or {
        "schema": "current-pool-history-summary/v1",
        "source_id": "jiaoch",
        "as_of": "2026-07-13",
        "items": [],
    }
    universe_path = tmp_path / "universe.json"
    history_path = tmp_path / "history.json"
    universe_path.write_text(json.dumps(universe), encoding="utf-8")
    history_path.write_text(json.dumps(history), encoding="utf-8")

    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs.main(
            [
                "research-current-pool-audit",
                "--universe-path",
                str(universe_path),
                "--history-summary-path",
                str(history_path),
                "--risk-path",
                str(_write_risk(tmp_path, json.loads(universe_path.read_text()))),
                "--output-dir",
                str(tmp_path / "audits"),
            ]
        )


def test_current_pool_audit_cli_normalizes_aware_instants_to_shanghai_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    universe_path = tmp_path / "universe.json"
    history_path = tmp_path / "history.json"
    universe_path.write_text(
        json.dumps(
            _signed_universe(
                {
                    "schema": "current-pool-universe-input/v1",
                    "source_id": "jiaoch",
                    "as_of": "2026-07-13T00:30:00+08:00",
                    "items": [],
                }
            )
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "schema": "current-pool-history-summary/v1",
                "source_id": "jiaoch",
                "as_of": "2026-07-12T16:30:00Z",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        jobs.main(
            [
                "research-current-pool-audit",
                "--universe-path",
                str(universe_path),
                "--history-summary-path",
                str(history_path),
                "--risk-path",
                str(_write_risk(tmp_path, json.loads(universe_path.read_text()))),
                "--output-dir",
                str(tmp_path / "audits"),
            ]
        )
        == 0
    )
    artifact = Path(json.loads(capsys.readouterr().out)["path"])
    assert json.loads(artifact.read_text(encoding="utf-8"))["source_as_of"] == "2026-07-13"


@pytest.mark.parametrize(
    ("history_as_of", "history_items"),
    [
        ("2026-07-12T15:30:00Z", []),
        ("2026-07-13T00:30:00", []),
        ("2026-07-13", [{"symbol": "300001", "bar_count": 90}]),
    ],
)
def test_current_pool_audit_cli_rejects_shanghai_date_mismatch_naive_and_orphan(
    tmp_path: Path,
    history_as_of: str,
    history_items: list[dict[str, object]],
) -> None:
    universe_path = tmp_path / "universe.json"
    history_path = tmp_path / "history.json"
    universe_path.write_text(
        json.dumps(
            _signed_universe(
                {
                    "schema": "current-pool-universe-input/v1",
                    "source_id": "jiaoch",
                    "as_of": "2026-07-13T00:30:00+08:00",
                    "items": [
                        {
                            "ts_code": "600001.SH",
                            "market": "主板",
                            "exchange": "SSE",
                            "list_status": "L",
                        }
                    ],
                }
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "schema": "current-pool-history-summary/v1",
                "source_id": "jiaoch",
                "as_of": history_as_of,
                "items": history_items,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs.main(
            [
                "research-current-pool-audit",
                "--universe-path",
                str(universe_path),
                "--history-summary-path",
                str(history_path),
                "--risk-path",
                str(_write_risk(tmp_path, json.loads(universe_path.read_text()))),
                "--output-dir",
                str(tmp_path / "audits"),
            ]
        )


def test_current_pool_audit_cli_requires_risk_path() -> None:
    with pytest.raises(SystemExit):
        jobs.main(
            [
                "research-current-pool-audit",
                "--universe-path", "universe.json",
                "--history-summary-path", "history.json",
                "--output-dir", "audits",
            ]
        )


def test_current_pool_audit_merges_verified_risk_and_preserves_original_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    universe = _signed_universe(
        {
            "schema": "current-pool-universe-input/v1",
            "source_id": "jiaoch",
            "as_of": "2026-07-13",
            "items": [
                {"ts_code": "600001.SH", "name": "原名甲", "market": "主板", "exchange": "SSE", "list_status": "L"},
                {"ts_code": "300001.SZ", "name": "原名乙", "market": "创业板", "exchange": "SZSE", "list_status": "L"},
            ],
        }
    )
    universe_path = tmp_path / "universe.json"
    universe_path.write_text(json.dumps(universe, ensure_ascii=False), encoding="utf-8")
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps({"schema": "current-pool-history-summary/v1", "source_id": "jiaoch", "as_of": "2026-07-13", "items": [{"ts_code": "600001.SH", "bar_count": 120}, {"ts_code": "300001.SZ", "bar_count": 120}]}), encoding="utf-8")
    risk_path = _write_risk(
        tmp_path,
        universe,
        items=[
            {"ts_code": "600001.SH", "is_st": True, "st_type": "ST", "is_suspended": False, "suspension_reason": None, "active_name": "ST新名甲"},
            {"ts_code": "300001.SZ", "is_st": False, "st_type": None, "is_suspended": True, "suspension_reason": "suspended", "active_name": "新名乙"},
        ],
    )
    assert jobs.main(["research-current-pool-audit", "--universe-path", str(universe_path), "--history-summary-path", str(history_path), "--risk-path", str(risk_path), "--output-dir", str(tmp_path / "audits")]) == 0
    report = json.loads(Path(json.loads(capsys.readouterr().out)["path"]).read_text())
    assert report["counts"]["eligible"] == 1
    assert report["risk_snapshot_complete"] is True
    assert report["risk_gate_passed"] is True
    assert report["production_recommendation_eligible"] is False
    assert report["source_ids"]["risk_snapshot"] == "jiaoch"
    assert report["input_descriptor_sha256"]["risk_snapshot"] == json.loads(risk_path.read_text())["descriptor_sha256"]
    by_symbol = {item["symbol"]: item for item in report["item_history_status"]}
    assert by_symbol["600001"]["eligible"] is False
    assert by_symbol["300001"]["eligible"] is True
    assert by_symbol["300001"]["signal_allowed_today"] is False
    assert report["universe_items"][0]["original_name"] == "原名乙"
    assert report["universe_items"][0]["name"] == "新名乙"


@pytest.mark.parametrize("mutation", ["universe_sha", "as_of", "missing_symbol"])
def test_current_pool_audit_rejects_unbound_or_incomplete_risk(
    tmp_path: Path, mutation: str
) -> None:
    universe = _signed_universe({"schema": "current-pool-universe-input/v1", "source_id": "jiaoch", "as_of": "2026-07-13", "items": [{"ts_code": "600001.SH", "name": "甲", "market": "主板", "exchange": "SSE", "list_status": "L"}]})
    universe_path = tmp_path / "universe.json"
    universe_path.write_text(json.dumps(universe), encoding="utf-8")
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps({"schema": "current-pool-history-summary/v1", "source_id": "jiaoch", "as_of": "2026-07-13", "items": []}), encoding="utf-8")
    risk = _signed_risk(universe, as_of="2026-07-12" if mutation == "as_of" else "2026-07-13", items=[] if mutation == "missing_symbol" else None)
    if mutation == "universe_sha":
        risk["universe_descriptor_sha256"] = "f" * 64
        unsigned = {key: value for key, value in risk.items() if key != "descriptor_sha256"}
        risk["descriptor_sha256"] = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    risk_path = tmp_path / "risk.json"
    risk_path.write_text(json.dumps(risk), encoding="utf-8")
    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs.main(["research-current-pool-audit", "--universe-path", str(universe_path), "--history-summary-path", str(history_path), "--risk-path", str(risk_path), "--output-dir", str(tmp_path / "audits")])


def test_current_pool_publish_cli_uses_configured_target_and_preserves_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "audits" / "source.json"
    payload = _write_publishable_audit(source)
    source_bytes = source.read_bytes()
    target = tmp_path / "runtime" / "current-pool-audit.json"
    monkeypatch.setenv("CURRENT_POOL_AUDIT_PATH", str(target))
    monkeypatch.setenv("PRODUCTION_CURRENT_POOL_MAX_AGE_HOURS", "36")

    assert (
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(source),
            ]
        )
        == 0
    )

    assert target.read_bytes() == source_bytes
    assert json.loads(capsys.readouterr().out) == {
        "canonical_sha256": payload["canonical_sha256"],
        "source_path": str(source),
        "path": str(target),
        "bytes_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "published": True,
    }


def test_current_pool_publish_replaces_a_matching_target_symlink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "audits" / "source.json"
    _write_publishable_audit(source)
    target = tmp_path / "runtime" / "current-pool-audit.json"
    target.parent.mkdir()
    target.symlink_to(source)

    assert (
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(source),
                "--target-path",
                str(target),
            ]
        )
        == 0
    )

    assert target.is_file()
    assert not target.is_symlink()
    assert target.read_bytes() == source.read_bytes()
    assert json.loads(capsys.readouterr().out)["published"] is True


def test_current_pool_publish_is_idempotent_when_source_is_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "runtime" / "current-pool-audit.json"
    _write_publishable_audit(target)
    original_bytes = target.read_bytes()
    original_inode = target.stat().st_ino

    assert (
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(target),
                "--target-path",
                str(target),
            ]
        )
        == 0
    )

    assert target.read_bytes() == original_bytes
    assert target.stat().st_ino == original_inode
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))
    assert json.loads(capsys.readouterr().out)["published"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("tampered", "canonical hash mismatch"),
        ("stale", "current-pool audit is stale"),
    ],
)
def test_current_pool_publish_rejects_invalid_source_without_touching_target(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    source = tmp_path / "audits" / "source.json"
    _write_publishable_audit(
        source,
        source_as_of="2000-01-01" if mutation == "stale" else None,
    )
    if mutation == "tampered":
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["universe_items"][0]["name"] = "被篡改"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "runtime" / "current-pool-audit.json"
    target.parent.mkdir()
    previous_bytes = b"previous-live-audit"
    target.write_bytes(previous_bytes)

    with pytest.raises(ValueError, match=message):
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(source),
                "--target-path",
                str(target),
                "--max-age-hours",
                "36",
            ]
        )

    assert target.read_bytes() == previous_bytes
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_current_pool_publish_replace_failure_keeps_old_target_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "audits" / "source.json"
    _write_publishable_audit(source)
    target = tmp_path / "runtime" / "current-pool-audit.json"
    target.parent.mkdir()
    previous_bytes = b"previous-live-audit"
    target.write_bytes(previous_bytes)

    def fail_replace(_source: str, _target: Path) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(jobs.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated atomic replace failure"):
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(source),
                "--target-path",
                str(target),
            ]
        )

    assert target.read_bytes() == previous_bytes
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def _fail_first_directory_fsync(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    real_fsync = jobs.os.fsync
    directory_calls: list[int] = []

    def fail_once(descriptor: int) -> None:
        if stat.S_ISDIR(jobs.os.fstat(descriptor).st_mode):
            directory_calls.append(descriptor)
            if len(directory_calls) == 1:
                raise OSError("simulated directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(jobs.os, "fsync", fail_once)
    return directory_calls


def test_current_pool_publish_directory_fsync_failure_atomically_restores_old_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "audits" / "source.json"
    _write_publishable_audit(source)
    target = tmp_path / "runtime" / "current-pool-audit.json"
    target.parent.mkdir()
    previous_bytes = b"previous-live-audit"
    target.write_bytes(previous_bytes)
    directory_calls = _fail_first_directory_fsync(monkeypatch)

    with pytest.raises(OSError, match="simulated directory fsync failure"):
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(source),
                "--target-path",
                str(target),
            ]
        )

    assert target.read_bytes() == previous_bytes
    assert len(directory_calls) == 2
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_current_pool_publish_directory_fsync_failure_removes_new_target_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "audits" / "source.json"
    _write_publishable_audit(source)
    target = tmp_path / "runtime" / "current-pool-audit.json"
    directory_calls = _fail_first_directory_fsync(monkeypatch)

    with pytest.raises(OSError, match="simulated directory fsync failure"):
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(source),
                "--target-path",
                str(target),
            ]
        )

    assert not target.exists()
    assert len(directory_calls) == 2
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_current_pool_publish_rollback_failure_reports_uncertain_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "audits" / "source.json"
    _write_publishable_audit(source)
    target = tmp_path / "runtime" / "current-pool-audit.json"
    target.parent.mkdir()
    target.write_bytes(b"previous-live-audit")
    _fail_first_directory_fsync(monkeypatch)
    real_replace = jobs.os.replace
    replace_calls = 0

    def fail_rollback_replace(source_path, target_path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("simulated rollback replace failure")
        real_replace(source_path, target_path)

    monkeypatch.setattr(jobs.os, "replace", fail_rollback_replace)

    with pytest.raises(jobs.CurrentPoolPublishUncertainStateError, match="uncertain"):
        jobs.main(
            [
                "research-current-pool-publish",
                "--audit-path",
                str(source),
                "--target-path",
                str(target),
            ]
        )

    assert replace_calls == 2
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))
