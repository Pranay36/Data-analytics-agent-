"use client";

import { Badge, Card, Notice, Skeleton } from "@/components/ui/primitives";
import { useSchema } from "@/lib/hooks";

export function SchemaBrowser({ id }: { id: string }) {
  const { data, isLoading, error } = useSchema(id);
  if (isLoading) return <Skeleton className="h-48 w-full" />;
  if (error) return <Notice tone="error">{error.message}</Notice>;
  if (!data) return null;

  return (
    <div className="space-y-4">
      {data.tables.map((table) => (
        <Card key={table.id} className="p-4">
          <div className="flex flex-wrap items-baseline gap-2">
            <h2 className="font-medium">{table.table_name}</h2>
            {table.row_count_estimate != null && (
              <span className="text-xs text-muted">~{table.row_count_estimate.toLocaleString()} rows</span>
            )}
            {!table.is_queryable && <Badge>Not queryable</Badge>}
          </div>
          {table.description && <p className="mt-1 text-sm text-ink-2">{table.description}</p>}
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="py-1 pr-4 font-medium">Column</th>
                <th className="py-1 pr-4 font-medium">Type</th>
                <th className="py-1 font-medium">Samples</th>
              </tr>
            </thead>
            <tbody>
              {table.columns.map((column) => (
                <tr key={column.name} className="border-t border-line">
                  <td className="py-1.5 pr-4 font-mono text-xs">{column.name}</td>
                  <td className="py-1.5 pr-4 text-ink-2">{column.data_type}</td>
                  <td className="py-1.5 text-xs text-muted">{column.sample_values?.slice(0, 3).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}
      {data.relationships.length > 0 && (
        <Card className="p-4">
          <h2 className="mb-2 font-medium">Relationships</h2>
          <ul className="space-y-1 font-mono text-xs text-ink-2">
            {data.relationships.map((r) => (
              <li key={`${r.from_table}.${r.from_column}`}>
                {r.from_table}.{r.from_column} → {r.to_table}.{r.to_column}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
