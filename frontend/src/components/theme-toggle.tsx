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

/** Three-state theme switch (system / light / dark), stored in localStorage. A small segmented control. */
export function ThemeToggle({ className }: { className?: string }) {
  const [theme, setTheme] = useState<ThemeChoice>("system");
  useEffect(() => setTheme(readTheme()), []);
  function choose(t: ThemeChoice) { applyTheme(t); setTheme(t); }
  return (
    <div role="group" aria-label="Colour theme" className={cn("inline-flex items-center rounded-full bg-sunken p-0.5", className)}>
      {OPTIONS.map(({ value, label, Icon }) => {
        const on = theme === value;
        return (
          <button key={value} type="button" aria-pressed={on} aria-label={label} title={label}
            onClick={() => choose(value)}
            className={cn("inline-flex size-11 items-center justify-center rounded-full transition-colors duration-150",
              on ? "bg-surface text-ink shadow-card" : "text-ink-2 hover:text-ink")}>
            <Icon size={17} />
          </button>
        );
      })}
    </div>
  );
}
