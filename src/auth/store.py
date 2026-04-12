"""Principal and binding CRUD store (P2-M3a)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import bcrypt
import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.auth.errors import BindingConflictError
from src.memory.writer import _uuid7
from src.session.models import PrincipalBindingRecord, PrincipalRecord

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger()


@dataclass(frozen=True)
class BindingResolution:
    """Result of resolve_binding()."""

    principal_id: str | None
    status: str  # 'verified' | 'unverified' | 'not_found'


class PrincipalStore:
    """CRUD store for principals and channel bindings."""

    def __init__(self, db_session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._db = db_session_factory

    async def get_owner(self) -> PrincipalRecord | None:
        """Return the owner principal, or None if not created yet."""
        async with self._db() as session:
            stmt = select(PrincipalRecord).where(PrincipalRecord.role == "owner")
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def ensure_owner(self, *, name: str, password_hash: str) -> PrincipalRecord:
        """Idempotent create or update owner principal.

        - Not exists → INSERT.
        - Exists, password_hash differs → UPDATE (password rotation).
        - Exists, password_hash same → return existing.
        Handles race condition via IntegrityError retry.
        """
        async with self._db() as session:
            stmt = select(PrincipalRecord).where(PrincipalRecord.role == "owner")
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is not None:
                if existing.password_hash != password_hash:
                    existing.password_hash = password_hash
                    await session.commit()
                    await session.refresh(existing)
                    logger.info("principal_owner_password_updated", principal_id=existing.id)
                return existing

            record = PrincipalRecord(
                id=str(_uuid7()),
                name=name,
                password_hash=password_hash,
                role="owner",
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                # Race: another process created owner concurrently → re-query
                await session.rollback()
                result = await session.execute(stmt)
                return result.scalar_one()  # must exist now
            await session.refresh(record)
            logger.info("principal_owner_created", principal_id=record.id, name=name)
            return record

    async def verify_password(self, password: str) -> PrincipalRecord | None:
        """Verify password against owner's bcrypt hash. Returns principal on success."""
        owner = await self.get_owner()
        if owner is None or owner.password_hash is None:
            return None
        if bcrypt.checkpw(password.encode(), owner.password_hash.encode()):
            return owner
        return None

    async def get_binding(
        self,
        *,
        channel_type: str,
        channel_identity: str,
    ) -> PrincipalBindingRecord | None:
        """Look up a binding by channel identity."""
        async with self._db() as session:
            stmt = select(PrincipalBindingRecord).where(
                PrincipalBindingRecord.channel_type == channel_type,
                PrincipalBindingRecord.channel_identity == channel_identity,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def ensure_binding(
        self,
        *,
        principal_id: str,
        channel_type: str,
        channel_identity: str,
        verified: bool = False,
    ) -> PrincipalBindingRecord:
        """Idempotent create binding. Raises BindingConflictError if different principal."""
        async with self._db() as session:
            existing = await self._get_binding_in_session(session, channel_type, channel_identity)

            if existing is not None:
                if existing.principal_id != principal_id:
                    raise BindingConflictError(
                        f"Binding ({channel_type}, {channel_identity}) already exists "
                        f"for principal {existing.principal_id}, cannot bind to {principal_id}"
                    )
                return existing

            record = PrincipalBindingRecord(
                id=str(_uuid7()),
                principal_id=principal_id,
                channel_type=channel_type,
                channel_identity=channel_identity,
                verified=verified,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            logger.info(
                "principal_binding_created",
                principal_id=principal_id,
                channel_type=channel_type,
                verified=verified,
            )
            return record

    async def resolve_binding(
        self,
        *,
        channel_type: str,
        channel_identity: str,
    ) -> BindingResolution:
        """Look up binding and return resolution with verified/unverified/not_found status."""
        binding = await self.get_binding(
            channel_type=channel_type,
            channel_identity=channel_identity,
        )
        if binding is None:
            return BindingResolution(principal_id=None, status="not_found")
        status = "verified" if binding.verified else "unverified"
        return BindingResolution(principal_id=binding.principal_id, status=status)

    async def verify_binding(
        self,
        *,
        channel_type: str,
        channel_identity: str,
    ) -> bool:
        """Upgrade an unverified binding to verified. Returns True if updated."""
        async with self._db() as session:
            stmt = (
                update(PrincipalBindingRecord)
                .where(
                    PrincipalBindingRecord.channel_type == channel_type,
                    PrincipalBindingRecord.channel_identity == channel_identity,
                    PrincipalBindingRecord.verified.is_(False),
                )
                .values(verified=True)
                .returning(PrincipalBindingRecord.id)
            )
            result = await session.execute(stmt)
            updated = result.scalar_one_or_none() is not None
            await session.commit()
            return updated

    @staticmethod
    async def _get_binding_in_session(
        session: AsyncSession,
        channel_type: str,
        channel_identity: str,
    ) -> PrincipalBindingRecord | None:
        stmt = select(PrincipalBindingRecord).where(
            PrincipalBindingRecord.channel_type == channel_type,
            PrincipalBindingRecord.channel_identity == channel_identity,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
