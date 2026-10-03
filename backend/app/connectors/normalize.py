"""Map engine-specific types and values onto our own vocabulary.

Two jobs:

* `normalize_type` collapses a hundred engine type names into eight, so callers
  can ask "is this numeric?" without a per-engine lookup table.
* `normalize_value` turns driver objects into things `json.dumps` accepts.
  Results travel to the frontend as JSON and into prompts as text, so a stray
  `Decimal` or `datetime` would break both.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.connectors.types import NormalizedType

# Checked in order; the first match wins, so narrower patterns come first.
_TYPE_PATTERNS: tuple[tuple[str, NormalizedType], ...] = (
    (r"^bool", "boolean"),
    (r"bool$", "boolean"),
    (r"^(timestamp|datetime)", "timestamp"),
    (r"^date$|^date\b", "date"),
    (r"^time\b|^time$", "other"),
    (r"^(numeric|decimal|money)", "decimal"),
    (r"^(float|double|real)", "float"),
    (r"^(int|bigint|smallint|tinyint|serial|bigserial|uint)", "integer"),
    (r"^(varchar|char|text|string|uuid|enum|json|name)", "string"),
)


def normalize_type(data_type: str) -> NormalizedType:
    """Reduce an engine type name to one of our eight categories.

    Handles ClickHouse's wrappers (`Nullable(Decimal(18, 2))`,
    `LowCardinality(String)`) by unwrapping before matching.
    """
    if not data_type:
        return "other"

    candidate = data_type.strip().lower()

    # Unwrap ClickHouse-style modifiers until nothing is left to strip.
    for _ in range(5):
        unwrapped = re.sub(
            r"^(nullable|lowcardinality|array|map|tuple|simpleaggregatefunction)\s*\((.*)\)$",
            r"\2",
            candidate,
        )
        if unwrapped == candidate:
            break
        candidate = unwrapped.strip()

    # Drop precision/length: "numeric(12,2)" -> "numeric"
    candidate = re.sub(r"\s*\(.*\)$", "", candidate).strip()
    # Postgres reports arrays as "integer[]" and internally as "_int4"
    candidate = candidate.removesuffix("[]").lstrip("_")

    for pattern, normalized in _TYPE_PATTERNS:
        if re.search(pattern, candidate):
            return normalized
    return "other"


def normalize_value(value: Any) -> Any:
    """Convert a driver value into something JSON can represent."""
    if value is None or isinstance(value, bool | int | str):
        return value

    if isinstance(value, float):
        # NaN and infinity are valid floats but not valid JSON.
        return value if math.isfinite(value) else None

    if isinstance(value, Decimal):
        # Analytics tolerates float precision; JSON has no decimal type.
        return None if value.is_nan() or value.is_infinite() else float(value)

    if isinstance(value, datetime | date | time):
        return value.isoformat()

    if isinstance(value, timedelta):
        return str(value)

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, bytes | bytearray | memoryview):
        return "<binary>"

    if isinstance(value, list | tuple | set):
        return [normalize_value(item) for item in value]

    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}

    return str(value)


def normalize_row(row: tuple | list) -> list[Any]:
    return [normalize_value(value) for value in row]


def infer_normalized_type(values: list[Any]) -> NormalizedType:
    """Guess a column's type from its values.

    Needed when a driver reports result column types poorly (or not at all) —
    DuckDB and ClickHouse both do in places.
    """
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float | Decimal):
            return "float"
        if isinstance(value, datetime):
            return "timestamp"
        if isinstance(value, date):
            return "date"
        return "string"
    return "other"
