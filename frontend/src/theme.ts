export type Theme = "light" | "dark";

const STORAGE_KEY = "webtask-theme";

type StorageLike = Pick<Storage, "getItem" | "setItem">;

interface RootLike {
  dataset: { theme?: string };
  style: { colorScheme?: string };
}

export function readStoredTheme(storage?: StorageLike): Theme | null {
  try {
    const value = (storage ?? localStorage).getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function saveTheme(theme: Theme, storage?: StorageLike) {
  try {
    (storage ?? localStorage).setItem(STORAGE_KEY, theme);
  } catch {
    /* The toggle still works when browser storage is unavailable. */
  }
}

export function initialTheme(
  storage?: StorageLike,
  prefersDark?: boolean,
): Theme {
  const stored = readStoredTheme(storage);
  if (stored) return stored;

  // index.html applies the preference before React renders; reuse it so the
  // first render and the painted page never disagree.
  if (typeof document !== "undefined") {
    const applied = document.documentElement.dataset.theme;
    if (applied === "light" || applied === "dark") return applied;
  }

  if (typeof prefersDark === "boolean") return prefersDark ? "dark" : "light";

  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(theme: Theme, root?: RootLike) {
  const target = root ?? document.documentElement;
  target.dataset.theme = theme;
  target.style.colorScheme = theme;
}
