"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { BarChart3, Database, History, Sparkles } from "lucide-react";

const NAV = [
  { href: "/analyze", label: "Analyze", icon: Sparkles },
  { href: "/history", label: "History", icon: History },
  { href: "/datasources", label: "Data sources", icon: Database },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-line bg-surface md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:border-b-0 md:border-r">
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
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
