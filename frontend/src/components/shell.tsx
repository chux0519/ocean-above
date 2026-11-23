"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { CloseIcon, SettingsIcon, SignOutIcon } from "@/components/icons";
import { ThemeToggle } from "@/components/theme-toggle";

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
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);
  async function signOut() { await api.logout().catch(() => {}); router.replace("/login"); }

  useEffect(() => {
    api.me().then(setUser).catch((e) => {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
    });
  }, [router]);

  if (!user) return <div className="p-6 text-ink-2" role="status">Loading…</div>;
  return (
    <div className="flex h-dvh flex-col">
      <header data-shell className="flex h-14 shrink-0 items-stretch gap-1 bg-shell px-2 text-shell-ink sm:gap-4 sm:px-4">
        {headerLeft}
        <span className="hidden items-center text-base font-bold tracking-[-0.01em] sm:inline-flex">Ocean Above</span>
        <nav className="flex flex-1 items-stretch" aria-label="Main">
          {NAV.map((n) => {
            const on = path.startsWith(n.href);
            return (
              <Link key={n.href} href={n.href} aria-current={on ? "page" : undefined}
                className={cn("inline-flex min-h-11 items-center border-b-[3px] px-3 pt-[3px] text-sm font-medium transition-colors duration-150",
                  on ? "border-white text-shell-ink" : "border-transparent text-shell-ink-2 hover:text-shell-ink")}>
                {n.label}
              </Link>
            );
          })}
        </nav>
        <ThemeToggle tone="shell" className="hidden self-center sm:inline-flex" />
        <Button variant="ghost" className="hidden self-center px-3 text-shell-ink-2 hover:bg-white/10 hover:text-shell-ink sm:inline-flex"
          onClick={signOut}>
          <SignOutIcon />Sign out
        </Button>
        <Button variant="ghost" className="self-center px-3 text-shell-ink-2 hover:bg-white/10 hover:text-shell-ink sm:hidden"
          aria-label="Settings" aria-expanded={menu} aria-controls="shell-menu" onClick={() => setMenu((m) => !m)}>
          {menu ? <CloseIcon /> : <SettingsIcon />}
        </Button>
        {menu && (
          <>
            <div className="fixed inset-0 z-30 sm:hidden" onClick={() => setMenu(false)} aria-hidden />
            <div id="shell-menu" className="enter fixed top-14 right-2 z-40 w-60 rounded-lg border border-hairline bg-surface p-3 text-ink shadow-[0_8px_24px_rgb(0_0_0/0.18)] sm:hidden">
              <p className="px-1 pb-1 text-xs font-semibold uppercase tracking-[0.06em] text-ink-2">Theme</p>
              <ThemeToggle />
              <div className="my-2 border-t border-hairline" />
              <Button variant="ghost" className="w-full justify-start px-2" onClick={signOut}><SignOutIcon />Sign out</Button>
            </div>
          </>
        )}
      </header>
      <main className="min-h-0 flex-1">{children(user)}</main>
    </div>
  );
}
