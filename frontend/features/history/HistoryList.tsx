"use client";

import Link from "next/link";
import { Card, Notice, Skeleton, StatusBadge } from "@/components/ui/primitives";
import { duration, timeAgo } from "@/lib/format";
import { useHistory } from "@/lib/hooks";

export function HistoryList() {
  const { data, isLoading, error } = useHistory();
  if (isLoading) return <Skeleton className="h-40 w-full" />;
  if (error) return <Notice tone="error">{error.message}</Notice>;
  if (!data || data.length === 0) return <Notice title="Nothing here yet">Your analyses will be listed here.</Notice>;

  return (
    <ul className="space-y-2">
      {data.map((a) => (
        <li key={a.id}>
          <Link href={`/analyses/${a.id}`}>
            <Card className="p-4 transition-colors hover:bg-wash">
              <p className="font-medium text-ink">{a.question}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                <StatusBadge status={a.status} />
                <span>{a.datasource_name ?? "–"}</span>
                <span>{timeAgo(a.created_at)}</span>
                {a.latency_ms != null && <span>{duration(a.latency_ms)}</span>}
                <span>{a.llm_calls} model calls</span>
              </div>
            </Card>
          </Link>
        </li>
      ))}
    </ul>
  );
}
