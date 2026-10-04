"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { Skeleton } from "@/components/ui/primitives";
import clsx from "clsx";
import { BarChart3, Database, History, LogOut, Sparkles, UserRound } from "lucide-react";

const NAV = [
  { href: "/analyze", label: "Analyze", icon: Sparkles },
  { href: "/history", label: "History", icon: History },
  { href: "/datasources", label: "Data sources", icon: Database },
  { href: "/account", label: "Account", icon: UserRound },
];

const PUBLIC_PATHS = ["/login", "/register"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { status, user, logout } = useAuth();
  const isPublic = PUBLIC_PATHS.includes(pathname);

  useEffect(() => {
    if (status === "anon" && !isPublic) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [status, isPublic, pathname, router]);

  if (isPublic) {
    return <main className="flex min-h-screen items-center justify-center px-4 py-10">{children}</main>;
  }
  // Until the session is known, show nothing that could belong to a signed-out visitor.
  if (status !== "authed") {
    return (
      <div className="mx-auto max-w-6xl p-8" aria-busy="true">
        <Skeleton className="h-8 w-48" />
      </div>
    );
  }

  return (
    <div className="min-h-screen md:flex">
      <aside className="flex flex-col border-b border-line bg-surface md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:border-b-0 md:border-r">
        <div className="flex items-center gap-2 px-5 py-4">
          <BarChart3 className="h-5 w-5 text-accent" aria-hidden />
          <span className="text-base font-semibold tracking-tight">InsightFlow</span>
        </div>
        <nav aria-label="Main" className="flex gap-1 px-3 pb-3 md:flex-col md:pb-0">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm",
                  active ? "bg-wash font-medium text-ink" : "text-ink-2 hover:bg-wash hover:text-ink",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto hidden border-t border-line p-3 md:block">
          <p className="truncate px-3 py-1 text-xs text-muted" title={user?.email}>
            {user?.email}
          </p>
          <button
            type="button"
            onClick={() => logout().then(() => router.replace("/login"))}
            className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-ink-2 hover:bg-wash hover:text-ink"
          >
            <LogOut className="h-4 w-4" aria-hidden />
            Sign out
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
