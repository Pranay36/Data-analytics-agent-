import { Badge } from "@/components/ui/primitives";
import { duration } from "@/lib/format";
import type { Analysis } from "@/types/api";

const KIND: Record<string, string> = {
  table: "Table",
  definition: "Definition",
  example_query: "Example",
};

/** What retrieval handed the model. Shown so a wrong answer can be traced to its cause. */
export function RetrievedContextPanel({ context }: { context: Analysis["retrieved_context"] }) {
  if (!context) return <p className="text-sm text-ink-2">Nothing was retrieved.</p>;
  return (
    <div>
      <p className="mb-3 text-sm text-ink-2">
        Retrieval confidence: <strong className="text-ink">{context.confidence}</strong>
      </p>
      <ul className="space-y-1.5">
        {context.chunks.map((chunk) => (
          <li key={`${chunk.kind}-${chunk.title}`} className="flex flex-wrap items-center gap-2 text-sm">
            <Badge>{KIND[chunk.kind] ?? chunk.kind}</Badge>
            <span className="text-ink">{chunk.title.split(".").pop()}</span>
            <span className="tabular-nums text-muted">{chunk.score.toFixed(2)}</span>
            {chunk.reason !== "vector" && <span className="text-xs text-muted">via {chunk.reason.replace("_", " ")}</span>}
          </li>
        ))}
      </ul>
      {context.joins.length > 0 && (
        <>
          <p className="mb-1.5 mt-4 text-sm font-medium text-ink-2">Joins supplied</p>
          <ul className="space-y-1 font-mono text-xs text-ink-2">
            {context.joins.map((join) => (
              <li key={join}>{join}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function RunStatsPanel({ analysis }: { analysis: Analysis }) {
  const { stats } = analysis;
  const rows: [string, string][] = [
    ["Model calls", String(stats.llm_calls)],
    ["Tokens", `${(stats.input_tokens + stats.output_tokens).toLocaleString()}`],
    ["Total time", duration(stats.latency_ms)],
    ["Drill-down levels", String(stats.drilldown_depth)],
    ["Question type", analysis.question_type ?? "–"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
      {rows.map(([name, value]) => (
        <div key={name}>
          <dt className="text-muted">{name}</dt>
          <dd className="font-medium tabular-nums text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
