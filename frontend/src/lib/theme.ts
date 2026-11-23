export type ThemeChoice = "system" | "light" | "dark";
export const THEME_KEY = "oa-theme";

/** Runs inline in <head> before first paint, so a stored choice never flashes the other theme. */
export const THEME_BOOT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export function readTheme(): ThemeChoice {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch { return "system"; }
}

export function applyTheme(t: ThemeChoice) {
  const root = document.documentElement;
  try {
    if (t === "system") { localStorage.removeItem(THEME_KEY); delete root.dataset.theme; }
    else { localStorage.setItem(THEME_KEY, t); root.dataset.theme = t; }
  } catch { if (t === "system") delete root.dataset.theme; else root.dataset.theme = t; }
}
