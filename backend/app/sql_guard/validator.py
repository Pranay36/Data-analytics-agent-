"""Validate and rewrite LLM-generated SQL before it reaches a database.

The guard parses SQL into a syntax tree and inspects it. It does not match
patterns against text, because text checks are straightforward to slip past:

    SELECT 1; DROP TABLE orders                              -- starts with SELECT
    WITH x AS (DELETE FROM orders RETURNING *) SELECT * ...  -- starts with WITH
    SELECT * FROM (SELECT id FROM orders LIMIT 5) t          -- "has a LIMIT"

All three read as harmless to a prefix or keyword check. None survives tree
inspection: the first is two statements, the second contains a `Delete` node,
and the third has no limit on its *outer* query — so it would return every row.

This is one layer of several (PROJECT_PLAN §14.1). The database connection is
also read-only, with a server-side timeout, so a bug here is not on its own
sufficient to cause damage.
"""

from __future__ import annotations

import logging

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from app.sql_guard.policy import ALLOWED_ROOTS, FORBIDDEN_NODES, GuardPolicy
from app.sql_guard.suggestions import did_you_mean

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    """Outcome of validating one statement.

    `errors` is written for the model, not for a human: each entry says what is
    wrong and what to do instead, because it is fed straight back as repair
    feedback.
    """

    ok: bool
    safe_sql: str | None = None
    errors: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    limit_applied: int | None = None

    @property
    def error_text(self) -> str:
        return " ".join(self.errors)


def _fail(*errors: str) -> ValidationResult:
    return ValidationResult(ok=False, errors=list(errors))


def _function_names(tree: exp.Expression) -> set[str]:
    """Every function called anywhere in the statement, lowercased.

    sqlglot represents functions two ways: ones it models get their own class
    (`read_csv` becomes `ReadCSV`), and ones it does not become `Anonymous`.
    Checking only `Anonymous` would miss exactly the file-reading functions we
    care most about.
    """
    names: set[str] = set()
    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            name = node.this
            if isinstance(name, str):
                names.add(name.lower())
        elif isinstance(node, exp.Func):
            names.add(node.sql_name().lower())
    return names


def _physical_tables(tree: exp.Expression) -> list[str]:
    """Real tables referenced, excluding names defined by CTEs in this query."""
    cte_names = {
        cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias
    }

    tables: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name or name.lower() in cte_names:
            continue
        qualified = f"{table.db}.{name}" if table.db else name
        if qualified not in tables:
            tables.append(qualified)
    return tables


def _matches_allowlist(table: str, allowed: set[str]) -> bool:
    """Accept `orders` against an allowlist of `public.orders`, and vice versa."""
    candidate = table.lower()
    for entry in allowed:
        entry = entry.lower()
        if candidate == entry:
            return True
        # Compare on the bare name when either side omits the schema.
        if candidate.split(".")[-1] == entry.split(".")[-1] and (
            "." not in candidate or "." not in entry
        ):
            return True
    return False


def _selects_star(tree: exp.Expression) -> bool:
    """True when the *outermost* query projects `*`.

    Only the top level matters. `count(*)` is an argument, not a projection, and
    `SELECT a FROM (SELECT * FROM t)` is bounded by the outer column list.
    """
    selects: list[exp.Select] = []
    if isinstance(tree, exp.Select):
        selects.append(tree)
    elif isinstance(tree, exp.Union | exp.Intersect | exp.Except):
        selects.extend(
            side for side in (tree.left, tree.right) if isinstance(side, exp.Select)
        )

    return any(
        isinstance(projection, exp.Star)
        or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star))
        for select in selects
        for projection in select.expressions
    )


def _apply_limit(tree: exp.Expression, max_rows: int) -> tuple[exp.Expression, int | None]:
    """Cap the outer result set, keeping a stricter limit if one is present."""
    existing = tree.args.get("limit")
    if existing is not None:
        value = existing.expression
        if isinstance(value, exp.Literal) and value.is_int and int(value.name) <= max_rows:
            return tree, None
    return tree.limit(max_rows), max_rows


def _check_columns(
    tree: exp.Expression, known_columns: dict[str, set[str]]
) -> list[str]:
    """Best-effort check of qualified columns against the catalog.

    Only columns written as `alias.column` can be checked without full name
    resolution. Unqualified columns are skipped rather than guessed at — a false
    rejection costs a repair attempt, which is worse than a missed check that
    the database will catch anyway.
    """
    lookup = {table.lower(): columns for table, columns in known_columns.items()}

    def columns_for(table_name: str) -> set[str] | None:
        name = table_name.lower()
        if name in lookup:
            return lookup[name]
        bare = name.split(".")[-1]
        for key, columns in lookup.items():
            if key.split(".")[-1] == bare:
                return columns
        return None

    # alias (or bare table name) -> the table it refers to
    sources: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        if not table.name:
            continue
        qualified = f"{table.db}.{table.name}" if table.db else table.name
        sources[table.name.lower()] = qualified
        if table.alias:
            sources[table.alias.lower()] = qualified

    cte_names = {cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias}

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for column in tree.find_all(exp.Column):
        qualifier = column.table
        if not qualifier or qualifier.lower() in cte_names:
            continue
        table_name = sources.get(qualifier.lower())
        if table_name is None:
            continue
        valid = columns_for(table_name)
        if valid is None or column.name in valid:
            continue
        if (table_name, column.name) in seen:
            continue
        seen.add((table_name, column.name))
        errors.append(
            f"Column `{qualifier}.{column.name}` does not exist on `{table_name}`."
            + did_you_mean(column.name, valid)
        )
    return errors


def validate_sql(
    sql: str,
    *,
    dialect: str,
    allowed_tables: set[str] | None = None,
    known_columns: dict[str, set[str]] | None = None,
    max_rows: int = 500,
    policy: GuardPolicy | None = None,
) -> ValidationResult:
    """Check one statement and return it rewritten with a row cap.

    Args:
        sql: the statement as generated.
        dialect: `postgres`, `clickhouse` or `duckdb`.
        allowed_tables: when given, only these tables may be referenced.
        known_columns: table -> column names, for the best-effort column check.
        max_rows: cap applied to the outer query.
        policy: overrides the per-dialect defaults; mainly for tests.

    Never raises for bad input: invalid SQL is a normal outcome that becomes
    repair feedback.
    """
    guard = policy or GuardPolicy.for_dialect(dialect, max_rows=max_rows)

    if not sql or not sql.strip():
        return _fail("Empty query.")
    if len(sql) > guard.max_sql_length:
        return _fail(f"Query is too long ({len(sql)} characters, limit {guard.max_sql_length}).")

    try:
        statements = [
            statement for statement in sqlglot.parse(sql, read=guard.dialect) if statement
        ]
    except sqlglot.ParseError as exc:
        message = str(exc).split("\n")[0]
        return _fail(f"Could not parse the SQL for {guard.dialect}: {message}")

    if not statements:
        return _fail("No statement found.")
    if len(statements) > 1:
        return _fail(
            f"Only one statement is allowed, found {len(statements)}. "
            "Combine the logic into a single SELECT."
        )

    tree = statements[0]

    if not isinstance(tree, ALLOWED_ROOTS):
        return _fail(
            f"Only read queries are allowed, but this is a {type(tree).__name__.upper()} "
            "statement. Use SELECT or WITH."
        )

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _fail(
                f"`{type(node).__name__.upper()}` is not allowed. "
                "The query must only read data."
            )

    if forbidden := _function_names(tree) & guard.forbidden_functions:
        return _fail(
            f"Function(s) not allowed: {', '.join(f'`{name}`' for name in sorted(forbidden))}. "
            "Query the tables in the schema instead."
        )

    if not guard.allow_select_star and _selects_star(tree):
        return _fail("`SELECT *` is not allowed — list the columns you need explicitly.")

    tables = _physical_tables(tree)
    if allowed_tables is not None:
        unknown = [table for table in tables if not _matches_allowlist(table, allowed_tables)]
        if unknown:
            return _fail(
                *[
                    f"Table `{table}` is not available."
                    + did_you_mean(table.split(".")[-1], allowed_tables)
                    for table in unknown
                ]
            )

    if known_columns and (column_errors := _check_columns(tree, known_columns)):
        return _fail(*column_errors)

    try:
        limited, limit_applied = _apply_limit(tree, guard.max_rows)
        safe_sql = limited.sql(dialect=guard.dialect)
    except Exception as exc:  # pragma: no cover - sqlglot regeneration failure
        logger.warning("could not regenerate SQL", extra={"error": str(exc)})
        return _fail("Could not safely rewrite the query. Simplify it and try again.")

    return ValidationResult(
        ok=True,
        safe_sql=safe_sql,
        tables=tables,
        limit_applied=limit_applied,
    )
