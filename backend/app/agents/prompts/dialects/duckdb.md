## SQL dialect: DuckDB

- Month bucket: `date_trunc('month', order_date)`.
- Date literal: `DATE '2026-06-01'`. Interval: `INTERVAL 30 DAY`.
- Conditional aggregate: `SUM(x) FILTER (WHERE cond)`.
- Safe division: `x / NULLIF(y, 0)`. Integer division truncates; cast to `DOUBLE` first.
- Percentages: `ROUND(100.0 * a / NULLIF(b, 0), 2)`.
- Table names are lower case. Alias tables (`orders o`).
- `LIMIT` goes last. Order by an alias or position (`ORDER BY 2 DESC`).
