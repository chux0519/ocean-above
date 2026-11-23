import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/** Pill buttons; ≥ 44 px hit area. `primary` is the ink-filled action (Confirm booking, Send): one per view. */
const buttonVariants = cva(
  "inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-full px-4 text-sm font-medium transition-[background-color,color,box-shadow,opacity] duration-150 ease-[var(--ease-out)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-ink hover:bg-primary-hover active:bg-primary-hover",
        outline: "border border-border bg-surface text-ink shadow-card hover:bg-hover active:bg-active",
        ghost: "rounded-md text-ink hover:bg-hover active:bg-active",
        link: "min-w-0 px-0 text-accent underline underline-offset-4 decoration-1 hover:decoration-2",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, ...props }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant }), className)} {...props} />
));
Button.displayName = "Button";
