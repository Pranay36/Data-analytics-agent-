import { formatPeriod, humanize, isIsoDate } from "@/lib/format";
import type { ChartSpec, Dataset } from "@/types/api";

/** Categorical slots in fixed order. Colour follows the series, never its rank. */
export const SERIES_COLORS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
];

export const MAX_SERIES = 3;

export type Row = Record<string, string | number | null>;

export interface ChartData {
  rows: Row[];
  seriesKeys: string[];
  /** Display names for the series, keyed by the same keys. */
  labels: Record<string, string>;
  xKey: string;
  xIsTime: boolean;
}

/** Turns a query result into rows a chart can draw, pivoting a long result into series. */
export function buildChartData(dataset: Dataset, spec: ChartSpec): ChartData {
  const names = dataset.columns.map((c) => c.name);
  const index = (name: string) => names.indexOf(name);
  const xAt = index(spec.x);

  const xValues = dataset.rows.map((r) => r[xAt]);
  const xIsTime = xValues.length > 0 && xValues.every(isIsoDate);
  const label = (v: unknown) => (xIsTime ? formatPeriod(v) : String(v ?? ""));

  if (spec.series && index(spec.series) >= 0 && spec.y.length >= 1) {
    const seriesAt = index(spec.series);
    const valueAt = index(spec.y[0]);

    // Keep the biggest series: a seventh line is noise, and colours run out.
    const totals = new Map<string, number>();
    for (const row of dataset.rows) {
      const key = String(row[seriesAt]);
      totals.set(key, (totals.get(key) ?? 0) + Math.abs(Number(row[valueAt]) || 0));
    }
    const seriesKeys = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, MAX_SERIES).map(([k]) => k);

    const byX = new Map<string, Row>();
    for (const row of dataset.rows) {
      const key = String(row[seriesAt]);
      if (!seriesKeys.includes(key)) continue;
      const x = String(row[xAt]);
      const entry = byX.get(x) ?? { __x: label(row[xAt]) };
      entry[key] = row[valueAt] as number;
      byX.set(x, entry);
    }
    return {
      rows: [...byX.values()],
      seriesKeys,
      labels: Object.fromEntries(seriesKeys.map((k) => [k, k])),
      xKey: "__x",
      xIsTime,
    };
  }

  const seriesKeys = spec.y.filter((y) => index(y) >= 0).slice(0, MAX_SERIES);
  const rows = dataset.rows.map((row) => {
    const entry: Row = { __x: label(row[xAt]) };
    for (const key of seriesKeys) entry[key] = row[index(key)] as number | null;
    return entry;
  });
  return {
    rows,
    seriesKeys,
    labels: Object.fromEntries(seriesKeys.map((k) => [k, humanize(k)])),
    xKey: "__x",
    xIsTime,
  };
}
