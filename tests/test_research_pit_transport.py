from __future__ import annotations

import pytest

from app.research_pit_transport import UrllibTushareTransport


class _Headers:
    def __init__(self, content_lengths: tuple[object, ...] = ()) -> None:
        self._content_lengths = list(content_lengths)

    def get_all(self, name: str, default=None):
        if name.lower() == "content-length":
            return list(self._content_lengths) or default
        return default


class _Response:
    def __init__(
        self,
        chunks: list[object],
        *,
        content_lengths: tuple[object, ...] = (),
    ) -> None:
        self.headers = _Headers(content_lengths)
        self._chunks = list(chunks)
        self.read_sizes: list[int] = []

    def read(self, size: int):
        self.read_sizes.append(size)
        if not self._chunks:
            raise AssertionError("transport read beyond the declared entity boundary")
        return self._chunks.pop(0)


def test_unique_valid_content_length_stops_without_waiting_for_eof() -> None:
    response = _Response([b"abcd"], content_lengths=(4,))

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"abcd"
    assert complete is True
    assert response.read_sizes == [4]


def test_unique_valid_content_length_loops_until_short_reads_are_filled() -> None:
    response = _Response([b"a", b"bc", b"d"], content_lengths=(4,))

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"abcd"
    assert complete is True
    assert response.read_sizes == [4, 3, 1]


def test_unique_valid_content_length_marks_early_eof_incomplete() -> None:
    response = _Response([b"ab", b""], content_lengths=(4,))

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"ab"
    assert complete is False
    assert response.read_sizes == [4, 2]


def test_unique_valid_content_length_rejects_non_bytes_chunk() -> None:
    response = _Response([b"ab", "cd"], content_lengths=(4,))

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"ab"
    assert complete is False
    assert response.read_sizes == [4, 2]


def test_unique_valid_content_length_rejects_chunk_larger_than_requested() -> None:
    response = _Response([b"abcde"], content_lengths=(4,))

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"abcde"
    assert complete is False
    assert response.read_sizes == [4]


def test_zero_content_length_completes_without_reading() -> None:
    response = _Response([], content_lengths=(0,))

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b""
    assert complete is True
    assert response.read_sizes == []


@pytest.mark.parametrize(
    "content_lengths",
    [
        (4, 4),
        (4, 5),
        ("",),
        ("invalid",),
        ("+4",),
        ("-1",),
        ("4.0",),
        (" 4",),
        ("4 ",),
        ("\t4\t",),
        (9,),
    ],
)
def test_untrusted_content_length_never_marks_entity_complete(
    content_lengths: tuple[object, ...],
) -> None:
    response = _Response([b"abcd", b""], content_lengths=content_lengths)

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"abcd"
    assert complete is False
    assert len(body) <= 8


def test_missing_content_length_preserves_eof_completion_semantics() -> None:
    response = _Response([b"ab", b"cd", b""])

    body, complete = UrllibTushareTransport._read_bounded(response, 4)

    assert body == b"abcd"
    assert complete is True
    assert len(response.read_sizes) == 3


def test_missing_content_length_remains_bounded_when_entity_is_oversized() -> None:
    response = _Response([b"abcd", b"e"])

    body, complete = UrllibTushareTransport._read_bounded(response, 4)

    assert body == b"abcd"
    assert complete is False
    assert len(body) == 4


@pytest.mark.parametrize("terminator", [None, "", bytearray()])
def test_missing_content_length_rejects_falsy_non_bytes_as_eof(terminator: object) -> None:
    response = _Response([b"ab", terminator])

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"ab"
    assert complete is False
    assert response.read_sizes == [9, 7]
