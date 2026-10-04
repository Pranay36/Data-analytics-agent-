import { Card } from "@/components/ui/primitives";
import type { Dashboard } from "@/types/api";
import { ChartCard } from "./ChartCard";
import { DataTable } from "./DataTable";
import { InsightCards } from "./InsightCards";
import { KpiCard } from "./KpiCard";

/**
 * Renders a dashboard spec with a fixed set of components. The spec names widgets and the
 * data they read; nothing here interprets free-form instructions, which is why a model's
 * output cannot change what the page does.
 */
export function DashboardView({ dashboard }: { dashboard: Dashboard }) {
  const { spec, kpis, datasets } = dashboard;
  const dataset = (seq: number) => datasets[String(seq)];

  return (
    <div className="space-y-8">
      {kpis.length > 0 && (
        <section aria-label="Headline figures" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {kpis.map((kpi) => (
            <KpiCard key={kpi.id} kpi={kpi} />
          ))}
        </section>
      )}

      {spec.charts.length > 0 && (
        <section aria-label="Charts" className="grid gap-4 lg:grid-cols-2">
          {spec.charts.map((chart) => (
            <ChartCard key={chart.id} spec={chart} dataset={dataset(chart.query_seq)} />
          ))}
        </section>
      )}

      <InsightCards insights={spec.insights} />

      {spec.tables.map((table) => {
        const data = dataset(table.query_seq);
        if (!data) return null;
        return (
          <section key={table.id} aria-label={table.title}>
            <h2 className="mb-3 text-lg font-semibold">{table.title}</h2>
            <Card className="p-4">
              <DataTable dataset={data} columns={table.columns} caption={table.title} />
            </Card>
          </section>
        );
      })}
    </div>
  );
}
