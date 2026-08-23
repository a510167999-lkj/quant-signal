#!/usr/bin/env python3
"""Copy bounce daily LATEST.json to the VPS website. Password stays in env."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_protocol_bounce_daily import DEFAULT_LATEST_PATH  # noqa: E402


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main() -> int:
    _load_dotenv(ROOT / ".env")
    host = os.getenv("VPS_HOST", "").strip()
    user = os.getenv("VPS_USER", "ubuntu").strip()
    password = os.getenv("VPS_SSH_PASS", "").strip()
    local = ROOT / DEFAULT_LATEST_PATH
    if not host or not password:
        print("skip vps sync: VPS_HOST/VPS_SSH_PASS missing")
        return 0
    if not local.is_file():
        print("skip vps sync: local LATEST.json missing")
        return 0
    import paramiko

    remote_dir = "/home/ubuntu/quant-signal/data/research_runs/path_a_protocol_bounce_daily"
    remote = f"{remote_dir}/LATEST.json"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username=user,
        password=password,
        timeout=15,
        allow_agent=False,
        look_for_keys=False,
    )
    client.exec_command(f"mkdir -p {remote_dir}", timeout=10)
    sftp = client.open_sftp()
    sftp.put(str(local), remote)
    personal_local = ROOT / "data/personal_book/LATEST.json"
    personal_remote_dir = "/home/ubuntu/quant-signal/data/personal_book"
    if personal_local.is_file():
        client.exec_command(f"mkdir -p {personal_remote_dir}", timeout=10)
        sftp.put(str(personal_local), f"{personal_remote_dir}/LATEST.json")
        print("synced", f"{personal_remote_dir}/LATEST.json")
    sftp.close()
    client.close()
    print("synced", remote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
