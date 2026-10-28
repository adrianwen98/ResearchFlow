import { expect, it } from "vitest";
import {
  applyTheme,
  initialTheme,
  readStoredTheme,
  saveTheme,
} from "./theme";

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

it("prefers an explicit stored choice over the system preference", () => {
  expect(initialTheme(memoryStorage({ "webtask-theme": "dark" }), false)).toBe(
    "dark",
  );
  expect(initialTheme(memoryStorage({ "webtask-theme": "light" }), true)).toBe(
    "light",
  );
});

it("falls back to the system preference and ignores corrupted values", () => {
  expect(initialTheme(memoryStorage({ "webtask-theme": "blue" }), true)).toBe(
    "dark",
  );
  expect(initialTheme(memoryStorage(), false)).toBe("light");
});

it("stores and reads the chosen theme without throwing on broken storage", () => {
  const storage = memoryStorage();
  saveTheme("dark", storage);
  expect(readStoredTheme(storage)).toBe("dark");
  const broken = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };
  expect(readStoredTheme(broken)).toBeNull();
  expect(() => saveTheme("light", broken)).not.toThrow();
});

it("applies the theme to the document root", () => {
  const root = { dataset: {} as { theme?: string }, style: {} };
  applyTheme("dark", root);
  expect(root.dataset.theme).toBe("dark");
  expect(root.style).toEqual({ colorScheme: "dark" });
});
