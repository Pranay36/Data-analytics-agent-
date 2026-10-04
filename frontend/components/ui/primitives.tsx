import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";

export function Card({
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx("rounded-xl border border-line bg-surface", className)} {...rest}>
      {children}
    </div>
  );
}

export function Button({
  variant = "primary",
  className,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary"
          ? "bg-accent text-white hover:opacity-90"
          : "border border-line bg-surface text-ink hover:bg-wash",
        className,
      )}
      {...rest}
    />
  );
}

const field =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-muted";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(field, props.className)} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(field, props.className)} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(field, "resize-none", props.className)} />;
}

export function Label({ children, htmlFor }: { children: React.ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium text-ink-2">
      {children}
    </label>
  );
}

type Tone = "neutral" | "good" | "bad" | "warn" | "active";

/**
 * Status is never carried by colour alone: every badge pairs an icon with its label, so it
 * reads the same to a colour-blind reader and in a greyscale print.
 */
export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { tone: Tone; label: string; icon: React.ReactNode }> = {
    queued: { tone: "neutral", label: "Queued", icon: <CircleDashed className="h-3.5 w-3.5" /> },
    running: { tone: "active", label: "Running", icon: <Loader2 className="h-3.5 w-3.5 animate-spin" /> },
    completed: { tone: "good", label: "Completed", icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
    failed: { tone: "bad", label: "Failed", icon: <XCircle className="h-3.5 w-3.5" /> },
    connected: { tone: "good", label: "Connected", icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
    error: { tone: "bad", label: "Error", icon: <XCircle className="h-3.5 w-3.5" /> },
    syncing: { tone: "active", label: "Syncing", icon: <Loader2 className="h-3.5 w-3.5 animate-spin" /> },
    pending: { tone: "warn", label: "Pending", icon: <AlertTriangle className="h-3.5 w-3.5" /> },
  };
  const entry = map[status] ?? { tone: "neutral" as Tone, label: status, icon: null };
  const colour: Record<Tone, string> = {
    neutral: "text-ink-2",
    good: "text-good",
    bad: "text-critical",
    warn: "text-ink-2",
    active: "text-accent",
  };
  return (
    <span className={clsx("inline-flex items-center gap-1 text-xs font-medium", colour[entry.tone])}>
      {entry.icon}
      {entry.label}
    </span>
  );
}

export function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-md bg-wash px-2 py-0.5 text-xs font-medium text-ink-2">
      {children}
    </span>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "error";
  title?: string;
  children: React.ReactNode;
}) {
  const Icon = tone === "error" ? XCircle : AlertTriangle;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className="flex gap-3 rounded-xl border border-line bg-surface p-4 text-sm"
    >
      <Icon
        className={clsx("mt-0.5 h-4 w-4 shrink-0", tone === "error" ? "text-critical" : "text-muted")}
        aria-hidden
      />
      <div>
        {title && <p className="font-medium text-ink">{title}</p>}
        <div className="text-ink-2">{children}</div>
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="mb-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-ink-2">{subtitle}</p>}
    </header>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-lg bg-wash", className)} aria-hidden />;
}
