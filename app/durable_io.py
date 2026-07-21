"""Small cross-platform durability primitives for atomic artifact publication."""

from __future__ import annotations

import os
import stat
from pathlib import Path


_IS_WINDOWS = os.name == "nt"


def _fsync_windows_directory(directory: Path) -> None:
    """Flush one directory handle using the native Windows file API.

    Python's ``os.open`` cannot open a directory on Windows.  ``CreateFileW``
    can do so with ``FILE_FLAG_BACKUP_SEMANTICS``; the handle must request
    ``GENERIC_WRITE`` for ``FlushFileBuffers`` to persist NTFS metadata.
    """

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = (wintypes.HANDLE,)
    flush_file_buffers.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    generic_write = 0x40000000
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    backup_semantics = 0x02000000
    invalid_handle = ctypes.c_void_p(-1).value

    ctypes.set_last_error(0)
    handle = create_file(
        str(directory),
        generic_write,
        share_read | share_write | share_delete,
        None,
        open_existing,
        backup_semantics,
        None,
    )
    if handle in (None, invalid_handle):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, ctypes.FormatError(error_code), str(directory))

    try:
        ctypes.set_last_error(0)
        if not flush_file_buffers(handle):
            error_code = ctypes.get_last_error()
            raise OSError(error_code, ctypes.FormatError(error_code), str(directory))
    finally:
        ctypes.set_last_error(0)
        closed = close_handle(handle)
        if not closed and not ctypes.get_last_error():
            raise OSError("CloseHandle failed without a Windows error code")
        if not closed:
            error_code = ctypes.get_last_error()
            raise OSError(error_code, ctypes.FormatError(error_code), str(directory))


def _fsync_posix_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(str(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(directory: str | os.PathLike[str]) -> None:
    """Persist directory metadata without swallowing durability failures."""

    path = Path(directory)
    mode = path.stat().st_mode
    if not stat.S_ISDIR(mode):
        raise NotADirectoryError(f"directory fsync target is not a directory: {path}")
    if _IS_WINDOWS:
        _fsync_windows_directory(path)
    else:
        _fsync_posix_directory(path)


def fsync_file(file_path: str | os.PathLike[str]) -> None:
    """Flush an existing regular file through a write-capable descriptor.

    Windows rejects ``os.fsync`` on the descriptor behind an ``"rb"`` file,
    even though POSIX permits it.  Opening the already-written artifact with
    ``O_RDWR`` preserves bytes while providing the access required by both
    platforms' flush implementations.
    """

    path = Path(file_path)
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode):
        raise OSError(f"file fsync target is not a regular file: {path}")
    descriptor = os.open(str(path), os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
