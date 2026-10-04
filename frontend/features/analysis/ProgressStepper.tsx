"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Check, Loader2 } from "lucide-react";
import { Card } from "@/components/ui/primitives";
import type { Analysis } from "@/types/api";

const STAGES: { keys: string[]; label: string }[] = [
  { keys: ["loading_context"], label: "Loading data source" },
  { keys: ["retrieving_context"], label: "Retrieving context" },
  { keys: ["generating_sql", "repairing_sql"], label: "Writing SQL" },
  { keys: ["validating_sql"], label: "Checking SQL" },
  { keys: ["executing_sql"], label: "Running query" },
  { keys: ["analyzing"], label: "Analysing results" },
  { keys: ["building_dashboard"], label: "Building dashboard" },
];

function useElapsed(since: string) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
}

export function ProgressStepper({ analysis }: { analysis: Analysis }) {
  const elapsed = useElapsed(analysis.created_at);
  const current = STAGES.findIndex((s) => analysis.stage && s.keys.includes(analysis.stage));
  const queries = analysis.steps.filter((s) => s.status === "succeeded").length;
  // An investigation loops back through earlier stages, so "done" is judged from the
  // position in the current pass, and the running count says how far it has got overall.
  const investigating = queries > 1 || (queries === 1 && current >= 0 && current < 5);

  return (
    <Card className="p-5" aria-live="polite">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="text-base font-medium">
          {analysis.status === "queued" ? "Waiting to start" : "Working on it"}
        </h2>
        <span className="text-sm tabular-nums text-muted">{elapsed}s</span>
      </div>
      <ol className="space-y-2.5">
        {STAGES.map((stage, index) => {
          const done = current > index;
          const active = current === index;
          return (
            <li key={stage.label} className="flex items-center gap-3 text-sm">
              <span
                className={clsx(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
                  done && "border-accent bg-accent text-white",
                  active && "border-accent text-accent",
                  !done && !active && "border-line text-transparent",
                )}
              >
                {done ? <Check className="h-3 w-3" aria-hidden /> : active ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : null}
              </span>
              <span className={clsx(active ? "font-medium text-ink" : done ? "text-ink-2" : "text-muted")}>
                {stage.label}
                {active && <span className="sr-only"> (in progress)</span>}
              </span>
            </li>
          );
        })}
      </ol>
      {investigating && (
        <p className="mt-4 text-sm text-ink-2">
          Investigating: {queries} {queries === 1 ? "query" : "queries"} run so far.
        </p>
      )}
    </Card>
  );
}
