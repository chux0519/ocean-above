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
      <header className="flex h-14 shrink-0 items-center justify-between pr-2 pl-4 sm:px-6">
        <span className="text-[0.9375rem] font-semibold tracking-[-0.01em]">Ocean Above</span>
        <ThemeToggle />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[6vh] pb-6 sm:pt-[10vh]">
        <Card className="w-full max-w-[25rem] shadow-raised sm:p-8">
          <h1 className="text-xl font-semibold tracking-[-0.02em]">Sign in</h1>
          <p className="mt-1.5 text-sm text-ink-2">Book flights in plain language. You confirm every leg, the distance and the price before anything is booked.</p>
          <div className="mt-6">
            {error ? <ErrorState message={error} onRetry={load} />
              : config ? <LoginOptions config={config} onDone={done} />
              : <LoadingState label="Loading sign-in options…" />}
          </div>
          <p className="mt-6 text-xs leading-5 text-ink-2">
            By signing in you agree to the <Link href="/terms" className="underline underline-offset-2 hover:text-ink">Terms of Service</Link> and
            the <Link href="/privacy" className="underline underline-offset-2 hover:text-ink">Privacy Policy</Link>. This is a demo: no real flights or payments.
          </p>
        </Card>
      </main>
      <SiteFooter />
    </div>
  );
}
