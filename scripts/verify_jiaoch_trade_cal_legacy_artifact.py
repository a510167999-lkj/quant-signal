from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


_LEGACY_MANIFEST_SHA256 = (
    "282fb91164f0618dd2509c62c396a039101656c7f53fead6c7ba6cc247675c79"
)
_LEGACY_MANIFEST_RELATIVE_PATH = (
    "trade_cal_manifest_candidates/sha256/28/"
    "282fb91164f0618dd2509c62c396a039101656c7f53fead6c7ba6cc247675c79.json"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline-verify the sealed legacy Jiaoch trade-calendar artifact."
    )
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument(
        "--publication-capability-env",
        default="JIAOCH_TRADE_CAL_PUBLICATION_CAPABILITY",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    capability = os.environ.get(args.publication_capability_env)
    if not capability:
        raise RuntimeError("legacy artifact publication capability is unavailable")
    from app.jiaoch_trade_cal_authority import verify_jiaoch_trade_cal_authority

    verified = verify_jiaoch_trade_cal_authority(
        output_root=args.artifact_root,
        authority_manifest_relative_path=_LEGACY_MANIFEST_RELATIVE_PATH,
        expected_authority_manifest_sha256=_LEGACY_MANIFEST_SHA256,
        publication_capability=capability,
    )
    print(json.dumps(verified, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
