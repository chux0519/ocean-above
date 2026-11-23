import * as React from "react";
import { cn } from "@/lib/utils";

/** Flat sheet: 1px hairline, no shadow (depth is reserved for overlays). */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-hairline bg-surface p-4 sm:p-5", className)} {...props} />;
}

export type Tone = "muted" | "ok" | "warn" | "danger";

/** Status label: always text (+ optional icon), never colour alone. */
export function Badge({ className, tone = "muted", ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  const tones: Record<Tone, string> = {
    muted: "bg-rail text-ink-2",
    ok: "bg-ok-bg text-ok",
    warn: "bg-warn-bg text-warn",
    danger: "bg-danger-bg text-danger",
  };
  return <span className={cn("inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-xs font-semibold", tones[tone], className)} {...props} />;
}
