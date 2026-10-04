import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Card } from "@/components/ui/primitives";
import { formatValue, full, signedPercent } from "@/lib/format";
import type { Kpi } from "@/types/api";

/**
 * A headline figure. The change is shown with an arrow and a signed percentage in neutral
 * ink. Whether a rise is good is a judgement about the business (rising refunds are bad),
 * and the card cannot know it, so it states direction without implying approval.
 */
export function KpiCard({ kpi }: { kpi: Kpi }) {
  const delta = kpi.delta_pct;
  const Icon = delta == null || delta === 0 ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  const direction = delta == null || delta === 0 ? "unchanged" : delta > 0 ? "up" : "down";

  return (
    <Card className="p-5">
      <p className="text-sm text-ink-2">{kpi.label}</p>
      <p className="mt-2 text-3xl font-semibold tracking-tight" title={full(kpi.value)}>
        {formatValue(kpi.value, kpi.format)}
      </p>
      {delta !== undefined && (
        <p
          className="mt-2 inline-flex items-center gap-1 text-sm text-ink-2"
          aria-label={delta == null ? "no comparison available" : `${direction} ${Math.abs(delta).toFixed(1)} percent versus previous`}
        >
          <Icon className="h-4 w-4" aria-hidden />
          <span>{delta == null ? "No comparison" : signedPercent(delta)}</span>
          {delta != null && <span className="text-muted">vs previous</span>}
        </p>
      )}
    </Card>
  );
}
