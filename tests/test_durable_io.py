import os

import pytest

from app import durable_io


def test_fsync_directory_dispatches_to_windows_backend(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(durable_io, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        durable_io, "_fsync_windows_directory", lambda path: calls.append(path)
    )

    durable_io.fsync_directory(tmp_path)

    assert calls == [tmp_path]


def test_fsync_directory_rejects_non_directory(tmp_path):
    target = tmp_path / "artifact.json"
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="not a directory"):
        durable_io.fsync_directory(target)


def test_posix_directory_fsync_propagates_error_and_closes(tmp_path, monkeypatch):
    closed = []
    monkeypatch.setattr(durable_io, "_IS_WINDOWS", False)
    monkeypatch.setattr(durable_io.os, "open", lambda _path, _flags: 41)

    def fail_fsync(descriptor):
        assert descriptor == 41
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(durable_io.os, "fsync", fail_fsync)
    monkeypatch.setattr(durable_io.os, "close", closed.append)

    with pytest.raises(OSError, match="simulated directory fsync failure"):
        durable_io.fsync_directory(tmp_path)

    assert closed == [41]


def test_file_fsync_requests_write_capable_descriptor_and_closes(tmp_path, monkeypatch):
    target = tmp_path / "metadata.sqlite3"
    target.write_bytes(b"sqlite fixture")
    opened = []
    synced = []
    closed = []

    def fake_open(path, flags):
        opened.append((path, flags))
        return 43

    monkeypatch.setattr(durable_io.os, "open", fake_open)
    monkeypatch.setattr(durable_io.os, "fsync", synced.append)
    monkeypatch.setattr(durable_io.os, "close", closed.append)

    durable_io.fsync_file(target)

    assert opened == [(str(target), os.O_RDWR)]
    assert synced == [43]
    assert closed == [43]


def test_file_fsync_propagates_error_and_closes(tmp_path, monkeypatch):
    target = tmp_path / "metadata.sqlite3"
    target.write_bytes(b"sqlite fixture")
    closed = []
    monkeypatch.setattr(durable_io.os, "open", lambda _path, _flags: 47)

    def fail_fsync(descriptor):
        assert descriptor == 47
        raise OSError("simulated file fsync failure")

    monkeypatch.setattr(durable_io.os, "fsync", fail_fsync)
    monkeypatch.setattr(durable_io.os, "close", closed.append)

    with pytest.raises(OSError, match="simulated file fsync failure"):
        durable_io.fsync_file(target)

    assert closed == [47]


@pytest.mark.skipif(os.name != "nt", reason="requires the native Windows file API")
def test_windows_directory_fsync_uses_real_ntfs_handle(tmp_path):
    durable_io.fsync_directory(tmp_path)
