## SQL dialect: PostgreSQL

- Month bucket: `date_trunc('month', order_date)`.
- Date literal: `DATE '2026-06-01'`. Interval: `now() - INTERVAL '30 days'`.
- Conditional aggregate: `SUM(x) FILTER (WHERE cond)`.
- Safe division: `x / NULLIF(y, 0)`. Cast with `::numeric` before dividing integers.
- Percentages: `ROUND(100.0 * a / NULLIF(b, 0), 2)`.
- Quote identifiers only when they contain spaces or capitals. Alias tables (`orders o`).
- `LIMIT` goes last. Order by an alias or position (`ORDER BY 2 DESC`).
