import type { NumberFormat } from "@/types/api";

const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
const FULL = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** Short form for tiles and axis ticks: 19,191,285.85 becomes 19.19M. */
export function compact(value: number): string {
  return COMPACT.format(value);
}

/** Exact form for tooltips and tables, so a figure can always be read to the digit. */
export function full(value: number): string {
  return FULL.format(value);
}

export function formatValue(value: number | null | undefined, format: NumberFormat = "number"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  switch (format) {
    case "percent":
      return `${FULL.format(value)}%`;
    case "compact":
    case "currency":
      return compact(value);
    default:
      return Math.abs(value) >= 100_000 ? compact(value) : full(value);
  }
}

/** A signed percentage: +12.5% or −11.4%. The real minus sign aligns with the plus. */
export function signedPercent(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${FULL.format(Math.abs(value))}%`;
}

const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** 2026-06-01T00:00:00 reads as "Jun 2026"; a date with a real day keeps the day. */
export function formatPeriod(value: unknown): string {
  if (typeof value !== "string") return String(value ?? "");
  const match = ISO.exec(value);
  if (!match) return value;
  const [, year, month, day] = match;
  const name = MONTHS[Number(month) - 1];
  return day === "01" ? `${name} ${year}` : `${Number(day)} ${name} ${year}`;
}

export function isIsoDate(value: unknown): boolean {
  return typeof value === "string" && ISO.test(value);
}

/** Column names are for machines: previous_value reads as "Previous value". */
export function humanize(name: string): string {
  const spaced = name.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
