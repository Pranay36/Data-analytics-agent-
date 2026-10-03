"""embedding dim 1024 and embedding model column

Revision ID: 8f1dc7ea4b67
Revises: 608fbd13e7ca
Create Date: 2026-10-04 01:04:00.078887

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = '8f1dc7ea4b67'
down_revision: Union[str, Sequence[str], None] = '608fbd13e7ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Vectors from different models are not comparable, and Postgres cannot cast a
    # 384-dimension vector to 1024. Clear them first; they are rebuilt by re-indexing.
    # Without this, the ALTER fails on any database that already holds embeddings.
    op.execute("UPDATE knowledge_chunks SET embedding = NULL")
    op.add_column('knowledge_chunks', sa.Column('embedding_model', sa.String(length=128), nullable=True))
    op.alter_column('knowledge_chunks', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=384),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=1024),
               existing_nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE knowledge_chunks SET embedding = NULL")
    op.alter_column('knowledge_chunks', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=1024),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=384),
               existing_nullable=True)
    op.drop_column('knowledge_chunks', 'embedding_model')
