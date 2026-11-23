import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeToggle } from "@/components/theme-toggle";
import { THEME_BOOT, THEME_KEY, readTheme } from "@/lib/theme";

describe("theme", () => {
  beforeEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme; });

  it("defaults to system: nothing stored, no data-theme", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "System theme" })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("dark → stored and applied; system → cleared", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("button", { name: "Dark theme" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "System theme" }));
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("boot script applies a stored choice before React (no flash) and ignores junk", () => {
    localStorage.setItem(THEME_KEY, "light");
    new Function(THEME_BOOT)();
    expect(document.documentElement.dataset.theme).toBe("light");
    delete document.documentElement.dataset.theme;
    localStorage.setItem(THEME_KEY, "purple");
    new Function(THEME_BOOT)();
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(readTheme()).toBe("system");
  });
});
