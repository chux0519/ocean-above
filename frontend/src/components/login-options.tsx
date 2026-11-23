"use client";
import { useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AppConfig } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { GoogleButton } from "@/components/google-button";

export const msg = (e: unknown, fallback: string) => (e instanceof ApiError || e instanceof Error) && e.message ? e.message : fallback;

/** One sign-in form: owns its busy flag and inline error. */
function useSubmit(run: () => Promise<unknown>, onDone: () => void, fallback: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true); setError(null);
    try { await run(); onDone(); } catch (err) { setError(msg(err, fallback)); } finally { setBusy(false); }
  };
  return { busy, error, setError, submit };
}

function InviteForm({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const f = useSubmit(() => api.loginInvite(code.trim(), name.trim()), onDone, "Sign-in failed");
  return (
    <form onSubmit={f.submit} className="space-y-3" aria-labelledby="invite-h">
      <h2 id="invite-h" className="text-sm font-semibold">Sign in with an invite code</h2>
      <label className="block text-sm font-medium">Invite code
        <Input className="mt-1" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" autoCapitalize="off" spellCheck={false} required />
      </label>
      <label className="block text-sm font-medium">Your name
        <Input className="mt-1" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={40} required />
      </label>
      {f.error && <Alert role="alert">{f.error}</Alert>}
      <Button type="submit" className="w-full" disabled={f.busy || !code.trim() || !name.trim()}>{f.busy ? "Signing in…" : "Continue"}</Button>
    </form>
  );
}

function DevForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const f = useSubmit(() => api.login(username, password), onDone, "Login failed");
  return (
    <form onSubmit={f.submit} className="space-y-3" aria-labelledby="dev-h">
      <h2 id="dev-h" className="text-sm font-semibold">Local account</h2>
      <label className="block text-sm font-medium">Username
        <Input className="mt-1" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
      </label>
      <label className="block text-sm font-medium">Password
        <Input className="mt-1" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      </label>
      {f.error && <Alert role="alert">{f.error}</Alert>}
      <Button type="submit" variant="outline" className="w-full" disabled={f.busy}>{f.busy ? "Signing in…" : "Sign in"}</Button>
    </form>
  );
}

function GoogleSection({ clientId, onDone }: { clientId: string; onDone: () => void }) {
  const credential = useRef("");
  const f = useSubmit(() => api.loginGoogle(credential.current), onDone, "Google sign-in failed");
  return (
    <div className="space-y-2">
      <GoogleButton clientId={clientId} onCredential={(c) => { credential.current = c; void f.submit(); }} />
      {f.busy && <p className="text-center text-sm text-ink-2" role="status">Signing in…</p>}
      {f.error && <Alert role="alert">{f.error}</Alert>}
    </div>
  );
}

const Or = () => (
  <div className="flex items-center gap-3 text-xs uppercase tracking-[0.06em] text-ink-2" aria-hidden>
    <span className="h-px flex-1 bg-hairline" />or<span className="h-px flex-1 bg-hairline" />
  </div>
);

export function LoginOptions({ config, onDone }: { config: AppConfig; onDone: () => void }) {
  const parts = [
    config.google_client_id && <GoogleSection key="g" clientId={config.google_client_id} onDone={onDone} />,
    config.invite_login && <InviteForm key="i" onDone={onDone} />,
    config.dev_login && <DevForm key="d" onDone={onDone} />,
  ].filter(Boolean);
  if (!parts.length) return <Alert role="alert">Sign-in is not available right now. Please try again later.</Alert>;
  return <div className="space-y-5">{parts.flatMap((p, i) => (i ? [<Or key={`or${i}`} />, p] : [p]))}</div>;
}
