"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

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

  const input = "mt-1 block min-h-11 w-full rounded-md border border-border bg-card px-3 text-base";
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm items-center px-4">
      <Card className="w-full">
        <h1 className="text-xl font-semibold">Ocean Above</h1>
        <p className="mt-1 text-sm text-muted">Sign in to book flights in plain language.</p>
        <form onSubmit={submit} className="mt-4 space-y-3">
          <label className="block text-sm">Username
            <input className={input} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </label>
          <label className="block text-sm">Password
            <input className={input} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          <Button type="submit" className="w-full" disabled={busy}>Sign in</Button>
        </form>
      </Card>
    </main>
  );
}
