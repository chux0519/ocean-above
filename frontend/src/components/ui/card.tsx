import * as React from "react";
import { cn } from "@/lib/utils";

/** Soft sheet: 16 px radius, low-contrast border, gentle depth (none in dark; the border carries it). */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-border bg-surface p-4 shadow-card sm:p-5", className)} {...props} />;
}

export type Tone = "muted" | "ok" | "warn" | "danger";

/** Status label: pill, always text (+ optional icon), never colour alone. */
export function Badge({ className, tone = "muted", ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  const tones: Record<Tone, string> = {
    muted: "bg-sunken text-ink-2",
    ok: "bg-ok-bg text-ok",
    warn: "bg-warn-bg text-warn",
    danger: "bg-danger-bg text-danger",
  };
  return <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium", tones[tone], className)} {...props} />;
}
