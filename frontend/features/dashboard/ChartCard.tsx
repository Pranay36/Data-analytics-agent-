"use client";

import { useState } from "react";
import clsx from "clsx";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "@/components/ui/primitives";
import { compact, full, humanize } from "@/lib/format";
import type { ChartSpec, Dataset } from "@/types/api";
import { DataTable } from "./DataTable";
import { SERIES_COLORS, buildChartData, type ChartData } from "./chart-data";

const INK_MUTED = "var(--ink-muted)";
const GRID = "var(--grid)";
const AXIS = "var(--axis)";
const SURFACE = "var(--surface)";
const HEIGHT = 280;
const HORIZONTAL_BARS_AFTER = 6;

const tick = { fill: INK_MUTED, fontSize: 12 };

interface TooltipEntry {
  dataKey?: string | number;
  value?: number | string | null;
  color?: string;
  name?: string;
  fill?: string;
  stroke?: string;
  payload?: Record<string, unknown>;
}

/** Text stays in ink; a small swatch beside it carries the series identity. */
function ChartTooltip({
  active,
  payload,
  label,
  data,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  data: ChartData;
}) {
  if (!active || !payload?.length) return null;
  const heading = label ?? String(payload[0]?.payload?.__x ?? payload[0]?.name ?? "");
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-md">
      <p className="mb-1 font-medium text-ink">{heading}</p>
      {payload.map((entry, i) => {
        const key = String(entry.dataKey ?? entry.name ?? "");
        const colour = entry.fill && entry.fill !== "none" ? entry.fill : (entry.stroke ?? entry.color);
        return (
          <p key={`${key}-${i}`} className="flex items-center gap-2 text-ink-2">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: colour }} aria-hidden />
            <span>{data.labels[key] ?? humanize(key)}</span>
            <span className="ml-auto pl-4 font-medium tabular-nums text-ink">
              {typeof entry.value === "number" ? full(entry.value) : (entry.value ?? "–")}
            </span>
          </p>
        );
      })}
    </div>
  );
}

function Legend({ data }: { data: ChartData }) {
  if (data.seriesKeys.length < 2) return null; // one series: the title already names it
  return (
    <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1" aria-label="Legend">
      {data.seriesKeys.map((key, i) => (
        <li key={key} className="flex items-center gap-1.5 text-sm text-ink-2">
          <span
            className="h-2.5 w-2.5 rounded-sm"
            style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }}
            aria-hidden
          />
          {data.labels[key]}
        </li>
      ))}
    </ul>
  );
}

function BarView({ data }: { data: ChartData }) {
  const horizontal = data.rows.length > HORIZONTAL_BARS_AFTER;
  const tooltip = <Tooltip cursor={{ fill: "var(--wash)" }} content={<ChartTooltip data={data} />} />;

  return (
    <ResponsiveContainer width="100%" height={horizontal ? Math.max(HEIGHT, data.rows.length * 34) : HEIGHT}>
      <BarChart
        data={data.rows}
        layout={horizontal ? "vertical" : "horizontal"}
        barGap={2}
        margin={{ top: 8, right: 16, bottom: 4, left: horizontal ? 8 : 0 }}
      >
        <CartesianGrid stroke={GRID} vertical={horizontal} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" tick={tick} tickLine={false} axisLine={false} tickFormatter={compact} />
            <YAxis
              type="category"
              dataKey={data.xKey}
              tick={tick}
              tickLine={false}
              axisLine={{ stroke: AXIS }}
              width={120}
              tickFormatter={(v: string) => (v.length > 18 ? `${v.slice(0, 17)}…` : v)}
            />
          </>
        ) : (
          <>
            <XAxis dataKey={data.xKey} tick={tick} tickLine={false} axisLine={{ stroke: AXIS }} />
            <YAxis tick={tick} tickLine={false} axisLine={false} tickFormatter={compact} width={52} />
          </>
        )}
        {tooltip}
        {data.seriesKeys.map((key, i) => (
          <Bar
            key={key}
            dataKey={key}
            fill={SERIES_COLORS[i % SERIES_COLORS.length]}
            maxBarSize={24}
            // 4px rounded at the data end, square at the baseline.
            radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function LineView({ data, area }: { data: ChartData; area: boolean }) {
  const Chart = area ? AreaChart : LineChart;
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <Chart data={data.rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey={data.xKey} tick={tick} tickLine={false} axisLine={{ stroke: AXIS }} minTickGap={24} />
        <YAxis tick={tick} tickLine={false} axisLine={false} tickFormatter={compact} width={52} />
        <Tooltip cursor={{ stroke: AXIS }} content={<ChartTooltip data={data} />} />
        {data.seriesKeys.map((key, i) => {
          const colour = SERIES_COLORS[i % SERIES_COLORS.length];
          const shared = {
            key,
            dataKey: key,
            stroke: colour,
            strokeWidth: 2,
            strokeLinejoin: "round" as const,
            strokeLinecap: "round" as const,
            dot: false,
            // A 2px ring in the surface colour keeps the marker legible over the line.
            activeDot: { r: 5, fill: colour, stroke: SURFACE, strokeWidth: 2 },
          };
          return area ? (
            <Area {...shared} type="monotone" fill={colour} fillOpacity={0.14} />
          ) : (
            <Line {...shared} type="monotone" />
          );
        })}
      </Chart>
    </ResponsiveContainer>
  );
}

function PieView({ data }: { data: ChartData }) {
  const key = data.seriesKeys[0];
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <PieChart>
        <Tooltip content={<ChartTooltip data={data} />} />
        <Pie
          data={data.rows}
          dataKey={key}
          nameKey={data.xKey}
          innerRadius="55%"
          outerRadius="85%"
          paddingAngle={2}
          stroke={SURFACE}
          strokeWidth={2}
        >
          {data.rows.map((row, i) => (
            <Cell key={String(row[data.xKey])} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
}

/** A pie has one colour per slice rather than per series, so it needs its own legend. */
function SliceLegend({ data }: { data: ChartData }) {
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1" aria-label="Legend">
      {data.rows.map((row, i) => (
        <li key={String(row[data.xKey])} className="flex items-center gap-1.5 text-sm text-ink-2">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} aria-hidden />
          {String(row[data.xKey])}
        </li>
      ))}
    </ul>
  );
}

export function ChartCard({ spec, dataset }: { spec: ChartSpec; dataset: Dataset | undefined }) {
  const [view, setView] = useState<"chart" | "table">("chart");

  if (!dataset) return null;
  const data = buildChartData(dataset, spec);
  const empty = data.rows.length === 0 || data.seriesKeys.length === 0;

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-medium">{spec.title}</h3>
          {spec.description && <p className="mt-0.5 text-sm text-ink-2">{spec.description}</p>}
        </div>
        <div className="flex shrink-0 rounded-lg border border-line p-0.5 text-xs" role="group" aria-label="View">
          {(["chart", "table"] as const).map((v) => (
            <button
              key={v}
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={clsx(
                "rounded-md px-2.5 py-1 capitalize",
                view === v ? "bg-wash font-medium text-ink" : "text-ink-2 hover:text-ink",
              )}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {view === "table" || empty ? (
        <DataTable dataset={dataset} caption={spec.title} />
      ) : (
        <>
          {spec.type === "pie" ? null : <Legend data={data} />}
          {spec.type === "bar" && <BarView data={data} />}
          {spec.type === "line" && <LineView data={data} area={false} />}
          {spec.type === "area" && <LineView data={data} area />}
          {spec.type === "pie" && (
            <>
              <PieView data={data} />
              <SliceLegend data={data} />
            </>
          )}
        </>
      )}
    </Card>
  );
}
