"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button, Card, Input, Label, Notice } from "@/components/ui/primitives";
import { safeNext, useAuth } from "@/lib/auth";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const { login, register } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isLogin = mode === "login";

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (isLogin) await login(email, password);
      else await register(email, password, fullName.trim() || undefined);
      router.replace(safeNext(params.get("next")));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setBusy(false);
    }
  };

  const other = isLogin ? "/register" : "/login";
  const next = params.get("next");
  const otherHref = next ? `${other}?next=${encodeURIComponent(next)}` : other;

  return (
    <Card className="w-full max-w-sm p-6">
      <h1 className="text-xl font-semibold tracking-tight">
        {isLogin ? "Sign in to InsightFlow" : "Create your account"}
      </h1>
      <p className="mt-1 text-sm text-ink-2">
        {isLogin ? "Ask questions about your data." : "Your analyses and usage are private to you."}
      </p>

      <form onSubmit={submit} className="mt-5 space-y-4">
        {!isLogin && (
          <div>
            <Label htmlFor="name">Name (optional)</Label>
            <Input id="name" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" />
          </div>
        )}
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
        </div>
        <div>
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            required
            minLength={isLogin ? undefined : 8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={isLogin ? "current-password" : "new-password"}
          />
          {!isLogin && <p className="mt-1 text-xs text-muted">At least 8 characters.</p>}
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" className="w-full" disabled={busy || !email || !password}>
          {busy ? "Please wait…" : isLogin ? "Sign in" : "Create account"}
        </Button>
      </form>

      <p className="mt-4 text-center text-sm text-ink-2">
        {isLogin ? "New here? " : "Already registered? "}
        <Link href={otherHref} className="text-accent underline">
          {isLogin ? "Create an account" : "Sign in"}
        </Link>
      </p>
    </Card>
  );
}
