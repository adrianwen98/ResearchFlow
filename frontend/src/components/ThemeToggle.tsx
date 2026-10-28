import { Moon, Sun } from "lucide-react";
import type { Theme } from "../theme";

export default function ThemeToggle({
  theme,
  onToggle,
}: {
  theme: Theme;
  onToggle: () => void;
}) {
  const dark = theme === "dark";
  const label = dark ? "切换到浅色模式" : "切换到深色模式";
  return (
    <button
      type="button"
      className="icon-button theme-toggle"
      aria-label={label}
      aria-pressed={dark}
      title={label}
      onClick={onToggle}
    >
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
