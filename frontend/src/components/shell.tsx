"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/chat", label: "Chat" },
  { href: "/tickets", label: "My tickets" },
  { href: "/history", label: "History" },
];

/** Authenticated page frame: header + nav; redirects to /login on 401. */
export function Shell({ children, headerLeft }: { children: (user: User) => ReactNode; headerLeft?: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const router = useRouter();
  const path = usePathname();

  useEffect(() => {
    api.me().then(setUser).catch((e) => {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
    });
  }, [router]);

  if (!user) return <div className="p-6 text-muted">Loading…</div>;
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center gap-1 border-b border-border bg-card px-2 sm:px-4">
        {headerLeft}
        <span className="mr-2 hidden font-semibold sm:inline">Ocean Above</span>
        <nav className="flex flex-1 gap-1" aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href}
              className={cn("inline-flex min-h-11 items-center rounded-md px-3 text-sm hover:bg-slate-100", path.startsWith(n.href) && "bg-slate-100 font-medium")}>
              {n.label}
            </Link>
          ))}
        </nav>
        <Button variant="ghost" onClick={async () => { await api.logout().catch(() => {}); router.replace("/login"); }}>
          Sign out
        </Button>
      </header>
      <div className="min-h-0 flex-1">{children(user)}</div>
    </div>
  );
}
