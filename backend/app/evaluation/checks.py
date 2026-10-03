"""Checks on *how* an answer was reached, not just what it was.

A correct number can come from wrong SQL (it just happens to match), and wrong SQL is
the thing that will fail on the next question. So alongside comparing results this
module inspects the query itself, on its syntax tree rather than its text: did it
apply the business rule, use the right column, avoid the decoy table?

It also checks groundedness: every number the written summary quotes has to appear in
the data. A model asked to summarise results will occasionally "round" a figure into
something else or recall one it never saw, and that is exactly the failure a reader
cannot spot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

# ── SQL rules ────────────────────────────────────────────────────────────────


@dataclass
class RuleResult:
    rule: str
    ok: bool
    detail: str = ""


def _parse(sql: str, dialect: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read=dialect)
    except sqlglot.ParseError:
        return None


def _aliases(tree: exp.Expression) -> dict[str, str]:
    """alias (or bare name) -> real table name."""
    mapping: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        if not table.name:
            continue
        mapping[table.name.lower()] = table.name.lower()
        if table.alias:
            mapping[table.alias.lower()] = table.name.lower()
    return mapping


def _column_table(column: exp.Column, aliases: dict[str, str], tree: exp.Expression) -> str | None:
    """The table a column belongs to, when the SQL says so."""
    if column.table:
        return aliases.get(column.table.lower())
    # Unqualified: attributable only when the query touches a single table.
    tables = {t.name.lower() for t in tree.find_all(exp.Table) if t.name}
    return next(iter(tables)) if len(tables) == 1 else None


def _literal(node: exp.Expression | None) -> str | None:
    return node.name if isinstance(node, exp.Literal) else None


def _has_filter(tree: exp.Expression, column: str, value: str) -> bool:
    wanted_column, wanted_value = column.lower(), value.lower()

    for eq in tree.find_all(exp.EQ):
        for left, right in ((eq.left, eq.right), (eq.right, eq.left)):
            if (
                isinstance(left, exp.Column)
                and left.name.lower() == wanted_column
                and (_literal(right) or "").lower() == wanted_value
            ):
                return True

    for member in tree.find_all(exp.In):
        if (
            isinstance(member.this, exp.Column)
            and member.this.name.lower() == wanted_column
            and wanted_value in {(_literal(v) or "").lower() for v in member.expressions}
        ):
            return True
    return False


def check_rules(
    sql: str, rules: list[dict[str, Any]], dialect: str = "postgres"
) -> list[RuleResult]:
    """Evaluate each rule against the SQL's syntax tree."""
    tree = _parse(sql, dialect)
    if tree is None:
        return [RuleResult("parse", False, "the SQL could not be parsed")]

    aliases = _aliases(tree)
    columns = list(tree.find_all(exp.Column))
    results: list[RuleResult] = []

    for rule in rules:
        kind = rule["kind"]

        if kind == "filter_equals":
            label = f"filters {rule['column']} = {rule['value']!r}"
            ok = _has_filter(tree, rule["column"], rule["value"])
            results.append(RuleResult(label, ok, "" if ok else "the filter is missing"))

        elif kind == "uses_column":
            label = f"uses {rule['table']}.{rule['column']}"
            ok = any(
                c.name.lower() == rule["column"].lower()
                and _column_table(c, aliases, tree) == rule["table"].lower()
                for c in columns
            )
            results.append(RuleResult(label, ok, "" if ok else "the column was not used"))

        elif kind == "forbid_column":
            label = f"avoids {rule['table']}.{rule['column']}"
            used = any(
                c.name.lower() == rule["column"].lower()
                and _column_table(c, aliases, tree) == rule["table"].lower()
                for c in columns
            )
            results.append(
                RuleResult(label, not used, "it used the forbidden column" if used else "")
            )

        else:
            raise ValueError(f"Unknown rule kind {kind!r}")

    return results


def tables_in(sql: str, dialect: str = "postgres") -> set[str]:
    tree = _parse(sql, dialect)
    if tree is None:
        return set()
    ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias}
    return {
        t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes
    }


# ── Groundedness ─────────────────────────────────────────────────────────────

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
