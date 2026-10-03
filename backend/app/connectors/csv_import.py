"""Turn uploaded CSV files into a DuckDB database file.

Importing once, rather than reading the CSV on every query, buys three things:
type inference happens a single time, queries run against columnar storage, and
— most importantly — the connection used for *queries* can then run with file
access switched off entirely (see `duckdb_connector`). Keeping CSVs as files
would mean leaving `read_csv` available to generated SQL.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb

from app.connectors.errors import ConnectorError

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^a-z0-9_]+")
_RESERVED = {"table", "select", "from", "where", "group", "order", "join", "index", "all"}


class CsvImportError(ConnectorError):
    """The CSV could not be read — malformed, empty, or unreadable encoding."""


def table_name_from_filename(filename: str) -> str:
    """Derive a usable SQL identifier from an uploaded file name.

    `Q3 Sales (final).csv` becomes `q3_sales_final`. Names are also the only
    description the model gets for an uploaded file, so they are kept readable
    rather than hashed.
    """
    stem = Path(filename).stem.lower()
    name = _SAFE_NAME.sub("_", stem).strip("_")
    name = re.sub(r"_+", "_", name)
    if not name:
        name = "data"
    if name[0].isdigit() or name in _RESERVED:
        name = f"t_{name}"
    return name[:63]


_DELIMITERS = (",", ";", "\t", "|")


def _reject_unparsed(conn: duckdb.DuckDBPyConnection, table: str, filename: str) -> None:
    """Catch a CSV that DuckDB read without splitting into columns.

    Given a file whose rows have inconsistent field counts, DuckDB does not
    raise. It falls back to one text column holding whole lines, naming that
    column after the unsplit header — so a ragged file silently becomes a table
    with a single column called `id,name,amount`.

    That is worse than an error: the import looks successful, and the failure
    resurfaces much later as queries that cannot find their columns. A genuine
    single-column CSV has no delimiter in its header, so the two are
    distinguishable.
    """
    columns = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()

    if len(columns) == 1 and any(d in str(columns[0][0]) for d in _DELIMITERS):
        raise CsvImportError(
            f"Could not split {filename} into columns — rows appear to have "
            "inconsistent field counts. Check for unescaped delimiters or "
            "missing values."
        )


def build_duckdb_from_csvs(csv_paths: list[Path], target: Path) -> dict[str, int]:
    """Load each CSV into its own table in a new DuckDB file.

    Returns table name -> row count.

    Raises:
        CsvImportError: a file is malformed or unreadable. Nothing is kept —
            a half-loaded data source would be worse than none.
    """
    if not csv_paths:
        raise CsvImportError("No CSV files provided.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)

    loaded: dict[str, int] = {}
    conn = duckdb.connect(str(target))  # writable: this is the only import step
    try:
        for path in csv_paths:
            table = table_name_from_filename(path.name)

            # Disambiguate rather than silently overwrite a same-named table.
            suffix = 2
            while table in loaded:
                table = f"{table_name_from_filename(path.name)}_{suffix}"
                suffix += 1

            try:
                conn.execute(
                    f'CREATE TABLE "{table}" AS '
                    "SELECT * FROM read_csv_auto(?, sample_size=-1, header=true)",
                    [str(path)],
                )
            except duckdb.Error as exc:
                raise CsvImportError(
                    f"Could not read {path.name}: {str(exc).splitlines()[0]}", original=exc
                ) from exc

            count = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()
            row_count = int(count[0]) if count else 0
            if row_count == 0:
                raise CsvImportError(f"{path.name} contains no rows.")

            _reject_unparsed(conn, table, path.name)

            loaded[table] = row_count
            logger.info("imported csv", extra={"table": table, "rows": row_count})
    except Exception:
        conn.close()
        target.unlink(missing_ok=True)
        raise
    else:
        conn.close()

    return loaded
