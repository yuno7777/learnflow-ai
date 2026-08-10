"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { ThemeToggle } from "@/components/theme";
import { Badge, Button, Skeleton, cn } from "@/components/ui";
import { ApiError, api, tokens } from "@/lib/api";
import type { Me } from "@/lib/types";

const NAV = [
  { href: "/learn", label: "My learning" },
  { href: "/learn/results", label: "Results" },
  { href: "/learn/assistant", label: "Ask a question" },
];

/**
 * The learner surface is deliberately a separate shell, not the admin sidebar with items
 * hidden. The learner role can only reach a handful of endpoints, so showing admin
 * navigation would just be a wall of 403s.
 */
export default function LearnLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: me, isLoading, error } = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/api/v1/auth/me"),
    retry: false,
    staleTime: 60_000,
  });

  React.useEffect(() => {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      tokens.clear();
      router.replace("/login");
    }
  }, [error, router]);

  // Staff who land here get sent to the tools that actually work for them.
  React.useEffect(() => {
    if (me && me.role !== "learner") router.replace("/dashboard");
  }, [me, router]);

  function signOut() {
    const refresh = tokens.refresh;
    if (refresh) void api.post("/api/v1/auth/logout", { refresh_token: refresh }).catch(() => {});
    tokens.clear();
    router.replace("/login");
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-4xl items-center gap-4 px-4">
          <Link href="/learn" className="text-sm font-semibold tracking-tight">
            LearnFlow
          </Link>
          <nav className="flex items-center gap-1" aria-label="Learner">
            {NAV.map((item) => {
              const active =
                item.href === "/learn" ? pathname === "/learn" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-md px-2.5 py-1.5 text-sm",
                    active
                      ? "bg-primary/10 font-medium text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {isLoading ? (
              <Skeleton className="h-4 w-28" />
            ) : me ? (
              <>
                <span className="hidden text-xs font-medium sm:block">{me.user.full_name}</span>
                <Badge tone="info">learner</Badge>
              </>
            ) : null}
            <ThemeToggle />
            <Button variant="secondary" size="sm" onClick={signOut}>
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-4xl px-4 py-6">
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-8 w-56" />
            <Skeleton className="h-32" />
          </div>
        ) : (
          children
        )}
      </main>
    </div>
  );
}
