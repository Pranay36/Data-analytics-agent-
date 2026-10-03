"""Registered data sources: where a customer's data lives and how to reach it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.db.models.catalog import CatalogTable


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(32))
    """postgres | clickhouse | csv | duckdb"""

    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """Non-secret connection settings. Safe to return from the API."""

    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Fernet ciphertext of the password. Decrypted only when building a connector."""

    status: Mapped[str] = mapped_column(String(24), default="pending")
    """pending | connected | error | syncing"""
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    business_context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """`as_of_date`, `currency`, free-form notes. `as_of_date` is what makes
    "last month" reproducible when the data stops at a fixed date."""

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tables: Mapped[list[CatalogTable]] = relationship(
        back_populates="data_source", cascade="all, delete-orphan"
    )

    @property
    def has_secret(self) -> bool:
        return bool(self.encrypted_secret)
