from src.infra.auth.roles import (
    ROLE_ADMIN,
    ROLE_CLIENT,
    ROLE_MASTER,
    is_admin_role,
    is_master_role,
    normalize_role,
)


def test_normalize_role_barber_to_master() -> None:
    assert normalize_role("barber") == ROLE_MASTER
    assert normalize_role("BARBER") == ROLE_MASTER


def test_normalize_role_defaults() -> None:
    assert normalize_role(None) == ROLE_CLIENT
    assert normalize_role("") == ROLE_CLIENT
    assert normalize_role("  ") == ROLE_CLIENT


def test_normalize_unknown_to_client() -> None:
    assert normalize_role("superuser") == ROLE_CLIENT


def test_is_admin_master() -> None:
    assert is_admin_role("admin") is True
    assert is_admin_role("master") is False
    assert is_master_role("master") is True
    assert is_master_role("client") is False


def test_parse_master_invite_payload() -> None:
    from src.app.services.master_invite_service import MasterInviteService

    s = MasterInviteService()
    assert s.parse_payload("mi_deadbeef") == "deadbeef"
    assert s.parse_payload("  mi_abc  ") == "abc"
    assert s.parse_payload(None) is None
    assert s.parse_payload("other") is None
