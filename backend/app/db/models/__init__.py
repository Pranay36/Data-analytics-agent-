"""Importing this package registers every model on `Base.metadata`.

Alembic's autogenerate only sees tables that have been imported, so anything
added here is automatically picked up by migrations.
"""

from app.db.models.analysis import Analysis, AnalysisQuery, Dashboard
from app.db.models.catalog import CatalogColumn, CatalogTable, TableRelationship
from app.db.models.datasource import DataSource
from app.db.models.evaluation import EvaluationRun
from app.db.models.knowledge import EMBEDDING_DIM, KnowledgeChunk
from app.db.models.telemetry import LlmCall
from app.db.models.user import RefreshToken, User, normalise_email

__all__ = [
    "EMBEDDING_DIM",
    "Analysis",
    "AnalysisQuery",
    "CatalogColumn",
    "CatalogTable",
    "Dashboard",
    "DataSource",
    "EvaluationRun",
    "KnowledgeChunk",
    "RefreshToken",
    "LlmCall",
    "TableRelationship",
    "User",
    "normalise_email",
]
