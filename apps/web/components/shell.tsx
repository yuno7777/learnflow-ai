"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { Badge, Button, Skeleton, cn } from "@/components/ui";
import { ApiError, api, tokens } from "@/lib/api";
import type { Me } from "@/lib/types";

export const NAV: Array<{ group: string; items: Array<{ href: string; label: string }> }> = [
  {
    group: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard" },
      { href: "/dashboard/analytics", label: "Analytics" },
      { href: "/dashboard/usage", label: "AI usage & cost" },
    ],
  },
  {
    group: "Knowledge",
    items: [
      { href: "/dashboard/knowledge-bases", label: "Knowledge bases" },
      { href: "/dashboard/documents", label: "Documents" },
      { href: "/dashboard/assistant", label: "AI assistant" },
    ],
  },
  {
    group: "Learning",
    items: [
      { href: "/dashboard/courses", label: "Courses" },
      { href: "/dashboard/assessments", label: "Assessments" },
      { href: "/dashboard/reviews", label: "Evaluation review" },
      { href: "/dashboard/learners", label: "Learners" },
    ],
  },
  {
    group: "Operations",
    items: [
      { href: "/dashboard/workflows", label: "Workflows" },
      { href: "/dashboard/executions", label: "Executions" },
      { href: "/dashboard/tickets", label: "Support tickets" },
      { href: "/dashboard/jobs", label: "Background jobs" },
    ],
  },
  {
    group: "Settings",
    items: [
      { href: "/dashboard/integrations", label: "Integrations" },
      { href: "/dashboard/api-keys", label: "API keys" },
      { href: "/dashboard/audit", label: "Audit log" },
      { href: "/dashboard/settings", label: "Organization" },
    ],
  },
];

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/api/v1/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: me, isLoading, error } = useMe();
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      tokens.clear();
      router.replace("/login");
    }
  }, [error, router]);

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function signOut() {
    const refresh = tokens.refresh;
    if (refresh) void api.post("/api/v1/auth/logout", { refresh_token: refresh }).catch(() => {});
    tokens.clear();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 w-60 shrink-0 overflow-y-auto border-r border-border bg-card",
          "transition-transform lg:static lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center px-4">
          <Link href="/dashboard" className="text-sm font-semibold tracking-tight">
            LearnFlow AI
          </Link>
        </div>
        <nav className="px-2 pb-6" aria-label="Main">
          {NAV.map((section) => (
            <div key={section.group} className="mb-4">
              <p className="px-2 py-1 text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                {section.group}
              </p>
              <ul>
                {section.items.map((item) => {
                  const active =
                    pathname === item.href ||
                    (item.href !== "/dashboard" && pathname.startsWith(item.href));
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={() => setMobileOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "block rounded-md px-2 py-1.5 text-sm",
                          active
                            ? "bg-primary/10 font-medium text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen((open) => !open)}
            aria-label="Toggle navigation"
          >
            ☰
          </Button>
          <button
            onClick={() => setPaletteOpen(true)}
            className="hidden h-8 flex-1 max-w-xs items-center gap-2 rounded-md border border-border px-3 text-xs text-muted-foreground hover:bg-muted sm:flex"
          >
            Search or jump to…
            <kbd className="ml-auto rounded border border-border px-1 text-2xs">⌘K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-3">
            {isLoading ? (
              <Skeleton className="h-4 w-32" />
            ) : me ? (
              <>
                <div className="hidden text-right sm:block">
                  <p className="text-xs font-medium leading-tight">{me.user.full_name}</p>
                  <p className="text-2xs leading-tight text-muted-foreground">{me.user.email}</p>
                </div>
                <Badge tone="info">{me.role.replace(/_/g, " ")}</Badge>
              </>
            ) : null}
            <Button variant="secondary" size="sm" onClick={signOut}>
              Sign out
            </Button>
          </div>
        </header>

        <main id="main" className="min-w-0 flex-1 p-4 lg:p-6">
          {children}
        </main>
      </div>

      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
    </div>
  );
}

function CommandPalette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [query, setQuery] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => inputRef.current?.focus(), []);
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const results = NAV.flatMap((section) =>
    section.items.map((item) => ({ ...item, group: section.group })),
  ).filter((item) => item.label.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-24" role="dialog" aria-modal="true">
      <button className="absolute inset-0 bg-black/40" onClick={onClose} aria-label="Close" />
      <div className="relative w-full max-w-md overflow-hidden rounded-lg border border-border bg-card shadow-lg">
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && results[0]) {
              router.push(results[0].href);
              onClose();
            }
          }}
          placeholder="Jump to…"
          aria-label="Command palette"
          className="w-full border-b border-border bg-transparent px-4 py-3 text-sm outline-none"
        />
        <ul className="max-h-72 overflow-y-auto py-1">
          {results.length === 0 && (
            <li className="px-4 py-6 text-center text-xs text-muted-foreground">No matches</li>
          )}
          {results.map((item) => (
            <li key={item.href}>
              <button
                onClick={() => {
                  router.push(item.href);
                  onClose();
                }}
                className="flex w-full items-center justify-between px-4 py-2 text-left text-sm hover:bg-muted"
              >
                {item.label}
                <span className="text-2xs text-muted-foreground">{item.group}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
