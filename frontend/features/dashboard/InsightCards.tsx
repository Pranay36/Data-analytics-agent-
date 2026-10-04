import { AlertTriangle, Info, TrendingDown, TrendingUp } from "lucide-react";
import { Card } from "@/components/ui/primitives";
import type { Insight, Severity } from "@/types/api";

/** Severity is an icon plus a word, so it never depends on telling two colours apart. */
const SEVERITY: Record<Severity, { label: string; Icon: typeof Info; tone: string }> = {
  info: { label: "Note", Icon: Info, tone: "text-accent" },
  positive: { label: "Increase", Icon: TrendingUp, tone: "text-good" },
  negative: { label: "Decrease", Icon: TrendingDown, tone: "text-critical" },
  warning: { label: "Warning", Icon: AlertTriangle, tone: "text-ink-2" },
};

export function InsightCards({ insights }: { insights: Insight[] }) {
  if (insights.length === 0) return null;
  return (
    <section aria-labelledby="insights-heading">
      <h2 id="insights-heading" className="mb-3 text-lg font-semibold">
        Key findings
      </h2>
      <ul className="grid gap-3 md:grid-cols-2">
        {insights.map((insight) => {
          const { label, Icon, tone } = SEVERITY[insight.severity] ?? SEVERITY.info;
          return (
            <li key={insight.id}>
              <Card className="h-full p-4">
                <p className={`flex items-center gap-1.5 text-xs font-medium ${tone}`}>
                  <Icon className="h-3.5 w-3.5" aria-hidden />
                  {label}
                </p>
                <p className="mt-2 text-sm leading-relaxed text-ink">{insight.text}</p>
              </Card>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
