"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { ThemeToggle } from "@/components/theme-toggle";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.login(username, password); router.replace("/chat"); }
    catch (err) { setError(err instanceof Error ? err.message : "Login failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between bg-shell px-4 text-shell-ink" data-shell>
        <span className="text-base font-bold tracking-[-0.01em]">Ocean Above</span>
        <ThemeToggle tone="shell" />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[12vh] pb-10">
        <Card className="w-full max-w-sm">
          <h1 className="text-2xl font-bold tracking-[-0.01em]">Sign in</h1>
          <p className="mt-1 text-sm text-ink-2">Book flights in plain language. You confirm every leg, the distance and the price before anything is booked.</p>
          <form onSubmit={submit} className="mt-5 space-y-4">
            <label className="block text-sm font-medium">Username
              <Input className="mt-1" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
            </label>
            <label className="block text-sm font-medium">Password
              <Input className="mt-1" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            </label>
            {error && <Alert role="alert">{error}</Alert>}
            <Button type="submit" className="w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
          </form>
        </Card>
      </main>
    </div>
  );
}
