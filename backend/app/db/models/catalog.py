"""What we have discovered about a data source's schema.

Re-syncing must never wipe a human-written description, so descriptions carry a
`description_source`: sync only overwrites ones it wrote itself.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk
from app.db.models.datasource import DataSource


class CatalogTable(Base, TimestampMixin):
    __tablename__ = "catalog_tables"
    __table_args__ = (
        UniqueConstraint("data_source_id", "schema_name", "table_name", name="uq_catalog_table"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    schema_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(128))
    object_type: Mapped[str] = mapped_column(String(16), default="table")

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_source: Mapped[str] = mapped_column(String(16), default="db")
    """db (from a database comment) | user (edited by hand; never overwritten)"""

    row_count_estimate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    """False once the table disappears upstream. Soft-deleted so knowledge survives."""
    is_queryable: Mapped[bool] = mapped_column(Boolean, default=True)
    """The allowlist switch: tables set false never reach the SQL guard's allowlist."""

    data_source: Mapped[DataSource] = relationship(back_populates="tables")
    columns: Mapped[list[CatalogColumn]] = relationship(
        back_populates="table", cascade="all, delete-orphan", order_by="CatalogColumn.ordinal"
    )

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class CatalogColumn(Base, TimestampMixin):
    __tablename__ = "catalog_columns"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_catalog_column"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog_tables.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    data_type: Mapped[str] = mapped_column(String(128))
    normalized_type: Mapped[str] = mapped_column(String(16))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    nullable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_source: Mapped[str] = mapped_column(String(16), default="db")

    distinct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    null_fraction: Mapped[float | None] = mapped_column(nullable=True)
    sample_values: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    is_dimension: Mapped[bool] = mapped_column(Boolean, default=False)
    """Low-cardinality text/boolean column: a candidate to drill down by."""

    table: Mapped[CatalogTable] = relationship(back_populates="columns")


class TableRelationship(Base, TimestampMixin):
    """A join path. Attached deterministically to retrieved tables, never embedded."""

    __tablename__ = "table_relationships"
    __table_args__ = (
        UniqueConstraint(
            "data_source_id", "from_table", "from_column", "to_table", "to_column",
            name="uq_table_relationship",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    from_table: Mapped[str] = mapped_column(String(256))
    from_column: Mapped[str] = mapped_column(String(128))
    to_table: Mapped[str] = mapped_column(String(256))
    to_column: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), default="fk")
    """fk (introspected) | user (declared by hand)"""
