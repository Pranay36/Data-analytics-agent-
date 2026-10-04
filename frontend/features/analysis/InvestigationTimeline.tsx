import { CheckCircle2, ShieldAlert, XCircle } from "lucide-react";
import { Card } from "@/components/ui/primitives";
import { duration } from "@/lib/format";
import type { Step } from "@/types/api";

const STATUS = {
  succeeded: { label: "Ran", Icon: CheckCircle2, tone: "text-good" },
  failed: { label: "Failed", Icon: XCircle, tone: "text-critical" },
  rejected: { label: "Blocked by the safety check", Icon: ShieldAlert, tone: "text-critical" },
} as const;

function filterText(filters: Step["filters"]): string | null {
  if (!filters || filters.length === 0) return null;
  const merged = Object.assign({}, ...filters) as Record<string, string>;
  return Object.entries(merged).map(([column, value]) => `${column.split(".").pop()} = ${value}`).join(", ");
}

/** Every query the pipeline tried, including the rejected ones: what it did is inspectable. */
export function InvestigationTimeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return <p className="text-sm text-ink-2">No queries were run.</p>;

  return (
    <ol className="space-y-3">
      {steps.map((step) => {
        const { label, Icon, tone } = STATUS[step.status];
        const where = filterText(step.filters);
        return (
          <li key={step.seq}>
            <Card className="p-4">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                <span className="font-medium">
                  {step.purpose === "drilldown" ? "Drill-down" : "Query"} {step.seq}
                  {step.attempt > 1 && <span className="text-muted"> · attempt {step.attempt}</span>}
                </span>
                <span className={`inline-flex items-center gap-1 text-xs font-medium ${tone}`}>
                  <Icon className="h-3.5 w-3.5" aria-hidden />
                  {label}
                </span>
                {step.status === "succeeded" && (
                  <span className="text-xs text-muted">
                    {step.row_count ?? 0} rows · {duration(step.execution_ms)}
                  </span>
                )}
              </div>
              {step.step_question && <p className="mt-1.5 text-sm text-ink-2">{step.step_question}</p>}
              {where && <p className="mt-1 text-xs text-muted">Within {where}</p>}
              {step.error && <p className="mt-2 text-sm text-ink-2">{step.error}</p>}
              <details className="mt-3">
                <summary className="cursor-pointer text-sm text-accent">
                  {step.sql ? "SQL that ran" : "SQL that was proposed"}
                </summary>
                <pre className="mt-2 overflow-x-auto rounded-lg bg-wash p-3 font-mono text-xs leading-relaxed text-ink">
                  {step.sql ?? step.original_sql}
                </pre>
              </details>
            </Card>
          </li>
        );
      })}
    </ol>
  );
}
