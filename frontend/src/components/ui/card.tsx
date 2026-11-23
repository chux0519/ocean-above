import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-border bg-card p-4 shadow-sm", className)} {...props} />;
}

export function Badge({ className, tone = "muted", ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: "muted" | "ok" | "warn" | "danger" }) {
  const tones = { muted: "bg-slate-100 text-slate-700", ok: "bg-green-100 text-green-800", warn: "bg-amber-100 text-amber-800", danger: "bg-red-100 text-red-800" };
  return <span className={cn("inline-block rounded-full px-2 py-0.5 text-xs font-medium", tones[tone], className)} {...props} />;
}
