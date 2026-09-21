/**
 * Light, dark, or whatever the machine says.
 *
 * The class lives on `<html>` because that is what `@custom-variant dark` in
 * `app.css` matches, and `color-scheme` goes with it so the browser's own
 * chrome — scrollbars, form controls, the flash before the first paint —
 * follows the page rather than fighting it.
 */

export type Theme = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "terusan.theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

/** What the choice actually resolves to right now. */
export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  const resolved = resolveTheme(theme);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

/**
 * The stored choice, or "system".
 *
 * Wrapped because storage throws rather than returning null in a browser with
 * cookies blocked, and a theme is not worth a blank page.
 */
export function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Storage unavailable; fall through.
  }
  return "system";
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage unavailable; the choice lasts as long as the page does.
  }
}

/** Calls `listener` while the choice is "system" and the machine changes. */
export function watchSystemTheme(listener: () => void): () => void {
  const query = window.matchMedia(DARK_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

/**
 * Run in `<head>`, before anything paints.
 *
 * React cannot do this: by the time the app hydrates, a dark-mode reader has
 * already been shown a white page. The same logic as `applyTheme`, written
 * small enough to inline and with no imports to wait for.
 */
export const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)})||"system";var d=t==="dark"||(t!=="light"&&matchMedia(${JSON.stringify(
  DARK_QUERY,
)}).matches);var r=document.documentElement;r.classList.toggle("dark",d);r.style.colorScheme=d?"dark":"light"}catch(e){}`;
