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


# Groundedness lives in `analytics` because production code uses it as well; these are
# re-exported so existing imports keep working.
from app.analytics.grounding import (  # noqa: E402
    Grounding,
    allowed_numbers,
    check_grounding,
    extract_numbers,
)

__all__ = [
    "Grounding", "RuleResult", "allowed_numbers", "check_grounding",
    "check_rules", "extract_numbers", "tables_in",
]
