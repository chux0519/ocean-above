import * as React from "react";
import { cn } from "@/lib/utils";

/** 44 px text field; 16 px text (no iOS zoom); `field` outline (≥ 3:1), accent ring on focus. */
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref}
    className={cn("min-h-11 w-full min-w-0 rounded-lg border border-field bg-surface px-3 text-md text-ink transition-colors duration-150 hover:border-ink-2 focus-visible:border-accent disabled:opacity-40", className)}
    {...props} />
));
Input.displayName = "Input";
