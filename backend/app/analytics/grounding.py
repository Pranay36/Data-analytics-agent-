"""Whether the numbers in a piece of text come from the data.

A model asked to summarise results will occasionally "round" a figure into something
else, or recall one it never saw, and a reader cannot spot that. Every number a text
quotes must appear in the executed results, within a small tolerance.

Lives in `analytics`, not `evaluation`, because production code needs it too: the
dashboard validator uses it to drop insight cards that quote an invented figure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(%|[KkMmBb]\b)?")
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}


def _is_year(text: str, value: float) -> bool:
    return "," not in text and "." not in text and 1900 <= value <= 2100


def extract_numbers(text: str) -> list[tuple[str, float]]:
    """Numbers a reader would take as claims about the data.

    Small bare integers ("3 levels", "top 5") and years are skipped: they structure
    the prose rather than state a finding, and checking them would only generate noise.
    """
    found: list[tuple[str, float]] = []
    for match in _NUMBER.finditer(text):
        raw, suffix = match.group(1), match.group(2)
        value = float(raw.replace(",", ""))
        if suffix and suffix.lower() in _SUFFIX:
            value *= _SUFFIX[suffix.lower()]
        elif (
            not suffix and not ("," in raw or "." in raw) and (value < 100 or _is_year(raw, value))
        ):
            continue
        found.append((match.group(0).strip(), value))
    return found


def _collect(node: Any, key: str, out: set[float]) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int | float):
        out.add(abs(float(node)))
        if "share" in key:  # stored as a fraction, quoted as a percentage
            out.add(abs(float(node)) * 100)
    elif isinstance(node, dict):
        for k, v in node.items():
            _collect(v, str(k), out)
    elif isinstance(node, list | tuple):
        for item in node:
            _collect(item, key, out)


def allowed_numbers(queries: list[Any]) -> set[float]:
    """Every figure the summary may legitimately quote: result cells and statistics."""
    allowed: set[float] = set()
    for query in queries:
        if getattr(query, "status", None) != "succeeded":
            continue
        _collect(query.rows, "", allowed)
        _collect(query.profile or {}, "", allowed)
    return allowed


@dataclass
class Grounding:
    checked: int
    ungrounded: list[str]

    @property
    def ok(self) -> bool:
        return not self.ungrounded


def check_grounding(text: str, queries: list[Any], *, tolerance: float = 0.01) -> Grounding:
    """Does every number in `text` appear in the data, within `tolerance`?"""
    allowed = allowed_numbers(queries)
    claimed = extract_numbers(text)

    def supported(value: float) -> bool:
        target = abs(value)
        return any(abs(target - known) <= max(known * tolerance, 0.011) for known in allowed)

    return Grounding(
        checked=len(claimed),
        ungrounded=[raw for raw, value in claimed if not supported(value)],
    )
