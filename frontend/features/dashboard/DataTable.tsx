import { formatPeriod, full, humanize, isIsoDate } from "@/lib/format";
import type { Dataset } from "@/types/api";

const MAX_ROWS = 50;

function cell(value: string | number | null, type: string): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "number") return full(value);
  return type === "temporal" || isIsoDate(value) ? formatPeriod(value) : String(value);
}

export function DataTable({
  dataset,
  columns,
  caption,
}: {
  dataset: Dataset;
  columns?: string[] | null;
  caption: string;
}) {
  const shown = dataset.columns
    .map((col, index) => ({ col, index }))
    .filter(({ col }) => !columns || columns.length === 0 || columns.includes(col.name));
  const rows = dataset.rows.slice(0, MAX_ROWS);

  return (
    <div>
      <div className="max-h-96 overflow-auto rounded-lg border border-line">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 bg-surface">
            <tr>
              {shown.map(({ col }) => (
                <th
                  key={col.name}
                  scope="col"
                  className={`border-b border-line px-3 py-2 font-medium text-ink-2 ${col.type === "number" ? "text-right" : "text-left"}`}
                >
                  {humanize(col.name)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="border-b border-line last:border-0 hover:bg-wash">
                {shown.map(({ col, index }) => (
                  <td
                    key={col.name}
                    className={`px-3 py-2 ${col.type === "number" ? "text-right tabular-nums" : "text-left"}`}
                  >
                    {cell(row[index], col.type)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(dataset.row_count > MAX_ROWS || dataset.truncated) && (
        <p className="mt-2 text-xs text-muted">
          Showing {Math.min(MAX_ROWS, rows.length)} of {dataset.row_count}
          {dataset.truncated ? "+" : ""} rows.
        </p>
      )}
    </div>
  );
}
