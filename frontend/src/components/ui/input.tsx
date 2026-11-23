import * as React from "react";
import { cn } from "@/lib/utils";

/** 44 px text field; 16 px text (no iOS zoom); field border, accent on focus. */
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref}
    className={cn("min-h-11 w-full min-w-0 rounded-md border border-field bg-surface px-3 text-base text-ink transition-colors duration-150 hover:border-ink-2 focus-visible:border-accent disabled:opacity-45", className)}
    {...props} />
));
Input.displayName = "Input";
