from __future__ import annotations

from hashlib import sha256
from copy import deepcopy

import pandas as pd
import pytest

from app import research_security_code_transition as transition
from app.audited_pit_trend_pullback import (
    AuditedPITDevelopmentReplayError,
    _assert_execution_resolution_matches_frame,
    _strict_close_stop_trade,
)


def _pdf_with_text(text: str) -> bytes:
    encoded_text = text.encode("utf-16-be").hex().upper()
    content = f"BT /F1 12 Tf 72 720 Td <{encoded_text}> Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        ),
        (
            b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
            b"/Encoding /UniGB-UCS2-H /DescendantFonts [6 0 R] >>"
        ),
        (
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> >>"
        ),
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def _transition_pdf() -> bytes:
    return _pdf_with_text(
        "证券代码：300114 证券简称：中航电测 公告编号：2025-028 "
        "中航成飞股份有限公司 关于变更公司证券简称及证券代码的实施公告 "
        "公司股票自2025年2月17日开市起启用变更后的证券简称及证券代码。"
        "变更后的证券代码：302132。"
        "公司法人主体存续，上市主体没有发生实质变化。"
        "在证券代码变更日将证券代码“300114”股份变更为证券代码“302132”股份，"
        "投资者证券账户中持有公司股份数量不变。"
        "T-1日前（含T-1日）证券代码为“300114”，"
        "T日后（含T日）证券代码变更为“302132”。"
    )


def _test_contract(raw: bytes) -> dict:
    parsed = transition.parse_cninfo_security_code_transition_pdf(
        raw,
        source_url=(
            "https://static.cninfo.com.cn/finalpage/2025-02-15/1222544408.PDF"
        ),
        published_at="2025-02-15T00:00:00+08:00",
        predecessor_ts_code="300114.SZ",
        successor_ts_code="302132.SZ",
        effective_date="2025-02-17",
    )
    return transition.build_security_code_transition_contract([parsed])


def test_official_pdf_binds_code_identity_effective_date_and_quantity():
    raw = _transition_pdf()

    parsed = transition.parse_cninfo_security_code_transition_pdf(
        raw,
        source_url=(
            "https://static.cninfo.com.cn/finalpage/2025-02-15/1222544408.PDF"
        ),
        published_at="2025-02-15T00:00:00+08:00",
        predecessor_ts_code="300114.SZ",
        successor_ts_code="302132.SZ",
        effective_date="2025-02-17",
    )

    assert parsed["announcement_number"] == "2025-028"
    assert parsed["predecessor_ts_code"] == "300114.SZ"
    assert parsed["successor_ts_code"] == "302132.SZ"
    assert parsed["effective_date"] == "2025-02-17"
    assert parsed["share_quantity_ratio"] == 1.0
    assert parsed["issuer_continuity"] is True
    assert parsed["raw_sha256"] == sha256(raw).hexdigest()
    assert len(parsed["text_sha256"]) == 64


def test_official_pdf_rejects_missing_unchanged_quantity_claim():
    raw = _pdf_with_text(
        "证券代码：300114 公告编号：2025-028 "
        "中航成飞股份有限公司 关于变更公司证券简称及证券代码的实施公告 "
        "公司股票自2025年2月17日开市起启用变更后的证券代码302132。"
        "公司法人主体存续，上市主体没有发生实质变化。"
    )

    with pytest.raises(
        transition.SecurityCodeTransitionEvidenceError,
        match="quantity",
    ):
        transition.parse_cninfo_security_code_transition_pdf(
            raw,
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2025-02-15/"
                "1222544408.PDF"
            ),
            published_at="2025-02-15T00:00:00+08:00",
            predecessor_ts_code="300114.SZ",
            successor_ts_code="302132.SZ",
            effective_date="2025-02-17",
        )


def test_evidence_loader_requires_hash_named_untampered_pdf(
    monkeypatch,
    tmp_path,
):
    raw = _transition_pdf()
    contract = _test_contract(raw)
    contract_sha = transition.canonical_sha256(contract)
    monkeypatch.setattr(
        transition,
        "SECURITY_CODE_TRANSITION_CONTRACT",
        contract,
    )
    monkeypatch.setattr(
        transition,
        "SECURITY_CODE_TRANSITION_CONTRACT_SHA256",
        contract_sha,
    )
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    pdf_path = evidence_root / f"{sha256(raw).hexdigest()}.pdf"
    pdf_path.write_bytes(raw)

    loaded = transition.load_security_code_transition_evidence(
        evidence_root,
        expected_contract_sha256=contract_sha,
    )

    assert loaded["contract_sha256"] == contract_sha
    assert loaded["evidence_receipts"][0]["raw_sha256"] == sha256(raw).hexdigest()
    pdf_path.write_bytes(raw + b"tampered")
    with pytest.raises(
        transition.SecurityCodeTransitionEvidenceError,
        match="hash",
    ):
        transition.load_security_code_transition_evidence(
            evidence_root,
            expected_contract_sha256=contract_sha,
        )


def _bar(
    trade_date: str,
    ts_code: str,
    close: float,
    *,
    industry: str = "电器仪表",
    pre_close: float | None = None,
) -> dict:
    return {
        "date": trade_date,
        "ts_code": ts_code,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": close if pre_close is None else pre_close,
        "amount": 1_000_000.0,
        "adj_factor": 1.0,
        "membership_name": "中航电测" if ts_code == "300114.SZ" else "中航成飞",
        "membership_industry": industry,
        "membership_receipt_dataset": "bak_basic",
        "membership_receipt_partition": trade_date,
        "suspended": 0,
    }


def test_pit_boundary_filters_successor_backfill_and_merges_entity():
    sessions = ["2025-02-14", "2025-02-17"]
    bars = pd.DataFrame(
        [
            _bar("2025-02-14", "300114.SZ", 72.18),
            _bar("2025-02-14", "302132.SZ", 72.18, industry=""),
            _bar(
                "2025-02-17",
                "302132.SZ",
                68.01,
                pre_close=72.18,
            ),
        ]
    )

    canonical, receipt = transition.apply_security_code_transition_contract(
        bars,
        sessions=sessions,
        contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
    )

    assert canonical["date"].tolist() == sessions
    assert canonical["ts_code"].tolist() == ["300114.SZ", "300114.SZ"]
    assert canonical["source_ts_code"].tolist() == [
        "300114.SZ",
        "302132.SZ",
    ]
    assert canonical["security_id"].nunique() == 1
    assert receipt["excluded_successor_backfill_row_count"] == 1
    assert receipt["transition_boundary_count"] == 1


def test_pit_boundary_fails_closed_when_effective_successor_bar_is_missing():
    bars = pd.DataFrame(
        [
            _bar("2025-02-14", "300114.SZ", 72.18),
            _bar("2025-02-17", "300114.SZ", 68.01),
        ]
    )

    with pytest.raises(
        transition.SecurityCodeTransitionEvidenceError,
        match="boundary",
    ):
        transition.apply_security_code_transition_contract(
            bars,
            sessions=["2025-02-14", "2025-02-17"],
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )


def test_transition_application_rejects_tampered_contract_and_backfill():
    tampered_contract = deepcopy(
        transition.SECURITY_CODE_TRANSITION_CONTRACT
    )
    tampered_contract["transitions"][0]["share_quantity_ratio"] = 2.0
    with pytest.raises(
        transition.SecurityCodeTransitionEvidenceError,
        match="contract",
    ):
        transition.apply_security_code_transition_contract(
            pd.DataFrame(
                [_bar("2025-02-14", "300114.SZ", 72.18)]
            ),
            sessions=["2025-02-14"],
            contract=tampered_contract,
        )

    conflicting = pd.DataFrame(
        [
            _bar("2025-02-14", "300114.SZ", 72.18),
            _bar("2025-02-14", "302132.SZ", 71.00),
            _bar(
                "2025-02-17",
                "302132.SZ",
                68.01,
                pre_close=72.18,
            ),
        ]
    )
    with pytest.raises(
        transition.SecurityCodeTransitionEvidenceError,
        match="backfill conflicts",
    ):
        transition.apply_security_code_transition_contract(
            conflicting,
            sessions=["2025-02-14", "2025-02-17"],
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )


def test_transition_application_receipt_binds_sessions_and_bar_values():
    first = pd.DataFrame(
        [_bar("2025-01-02", "000001.SZ", 10.0)]
    )
    second = pd.DataFrame(
        [_bar("2025-01-02", "000001.SZ", 11.0)]
    )

    _, first_receipt = transition.apply_security_code_transition_contract(
        first,
        sessions=["2025-01-02"],
        contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
    )
    _, second_receipt = transition.apply_security_code_transition_contract(
        second,
        sessions=["2025-01-02"],
        contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
    )

    assert first_receipt["receipt_sha256"] != second_receipt["receipt_sha256"]
    assert first_receipt["sessions_sha256"] == transition.canonical_sha256(
        ["2025-01-02"]
    )
    with pytest.raises(
        transition.SecurityCodeTransitionEvidenceError,
        match="outside",
    ):
        transition.apply_security_code_transition_contract(
            first,
            sessions=["2025-01-03"],
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )


class _BaseAdapter:
    artifact_root_sha256 = "a" * 64
    contract_sha256 = "b" * 64

    def __init__(self, prices: dict[tuple[str, str, str], float]):
        self.prices = prices
        self.calls: list[tuple[str, str, str]] = []

    def next_open(self, symbol, trade_date, side="buy"):
        key = (str(symbol), str(trade_date), str(side))
        self.calls.append(key)
        price = self.prices[key]
        return {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": price,
            "generation_proof": {
                "trade_date": str(trade_date),
                "generation_id": f"generation-{trade_date}",
                "manifest_sha256": "c" * 64,
            },
        }


def test_transition_adapter_queries_actual_code_and_binds_resolution_proof():
    base = _BaseAdapter(
        {
            ("300114", "2025-02-14", "buy"): 72.18,
            ("302132", "2025-02-17", "sell"): 73.98,
        }
    )
    adapter = transition.SecurityCodeTransitionReplayAdapter(
        base,
        contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
    )

    before = adapter.next_open("300114", "2025-02-14", "buy")
    after = adapter.next_open("300114", "2025-02-17", "sell")

    assert base.calls == [
        ("300114", "2025-02-14", "buy"),
        ("302132", "2025-02-17", "sell"),
    ]
    assert before["generation_proof"]["resolved_ts_code"] == "300114.SZ"
    assert after["generation_proof"]["resolved_ts_code"] == "302132.SZ"
    assert (
        after["generation_proof"]["security_code_transition_contract_sha256"]
        == transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    )
    assert adapter.contract_sha256 != base.contract_sha256


def test_transition_adapter_resolves_unchanged_symbol_with_exchange_suffix():
    base = _BaseAdapter(
        {("000001", "2025-02-17", "buy"): 10.0}
    )
    adapter = transition.SecurityCodeTransitionReplayAdapter(
        base,
        contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
    )

    verdict = adapter.next_open("000001", "2025-02-17", "buy")

    assert verdict["generation_proof"]["resolved_ts_code"] == "000001.SZ"


def test_execution_resolution_rejects_source_code_mismatch():
    frame = pd.DataFrame(
        {
            "date": ["2025-02-17"],
            "source_ts_code": ["302132.SZ"],
        }
    )
    verdict = {
        "generation_proof": {
            "trade_date": "2025-02-17",
            "resolved_ts_code": "300114.SZ",
        }
    }

    with pytest.raises(
        AuditedPITDevelopmentReplayError,
        match="resolved security code",
    ):
        _assert_execution_resolution_matches_frame(
            frame=frame,
            row_index=0,
            verdict=verdict,
        )


def test_transition_evidence_remaps_actual_code_to_stable_identity():
    suspension = {
        ("302132", "2025-02-18"): [
            {
                "symbol": "302132",
                "trade_date": "2025-02-18",
                "suspend_type": "S",
            }
        ]
    }
    terminal = {
        "302132": {
            "symbol": "302132",
            "ts_code": "302132.SZ",
            "delist_date": "2025-03-03",
            "list_status": "D",
        }
    }

    mapped_suspension = (
        transition.remap_security_code_transition_suspension_evidence(
            suspension,
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )
    )
    mapped_terminal = (
        transition.remap_security_code_transition_terminal_evidence(
            terminal,
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )
    )

    assert list(mapped_suspension) == [("300114", "2025-02-18")]
    assert mapped_suspension[("300114", "2025-02-18")][0][
        "source_ts_code"
    ] == "302132.SZ"
    assert list(mapped_terminal) == ["300114"]
    assert mapped_terminal["300114"]["source_ts_code"] == "302132.SZ"


def test_transition_evidence_preserves_explicit_beijing_exchange_code():
    mapped = (
        transition.remap_security_code_transition_suspension_evidence(
            {
                ("830964", "2025-02-18"): [
                    {
                        "symbol": "830964",
                        "ts_code": "830964.BJ",
                        "trade_date": "2025-02-18",
                        "suspend_type": "S",
                    }
                ]
            },
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )
    )

    assert mapped[("830964", "2025-02-18")][0][
        "source_ts_code"
    ] == "830964.BJ"


def test_transition_evidence_rejects_code_outside_effective_interval():
    with pytest.raises(
        transition.SecurityCodeTransitionEvidenceError,
        match="effective",
    ):
        transition.remap_security_code_transition_suspension_evidence(
            {
                ("300114", "2025-02-18"): [
                    {
                        "symbol": "300114",
                        "trade_date": "2025-02-18",
                        "suspend_type": "S",
                    }
                ]
            },
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )


def test_strict_position_crosses_code_change_without_censor_or_fabricated_fill():
    sessions = [
        "2025-02-07",
        "2025-02-10",
        "2025-02-11",
        "2025-02-12",
        "2025-02-13",
        "2025-02-14",
        "2025-02-17",
    ]
    closes = [70.11, 70.90, 70.00, 70.70, 72.30, 72.18, 68.01]
    opens = [70.11, 70.12, 71.01, 69.50, 70.93, 71.89, 73.98]
    source_codes = ["300114.SZ"] * 6 + ["302132.SZ"]
    frame = pd.DataFrame(
        {
            "date": sessions,
            "open": opens,
            "high": [
                max(open_price, close)
                for open_price, close in zip(opens, closes, strict=True)
            ],
            "low": [
                min(open_price, close)
                for open_price, close in zip(opens, closes, strict=True)
            ],
            "close": closes,
            "adj_factor": [1.0] * len(sessions),
            "source_ts_code": source_codes,
        }
    )
    base = _BaseAdapter(
        {
            ("300114", "2025-02-10", "buy"): 70.12,
            ("302132", "2025-02-17", "sell"): 73.98,
        }
    )
    adapter = transition.SecurityCodeTransitionReplayAdapter(
        base,
        contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
    )

    trade, censored, event = _strict_close_stop_trade(
        adapter=adapter,
        verdict_cache={},
        frame=frame,
        symbol="300114",
        signal_index=0,
        sessions=sessions,
        session_positions={
            trade_date: index for index, trade_date in enumerate(sessions)
        },
        suspension_evidence={},
        terminal_listing_evidence={},
        hold_days=5,
        stop_loss_pct=5.0,
    )

    assert censored is None
    assert event["status"] == "candidate_built"
    assert trade is not None
    assert trade["exit_date"] == "2025-02-17"
    assert trade["exit_execution_evidence"]["generation_proof"][
        "resolved_ts_code"
    ] == "302132.SZ"
    assert trade.get("right_censored", False) is False
