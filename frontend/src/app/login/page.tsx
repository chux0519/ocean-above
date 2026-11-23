"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import type { AppConfig } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { ThemeToggle } from "@/components/theme-toggle";
import { LoginOptions, msg } from "@/components/login-options";
import { SiteFooter } from "@/components/site-footer";
import { ErrorState, LoadingState } from "@/components/states";

export default function LoginPage() {
  const router = useRouter();
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    api.config().then(setConfig).catch((e) => setError(msg(e, "Could not load the sign-in options.")));
  }, []);
  useEffect(load, [load]);
  const done = useCallback(() => router.replace("/chat"), [router]);

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between bg-shell px-4 text-shell-ink" data-shell>
        <span className="text-base font-bold tracking-[-0.01em]">Ocean Above</span>
        <ThemeToggle tone="shell" />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[8vh] pb-6">
        <Card className="w-full max-w-sm">
          <h1 className="text-2xl font-bold tracking-[-0.01em]">Sign in</h1>
          <p className="mt-1 text-sm text-ink-2">Book flights in plain language. You confirm every leg, the distance and the price before anything is booked.</p>
          <div className="mt-5">
            {error ? <ErrorState message={error} onRetry={load} />
              : config ? <LoginOptions config={config} onDone={done} />
              : <LoadingState label="Loading sign-in options…" />}
          </div>
          <p className="mt-5 text-xs text-ink-2">
            By signing in you agree to the <Link href="/terms" className="underline underline-offset-2 hover:text-ink">Terms of Service</Link> and
            the <Link href="/privacy" className="underline underline-offset-2 hover:text-ink">Privacy Policy</Link>. This is a demo: no real flights or payments.
          </p>
        </Card>
      </main>
      <SiteFooter />
    </div>
  );
}
