from __future__ import annotations

import hashlib
import importlib
import json
import os
import socket
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.research_pit_contracts import STOCK_BASIC_FIELDS
from app.research_pit_store import MARKET_SESSION_ROW_CAPS, NORMALIZED_FIELDS


AS_OF = "2026-07-03"
COMPACT_AS_OF = "20260703"
HISTORY_START = "2026-07-02"
HISTORY_END = "2026-07-03"
RETRIEVED_AT = "2026-07-03T18:00:00+08:00"
FROZEN_STOCK_BASIC_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
)
FROZEN_HISTORY_FIELDS = {
    "trade_cal": ("exchange", "cal_date", "is_open", "pretrade_date"),
    "daily": (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ),
    "adj_factor": ("ts_code", "trade_date", "adj_factor"),
    "stk_limit": ("trade_date", "ts_code", "pre_close", "up_limit", "down_limit"),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
}
FROZEN_MARKET_ROW_CAPS = {
    "daily": 6000,
    "adj_factor": 6000,
    "stk_limit": 5800,
    "suspend_d": 5000,
}
RISK_FIELDS = {
    "stock_st": ("ts_code", "name", "type", "type_name", "trade_date"),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    "namechange": (
        "ts_code",
        "name",
        "start_date",
        "end_date",
        "ann_date",
        "change_reason",
    ),
}


def _adapter():
    return importlib.import_module("app.current_pool_offline_replay")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _envelope(fields: tuple[str, ...], rows: list[list[object]], *, code: int = 0) -> bytes:
    return _canonical_bytes(
        {"code": code, "msg": "", "data": {"fields": list(fields), "items": rows}}
    )


def _write_manifest(path: Path, payload: dict) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = _sha256(unsigned)
    path.write_bytes(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    )
    return _file_sha256(path)


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resign_replay_manifest(path: Path, payload: dict) -> tuple[Path, str]:
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    digest = _sha256(unsigned)
    payload["manifest_sha256"] = digest
    content = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    replacement = path.parent / f"{digest}.json"
    path.unlink()
    replacement.write_bytes(content)
    return replacement, hashlib.sha256(content).hexdigest()


def _calendar_rows(exchange: str, open_dates: set[str]) -> list[list[object]]:
    rows = []
    prior_open = "20260701"
    for trade_date in ("20260702", "20260703"):
        is_open = 1 if trade_date in open_dates else 0
        rows.append([exchange, trade_date, is_open, prior_open])
        if is_open:
            prior_open = trade_date
    return rows


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "fixture"
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True)
    shards: list[dict] = []

    def add(
        *,
        stage: str,
        role: str,
        api_name: str,
        params: dict[str, str],
        fields: tuple[str, ...],
        row_cap: int,
        rows: list[list[object]],
    ) -> None:
        raw = _envelope(fields, rows)
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        relative = f"raw/{raw_sha256}.json"
        raw_path = root / relative
        if not raw_path.exists():
            raw_path.write_bytes(raw)
        shards.append(
            {
                "sequence": len(shards) + 1,
                "stage": stage,
                "role": role,
                "api_name": api_name,
                "params": params,
                "fields": list(fields),
                "raw_path": relative,
                "raw_bytes": len(raw),
                "raw_sha256": raw_sha256,
                "http_status": 200,
                "retrieved_at": RETRIEVED_AT,
                "row_cap": row_cap,
                "row_count": len(rows),
                "rows_sha256": _sha256(rows),
            }
        )

    universe_row = [
        "600001.SH",
        "600001",
        "样本股",
        "SSE",
        "主板",
        "L",
        "19910101",
        None,
    ]
    for exchange in ("SSE", "SZSE"):
        for list_status in ("L", "D", "P", "G"):
            add(
                stage="universe",
                role=f"universe.stock_basic.{exchange}.{list_status}",
                api_name="stock_basic",
                params={"exchange": exchange, "list_status": list_status},
                fields=FROZEN_STOCK_BASIC_FIELDS,
                row_cap=6000,
                rows=[universe_row] if (exchange, list_status) == ("SSE", "L") else [],
            )

    add(
        stage="risk",
        role="risk.stock_st.as_of",
        api_name="stock_st",
        params={"trade_date": COMPACT_AS_OF},
        fields=RISK_FIELDS["stock_st"],
        row_cap=10_000,
        rows=[],
    )
    add(
        stage="risk",
        role="risk.suspend_d.as_of",
        api_name="suspend_d",
        params={"trade_date": COMPACT_AS_OF},
        fields=RISK_FIELDS["suspend_d"],
        row_cap=10_000,
        rows=[],
    )
    for year in range(1990, 2027):
        add(
            stage="risk",
            role=f"risk.namechange.{year}",
            api_name="namechange",
            params={
                "start_date": f"{year}0101",
                "end_date": COMPACT_AS_OF if year == 2026 else f"{year}1231",
            },
            fields=RISK_FIELDS["namechange"],
            row_cap=10_000,
            rows=[],
        )

    trade_cal_fields = FROZEN_HISTORY_FIELDS["trade_cal"]
    for exchange in ("SSE", "SZSE"):
        add(
            stage="history",
            role=f"history.trade_cal.{exchange}",
            api_name="trade_cal",
            params={
                "exchange": exchange,
                "start_date": HISTORY_START.replace("-", ""),
                "end_date": HISTORY_END.replace("-", ""),
            },
            fields=trade_cal_fields,
            row_cap=6000,
            rows=_calendar_rows(exchange, {COMPACT_AS_OF}),
        )

    market_rows = {
        "daily": [
            [
                "600001.SH",
                COMPACT_AS_OF,
                10.0,
                10.5,
                9.8,
                10.2,
                9.9,
                0.3,
                3.03,
                1000,
                10100,
            ]
        ],
        "adj_factor": [["600001.SH", COMPACT_AS_OF, 1.5]],
        "stk_limit": [[COMPACT_AS_OF, "600001.SH", 10.0, 11.0, 9.0]],
        "suspend_d": [],
    }
    for dataset in ("daily", "adj_factor", "stk_limit", "suspend_d"):
        add(
            stage="history",
            role=f"history.market.{dataset}",
            api_name=dataset,
            params={"trade_date": COMPACT_AS_OF},
            fields=FROZEN_HISTORY_FIELDS[dataset],
            row_cap=FROZEN_MARKET_ROW_CAPS[dataset],
            rows=market_rows[dataset],
        )

    assert len(shards) == 53
    payload = {
        "schema": "current-pool-offline-fixture/v1",
        "as_of": AS_OF,
        "retrieved_at": RETRIEVED_AT,
        "history_start": HISTORY_START,
        "history_end": HISTORY_END,
        "shards": shards,
    }
    manifest_path = root / "manifest.json"
    expected_sha256 = _write_manifest(manifest_path, payload)
    return root, manifest_path, expected_sha256


def _replace_raw(
    root: Path,
    payload: dict,
    index: int,
    raw: bytes,
    *,
    update_rows: bool,
) -> None:
    ref = payload["shards"][index]
    old_relative = ref["raw_path"]
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"raw/{digest}.json"
    (root / relative).write_bytes(raw)
    ref["raw_path"] = relative
    ref["raw_bytes"] = len(raw)
    ref["raw_sha256"] = digest
    if update_rows:
        envelope = json.loads(raw.decode("utf-8"))
        rows = envelope["data"]["items"]
        ref["row_count"] = len(rows)
        ref["rows_sha256"] = _sha256(rows)
    if old_relative != relative and not any(
        item["raw_path"] == old_relative for item in payload["shards"]
    ):
        (root / old_relative).unlink()


def _replay_rejects(
    tmp_path: Path,
    root: Path,
    manifest_path: Path,
    expected_sha256: str,
    match: str = "offline fixture",
) -> None:
    run_root = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match=match):
        _adapter().replay_current_pool_offline_fixture(
            fixture_root=root,
            manifest_path=manifest_path,
            expected_manifest_sha256=expected_sha256,
            run_root=run_root,
        )
    assert not os.path.lexists(run_root)


def test_preflight_accepts_exact_53_shard_contract_and_distinct_suspend_roles(
    tmp_path: Path,
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)

    verified = _adapter().preflight_current_pool_offline_fixture(
        fixture_root=root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_sha256,
    )

    assert verified["shard_count"] == 53
    assert verified["stage_counts"] == {"universe": 8, "risk": 39, "history": 6}
    risk_suspend = verified["shards"][9]
    history_suspend = verified["shards"][52]
    assert risk_suspend["role"] == "risk.suspend_d.as_of"
    assert history_suspend["role"] == "history.market.suspend_d"
    assert risk_suspend["raw_path"] == history_suspend["raw_path"]


def test_frozen_v1_fixture_contract_does_not_derive_from_runtime_constants() -> None:
    assert tuple(STOCK_BASIC_FIELDS) == FROZEN_STOCK_BASIC_FIELDS
    assert {
        key: tuple(NORMALIZED_FIELDS[key]) for key in FROZEN_HISTORY_FIELDS
    } == FROZEN_HISTORY_FIELDS
    assert {
        key: MARKET_SESSION_ROW_CAPS[key] for key in FROZEN_MARKET_ROW_CAPS
    } == FROZEN_MARKET_ROW_CAPS


def test_replay_is_offline_content_addressed_and_independently_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    network_calls = []

    def explode(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("network or online collector was called")

    from app import current_pool_risk_source, current_pool_source, research_pit_transport
    from app import research_validation

    monkeypatch.setattr(socket, "create_connection", explode)
    monkeypatch.setattr(socket, "getaddrinfo", explode)
    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(research_pit_transport.UrllibTushareTransport, "__init__", explode)
    monkeypatch.setattr(current_pool_source, "resolve_tushare_source", explode)
    monkeypatch.setattr(current_pool_source, "fetch_jiaoch_current_pool_descriptor", explode)
    monkeypatch.setattr(current_pool_risk_source, "resolve_tushare_source", explode)
    monkeypatch.setattr(
        current_pool_risk_source, "fetch_jiaoch_current_pool_risk_descriptor", explode
    )
    monkeypatch.setattr(research_validation, "append_experiment_event", explode)
    run_root = tmp_path / "run"

    result = _adapter().replay_current_pool_offline_fixture(
        fixture_root=root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_sha256,
        run_root=run_root,
    )
    verified = _adapter().verify_current_pool_offline_replay_manifest(
        manifest_path=result["path"],
        expected_manifest_sha256=result["manifest_file_sha256"],
        fixture_root=root,
        fixture_manifest_path=manifest_path,
        expected_fixture_manifest_sha256=expected_sha256,
    )
    replay = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    repository = Path(_adapter().__file__).resolve().parents[1]
    source_paths = sorted(
        [
            "app/current_pool_history_source.py",
            "app/current_pool_offline_replay.py",
            "app/current_pool_risk_source.py",
            "app/current_pool_source.py",
            "app/durable_io.py",
            "app/research_pit_contracts.py",
            "app/research_pit_store.py",
        ]
    )
    source_entries = [
        {
            "path": relative,
            "bytes": (repository / relative).stat().st_size,
            "sha256": _file_sha256(repository / relative),
        }
        for relative in source_paths
    ]

    assert network_calls == []
    assert Path(result["path"]).name == f"{result['manifest_sha256']}.json"
    assert verified["schema"] == "current-pool-offline-replay/v1"
    assert verified["eligible_pool_count"] == 0
    assert verified["production_recommendation_eligible"] is False
    assert verified["replay_eligible"] is False
    assert verified["development_only"] is True
    assert verified["current_universe_bias"] is True
    assert verified["shard_count"] == 53
    assert replay["source_modules"] == source_entries
    assert replay["source_root_sha256"] == _sha256(source_entries)
    assert [ref["sequence"] for ref in replay["input"]["raw_refs"]] == list(range(1, 54))
    assert replay["pit"]["verified_receipt_count"] == 2
    assert replay["pit"]["generation"]["trade_date"] == AS_OF
    assert replay["pit"]["generation"]["vintage"] == "historical_backfill"
    for descriptor in replay["outputs"].values():
        raw = (Path(result["path"]).parent / descriptor["path"]).read_bytes()
        assert descriptor["bytes"] == len(raw)
        assert descriptor["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_preflight_rejects_52_or_54_shards_before_run_creation(
    tmp_path: Path, mode: str
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    if mode == "missing":
        payload["shards"].pop()
    else:
        extra = dict(payload["shards"][-1])
        extra["sequence"] = 54
        extra["role"] = "history.market.unknown"
        payload["shards"].append(extra)
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


@pytest.mark.parametrize(
    "mode", ["swap", "duplicate", "unknown_role", "extra_key", "sequence_gap"]
)
def test_preflight_rejects_noncanonical_or_conflicting_shard_keys(
    tmp_path: Path, mode: str
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    if mode == "swap":
        payload["shards"][0], payload["shards"][1] = (
            payload["shards"][1],
            payload["shards"][0],
        )
    elif mode == "duplicate":
        payload["shards"][1] = dict(payload["shards"][0])
    elif mode == "unknown_role":
        payload["shards"][0]["role"] = "universe.stock_basic.unknown"
    elif mode == "extra_key":
        payload["shards"][0]["unexpected"] = True
    else:
        payload["shards"][0]["sequence"] = 0
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


@pytest.mark.parametrize(
    "raw_path",
    [
        "../escape.json",
        "/absolute.json",
        "C:/absolute.json",
        "raw\\not-posix.json",
        "raw/stream.json:ads",
    ],
)
def test_preflight_rejects_noncanonical_or_escaping_raw_paths(
    tmp_path: Path, raw_path: str
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    payload["shards"][49]["raw_path"] = raw_path
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


@pytest.mark.parametrize("mode", ["missing", "directory", "unlisted"])
def test_preflight_rejects_missing_directory_or_unlisted_raw(
    tmp_path: Path, mode: str
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    raw_path = root / payload["shards"][49]["raw_path"]
    if mode == "missing":
        raw_path.unlink()
    elif mode == "directory":
        raw_path.unlink()
        raw_path.mkdir()
    else:
        (root / "raw" / "unlisted.json").write_text("{}", encoding="utf-8")

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


def test_preflight_rejects_symlink_or_windows_reparse_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    module = _adapter()
    target = (root / _load_manifest(manifest_path)["shards"][49]["raw_path"]).resolve()
    original = module._path_is_link_or_reparse
    monkeypatch.setattr(
        module,
        "_path_is_link_or_reparse",
        lambda path: path.resolve() == target or original(path),
    )

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


@pytest.mark.parametrize(
    ("mode", "attributes"),
    [(stat.S_IFLNK | 0o777, 0), (stat.S_IFREG | 0o644, 0x400)],
)
def test_link_gate_detects_posix_symlink_and_windows_reparse_attribute(
    mode: int, attributes: int
) -> None:
    class FakePath:
        def lstat(self):
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)

        def is_junction(self) -> bool:
            return False

    assert _adapter()._path_is_link_or_reparse(FakePath()) is True


@pytest.mark.parametrize("mode", ["tamper", "invalid_utf8", "duplicate_key", "nan", "infinity"])
def test_preflight_rejects_raw_integrity_or_strict_json_failures(
    tmp_path: Path, mode: str
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    if mode == "tamper":
        (root / payload["shards"][49]["raw_path"]).write_bytes(b"tampered")
    else:
        fields = payload["shards"][49]["fields"]
        if mode == "invalid_utf8":
            raw = b"\xff"
        elif mode == "duplicate_key":
            raw = b'{"code":0,"code":0,"msg":"","data":{"fields":[],"items":[]}}'
        else:
            constant = "NaN" if mode == "nan" else "Infinity"
            raw = (
                '{"code":0,"msg":"","data":{"fields":'
                + json.dumps(fields)
                + ',"items":[],"probe":'
                + constant
                + "}}"
            ).encode("utf-8")
        _replace_raw(root, payload, 49, raw, update_rows=False)
        expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


@pytest.mark.parametrize(
    "mode",
    [
        "provider_code",
        "fields",
        "params",
        "row_cap",
        "row_count",
        "rows_sha256",
        "http_status",
        "retrieved_at",
    ],
)
def test_preflight_rejects_response_or_request_contract_drift(
    tmp_path: Path, mode: str
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    ref = payload["shards"][49]
    if mode == "provider_code":
        raw = _envelope(tuple(ref["fields"]), [], code=1)
        _replace_raw(root, payload, 49, raw, update_rows=True)
    elif mode == "fields":
        ref["fields"] = ref["fields"][:-1]
    elif mode == "params":
        ref["params"] = {"trade_date": "20260702"}
    elif mode == "row_cap":
        ref["row_cap"] += 1
    elif mode == "row_count":
        ref["row_count"] += 1
    elif mode == "rows_sha256":
        ref["rows_sha256"] = "0" * 64
    elif mode == "http_status":
        ref["http_status"] = 500
    else:
        ref["retrieved_at"] = "2026-07-04T00:00:00+08:00"
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


def test_preflight_rejects_boolean_sequence_instead_of_exact_integer(tmp_path: Path) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    payload["shards"][0]["sequence"] = True
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


def test_preflight_rejects_raw_fields_drift_even_when_raw_is_fully_resigned(
    tmp_path: Path,
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    ref = payload["shards"][49]
    raw = _envelope(tuple(ref["fields"][:-1]), [])
    _replace_raw(root, payload, 49, raw, update_rows=True)
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b'{"schema":"current-pool-offline-fixture/v1","schema":"duplicate"}',
        b'{"probe":NaN}',
        b'{"probe":Infinity}',
    ],
)
def test_preflight_rejects_non_strict_manifest_json_before_run_creation(
    tmp_path: Path, raw: bytes
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    manifest_path.write_bytes(raw)

    _replay_rejects(tmp_path, root, manifest_path, _file_sha256(manifest_path))


def test_preflight_rejects_non_shanghai_top_timestamp_before_run_creation(
    tmp_path: Path,
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    payload["retrieved_at"] = "2026-07-03T10:00:00+00:00"
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


def test_preflight_rejects_invalid_calendar_pretrade_chain_before_run_creation(
    tmp_path: Path,
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    ref = payload["shards"][47]
    rows = json.loads((root / ref["raw_path"]).read_text(encoding="utf-8"))["data"][
        "items"
    ]
    rows[-1][-1] = rows[-1][1]
    raw = _envelope(tuple(ref["fields"]), rows)
    _replace_raw(root, payload, 47, raw, update_rows=True)
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


def test_preflight_rejects_market_shards_outside_generation_window_before_run_creation(
    tmp_path: Path,
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    payload["shards"][52]["retrieved_at"] = "2026-07-03T20:00:00+08:00"
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


def test_market_shards_just_inside_generation_window_do_not_fail_after_preflight(
    tmp_path: Path,
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    payload["shards"][52]["retrieved_at"] = "2026-07-03T18:59:59.500000+08:00"
    expected_sha256 = _write_manifest(manifest_path, payload)

    result = _adapter().replay_current_pool_offline_fixture(
        fixture_root=root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_sha256,
        run_root=tmp_path / "run",
    )

    assert Path(result["path"]).is_file()


def test_preflight_rejects_external_or_internal_manifest_hash_tamper(
    tmp_path: Path
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)

    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path / "second")
    payload = _load_manifest(manifest_path)
    payload["as_of"] = "2026-07-04"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    _replay_rejects(tmp_path, root, manifest_path, _file_sha256(manifest_path))


def test_preflight_rejects_dual_sha_resign_with_wrong_market_session(
    tmp_path: Path,
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    ref = payload["shards"][49]
    rows = json.loads((root / ref["raw_path"]).read_text(encoding="utf-8"))["data"][
        "items"
    ]
    rows[0][1] = "20260702"
    raw = _envelope(tuple(ref["fields"]), rows)
    _replace_raw(root, payload, 49, raw, update_rows=True)
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


@pytest.mark.parametrize("mode", ["zero", "two", "disagree"])
def test_preflight_rejects_calendar_without_exactly_one_common_open_session(
    tmp_path: Path, mode: str
) -> None:
    root, manifest_path, _expected_sha256 = _write_fixture(tmp_path)
    payload = _load_manifest(manifest_path)
    for index, exchange in ((47, "SSE"), (48, "SZSE")):
        if mode == "zero":
            opens: set[str] = set()
        elif mode == "two":
            opens = {"20260702", "20260703"}
        else:
            opens = {"20260703"} if exchange == "SSE" else {"20260702"}
        raw = _envelope(
            tuple(payload["shards"][index]["fields"]),
            _calendar_rows(exchange, opens),
        )
        _replace_raw(root, payload, index, raw, update_rows=True)
    expected_sha256 = _write_manifest(manifest_path, payload)

    _replay_rejects(tmp_path, root, manifest_path, expected_sha256)


def test_replay_requires_fresh_nonexistent_run_root(tmp_path: Path) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    run_root = tmp_path / "existing"
    run_root.mkdir()

    with pytest.raises(ValueError, match="fresh"):
        _adapter().replay_current_pool_offline_fixture(
            fixture_root=root,
            manifest_path=manifest_path,
            expected_manifest_sha256=expected_sha256,
            run_root=run_root,
        )


@pytest.mark.parametrize("failed_stage", ["universe", "risk"])
def test_stage_failure_never_calls_later_stages_or_publishes_final_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_stage: str
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    module = _adapter()
    calls = {"risk": 0, "history": 0}

    def fail(message: str):
        raise ValueError(message)

    if failed_stage == "universe":
        monkeypatch.setattr(
            module,
            "build_current_pool_descriptor",
            lambda **kwargs: fail("universe failed"),
        )

        def risk_bomb(**kwargs):
            calls["risk"] += 1
            raise AssertionError("risk ran after universe failure")

        monkeypatch.setattr(module, "build_current_pool_risk_descriptor", risk_bomb)
    else:

        def risk_fail(**kwargs):
            calls["risk"] += 1
            fail("risk failed")

        monkeypatch.setattr(module, "build_current_pool_risk_descriptor", risk_fail)

    def history_bomb(**kwargs):
        calls["history"] += 1
        raise AssertionError("history ran after an earlier failure")

    monkeypatch.setattr(module, "build_current_pool_history_summary", history_bomb)
    run_root = tmp_path / "partial-run"

    with pytest.raises(ValueError, match=f"{failed_stage} failed"):
        module.replay_current_pool_offline_fixture(
            fixture_root=root,
            manifest_path=manifest_path,
            expected_manifest_sha256=expected_sha256,
            run_root=run_root,
        )

    assert calls["history"] == 0
    assert calls["risk"] == (0 if failed_stage == "universe" else 1)
    assert not (run_root / "store").exists()
    assert not list(run_root.glob("*.json"))


@pytest.mark.parametrize("failed_stage", ["pit", "generation", "history"])
def test_pit_generation_or_history_failure_never_publishes_final_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_stage: str
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    module = _adapter()
    calls = {"history": 0, "publish": 0}

    if failed_stage == "pit":
        monkeypatch.setattr(
            module.PITReceiptStore,
            "ingest_tushare_response",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("pit failed")),
        )
    elif failed_stage == "generation":
        monkeypatch.setattr(
            module.PITReceiptStore,
            "stage_market_session_attempt",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("generation failed")),
        )

    original_history = module.build_current_pool_history_summary

    def history_call(**kwargs):
        calls["history"] += 1
        if failed_stage == "history":
            raise ValueError("history failed")
        return original_history(**kwargs)

    def publish_bomb(*args, **kwargs):
        calls["publish"] += 1
        raise AssertionError("final manifest published after stage failure")

    monkeypatch.setattr(module, "build_current_pool_history_summary", history_call)
    monkeypatch.setattr(module, "_atomic_write_json", publish_bomb)
    run_root = tmp_path / "partial-run"

    with pytest.raises(ValueError, match=f"{failed_stage} failed"):
        module.replay_current_pool_offline_fixture(
            fixture_root=root,
            manifest_path=manifest_path,
            expected_manifest_sha256=expected_sha256,
            run_root=run_root,
        )

    assert calls["history"] == (1 if failed_stage == "history" else 0)
    assert calls["publish"] == 0
    assert not list(run_root.glob("*.json"))


@pytest.mark.parametrize("target", ["manifest", "universe", "metadata", "input_raw"])
def test_replay_verifier_rejects_tampered_bound_artifacts(
    tmp_path: Path, target: str
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    module = _adapter()
    result = module.replay_current_pool_offline_fixture(
        fixture_root=root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_sha256,
        run_root=tmp_path / "run",
    )
    replay_path = Path(result["path"])
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    if target == "manifest":
        replay["eligible_pool_count"] = 1
        replay_path.write_text(json.dumps(replay), encoding="utf-8")
    elif target == "universe":
        (replay_path.parent / replay["outputs"]["universe"]["path"]).write_bytes(
            b"tampered"
        )
    elif target == "metadata":
        metadata_path = replay_path.parent / replay["pit"]["metadata"]["path"]
        metadata_path.write_bytes(metadata_path.read_bytes() + b"tampered")
    else:
        fixture = _load_manifest(manifest_path)
        raw_path = root / fixture["shards"][49]["raw_path"]
        raw_path.write_bytes(raw_path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="offline replay"):
        module.verify_current_pool_offline_replay_manifest(
            manifest_path=replay_path,
            expected_manifest_sha256=result["manifest_file_sha256"],
            fixture_root=root,
            fixture_manifest_path=manifest_path,
            expected_fixture_manifest_sha256=expected_sha256,
        )


@pytest.mark.parametrize("location", ["run", "store"])
def test_replay_verifier_rejects_unbound_extra_output_files(
    tmp_path: Path, location: str
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    module = _adapter()
    result = module.replay_current_pool_offline_fixture(
        fixture_root=root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_sha256,
        run_root=tmp_path / "run",
    )
    replay_path = Path(result["path"])
    poison_root = replay_path.parent / ("store" if location == "store" else "")
    (poison_root / "poison.bin").write_bytes(b"unbound")

    with pytest.raises(ValueError, match="offline replay"):
        module.verify_current_pool_offline_replay_manifest(
            manifest_path=replay_path,
            expected_manifest_sha256=result["manifest_file_sha256"],
            fixture_root=root,
            fixture_manifest_path=manifest_path,
            expected_fixture_manifest_sha256=expected_sha256,
        )


def test_replay_verifier_rejects_boolean_eligible_count_after_full_resign(
    tmp_path: Path,
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    module = _adapter()
    result = module.replay_current_pool_offline_fixture(
        fixture_root=root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_sha256,
        run_root=tmp_path / "run",
    )
    replay_path = Path(result["path"])
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    replay["eligible_pool_count"] = False
    replay_path, replay_file_sha256 = _resign_replay_manifest(replay_path, replay)

    with pytest.raises(ValueError, match="offline replay"):
        module.verify_current_pool_offline_replay_manifest(
            manifest_path=replay_path,
            expected_manifest_sha256=replay_file_sha256,
            fixture_root=root,
            fixture_manifest_path=manifest_path,
            expected_fixture_manifest_sha256=expected_sha256,
        )


def test_replay_verifier_cross_binds_verified_generation_to_history_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest_path, expected_sha256 = _write_fixture(tmp_path)
    module = _adapter()
    result = module.replay_current_pool_offline_fixture(
        fixture_root=root,
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_sha256,
        run_root=tmp_path / "run",
    )
    replay_path = Path(result["path"])
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    forged_generation = dict(replay["pit"]["generation"])
    forged_generation["generation_id"] = "f" * 64
    replay["pit"]["generation"] = forged_generation
    replay_path, replay_file_sha256 = _resign_replay_manifest(replay_path, replay)
    monkeypatch.setattr(
        module.PITReceiptStore,
        "verify_market_session_generation",
        lambda self, *, generation_id: dict(forged_generation),
    )

    with pytest.raises(ValueError, match="offline replay"):
        module.verify_current_pool_offline_replay_manifest(
            manifest_path=replay_path,
            expected_manifest_sha256=replay_file_sha256,
            fixture_root=root,
            fixture_manifest_path=manifest_path,
            expected_fixture_manifest_sha256=expected_sha256,
        )


def test_content_addressed_manifest_publish_rejects_existing_conflicting_bytes(
    tmp_path: Path,
) -> None:
    payload = {"schema": "collision-probe/v1", "value": 1}
    destination = tmp_path / f"{_sha256(payload)}.json"
    destination.write_bytes(b"conflict")

    with pytest.raises(ValueError, match="collision"):
        _adapter()._atomic_write_json(tmp_path, payload)

    assert destination.read_bytes() == b"conflict"
