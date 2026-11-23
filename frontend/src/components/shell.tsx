"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import type { Me } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ChatIcon, CloseIcon, HistoryIcon, MenuIcon, SignOutIcon, TicketIcon, UpDownIcon } from "@/components/icons";
import { ThemeToggle } from "@/components/theme-toggle";

const NAV = [
  { href: "/chat", label: "Chat", Icon: ChatIcon },
  { href: "/tickets", label: "My tickets", Icon: TicketIcon },
  { href: "/history", label: "History", Icon: HistoryIcon },
];

const PROVIDER: Record<Me["auth_provider"], string> = { google: "Google account", invite: "Invite code", dev: "Local account" };

/** "3 of 50 messages today" — both numbers straight from /api/me (no formatting or arithmetic here). */
export function usageText(u: Me["usage"]) {
  return `${u.messages_today} of ${u.daily_limit} messages today`;
}

/** Account menu content: identity, usage, theme, sign out, legal. */
export function AccountPanel({ user, onSignOut }: { user: Me; onSignOut: () => void }) {
  return (
    <>
      <div className="px-2 pt-1 pb-2">
        <p className="truncate font-medium" data-testid="account-name">{user.display_name}</p>
        <p className="truncate text-sm text-ink-2">{user.email ?? PROVIDER[user.auth_provider]}</p>
        <p className="mt-2 text-sm text-ink-2" data-testid="usage">{usageText(user.usage)}</p>
      </div>
      <div className="my-1 border-t border-hairline" />
      <div className="flex items-center justify-between gap-2 px-2">
        <span className="text-sm text-ink-2">Theme</span>
        <ThemeToggle />
      </div>
      <div className="my-1 border-t border-hairline" />
      <Button variant="ghost" className="w-full justify-start px-2 font-normal" onClick={onSignOut}><SignOutIcon size={18} />Sign out</Button>
      <div className="flex gap-1 px-1 text-xs text-ink-2">
        <Link href="/privacy" className="inline-flex min-h-11 min-w-11 items-center justify-center px-2 hover:text-ink hover:underline">Privacy</Link>
        <Link href="/terms" className="inline-flex min-h-11 min-w-11 items-center justify-center px-2 hover:text-ink hover:underline">Terms</Link>
      </div>
    </>
  );
}

function Initial({ name }: { name: string }) {
  return (
    <span aria-hidden className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-medium text-primary-ink">
      {name.trim().charAt(0).toUpperCase() || "?"}
    </span>
  );
}

/** Sidebar content (desktop rail and phone drawer): brand, main nav, the page's slot (chat: conversations), account. */
function SidebarBody({ user, path, slotRef, onSignOut, top }: { user: Me; path: string; slotRef: (el: HTMLDivElement | null) => void; onSignOut: () => void; top?: ReactNode }) {
  const [menu, setMenu] = useState(false);
  const acct = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setMenu(false); acct.current?.focus(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [menu]);
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center justify-between gap-2 pr-2 pl-4">
        <span className="text-[0.9375rem] font-semibold tracking-[-0.01em]">Ocean Above</span>
        {top}
      </div>
      <nav className="space-y-0.5 px-2" aria-label="Main">
        {NAV.map(({ href, label, Icon }) => {
          const on = path.startsWith(href);
          return (
            <Link key={href} href={href} aria-current={on ? "page" : undefined}
              className={cn("flex min-h-11 items-center gap-3 rounded-md px-3 text-sm transition-colors duration-150",
                on ? "bg-active font-medium text-ink" : "text-ink-2 hover:bg-hover hover:text-ink")}>
              <Icon size={18} />{label}
            </Link>
          );
        })}
      </nav>
      <div ref={slotRef} className="mt-4 flex min-h-0 flex-1 flex-col" />
      <div className="relative shrink-0 border-t border-hairline p-2">
        <button ref={acct} type="button" aria-label={`Account: ${user.display_name}`} aria-expanded={menu} aria-controls={menu ? "account-menu" : undefined} onClick={() => setMenu((m) => !m)}
          className="flex min-h-11 w-full items-center gap-3 rounded-md px-2 py-1.5 text-left text-sm transition-colors duration-150 hover:bg-hover">
          <Initial name={user.display_name} />
          <span className="min-w-0 flex-1 truncate font-medium">{user.display_name}</span>
          <UpDownIcon size={16} className="shrink-0 text-ink-3" />
        </button>
        {menu && (
          <>
            <div className="fixed inset-0 z-30" onClick={() => setMenu(false)} aria-hidden />
            <div id="account-menu" className="pop absolute right-2 bottom-full left-2 z-40 mb-2 rounded-xl border border-border bg-surface p-1.5 text-ink shadow-overlay">
              <AccountPanel user={user} onSignOut={onSignOut} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export type ShellApi = {
  user: Me;
  refreshUser: () => void;
  /** Element in the sidebar (or the open drawer) a page may portal its own navigation into. */
  sidebarSlot: HTMLElement | null;
  /** Close the phone drawer (after picking something in it). */
  closeNav: () => void;
  /** Right end of the phone top bar, for one page action (chat: New chat). */
  headerSlot: HTMLElement | null;
};

/** Authenticated app frame: sidebar (drawer on phones) + main; redirects to /login on 401. */
export function Shell({ children }: { children: (s: ShellApi) => ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const router = useRouter();
  const path = usePathname();
  const [drawer, setDrawer] = useState(false);
  const [deskSlot, setDeskSlot] = useState<HTMLElement | null>(null);
  const [drawerSlot, setDrawerSlot] = useState<HTMLElement | null>(null);
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const closer = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!drawer) return;
    closer.current?.focus();
    const back = opener.current;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawer(false); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); back?.focus(); };
  }, [drawer]);
  async function signOut() { await api.logout().catch(() => {}); router.replace("/login"); }

  const refreshUser = useCallback(() => {
    api.me().then(setUser).catch((e) => {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
    });
  }, [router]);
  useEffect(() => { refreshUser(); }, [refreshUser]);
  const closeNav = useCallback(() => setDrawer(false), []);

  if (!user) return <div className="p-6 text-ink-2" role="status">Loading…</div>;
  return (
    <div className="flex h-dvh">
      <aside className="hidden w-[272px] shrink-0 border-r border-hairline bg-sidebar md:block">
        <SidebarBody user={user} path={path} slotRef={setDeskSlot} onSignOut={signOut} />
      </aside>
      {drawer && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-scrim" onClick={() => setDrawer(false)} />
          <div className="drawer absolute inset-y-0 left-0 w-[300px] max-w-[85vw] bg-sidebar pb-[env(safe-area-inset-bottom)] shadow-overlay">
            <SidebarBody user={user} path={path} slotRef={setDrawerSlot} onSignOut={signOut}
              top={<Button ref={closer} variant="ghost" aria-label="Close menu" onClick={() => setDrawer(false)}><CloseIcon /></Button>} />
          </div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col" inert={drawer}>
        <header className="flex h-14 shrink-0 items-center gap-1 px-1.5 md:hidden">
          <Button ref={opener} variant="ghost" aria-label="Open menu" aria-expanded={drawer} onClick={() => setDrawer(true)}><MenuIcon /></Button>
          <span className="text-[0.9375rem] font-semibold tracking-[-0.01em]">Ocean Above</span>
          <div ref={setHeaderSlot} className="ml-auto flex" />
        </header>
        <main className="min-h-0 flex-1">{children({ user, refreshUser, sidebarSlot: drawer ? drawerSlot : deskSlot, closeNav, headerSlot })}</main>
      </div>
    </div>
  );
}
