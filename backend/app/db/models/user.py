"""Accounts and the refresh tokens issued to them.

Two revocation mechanisms, because they cover different things. `users.token_version` is
carried in every access token and checked on every request, so bumping it retires tokens
that are already out there — which a stateless token otherwise makes impossible. The
`refresh_tokens` rows cover the long-lived half: each is single-use, and using one revokes
it and issues its replacement.

`is_admin` ships unused — one boolean now saves a migration when an admin surface arrives.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk


def normalise_email(email: str) -> str:
    """Addresses are matched case-insensitively, so they are stored one way: lowercased.

    Done in code rather than with `citext` to avoid a Postgres extension for one column.
    """
    return email.strip().lower()


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    """Bumped on password change. Tokens carrying an older value are rejected."""

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(Base):
    """One issued refresh token. The token itself is never stored, only its SHA-256.

    `replaced_by` records the rotation chain, and that is what makes reuse detectable: a
    token that has already been exchanged should never be seen again, so if one is, the
    copy leaked and the whole chain is burned.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def is_usable(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)
