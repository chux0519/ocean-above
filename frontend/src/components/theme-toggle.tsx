"use client";
import { useEffect, useState } from "react";
import { applyTheme, readTheme, type ThemeChoice } from "@/lib/theme";
import { MoonIcon, SunIcon, SystemIcon } from "@/components/icons";
import { cn } from "@/lib/utils";

const OPTIONS: { value: ThemeChoice; label: string; Icon: typeof SunIcon }[] = [
  { value: "system", label: "System theme", Icon: SystemIcon },
  { value: "light", label: "Light theme", Icon: SunIcon },
  { value: "dark", label: "Dark theme", Icon: MoonIcon },
];

/** Three-state theme switch (system / light / dark), stored in localStorage. `tone="shell"` for the navy header. */
export function ThemeToggle({ tone = "surface", className }: { tone?: "surface" | "shell"; className?: string }) {
  const [theme, setTheme] = useState<ThemeChoice>("system");
  useEffect(() => setTheme(readTheme()), []);
  function choose(t: ThemeChoice) { applyTheme(t); setTheme(t); }
  return (
    <div role="group" aria-label="Colour theme" className={cn("inline-flex items-center", className)}>
      {OPTIONS.map(({ value, label, Icon }) => {
        const on = theme === value;
        return (
          <button key={value} type="button" aria-pressed={on} aria-label={label} title={label}
            onClick={() => choose(value)}
            className={cn("inline-flex size-11 items-center justify-center rounded-md transition-colors duration-150",
              tone === "shell"
                ? on ? "bg-white/15 text-shell-ink" : "text-shell-ink-2 hover:bg-white/10 hover:text-shell-ink"
                : on ? "bg-rail text-ink" : "text-ink-2 hover:bg-ink/5 hover:text-ink")}>
            <Icon size={18} />
          </button>
        );
      })}
    </div>
  );
}
