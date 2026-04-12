"""Tests for P2-M3a Slice B: PrincipalStore CRUD.

Unit tests (mocked DB) + integration tests (real PostgreSQL).
"""

from __future__ import annotations

import bcrypt
import pytest

from src.auth.errors import BindingConflictError
from src.auth.store import BindingResolution, PrincipalStore
from src.memory.writer import _uuid7

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


# ---------------------------------------------------------------------------
# Integration tests (real DB)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ensure_owner_creates_principal(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    pw_hash = _hash_password("secret")

    owner = await store.ensure_owner(name="Alice", password_hash=pw_hash)
    assert owner.name == "Alice"
    assert owner.role == "owner"
    assert owner.password_hash == pw_hash
    assert owner.id  # UUIDv7 string


@pytest.mark.integration
async def test_ensure_owner_idempotent(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    pw_hash = _hash_password("secret")

    owner1 = await store.ensure_owner(name="Alice", password_hash=pw_hash)
    owner2 = await store.ensure_owner(name="Alice", password_hash=pw_hash)
    assert owner1.id == owner2.id


@pytest.mark.integration
async def test_ensure_owner_password_rotation(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    old_hash = _hash_password("old-pass")
    new_hash = _hash_password("new-pass")

    owner = await store.ensure_owner(name="Alice", password_hash=old_hash)
    assert owner.password_hash == old_hash

    updated = await store.ensure_owner(name="Alice", password_hash=new_hash)
    assert updated.id == owner.id
    assert updated.password_hash == new_hash


@pytest.mark.integration
async def test_get_owner_returns_none_when_empty(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    assert await store.get_owner() is None


@pytest.mark.integration
async def test_verify_password_correct(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    pw_hash = _hash_password("correct-horse")
    await store.ensure_owner(name="Alice", password_hash=pw_hash)

    result = await store.verify_password("correct-horse")
    assert result is not None
    assert result.role == "owner"


@pytest.mark.integration
async def test_verify_password_incorrect(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    pw_hash = _hash_password("correct-horse")
    await store.ensure_owner(name="Alice", password_hash=pw_hash)

    result = await store.verify_password("wrong-horse")
    assert result is None


@pytest.mark.integration
async def test_verify_password_no_owner(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    assert await store.verify_password("anything") is None


@pytest.mark.integration
async def test_ensure_binding_creates_new(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))

    binding = await store.ensure_binding(
        principal_id=owner.id,
        channel_type="telegram",
        channel_identity="12345",
        verified=True,
    )
    assert binding.principal_id == owner.id
    assert binding.channel_type == "telegram"
    assert binding.channel_identity == "12345"
    assert binding.verified is True


@pytest.mark.integration
async def test_ensure_binding_idempotent_same_principal(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))

    b1 = await store.ensure_binding(
        principal_id=owner.id, channel_type="webchat", channel_identity="owner", verified=True,
    )
    b2 = await store.ensure_binding(
        principal_id=owner.id, channel_type="webchat", channel_identity="owner", verified=True,
    )
    assert b1.id == b2.id


@pytest.mark.integration
async def test_ensure_binding_conflict_different_principal(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))
    other_id = str(_uuid7())

    # Create binding for owner
    await store.ensure_binding(
        principal_id=owner.id, channel_type="telegram", channel_identity="99", verified=True,
    )

    # Attempt to bind same channel identity to different principal
    with pytest.raises(BindingConflictError, match="already exists"):
        await store.ensure_binding(
            principal_id=other_id, channel_type="telegram", channel_identity="99", verified=True,
        )


@pytest.mark.integration
async def test_resolve_binding_verified(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))
    await store.ensure_binding(
        principal_id=owner.id, channel_type="telegram", channel_identity="55", verified=True,
    )

    res = await store.resolve_binding(channel_type="telegram", channel_identity="55")
    assert res == BindingResolution(principal_id=owner.id, status="verified")


@pytest.mark.integration
async def test_resolve_binding_unverified(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))
    await store.ensure_binding(
        principal_id=owner.id, channel_type="telegram", channel_identity="66", verified=False,
    )

    res = await store.resolve_binding(channel_type="telegram", channel_identity="66")
    assert res == BindingResolution(principal_id=owner.id, status="unverified")


@pytest.mark.integration
async def test_resolve_binding_not_found(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    res = await store.resolve_binding(channel_type="telegram", channel_identity="nonexistent")
    assert res == BindingResolution(principal_id=None, status="not_found")


@pytest.mark.integration
async def test_verify_binding_upgrades_unverified(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))
    await store.ensure_binding(
        principal_id=owner.id, channel_type="telegram", channel_identity="77", verified=False,
    )

    upgraded = await store.verify_binding(channel_type="telegram", channel_identity="77")
    assert upgraded is True

    # After upgrade, resolve returns verified
    res = await store.resolve_binding(channel_type="telegram", channel_identity="77")
    assert res.status == "verified"


@pytest.mark.integration
async def test_verify_binding_already_verified(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))
    await store.ensure_binding(
        principal_id=owner.id, channel_type="telegram", channel_identity="88", verified=True,
    )

    result = await store.verify_binding(channel_type="telegram", channel_identity="88")
    assert result is False  # no-op, already verified


@pytest.mark.integration
async def test_verify_binding_not_found(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    result = await store.verify_binding(channel_type="telegram", channel_identity="nonexistent")
    assert result is False


@pytest.mark.integration
async def test_get_binding(db_session_factory) -> None:
    store = PrincipalStore(db_session_factory)
    owner = await store.ensure_owner(name="Alice", password_hash=_hash_password("pw"))
    await store.ensure_binding(
        principal_id=owner.id, channel_type="webchat", channel_identity="alice", verified=True,
    )

    binding = await store.get_binding(channel_type="webchat", channel_identity="alice")
    assert binding is not None
    assert binding.principal_id == owner.id

    missing = await store.get_binding(channel_type="webchat", channel_identity="bob")
    assert missing is None


# ---------------------------------------------------------------------------
# AuthSettings unit tests
# ---------------------------------------------------------------------------

def test_auth_settings_no_auth_mode() -> None:
    from src.auth.settings import AuthSettings
    settings = AuthSettings(password_hash=None)
    assert settings.password_hash is None


def test_auth_settings_valid_bcrypt_hash() -> None:
    from src.auth.settings import AuthSettings
    h = _hash_password("test")
    settings = AuthSettings(password_hash=h)
    assert settings.password_hash == h


def test_auth_settings_rejects_non_bcrypt() -> None:
    from pydantic import ValidationError

    from src.auth.settings import AuthSettings

    with pytest.raises(ValidationError, match="bcrypt"):
        AuthSettings(password_hash="not-a-hash")


def test_auth_settings_defaults() -> None:
    from src.auth.settings import AuthSettings
    settings = AuthSettings()
    assert settings.jwt_expire_hours == 24
    assert settings.owner_name == "Owner"
    assert settings.jwt_secret is None
