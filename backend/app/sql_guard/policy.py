"""What counts as a safe query, per dialect.

Kept as data rather than code so that supporting another engine is a new entry
in `_FUNCTION_DENYLIST`, not a change to the validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp

# Node types that must never appear *anywhere* in the tree.
#
# Checking only the statement's root is not enough. This parses as a single
# `Select`, with the destructive part nested inside a CTE:
#
#     WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x
#
# Resolved by name because sqlglot's class list varies between versions; an
# unknown name here would otherwise raise at import time.
_FORBIDDEN_NODE_NAMES = (
    # Data modification
    "Insert", "Update", "Delete", "Merge",
    # Schema modification
    "Create", "Drop", "Alter", "TruncateTable", "RenameTable",
    # Privilege and session changes
    "Grant", "Revoke", "Set", "SetItem",
    # Bulk IO and attachment
    "Copy", "Load", "Install", "Attach", "Detach", "Export",
    # `SELECT ... INTO new_table` writes despite looking like a query
    "Into",
    # sqlglot's catch-all for statements it does not model (CALL, VACUUM, …)
    "Command",
)

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    node
    for node in (getattr(exp, name, None) for name in _FORBIDDEN_NODE_NAMES)
    if node is not None and isinstance(node, type) and issubclass(node, exp.Expression)
)

# Only these may be the top of the statement.
ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = tuple(
    node
    for node in (
        getattr(exp, name, None) for name in ("Select", "Union", "Intersect", "Except", "Subquery")
    )
    if node is not None
)

# Functions that read files, open sockets, or stall the server. Dangerous
# regardless of credentials, because they escape the database entirely.
_SHARED_DENYLIST = frozenset(
    {"system", "shell", "exec", "sleep"}
)

_FUNCTION_DENYLIST: dict[str, frozenset[str]] = {
    "postgres": _SHARED_DENYLIST
    | {
        "pg_sleep", "pg_sleep_for", "pg_sleep_until",
        "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
        "lo_import", "lo_export",
        "dblink", "dblink_exec", "dblink_connect", "dblink_send_query",
        "query_to_xml", "database_to_xml",
        "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
        "pg_logical_emit_message", "pg_rotate_logfile",
    },
    "duckdb": _SHARED_DENYLIST
    | {
        "read_csv", "read_csv_auto", "sniff_csv",
        "read_parquet", "parquet_scan", "parquet_metadata",
        "read_json", "read_json_auto", "read_json_objects", "json_scan",
        "read_ndjson", "read_ndjson_auto", "read_ndjson_objects",
        "read_text", "read_blob", "glob",
        "install", "load", "attach",
    },
    "clickhouse": _SHARED_DENYLIST
    | {
        "url", "urlcluster", "file", "filecluster",
        "s3", "s3cluster", "hdfs", "hdfscluster",
        "remote", "remotesecure", "cluster", "clusterallreplicas",
        "mysql", "postgresql", "sqlite", "mongodb", "redis", "jdbc", "odbc",
        "azureblobstorage", "deltalake", "iceberg", "hudi",
        "executable", "input", "infile", "outfile",
    },
}

SUPPORTED_DIALECTS = frozenset(_FUNCTION_DENYLIST)


@dataclass(frozen=True)
class GuardPolicy:
    """Per-request safety limits."""

    dialect: str
    max_rows: int = 500
    max_sql_length: int = 10_000
    allow_select_star: bool = False
    forbidden_functions: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def for_dialect(cls, dialect: str, **overrides: object) -> GuardPolicy:
        if dialect not in _FUNCTION_DENYLIST:
            raise ValueError(
                f"Unsupported dialect {dialect!r}. Known: {', '.join(sorted(SUPPORTED_DIALECTS))}"
            )
        return cls(
            dialect=dialect,
            forbidden_functions=_FUNCTION_DENYLIST[dialect],
            **overrides,  # type: ignore[arg-type]
        )
