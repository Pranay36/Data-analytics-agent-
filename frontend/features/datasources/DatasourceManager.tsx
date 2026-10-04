"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { Button, Card, Input, Label, Notice, Skeleton, StatusBadge } from "@/components/ui/primitives";
import { api } from "@/lib/api-client";
import { timeAgo } from "@/lib/format";
import { useDatasources, useInvalidateDatasources } from "@/lib/hooks";

function CsvUpload() {
  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const refresh = useInvalidateDatasources();
  const upload = useMutation({
    mutationFn: () => api.uploadCsv(name.trim(), files),
    onSuccess: () => {
      setName("");
      setFiles([]);
      refresh();
    },
  });

  return (
    <Card className="space-y-3 p-5">
      <h2 className="text-base font-medium">Upload CSV files</h2>
      <div>
        <Label htmlFor="csv-name">Name</Label>
        <Input id="csv-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Sales export" />
      </div>
      <div>
        <Label htmlFor="csv-files">Files (one table per file)</Label>
        <Input
          id="csv-files"
          type="file"
          accept=".csv"
          multiple
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        />
      </div>
      {upload.error && <Notice tone="error">{upload.error.message}</Notice>}
      <Button disabled={upload.isPending || !name.trim() || files.length === 0} onClick={() => upload.mutate()}>
        {upload.isPending ? "Uploading and indexing…" : "Upload"}
      </Button>
    </Card>
  );
}

function PostgresForm() {
  const [form, setForm] = useState({
    name: "",
    host: "localhost",
    port: "5432",
    database: "",
    username: "",
    password: "",
    schemas: "public",
  });
  const refresh = useInvalidateDatasources();
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const payload = () => ({
    type: "postgres",
    config: {
      host: form.host,
      port: Number(form.port),
      database: form.database,
      username: form.username,
      schemas: form.schemas.split(",").map((s) => s.trim()).filter(Boolean),
    },
    password: form.password || null,
  });

  const test = useMutation({ mutationFn: () => api.testConnection(payload()) });
  const create = useMutation({
    mutationFn: () => api.createDatasource({ ...payload(), name: form.name.trim() }),
    onSuccess: refresh,
  });

  return (
    <Card className="space-y-3 p-5">
      <h2 className="text-base font-medium">Connect Postgres</h2>
      <p className="text-sm text-ink-2">Use a read-only role. Queries run in place; data is never copied.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {(
          [
            ["name", "Name"],
            ["database", "Database"],
            ["host", "Host"],
            ["port", "Port"],
            ["username", "Username"],
            ["password", "Password"],
            ["schemas", "Schemas (comma separated)"],
          ] as const
        ).map(([key, label]) => (
          <div key={key}>
            <Label htmlFor={`pg-${key}`}>{label}</Label>
            <Input
              id={`pg-${key}`}
              type={key === "password" ? "password" : "text"}
              value={form[key]}
              onChange={set(key)}
              autoComplete="off"
            />
          </div>
        ))}
      </div>
      {test.data && (
        <Notice tone={test.data.ok ? "info" : "error"}>
          {test.data.ok
            ? `Connected${test.data.server_version ? ` (${test.data.server_version})` : ""}.`
            : test.data.error}
        </Notice>
      )}
      {(test.error || create.error) && <Notice tone="error">{(test.error ?? create.error)?.message}</Notice>}
      <div className="flex gap-2">
        <Button variant="ghost" disabled={test.isPending || !form.database || !form.username} onClick={() => test.mutate()}>
          {test.isPending ? "Testing…" : "Test connection"}
        </Button>
        <Button disabled={create.isPending || !form.name.trim() || !form.database || !form.username} onClick={() => create.mutate()}>
          {create.isPending ? "Connecting and indexing…" : "Connect"}
        </Button>
      </div>
    </Card>
  );
}

export function DatasourceManager() {
  const { data, isLoading, error } = useDatasources();

  return (
    <div className="space-y-8">
      <section aria-label="Connected data sources">
        {isLoading && <Skeleton className="h-24 w-full" />}
        {error && <Notice tone="error">{error.message}</Notice>}
        {data && data.length === 0 && <Notice title="No data sources yet">Add one below.</Notice>}
        <ul className="space-y-2">
          {data?.map((s) => (
            <li key={s.id}>
              <Link href={`/datasources/${s.id}`}>
                <Card className="p-4 transition-colors hover:bg-wash">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{s.name}</span>
                    <StatusBadge status={s.status} />
                  </div>
                  <p className="mt-1 text-xs text-muted">
                    {s.type} · {s.table_count} tables
                    {s.last_synced_at && ` · synced ${timeAgo(s.last_synced_at)}`}
                  </p>
                  {s.status_message && <p className="mt-1 text-xs text-ink-2">{s.status_message}</p>}
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <div className="grid gap-4 lg:grid-cols-2">
        <CsvUpload />
        <PostgresForm />
      </div>
    </div>
  );
}
