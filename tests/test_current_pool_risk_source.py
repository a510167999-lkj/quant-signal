import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import current_pool_risk_source, jobs
from app.current_pool_risk_source import (
    CurrentPoolRiskSourceError,
    PARTITION_ROW_CAP,
    build_current_pool_risk_descriptor,
    fetch_jiaoch_current_pool_risk_descriptor,
    verify_current_pool_risk_descriptor,
    is_st_risk_name,
)
from app.current_pool_source import build_current_pool_descriptor


def _stock_basic(exchange: str, status: str, fields: tuple[str, ...]):
    if exchange == "SSE" and status == "L":
        rows = [["600000.SH", "600000", "浦发银行", "SSE", "主板", "L", "19991110", None]]
    elif exchange == "SZSE" and status == "L":
        rows = [["300001.SZ", "300001", "特锐德", "SZSE", "创业板", "L", "20091030", None]]
    else:
        rows = []
    return {"code": 0, "msg": None, "data": {"fields": list(fields), "items": rows}}


def _universe(tmp_path: Path) -> Path:
    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:00:00+08:00",
        output_dir=tmp_path / "universe",
        fetch_partition=_stock_basic,
    )
    return Path(result["path"])


def _envelope(fields, rows):
    return {"code": 0, "msg": None, "data": {"fields": list(fields), "items": rows}}


def _fetch(api_name: str, params: dict, fields: tuple[str, ...]):
    if api_name == "stock_st":
        assert params == {"trade_date": "20260713"}
        assert fields == ("ts_code", "name", "type", "type_name", "trade_date")
        return _envelope(fields, [["600000.SH", "浦发ST", "ST", "风险警示", "20260713"]])
    if api_name == "suspend_d":
        assert params == {"trade_date": "20260713"}
        return _envelope(fields, [["300001.SZ", "20260713", "09:30", "S"]])
    assert api_name == "namechange"
    assert set(params) == {"start_date", "end_date"}
    if params["start_date"] == "20260101":
        return _envelope(fields, [["600000.SH", "*ST浦发", "20260101", None, "20251231", "风险警示"]])
    return _envelope(fields, [])


def test_builds_complete_content_addressed_risk_snapshot_bound_to_verified_universe(tmp_path: Path):
    universe = _universe(tmp_path)
    universe_payload = json.loads(universe.read_text(encoding="utf-8"))
    calls = []

    def fetch(api_name, params, fields):
        calls.append((api_name, dict(params), fields))
        return _fetch(api_name, params, fields)

    result = build_current_pool_risk_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        universe_path=universe,
        output_dir=tmp_path / "risk",
        fetch_partition=fetch,
    )

    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["schema"] == "current-pool-risk-input/v1"
    assert payload["universe_descriptor_sha256"] == universe_payload["descriptor_sha256"]
    assert payload["risk_snapshot_complete"] is True
    assert payload["risk_gate_passed"] is True
    assert payload["production_recommendation_eligible"] is False
    assert payload["partition_coverage"] == {
        "stock_st": 1,
        "suspend_d": 1,
        "namechange": 37,
        "partition_count": 39,
    }
    assert payload["items"] == [
        {
            "ts_code": "300001.SZ",
            "is_st": False,
            "st_type": None,
            "is_suspended": True,
            "suspension_reason": "suspended",
            "active_name": None,
        },
        {
            "ts_code": "600000.SH",
            "is_st": True,
            "st_type": "ST",
            "is_suspended": False,
            "suspension_reason": None,
            "active_name": "*ST浦发",
        },
    ]
    assert len(calls) == 39
    assert len(payload["partition_receipts"]) == 39
    assert all(set(receipt) == {"api_name", "params", "row_count", "rows_sha256"} for receipt in payload["partition_receipts"])
    assert verify_current_pool_risk_descriptor(payload, universe_payload)["descriptor_sha256"] == payload["descriptor_sha256"]
    assert Path(result["path"]).name == f"{payload['descriptor_sha256']}.json"
    loaded = jobs._load_current_pool_descriptor(str(result["path"]), "current-pool-risk-input/v1")
    assert loaded["descriptor_sha256"] == payload["descriptor_sha256"]


@pytest.mark.parametrize("resume_time", ["09:30", "13:00"])
def test_resume_day_blocks_new_entry_at_morning_and_afternoon_boundaries(
    tmp_path: Path, resume_time: str
):
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name == "suspend_d":
            return _envelope(fields, [["300001.SZ", "20260713", resume_time, "R"]])
        return _envelope(fields, [])

    result = build_current_pool_risk_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T12:00:00+08:00",
        universe_path=universe,
        output_dir=tmp_path / "risk",
        fetch_partition=fetch,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["risk_snapshot_complete"] is True
    item = next(item for item in payload["items"] if item["ts_code"] == "300001.SZ")
    assert item["is_suspended"] is True
    assert item["suspension_reason"] == "resume_day_no_new_entry"


@pytest.mark.parametrize("failure", ["unknown", "bad_date", "bad_schema", "cap"])
def test_fails_closed_on_unsafe_partition_data(tmp_path: Path, failure: str):
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name != "stock_st":
            return _envelope(fields, [])
        row = ["600000.SH", "ST浦发", "ST", "20260713", "20260713", None, "风险警示"]
        if failure == "unknown":
            row[0] = "000999.SZ"
        if failure == "bad_date":
            row[3] = "2026/07/13"
        if failure == "bad_schema":
            return {"code": 0, "data": {"fields": ["ts_code"], "items": [["600000.SH"]]}}
        if failure == "cap":
            return _envelope(fields, [row] * current_pool_risk_source.PARTITION_ROW_CAP)
        return _envelope(fields, [row])

    with pytest.raises(ValueError):
        build_current_pool_risk_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            universe_path=universe,
            output_dir=tmp_path / "risk",
            fetch_partition=fetch,
        )


def test_rejects_tampered_universe_and_wrong_collection_day_before_fetch(tmp_path: Path):
    universe = _universe(tmp_path)
    tampered = json.loads(universe.read_text(encoding="utf-8"))
    tampered["items"][0]["name"] = "tampered"
    candidate = tmp_path / "tampered.json"
    candidate.write_text(json.dumps(tampered))
    called = False

    def fetch(*args):
        nonlocal called
        called = True
        return _envelope(args[-1], [])

    with pytest.raises(ValueError):
        build_current_pool_risk_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            universe_path=candidate,
            output_dir=tmp_path / "risk",
            fetch_partition=fetch,
        )
    assert called is False


@pytest.mark.parametrize("mode", ["cross_year_filtered", "duplicate_still_rejected"])
def test_namechange_partition_filters_cross_year_rows_but_rejects_duplicates(tmp_path: Path, mode: str):
    """jiaoch namechange API 按 end_date 范围返回,会包括 start_date 跨年的"仍有效"行。

    codex 原来对每个 partition 强制 start_date ∈ [year0101, year1231],这与 jiaoch API
    语义不符(实测 1994 partition 返回了 1996 start_date 的行)。修复:跨年行被静默
    过滤(按真实 start_date 归属到自己的 partition),但同一 partition 内重复行仍
    fail-closed(防止 jiaoch 返回异常导致重复风险记录)。
    """
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name != "namechange":
            return _envelope(fields, [])
        if params["start_date"] == "20250101":
            if mode == "cross_year_filtered":
                # 跨年行(start_date 在 2026 不属于 2025 partition)+ 本年正常行
                return _envelope(
                    fields,
                    [
                        ["600000.SH", "未来名称", "20260101", None, "20251231", "其他"],
                        ["600000.SH", "本年名称", "20250601", None, None, "更名"],
                    ],
                )
            if mode == "duplicate_still_rejected":
                row = ["600000.SH", "旧名", "20250101", None, "20250101", "更名"]
                return _envelope(fields, [row, row])
        return _envelope(fields, [])

    if mode == "cross_year_filtered":
        # 跨年行被过滤,build 应成功完成
        build_current_pool_risk_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            universe_path=universe,
            output_dir=tmp_path / "risk",
            fetch_partition=fetch,
        )
    else:
        # 重复行仍 fail-closed
        with pytest.raises(ValueError):
            build_current_pool_risk_descriptor(
                as_of="2026-07-13",
                retrieved_at="2026-07-13T09:30:00+08:00",
                universe_path=universe,
                output_dir=tmp_path / "risk",
                fetch_partition=fetch,
            )


def test_risk_api_symbols_outside_universe_are_silently_dropped(tmp_path: Path):
    """jiaoch risk API 可能返回 universe 当前快照外的 symbol(例如已退市但在 namechange
    仍有历史记录的股票)。对 universe 外的 symbol 静默跳过,不 fail-closed。
    """
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name == "stock_st":
            # fields = ts_code, name, type, type_name, trade_date
            # 第一个 symbol 在 universe 内,第二个不在
            return _envelope(
                fields,
                [
                    ["600000.SH", "ST浦发", "ST", "风险警示", "20260713"],
                    ["999999.SH", "假股票", "ST", "假", "20260713"],
                ],
            )
        if api_name == "suspend_d":
            # fields = ts_code, trade_date, suspend_timing, suspend_type
            return _envelope(
                fields,
                [["999999.SZ", "20260713", "S", "停牌"]],
            )
        if api_name == "namechange":
            # fields = ts_code, name, start_date, end_date, ann_date, change_reason
            return _envelope(
                fields,
                [["999999.BJ", "假更名", "20250101", None, None, "更名"]],
            )
        return _envelope(fields, [])

    # 应该成功完成,universe 外的行被静默跳过
    build_current_pool_risk_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        universe_path=universe,
        output_dir=tmp_path / "risk",
        fetch_partition=fetch,
    )


def test_multiple_active_namechange_rows_pick_latest_start_date(tmp_path: Path):
    """jiaoch namechange 可能在 as_of 时点返回多条有效记录(过渡期重叠)。

    按 start_date 最新那条作为 active name,不 fail-closed——时点上"最新生效的名称"
    就是正确答案,避免一次公司重组的多阶段名称变更触发误判。
    """
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name == "namechange" and params["start_date"] == "20250101":
            # 同 symbol 两条都覆盖 as_of=2026-07-13
            return _envelope(
                fields,
                [
                    ["600000.SH", "旧名过渡期", "20200601", "20241231", None, "更名"],  # end < as_of 不应生效
                    ["600000.SH", "中期名", "20230101", "20251231", None, "更名"],  # 覆盖 as_of
                    ["600000.SH", "最新名", "20250601", None, None, "更名"],  # 覆盖 as_of 且 start 最新
                ],
            )
        return _envelope(fields, [])

    result = build_current_pool_risk_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        universe_path=universe,
        output_dir=tmp_path / "risk",
        fetch_partition=fetch,
    )
    # 通过 descriptor 文件读出来验证 active_name 选了最新 start_date 那条
    import json as _json
    payload = _json.loads(open(result["path"], encoding="utf-8").read())
    by_symbol = {row["ts_code"]: row for row in payload["items"]}
    assert by_symbol["600000.SH"]["active_name"] == "最新名"


@pytest.mark.parametrize("mutation", ["wrong_trade_date", "expired", "blank_type"])
def test_stock_st_must_be_effective_on_as_of(tmp_path: Path, mutation: str):
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name != "stock_st":
            return _envelope(fields, [])
        row = ["600000.SH", "ST浦发", "ST", "20260713", "20260701", None, "风险警示"]
        if mutation == "wrong_trade_date":
            row[3] = "20260712"
        if mutation == "expired":
            row[5] = "20260712"
        if mutation == "blank_type":
            row[2] = ""
        return _envelope(fields, [row])

    with pytest.raises(ValueError):
        build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=tmp_path / "risk", fetch_partition=fetch)


def test_same_day_suspend_and_resume_conflict_blocks_entry(tmp_path: Path):
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name == "suspend_d":
            return _envelope(fields, [["300001.SZ", "20260713", "09:30", "S"], ["300001.SZ", "20260713", "13:00", "R"]])
        return _envelope(fields, [])

    result = build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=tmp_path / "risk", fetch_partition=fetch)
    item = next(item for item in json.loads(Path(result["path"]).read_text(encoding="utf-8"))["items"] if item["ts_code"] == "300001.SZ")
    assert item["is_suspended"] is True
    assert item["suspension_reason"] == "conflict"


@pytest.mark.parametrize(
    "retrieved_at",
    [
        None,
        "2026-07-13T09:30:00",
        "2026-07-13T01:30:00+00:00",
        "2026-07-12T09:30:00+08:00",
    ],
)
def test_risk_verifier_rejects_resigned_invalid_retrieved_at(
    tmp_path: Path, retrieved_at: str | None
) -> None:
    universe = _universe(tmp_path)
    universe_payload = json.loads(universe.read_text(encoding="utf-8"))
    result = build_current_pool_risk_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        universe_path=universe,
        output_dir=tmp_path / "risk",
        fetch_partition=lambda api, params, fields: _envelope(fields, []),
    )
    candidate = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    candidate["retrieved_at"] = retrieved_at
    candidate.pop("descriptor_sha256")
    candidate["descriptor_sha256"] = hashlib.sha256(
        json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="current-pool risk descriptor rejected"):
        verify_current_pool_risk_descriptor(candidate, universe_payload)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_fetch_rejects_invalid_timeout_before_transport(monkeypatch, tmp_path: Path, timeout: float):
    universe = _universe(tmp_path)
    with pytest.raises(CurrentPoolRiskSourceError, match="risk source collection failed"):
        fetch_jiaoch_current_pool_risk_descriptor(as_of="2026-07-13", universe_path=universe, output_dir=tmp_path / "risk", timeout_seconds=timeout, now_provider=lambda: datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")))


def test_transport_exception_never_exposes_token(monkeypatch, tmp_path: Path):
    universe = _universe(tmp_path)
    token = "do-not-leak-token"

    class Transport:
        def __init__(self, **kwargs):
            pass

        def post(self, **kwargs):
            raise RuntimeError(f"network failed {token}")

    monkeypatch.setattr(current_pool_risk_source, "resolve_tushare_source", lambda *a, **k: SimpleNamespace(token=token, proxy_url=None, api_url="https://jiaoch.site"))
    monkeypatch.setattr(current_pool_risk_source, "UrllibTushareTransport", Transport)
    with pytest.raises(CurrentPoolRiskSourceError) as caught:
        fetch_jiaoch_current_pool_risk_descriptor(as_of="2026-07-13", universe_path=universe, output_dir=tmp_path / "risk", now_provider=lambda: datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert token not in str(caught.value)
    assert caught.value.__cause__ is None


def test_transport_exception_message_includes_exception_type_without_token(monkeypatch, tmp_path: Path):
    """失败消息必须带异常类型名(便于诊断 Windows clock gate / TLS / 超时),但不带原始消息(防 token 泄露)。"""
    universe = _universe(tmp_path)
    token = "do-not-leak-token"

    class Transport:
        def __init__(self, **kwargs):
            pass

        def post(self, **kwargs):
            raise TimeoutError(f"connect timed out leaked={token}")

    monkeypatch.setattr(current_pool_risk_source, "resolve_tushare_source", lambda *a, **k: SimpleNamespace(token=token, proxy_url=None, api_url="https://jiaoch.site"))
    monkeypatch.setattr(current_pool_risk_source, "UrllibTushareTransport", Transport)
    with pytest.raises(CurrentPoolRiskSourceError) as caught:
        fetch_jiaoch_current_pool_risk_descriptor(
            as_of="2026-07-13",
            universe_path=universe,
            output_dir=tmp_path / "risk",
            now_provider=lambda: datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    message = str(caught.value)
    assert "TimeoutError" in message, f"exception type missing from message: {message}"
    assert token not in message, f"token leaked into message: {message}"
    assert "network failed" not in message
    assert caught.value.__cause__ is None


def test_existing_content_address_conflict_is_rejected(tmp_path: Path, monkeypatch):
    universe = _universe(tmp_path)
    output = tmp_path / "risk"
    result = build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=output, fetch_partition=lambda api, params, fields: _envelope(fields, []))
    Path(result["path"]).write_text("conflict")
    with pytest.raises(ValueError, match="content-addressed"):
        build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=output, fetch_partition=lambda api, params, fields: _envelope(fields, []))


def test_risk_publisher_uses_cross_platform_directory_fsync(tmp_path: Path, monkeypatch):
    universe = _universe(tmp_path)
    output = tmp_path / "risk"
    calls = []
    monkeypatch.setattr(
        current_pool_risk_source,
        "fsync_directory",
        lambda directory: calls.append(Path(directory)),
    )

    build_current_pool_risk_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        universe_path=universe,
        output_dir=output,
        fetch_partition=lambda api, params, fields: _envelope(fields, []),
    )

    assert calls == [output]


def test_risk_verifier_rejects_topology_and_item_semantic_mutations(tmp_path: Path):
    universe = _universe(tmp_path)
    universe_payload = json.loads(universe.read_text(encoding="utf-8"))
    result = build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=tmp_path / "risk", fetch_partition=lambda api, params, fields: _envelope(fields, []))
    original = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    mutations = [
        lambda value: value["partition_receipts"][0]["params"].update(trade_date="20260712"),
        lambda value: value["partition_receipts"][2]["params"].update(start_date="19910101"),
        lambda value: value["partition_receipts"][0].update(row_count=PARTITION_ROW_CAP),
        lambda value: value["partition_receipts"][0].update(rows_sha256="g" * 64),
        lambda value: value["items"][0].update(is_st=True, st_type=None),
        lambda value: value["items"][0].update(is_suspended=True, suspension_reason=None),
        lambda value: value["items"][0].update(active_name=""),
        lambda value: value["items"].pop(),
    ]
    for mutate in mutations:
        candidate = json.loads(json.dumps(original))
        mutate(candidate)
        candidate.pop("descriptor_sha256", None)
        candidate["descriptor_sha256"] = hashlib.sha256(json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with pytest.raises(ValueError):
            verify_current_pool_risk_descriptor(candidate, universe_payload)


@pytest.mark.parametrize("different_type", [False, True])
def test_duplicate_stock_st_symbol_is_rejected(tmp_path: Path, different_type: bool):
    universe = _universe(tmp_path)
    def fetch(api_name, params, fields):
        if api_name != "stock_st":
            return _envelope(fields, [])
        first = ["600000.SH", "ST浦发", "ST", "风险警示", "20260713"]
        second = list(first)
        if different_type:
            second[2] = "*ST"
        return _envelope(fields, [first, second])
    with pytest.raises(ValueError, match="duplicate stock_st"):
        build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=tmp_path / "risk", fetch_partition=fetch)


def test_active_star_st_name_alone_marks_st_and_verifies(tmp_path: Path):
    universe = _universe(tmp_path)
    universe_payload = json.loads(universe.read_text(encoding="utf-8"))

    def fetch(api_name, params, fields):
        if api_name == "namechange" and params["start_date"] == "20260101":
            return _envelope(fields, [["600000.SH", "  *st 浦发", "20260101", None, "20251231", "风险警示"]])
        return _envelope(fields, [])

    result = build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=tmp_path / "risk", fetch_partition=fetch)
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    item = next(item for item in payload["items"] if item["ts_code"] == "600000.SH")
    assert item["is_st"] is True
    assert item["st_type"] is None
    assert verify_current_pool_risk_descriptor(payload, universe_payload)


@pytest.mark.parametrize("name", ["TEST科技", "Best Holdings", "测试ST科技"])
def test_nonprefix_st_text_does_not_mark_risk(tmp_path: Path, name: str):
    assert is_st_risk_name(name) is False
    universe = _universe(tmp_path)

    def fetch(api_name, params, fields):
        if api_name == "namechange" and params["start_date"] == "20260101":
            return _envelope(fields, [["600000.SH", name, "20260101", None, "20251231", "更名"]])
        return _envelope(fields, [])

    result = build_current_pool_risk_descriptor(as_of="2026-07-13", retrieved_at="2026-07-13T09:30:00+08:00", universe_path=universe, output_dir=tmp_path / "risk", fetch_partition=fetch)
    item = next(item for item in json.loads(Path(result["path"]).read_text(encoding="utf-8"))["items"] if item["ts_code"] == "600000.SH")
    assert item["is_st"] is False
    assert item["st_type"] is None


def test_jiaoch_adapter_uses_fixed_https_paths_and_rejects_secret(monkeypatch, tmp_path: Path):
    universe = _universe(tmp_path)
    token = "secret/test"
    calls = []
    mode = {"secret": False}

    class Transport:
        def __init__(self, *, proxy_url):
            assert proxy_url is None

        def post(self, **kwargs):
            calls.append(kwargs)
            request = json.loads(kwargs["body"])
            body = json.dumps(_envelope(tuple(request["fields"].split(",")), [])).encode()
            if mode["secret"]:
                body = token.encode()
            return SimpleNamespace(status=200, body_complete=True, body=body)

    monkeypatch.setattr(
        current_pool_risk_source,
        "resolve_tushare_source",
        lambda *a, **k: SimpleNamespace(token=token, proxy_url=None, api_url="https://jiaoch.site"),
    )
    monkeypatch.setattr(current_pool_risk_source, "UrllibTushareTransport", Transport)
    def now():
        return datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    fetch_jiaoch_current_pool_risk_descriptor(
        as_of="2026-07-13",
        universe_path=universe,
        output_dir=tmp_path / "risk",
        now_provider=now,
    )
    assert len(calls) == 39
    assert {call["url"] for call in calls} == {
        "https://jiaoch.site/stock_st",
        "https://jiaoch.site/suspend_d",
        "https://jiaoch.site/namechange",
    }
    assert token not in Path(next((tmp_path / "risk").glob("*.json"))).read_text(
        encoding="utf-8"
    )

    mode["secret"] = True
    with pytest.raises(CurrentPoolRiskSourceError, match="risk source collection failed"):
        fetch_jiaoch_current_pool_risk_descriptor(
            as_of="2026-07-13",
            universe_path=universe,
            output_dir=tmp_path / "unsafe",
            now_provider=now,
        )


def test_jobs_exposes_risk_fetch_cli(monkeypatch, capsys, tmp_path: Path):
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return {"path": str(tmp_path / "risk.json"), "created": True}

    monkeypatch.setattr(jobs, "fetch_jiaoch_current_pool_risk_descriptor", fake_fetch)
    assert jobs.main(
        [
            "research-current-pool-fetch-risk-jiaoch",
            "--as-of",
            "2026-07-13",
            "--universe-path",
            str(tmp_path / "universe.json"),
            "--output-dir",
            str(tmp_path),
        ]
    ) == 0
    assert calls == [
        {
            "as_of": "2026-07-13",
            "universe_path": str(tmp_path / "universe.json"),
            "output_dir": str(tmp_path),
            "timeout_seconds": 30.0,
        }
    ]
    assert "risk.json" in capsys.readouterr().out
