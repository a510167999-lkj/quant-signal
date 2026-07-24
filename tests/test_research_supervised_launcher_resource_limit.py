from __future__ import annotations

import os
import sys
import hashlib
from pathlib import Path

import pytest

from app.research_supervised_launcher import (
    LauncherError,
    _validated_job_memory_limit_bytes,
    run_resource_capped_command,
)


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "1024"])
def test_job_memory_limit_rejects_invalid_values(value):
    with pytest.raises(LauncherError, match="job memory limit is invalid"):
        _validated_job_memory_limit_bytes(value)


def test_job_memory_limit_accepts_exact_positive_bytes():
    assert _validated_job_memory_limit_bytes(10 * 1024**3) == 10 * 1024**3
    assert _validated_job_memory_limit_bytes(None) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_resource_capped_command_runs_inside_hard_job_memory_limit(tmp_path: Path):
    receipt = run_resource_capped_command(
        command=[
            sys.executable,
            "-c",
            "print('resource-cap-ok', flush=True)",
        ],
        cwd=tmp_path,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        job_memory_limit_bytes=256 * 1024**2,
    )

    assert receipt["exit_code"] == 0
    assert receipt["job_memory_limit_bytes"] == 256 * 1024**2
    assert receipt["job_memory_limit_hard"] is True
    assert receipt["child_reaped"] is True
    assert receipt["peak_job_memory_bytes"] <= receipt["job_memory_limit_bytes"]
    assert (tmp_path / "stdout.log").read_text(encoding="utf-8").strip() == (
        "resource-cap-ok"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_resource_capped_command_prevents_allocation_beyond_hard_limit(
    tmp_path: Path,
):
    receipt = run_resource_capped_command(
        command=[
            sys.executable,
            "-c",
            (
                "blocks=[]\n"
                "while True:\n"
                "    blocks.append(bytearray(16 * 1024 * 1024))\n"
            ),
        ],
        cwd=tmp_path,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        job_memory_limit_bytes=192 * 1024**2,
    )

    assert receipt["exit_code"] != 0
    assert receipt["job_memory_limit_hard"] is True
    assert receipt["child_reaped"] is True
    assert receipt["peak_job_memory_bytes"] < 256 * 1024**2


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_resource_capped_command_waits_for_entire_job_tree_before_hashing(
    tmp_path: Path,
):
    stdout_path = tmp_path / "stdout.log"
    receipt = run_resource_capped_command(
        command=[
            sys._base_executable,
            "-c",
            (
                "import subprocess, sys\n"
                "subprocess.Popen([sys._base_executable, '-c', "
                "\"import time; time.sleep(0.25); print('child-done', flush=True)\"])\n"
                "print('parent-done', flush=True)\n"
            ),
        ],
        cwd=tmp_path,
        stdout_path=stdout_path,
        stderr_path=tmp_path / "stderr.log",
        job_memory_limit_bytes=256 * 1024**2,
    )

    final_bytes = stdout_path.read_bytes()
    assert receipt["exit_code"] == 0
    assert receipt["process_tree_drained"] is True
    assert final_bytes.decode("utf-8").splitlines() == [
        "parent-done",
        "child-done",
    ]
    assert receipt["stdout"]["bytes"] == len(final_bytes)
    assert receipt["stdout"]["sha256"] == hashlib.sha256(final_bytes).hexdigest()
