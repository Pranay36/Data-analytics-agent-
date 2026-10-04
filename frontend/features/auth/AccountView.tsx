"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Card, Input, Label, Notice, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api-client";
import { useAuth } from "@/lib/auth";
import { compact, full } from "@/lib/format";

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const share = limit > 0 ? Math.min(used / limit, 1) : 0;
  const near = share >= 0.8;
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-ink-2">{label}</span>
        <span className="tabular-nums text-ink">
          {full(used)} <span className="text-muted">of {compact(limit)}</span>
        </span>
      </div>
      <div
        className="mt-1.5 h-2 overflow-hidden rounded-full bg-wash"
        role="meter"
        aria-label={label}
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={limit}
      >
        {/* Status is never colour alone: the figures beside it say the same thing. */}
        <div
          className={`h-full rounded-full ${near ? "bg-critical" : "bg-accent"}`}
          style={{ width: `${Math.max(share * 100, used > 0 ? 2 : 0)}%` }}
        />
      </div>
    </div>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const { logout } = useAuth();
  const router = useRouter();
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    // Every session is ended on a password change, this one included.
    onSuccess: () => logout().then(() => router.replace("/login")),
  });

  return (
    <Card className="space-y-3 p-5">
      <h2 className="text-base font-medium">Change password</h2>
      <p className="text-sm text-ink-2">You will be signed out everywhere and asked to sign in again.</p>
      <div>
        <Label htmlFor="current">Current password</Label>
        <Input id="current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
      </div>
      <div>
        <Label htmlFor="new">New password</Label>
        <Input id="new" type="password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
      </div>
      {change.error && <Notice tone="error">{change.error.message}</Notice>}
      <Button disabled={change.isPending || !current || next.length < 8} onClick={() => change.mutate()}>
        {change.isPending ? "Saving…" : "Change password"}
      </Button>
    </Card>
  );
}

export function AccountView() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const { data, isLoading, error } = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 0 });

  return (
    <div className="max-w-xl space-y-6">
      <Card className="p-5">
        <p className="text-sm text-muted">Signed in as</p>
        <p className="font-medium">{user?.email}</p>
        <Button variant="ghost" className="mt-3" onClick={() => logout().then(() => router.replace("/login"))}>
          Sign out
        </Button>
      </Card>

      <Card className="space-y-4 p-5">
        <div>
          <h2 className="text-base font-medium">Today&apos;s usage</h2>
          {data && (
            <p className="text-sm text-ink-2">
              Resets at {new Date(data.resets_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.
            </p>
          )}
        </div>
        {isLoading && <Skeleton className="h-24 w-full" />}
        {error && <Notice tone="error">{error.message}</Notice>}
        {data && (
          <>
            <Meter label="Analyses" used={data.usage_today.analyses} limit={data.limits.analyses_per_day} />
            <Meter label="Model calls" used={data.usage_today.llm_calls} limit={data.limits.llm_calls_per_day} />
            <Meter
              label="Tokens"
              used={data.usage_today.input_tokens + data.usage_today.output_tokens}
              limit={data.limits.tokens_per_day}
            />
          </>
        )}
      </Card>

      <ChangePassword />
    </div>
  );
}
