"use client";
import { useEffect, useRef, useState } from "react";

// Google Identity Services, ID-token flow: GIS renders its own button; on sign-in it calls back with a JWT
// `credential` that the backend verifies (POST /api/auth/google). No OAuth scopes, no client secret.
type Gis = {
  accounts: { id: {
    initialize: (o: { client_id: string; callback: (r: { credential?: string }) => void; ux_mode?: "popup"; auto_select?: boolean }) => void;
    renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
  } };
};
declare global { interface Window { google?: Gis } }

export const GIS_SRC = "https://accounts.google.com/gsi/client";

let loading: Promise<Gis> | null = null;
/** Load the GIS script once per page. */
export function loadGis(): Promise<Gis> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google);
  if (loading) return loading;
  loading = new Promise<Gis>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = GIS_SRC; s.async = true; s.defer = true;
    s.onload = () => (window.google?.accounts?.id ? resolve(window.google) : reject(new Error("gis")));
    s.onerror = () => { loading = null; s.remove(); reject(new Error("gis")); };
    document.head.appendChild(s);
  });
  return loading;
}

export function GoogleButton({ clientId, onCredential }: { clientId: string; onCredential: (credential: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef(onCredential);
  cb.current = onCredential;
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    loadGis().then((g) => {
      if (!live || !box.current) return;
      g.accounts.id.initialize({ client_id: clientId, ux_mode: "popup", auto_select: false,
        callback: (r) => { if (r.credential) cb.current(r.credential); } });
      const width = Math.min(400, Math.max(200, Math.floor(box.current.getBoundingClientRect().width) || 320));
      g.accounts.id.renderButton(box.current, { type: "standard", theme: "outline", size: "large", text: "continue_with", locale: "en", shape: "rectangular", logo_alignment: "left", width });
    }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [clientId]);
  if (failed) return <p className="text-sm text-ink-2" role="status">Google sign-in could not load. Check your connection, or use another way to sign in.</p>;
  // color-scheme: light keeps the GIS iframe transparent in dark mode (else the browser paints it opaque white).
  return <div ref={box} style={{ colorScheme: "light" }} className="flex min-h-11 w-full justify-center" data-testid="google-button" />;
}
