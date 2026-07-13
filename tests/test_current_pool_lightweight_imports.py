from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module_name",
    ("app.current_pool_source", "app.current_pool_risk_source"),
)
def test_current_pool_sources_import_without_research_heavy_dependencies(
    module_name: str,
) -> None:
    script = r"""
import importlib
import importlib.abc
import sys

blocked = {"pandas", "pypdf"}


class BlockHeavyResearchImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in blocked:
            raise ModuleNotFoundError(f"blocked heavy dependency: {fullname}", name=fullname)
        return None


sys.meta_path.insert(0, BlockHeavyResearchImports())
importlib.import_module(sys.argv[1])
loaded = {name.split(".", 1)[0] for name in sys.modules}
assert blocked.isdisjoint(loaded), sorted(blocked & loaded)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, module_name],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
