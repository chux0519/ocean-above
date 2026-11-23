import * as React from "react";
import { cn } from "@/lib/utils";
import { AlertIcon } from "@/components/icons";

type Tone = "warn" | "danger";
const tones: Record<Tone, string> = { warn: "bg-warn-bg text-warn", danger: "bg-danger-bg text-danger" };

/** Tinted strip with a leading icon. Status is carried by the text and the icon, never by colour alone. */
export function Alert({ tone = "danger", className, children, icon = true, ...props }: React.HTMLAttributes<HTMLDivElement> & { tone?: Tone; icon?: boolean }) {
  return (
    <div className={cn("flex gap-2 rounded-md px-3 py-2 text-sm", tones[tone], className)} {...props}>
      {icon && <AlertIcon size={16} className="mt-0.5 shrink-0" />}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
