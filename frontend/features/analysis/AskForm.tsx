"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Button, Card, Label, Notice, Select, Textarea } from "@/components/ui/primitives";
import { useDatasources, useExamples, useStartAnalysis } from "@/lib/hooks";

export function AskForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: sources, isLoading } = useDatasources();
  const [chosenSource, setSourceId] = useState(params.get("source") ?? "");
  const [question, setQuestion] = useState(params.get("q") ?? "");
  const start = useStartAnalysis();

  const usable = (sources ?? []).filter((s) => s.status === "connected" && s.table_count > 0);
  // Until the user picks one, fall back to the first usable source.
  const sourceId = chosenSource || usable[0]?.id || "";

  const { data: examples } = useExamples(sourceId || undefined);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!question.trim() || !sourceId) return;
    start.mutate(
      { datasourceId: sourceId, question: question.trim() },
      { onSuccess: ({ id }) => router.push(`/analyses/${id}`) },
    );
  };

  if (!isLoading && usable.length === 0) {
    return (
      <Notice title="No data source ready yet">
        <Link href="/datasources" className="text-accent underline">
          Add a data source
        </Link>{" "}
        first — a Postgres database or a CSV file.
      </Notice>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Card className="space-y-4 p-5">
        <div>
          <Label htmlFor="source">Data source</Label>
          <Select id="source" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
            {usable.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="question">Your question</Label>
          <Textarea
            id="question"
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. Why did revenue drop last month?"
          />
        </div>
        {start.error && <Notice tone="error">{start.error.message}</Notice>}
        <Button type="submit" disabled={start.isPending || !question.trim() || !sourceId}>
          {start.isPending ? "Starting…" : "Analyze"}
        </Button>
      </Card>

      {examples && examples.length > 0 && (
        <div>
          <p className="mb-2 text-sm text-ink-2">Try one of these</p>
          <div className="flex flex-wrap gap-2">
            {examples.slice(0, 6).map((example) => (
              <button
                type="button"
                key={example}
                onClick={() => setQuestion(example)}
                className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-sm text-ink-2 hover:bg-wash"
              >
                {example}
              </button>
            ))}
          </div>
        </div>
      )}
    </form>
  );
}
