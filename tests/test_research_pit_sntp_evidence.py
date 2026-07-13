from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.research_pit_collector import PITCollectionError, SystemTrustedClock


def _exchange(*, result: str, offset: str, address: str) -> str:
    return f"""sntp_exchange {{
        result: {result}
        header: 24 (li:0 vn:4 mode:4)
       stratum: 01 (1)
        offset: 0000000000000000.0000000000000000 ({offset})
          addr: {address}
}}
"""


def _selected_success(offset: str = "0.250000000") -> str:
    block = _exchange(
        result="0 (Success)",
        offset=offset,
        address="2403:300:a16:3000::21",
    )
    return f"{block}selected:\n{block}+{float(offset):.6f} +/- 0.070000 time.apple.com\n"


def _assert_synchronized(stdout: str, *, returncode: int = 0):
    completed = SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr="" if returncode == 0 else "sntp failed after producing output",
    )

    def which(name):
        return "/usr/bin/sntp" if name == "sntp" else None

    with (
        patch("app.research_pit_collector.platform.system", return_value="Darwin"),
        patch("app.research_pit_collector.Path.exists", return_value=False),
        patch("app.research_pit_collector.shutil.which", side_effect=which),
        patch("app.research_pit_collector.subprocess.run", return_value=completed),
    ):
        return SystemTrustedClock().assert_synchronized()


@pytest.mark.parametrize("returncode", [-1, 1, 2, 127])
def test_nonzero_sntp_returncode_is_never_trusted(returncode):
    with pytest.raises(PITCollectionError, match="synchronization"):
        _assert_synchronized(_selected_success(), returncode=returncode)


def test_only_the_unique_selected_success_offset_is_attested():
    unselected_success = _exchange(
        result="0 (Success)",
        offset="0.100000000",
        address="2403:300:a16:4000::21",
    )
    failed_smaller_sample = _exchange(
        result="6 (Timeout)",
        offset="0.010000000",
        address="2403:300:a0c:4000::1e2",
    )
    selected = _exchange(
        result="0 (Success)",
        offset="0.750000000",
        address="2403:300:a16:3000::21",
    )
    stdout = (
        unselected_success
        + failed_smaller_sample
        + "selected:\n"
        + selected
        + "+0.750000 +/- 0.070000 time.apple.com\n"
    )

    evidence = _assert_synchronized(stdout)

    assert evidence["source"] == "sntp"
    assert evidence["offset_seconds"] == pytest.approx(0.75)


def test_selected_five_second_offset_is_rejected_even_with_smaller_failed_sample():
    failed_smaller_sample = _exchange(
        result="6 (Timeout)",
        offset="0.100000000",
        address="2403:300:a0c:4000::1e2",
    )
    selected = _exchange(
        result="0 (Success)",
        offset="5.000000000",
        address="2403:300:a16:3000::21",
    )
    stdout = (
        failed_smaller_sample
        + "selected:\n"
        + selected
        + "+5.000000 +/- 0.070000 time.apple.com\n"
    )

    with pytest.raises(PITCollectionError, match="synchronization"):
        _assert_synchronized(stdout)


def test_multiple_selected_blocks_are_rejected_as_ambiguous():
    first = _exchange(
        result="0 (Success)",
        offset="0.200000000",
        address="2403:300:a16:3000::21",
    )
    second = _exchange(
        result="0 (Success)",
        offset="0.300000000",
        address="2403:300:a16:4000::21",
    )
    stdout = (
        "selected:\n"
        + first
        + "+0.200000 +/- 0.070000 time.apple.com\n"
        + "selected:\n"
        + second
        + "+0.300000 +/- 0.080000 time.apple.com\n"
    )

    with pytest.raises(PITCollectionError, match="synchronization"):
        _assert_synchronized(stdout)


def test_valid_selected_plus_malformed_second_marker_is_rejected():
    stdout = _selected_success() + "selected:\nmalformed trailing evidence\n"

    with pytest.raises(PITCollectionError, match="synchronization"):
        _assert_synchronized(stdout)


def test_selected_block_with_multiple_result_lines_is_rejected():
    ambiguous = _exchange(
        result="6 (Timeout)\n        result: 0 (Success)",
        offset="0.200000000",
        address="2403:300:a16:3000::21",
    )
    stdout = "selected:\n" + ambiguous + "+0.200000 +/- 0.070000 time.apple.com\n"

    with pytest.raises(PITCollectionError, match="synchronization"):
        _assert_synchronized(stdout)


def test_current_macos_sntp_selected_output_format_is_accepted():
    unselected = _exchange(
        result="0 (Success)",
        offset="0.195026692",
        address="2403:300:a16:4000::21",
    )
    selected = _exchange(
        result="0 (Success)",
        offset="0.194460273",
        address="2403:300:a16:3000::21",
    )
    stdout = (
        "sntp: Exchange failed: Timeout\n"
        + unselected
        + "selected:\n"
        + selected
        + "+0.194460 +/- 0.070533 time.apple.com 2403:300:a16:3000::21\n"
    )

    evidence = _assert_synchronized(stdout)

    assert evidence["source"] == "sntp"
    assert evidence["server"] == "time.apple.com"
    assert evidence["synchronized"] is True
    assert evidence["offset_seconds"] == pytest.approx(0.194460273)
    assert evidence["maximum_offset_seconds"] == 1.0
