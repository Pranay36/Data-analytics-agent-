"""Dashboard specification, validation and hydration."""

from app.dashboard.fallback import fallback_dashboard
from app.dashboard.hydrate import hydrate
from app.dashboard.spec import (
    ChartWidget,
    DashboardSpec,
    InsightCard,
    KPIWidget,
    TableWidget,
)
from app.dashboard.validator import Outcome, column_kind, validate_dashboard

__all__ = [
    "ChartWidget",
    "DashboardSpec",
    "InsightCard",
    "KPIWidget",
    "Outcome",
    "TableWidget",
    "column_kind",
    "fallback_dashboard",
    "hydrate",
    "validate_dashboard",
]
