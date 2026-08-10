from __future__ import annotations

import pytest

from app import audited_pit_factor_v3_formal_materializer_v2_authority as authority
from app import factor_v3_materializer_v2_development_registration_fixture as fixture


def test_fixture_surface_is_development_only_and_ready() -> None:
    descriptor = fixture.validate_fixture_surface()
    assert descriptor["schema"] == fixture.FIXTURE_SCHEMA
    assert descriptor["development_only"] is True
    assert descriptor["production_profile_registered"] is False
    assert descriptor["automatic_trading_eligible"] is False
    assert descriptor["formal_materialization_red_path_still_blocked"] is True
    assert fixture.is_development_registration_fixture_ready() is True
    assert set(descriptor["required_slot_names"]) == set(
        fixture.REQUIRED_REGISTRATION_SLOT_NAMES
    )
    for field, value in descriptor["safety"].items():
        assert value is False


def test_install_requires_local_research_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(fixture.DevelopmentMaterializerRegistrationError):
        fixture.install_development_registration_fixture()

    monkeypatch.setenv("VPS_RUNTIME_ROLE", "recommendation_only")
    with pytest.raises(fixture.DevelopmentMaterializerRegistrationError):
        fixture.install_development_registration_fixture()


def test_install_uninstall_is_process_local_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    if fixture.is_fixture_installed():
        fixture.uninstall_development_registration_fixture()

    assert authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
    assert fixture.production_registration_slots_closed() is True

    installed = fixture.install_development_registration_fixture()
    assert installed["installed"] is True
    assert installed["development_only"] is True
    assert fixture.is_fixture_installed() is True
    assert authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is not None
    assert (
        authority.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256 is not None
    )
    assert fixture.production_registration_slots_closed() is False

    with pytest.raises(fixture.DevelopmentMaterializerRegistrationError):
        fixture.install_development_registration_fixture()

    uninstalled = fixture.uninstall_development_registration_fixture()
    assert uninstalled["installed"] is False
    assert uninstalled["production_slots_closed"] is True
    assert authority.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
    assert fixture.production_registration_slots_closed() is True
