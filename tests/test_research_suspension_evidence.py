"""Independent official evidence for full-session suspension gaps."""

from __future__ import annotations

from hashlib import sha256
import json
import sqlite3

import pytest

from app import jobs
from app import research_pit_store as pit_store_module
from app.research_pit_store import PITReceiptStore, _coverage_verifier_contract_sha256

from app.research_suspension_evidence import (
    CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
    SuspensionEvidenceError,
    parse_cninfo_suspension_pdf,
)


START_URL = "https://static.cninfo.com.cn/finalpage/2023-01-12/1215580484.PDF"
RESUME_URL = "https://static.cninfo.com.cn/finalpage/2023-02-02/1215749576.PDF"


def _pdf_with_text(text: str) -> bytes:
    """Build a tiny CJK PDF with a deterministic cross-reference table."""

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


def _start_pdf(*, code: str = "300114") -> bytes:
    return _pdf_with_text(
        f"证券代码：{code} 证券简称：中航电测 公告编号：2023-001 "
        "中航电测仪器股份有限公司 关于筹划发行股份购买资产事项的停牌公告 "
        f"公司股票（证券代码：{code}）自 2023 年 1 月 12 日开市时起 开始停牌。"
    )


def _resume_pdf() -> bytes:
    return _pdf_with_text(
        "证券代码：300114 证券简称：中航电测 公告编号：2023-007 "
        "中航电测仪器股份有限公司 关于披露重组预案的一般风险提示暨公司股票复牌的公告 "
        "公司股票（证券代码：300114）将于 2023 年 2 月 2 日开市 起复牌。"
    )


def _suspended_listing_pdf() -> bytes:
    return _pdf_with_text(
        "证券代码：000670 证券简称：*ST盈方 公告编号：2020-024 "
        "盈方微电子股份有限公司 关于股票暂停上市公告 "
        "公司股票将自 2020 年 4 月 7 日起暂停上市。"
    )


def _restored_listing_pdf() -> bytes:
    return _pdf_with_text(
        "证券代码：000670 证券简称：盈方微 公告编号：2022-075 "
        "盈方微电子股份有限公司 关于公司股票恢复上市首日的提示性公告 "
        "公司股票自 2022 年 8 月 22 日起恢复上市。"
    )


def _delisting_period_pdf() -> bytes:
    return _pdf_with_text(
        "证券代码：002260 证券简称：*ST德奥 公告编号：2022-033 "
        "德奥通用航空股份有限公司 关于公司股票进入退市整理期交易的公告 "
        "公司股票于 2022 年 5 月 5 日进入退市整理期。"
    )


def test_start_notice_binds_official_pdf_to_symbol_and_effective_date():
    raw = _start_pdf()

    evidence = parse_cninfo_suspension_pdf(
        raw,
        source_url=START_URL,
        published_at="2023-01-12T00:00:00+08:00",
        expected_ts_code="300114.SZ",
        notice_role="start",
    )

    assert evidence == {
        "schema_version": CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
        "source_profile": "cninfo-official",
        "source_url": START_URL,
        "published_at": "2023-01-12T00:00:00+08:00",
        "notice_role": "start",
        "ts_code": "300114.SZ",
        "announcement_number": "2023-001",
        "effective_date": "2023-01-12",
        "raw_sha256": sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "text_sha256": evidence["text_sha256"],
        "parser_version": evidence["parser_version"],
    }
    assert len(evidence["text_sha256"]) == 64


def test_resume_notice_binds_half_open_interval_end():
    evidence = parse_cninfo_suspension_pdf(
        _resume_pdf(),
        source_url=RESUME_URL,
        published_at="2023-02-02T00:00:00+08:00",
        expected_ts_code="300114.SZ",
        notice_role="resume",
    )

    assert evidence["notice_role"] == "resume"
    assert evidence["effective_date"] == "2023-02-02"
    assert evidence["announcement_number"] == "2023-007"


def test_latest_parser_recognizes_suspended_listing_notice():
    evidence = parse_cninfo_suspension_pdf(
        _suspended_listing_pdf(),
        source_url="https://static.cninfo.com.cn/finalpage/2020-04-03/1207458442.PDF",
        published_at="2020-04-03T00:00:00+08:00",
        expected_ts_code="000670.SZ",
        notice_role="start",
    )

    assert evidence["effective_date"] == "2020-04-07"
    assert evidence["parser_version"] == "cninfo-suspension-pdf/v2"


def test_latest_parser_recognizes_restored_listing_notice():
    evidence = parse_cninfo_suspension_pdf(
        _restored_listing_pdf(),
        source_url="https://static.cninfo.com.cn/finalpage/2022-08-22/1214345659.PDF",
        published_at="2022-08-22T00:00:00+08:00",
        expected_ts_code="000670.SZ",
        notice_role="resume",
    )

    assert evidence["effective_date"] == "2022-08-22"
    assert evidence["parser_version"] == "cninfo-suspension-pdf/v2"


def test_latest_parser_recognizes_delisting_period_as_trading_resume():
    evidence = parse_cninfo_suspension_pdf(
        _delisting_period_pdf(),
        source_url="https://static.cninfo.com.cn/finalpage/2022-04-26/1213101155.PDF",
        published_at="2022-04-26T00:00:00+08:00",
        expected_ts_code="002260.SZ",
        notice_role="resume",
    )

    assert evidence["effective_date"] == "2022-05-05"
    assert evidence["parser_version"] == "cninfo-suspension-pdf/v2"


def test_v1_parser_keeps_rejecting_suspended_listing_syntax():
    with pytest.raises(SuspensionEvidenceError, match="start suspension notice"):
        parse_cninfo_suspension_pdf(
            _suspended_listing_pdf(),
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2020-04-03/1207458442.PDF"
            ),
            published_at="2020-04-03T00:00:00+08:00",
            expected_ts_code="000670.SZ",
            notice_role="start",
            parser_version="cninfo-suspension-pdf/v1",
        )


def test_v2_rejects_listing_phrase_when_official_notice_title_is_missing():
    raw = _pdf_with_text(
        "证券代码：000670 公告编号：2020-024 年度报告风险提示 "
        "公司股票将自 2020 年 4 月 7 日起暂停上市。"
    )

    with pytest.raises(SuspensionEvidenceError, match="start suspension notice"):
        parse_cninfo_suspension_pdf(
            raw,
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2020-04-03/1207458442.PDF"
            ),
            published_at="2020-04-03T00:00:00+08:00",
            expected_ts_code="000670.SZ",
            notice_role="start",
        )


def test_v2_listing_title_takes_precedence_over_historical_halt_reference():
    raw = _pdf_with_text(
        "证券代码：000670 公告编号：2020-024 "
        "盈方微电子股份有限公司 关于股票暂停上市公告 "
        "公司曾在关于重大事项的停牌公告中披露，"
        "公司股票自 2017 年 4 月 5 日开市起开始停牌。"
        "根据最新决定，公司股票将自 2020 年 4 月 7 日起暂停上市。"
    )

    evidence = parse_cninfo_suspension_pdf(
        raw,
        source_url="https://static.cninfo.com.cn/finalpage/2020-04-03/1207458442.PDF",
        published_at="2020-04-03T00:00:00+08:00",
        expected_ts_code="000670.SZ",
        notice_role="start",
    )

    assert evidence["effective_date"] == "2020-04-07"


def test_v2_rejects_report_that_only_quotes_listing_notice_later_in_body():
    raw = _pdf_with_text(
        "证券代码：000670 公告编号：2020-030 "
        "盈方微电子股份有限公司 2019 年年度报告风险提示 "
        + "本报告内容。" * 40
        + "公司曾披露《关于股票暂停上市公告》，"
        "公司股票将自 2020 年 4 月 7 日起暂停上市。"
    )

    with pytest.raises(SuspensionEvidenceError, match="start suspension notice"):
        parse_cninfo_suspension_pdf(
            raw,
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2020-04-03/1207458442.PDF"
            ),
            published_at="2020-04-03T00:00:00+08:00",
            expected_ts_code="000670.SZ",
            notice_role="start",
        )


def test_v2_rejects_report_that_quotes_listing_notice_near_document_start():
    raw = _pdf_with_text(
        "证券代码：000670 公告编号：2020-030 "
        "盈方微电子股份有限公司 2019 年年度报告风险提示 "
        "公司曾披露《关于股票暂停上市公告》，"
        "公司股票将自 2020 年 4 月 7 日起暂停上市。"
    )

    with pytest.raises(SuspensionEvidenceError, match="start suspension notice"):
        parse_cninfo_suspension_pdf(
            raw,
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2020-04-03/1207458442.PDF"
            ),
            published_at="2020-04-03T00:00:00+08:00",
            expected_ts_code="000670.SZ",
            notice_role="start",
        )


def test_v2_rejects_conflicting_listing_effective_dates():
    raw = _pdf_with_text(
        "证券代码：000670 公告编号：2020-024 "
        "盈方微电子股份有限公司 关于股票暂停上市公告 "
        "公司股票将自 2020 年 4 月 7 日起暂停上市。"
        "另一段称公司股票将自 2020 年 4 月 8 日起暂停上市。"
    )

    with pytest.raises(SuspensionEvidenceError, match="ambiguous"):
        parse_cninfo_suspension_pdf(
            raw,
            source_url=(
                "https://static.cninfo.com.cn/finalpage/2020-04-03/1207458442.PDF"
            ),
            published_at="2020-04-03T00:00:00+08:00",
            expected_ts_code="000670.SZ",
            notice_role="start",
        )


def test_parser_rejects_unknown_version():
    with pytest.raises(SuspensionEvidenceError, match="version"):
        parse_cninfo_suspension_pdf(
            _start_pdf(),
            source_url=START_URL,
            published_at="2023-01-12T00:00:00+08:00",
            expected_ts_code="300114.SZ",
            notice_role="start",
            parser_version="cninfo-suspension-pdf/v999",
        )


@pytest.mark.parametrize(
    "source_url",
    [
        "http://static.cninfo.com.cn/finalpage/2023-01-12/1215580484.PDF",
        "https://evil.example/finalpage/2023-01-12/1215580484.PDF",
        "https://static.cninfo.com.cn:443/finalpage/2023-01-12/1215580484.PDF",
        "https://static.cninfo.com.cn/finalpage/2023-01-12/1215580484.PDF?x=1",
    ],
)
def test_notice_rejects_unpinned_source_url(source_url):
    with pytest.raises(SuspensionEvidenceError, match="source URL"):
        parse_cninfo_suspension_pdf(
            _start_pdf(),
            source_url=source_url,
            published_at="2023-01-12T00:00:00+08:00",
            expected_ts_code="300114.SZ",
            notice_role="start",
        )


def test_notice_rejects_symbol_mismatch_in_pdf():
    with pytest.raises(SuspensionEvidenceError, match="symbol"):
        parse_cninfo_suspension_pdf(
            _start_pdf(code="300115"),
            source_url=START_URL,
            published_at="2023-01-12T00:00:00+08:00",
            expected_ts_code="300114.SZ",
            notice_role="start",
        )


def test_notice_rejects_source_path_date_after_published_timestamp():
    with pytest.raises(SuspensionEvidenceError, match="publication date"):
        parse_cninfo_suspension_pdf(
            _start_pdf(),
            source_url=START_URL,
            published_at="2023-01-11T23:59:59+08:00",
            expected_ts_code="300114.SZ",
            notice_role="start",
        )


def test_notice_rejects_non_pdf_bytes():
    with pytest.raises(SuspensionEvidenceError, match="PDF"):
        parse_cninfo_suspension_pdf(
            b"not a pdf",
            source_url=START_URL,
            published_at="2023-01-12T00:00:00+08:00",
            expected_ts_code="300114.SZ",
            notice_role="start",
        )


def test_cninfo_interval_cli_ingests_local_files_without_network(tmp_path, capsys):
    start_path = tmp_path / "start.pdf"
    resume_path = tmp_path / "resume.pdf"
    start_path.write_bytes(_start_pdf())
    resume_path.write_bytes(_resume_pdf())
    store_dir = tmp_path / "store"

    assert jobs.main(
        [
            "research-pit-ingest-cninfo-suspension",
            "--store-dir",
            str(store_dir),
            "--ts-code",
            "300114.SZ",
            "--start-pdf-path",
            str(start_path),
            "--start-source-url",
            START_URL,
            "--start-published-at",
            "2023-01-12T00:00:00+08:00",
            "--start-retrieved-at",
            "2026-07-13T10:00:00+08:00",
            "--resume-pdf-path",
            str(resume_path),
            "--resume-source-url",
            RESUME_URL,
            "--resume-published-at",
            "2023-02-02T00:00:00+08:00",
            "--resume-retrieved-at",
            "2026-07-13T10:00:01+08:00",
        ]
    ) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["ts_code"] == "300114.SZ"
    assert output["start_date"] == "2023-01-12"
    assert output["resume_date"] == "2023-02-02"
    with sqlite3.connect(store_dir / "metadata.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM official_notice_receipts"
        ).fetchone()[0] == 2


def test_store_replays_existing_v1_notice_receipt_with_its_recorded_parser(
    tmp_path, monkeypatch
):
    store = PITReceiptStore(str(tmp_path / "store"))
    latest_parser = pit_store_module.parse_cninfo_suspension_pdf

    def parse_as_v1(*args, **kwargs):
        kwargs["parser_version"] = "cninfo-suspension-pdf/v1"
        return latest_parser(*args, **kwargs)

    monkeypatch.setattr(
        pit_store_module, "parse_cninfo_suspension_pdf", parse_as_v1
    )
    store.ingest_cninfo_suspension_interval(
        ts_code="300114.SZ",
        start_raw_bytes=_start_pdf(),
        start_source_url=START_URL,
        start_published_at="2023-01-12T00:00:00+08:00",
        start_retrieved_at="2026-07-13T10:00:00+08:00",
        resume_raw_bytes=_resume_pdf(),
        resume_source_url=RESUME_URL,
        resume_published_at="2023-02-02T00:00:00+08:00",
        resume_retrieved_at="2026-07-13T10:00:01+08:00",
    )
    monkeypatch.setattr(
        pit_store_module, "parse_cninfo_suspension_pdf", latest_parser
    )

    with store._connect() as connection:
        receipts = list(
            connection.execute(
                "SELECT receipt_id, parser_version FROM official_notice_receipts "
                "ORDER BY notice_role"
            )
        )
        verified = [
            store._verify_official_notice_receipt_on_connection(
                connection, receipt["receipt_id"]
            )
            for receipt in receipts
        ]

    assert {receipt["parser_version"] for receipt in receipts} == {
        "cninfo-suspension-pdf/v1"
    }
    assert {receipt["parser_version"] for receipt in verified} == {
        "cninfo-suspension-pdf/v1"
    }


def test_v1_only_coverage_contract_keeps_published_artifact_identity():
    assert _coverage_verifier_contract_sha256(
        ["cninfo-suspension-pdf/v1"]
    ) == "56a2b8169fc55cbef0bfb1e744fd743b7d8e096302ab8ebc5e8aeca35ffb3e56"


def test_v2_coverage_contract_normalizes_source_line_endings(
    tmp_path, monkeypatch
):
    lf_path = tmp_path / "parser_lf.py"
    crlf_path = tmp_path / "parser_crlf.py"
    changed_path = tmp_path / "parser_changed.py"
    lf_path.write_bytes(b"def parse():\n    return 1\n")
    crlf_path.write_bytes(b"def parse():\r\n    return 1\r\n")
    changed_path.write_bytes(b"def parse():\n    return 2\n")

    monkeypatch.setattr(
        pit_store_module, "_cninfo_parser_code_path", lambda: lf_path
    )
    lf_contract = _coverage_verifier_contract_sha256(
        ["cninfo-suspension-pdf/v2"]
    )
    monkeypatch.setattr(
        pit_store_module, "_cninfo_parser_code_path", lambda: crlf_path
    )
    crlf_contract = _coverage_verifier_contract_sha256(
        ["cninfo-suspension-pdf/v2"]
    )
    monkeypatch.setattr(
        pit_store_module, "_cninfo_parser_code_path", lambda: changed_path
    )
    changed_contract = _coverage_verifier_contract_sha256(
        ["cninfo-suspension-pdf/v2"]
    )

    assert crlf_contract == lf_contract
    assert changed_contract != lf_contract


def test_v2_coverage_contract_keeps_published_artifact_identity():
    assert _coverage_verifier_contract_sha256(
        ["cninfo-suspension-pdf/v2"]
    ) == "e8dccad5a7f1fee29431230f650f5ae3470bd33393f1b6a6090f7ab4a96d9ea2"
