import pytest


@pytest.fixture(autouse=True)
def _explicit_local_research_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # VPS_RUNTIME_ROLE is consumed only by app.jobs. Boundary tests delete or
    # override it explicitly when exercising the fail-closed paths.
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
