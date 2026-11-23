import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-md px-4 text-sm font-semibold transition-colors duration-150 ease-[var(--ease-out)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "bg-accent text-accent-ink hover:bg-accent-hover active:bg-accent-hover",
        outline: "border border-field bg-surface text-ink hover:bg-ground active:bg-rail",
        ghost: "text-ink hover:bg-ink/5 active:bg-ink/10",
        link: "min-w-0 px-0 font-medium text-accent underline underline-offset-4 decoration-1 hover:decoration-2",
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
