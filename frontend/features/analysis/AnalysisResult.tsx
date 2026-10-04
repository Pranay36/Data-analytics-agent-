"use client";

import Link from "next/link";
import { RotateCcw } from "lucide-react";
import { Badge, Button, Card, Notice, Skeleton } from "@/components/ui/primitives";
import { DashboardView } from "@/features/dashboard/DashboardView";
import { useAnalysis } from "@/lib/hooks";
import { InvestigationTimeline } from "./InvestigationTimeline";
import { ProgressStepper } from "./ProgressStepper";
import { RetrievedContextPanel, RunStatsPanel } from "./ContextAndStats";

const STOP_MESSAGES: Record<string, string> = {
  invalid_sql: "The generated SQL was rejected by the safety check, and repairs did not fix it.",
  query_failed: "The query kept failing against the data source.",
  llm_unavailable: "None of the language models were available. Free-tier limits may be exhausted; try again shortly.",
  budget_exhausted: "The run hit its model-call budget before it could finish.",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="group rounded-xl border border-line bg-surface">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium">{title}</summary>
      <div className="border-t border-line p-4">{children}</div>
    </details>
  );
}

export function AnalysisResult({ id }: { id: string }) {
  const { data: analysis, error, isLoading } = useAnalysis(id);

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (error || !analysis) {
    return (
      <Notice tone="error" title="Could not load this analysis">
        {error instanceof Error ? error.message : "Unknown error."}
      </Notice>
    );
  }

  const retry = `/analyze?q=${encodeURIComponent(analysis.question)}${
    analysis.datasource_id ? `&source=${analysis.datasource_id}` : ""
  }`;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm text-muted">{analysis.datasource_name ?? "Data source"}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">{analysis.question}</h1>
      </header>

      {(analysis.status === "queued" || analysis.status === "running") && <ProgressStepper analysis={analysis} />}

      {analysis.status === "failed" && (
        <>
          <Notice tone="error" title="This analysis did not finish">
            {analysis.error?.message ??
              STOP_MESSAGES[analysis.stop_reason ?? ""] ??
              "Something went wrong while running it."}
          </Notice>
          <Link href={retry}>
            <Button variant="ghost">
              <RotateCcw className="h-4 w-4" aria-hidden /> Try again
            </Button>
          </Link>
        </>
      )}

      {analysis.status === "completed" && analysis.stop_reason === "cannot_answer" && (
        <Notice title="This data can't answer that question">
          {analysis.findings?.summary ?? "The question refers to something not present in the data source."}
        </Notice>
      )}

      {analysis.status === "completed" && analysis.stop_reason !== "cannot_answer" && analysis.findings && (
        <Card className="p-5">
          <div className="flex items-start justify-between gap-4">
            <p className="text-base leading-relaxed text-ink">{analysis.findings.summary}</p>
            <Badge>{analysis.findings.confidence} confidence</Badge>
          </div>
          {analysis.findings.caveats.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-ink-2">
              {analysis.findings.caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {analysis.dashboard && <DashboardView dashboard={analysis.dashboard} />}

      {analysis.status !== "queued" && analysis.steps.length > 0 && (
        <div className="space-y-3">
          <Section title={`How this was worked out (${analysis.steps.length} step${analysis.steps.length === 1 ? "" : "s"})`}>
            <InvestigationTimeline steps={analysis.steps} />
          </Section>
          <Section title="What the model was given">
            <RetrievedContextPanel context={analysis.retrieved_context} />
          </Section>
          {(analysis.status === "completed" || analysis.status === "failed") && (
            <Section title="Run details">
              <RunStatsPanel analysis={analysis} />
            </Section>
          )}
        </div>
      )}
    </div>
  );
}
