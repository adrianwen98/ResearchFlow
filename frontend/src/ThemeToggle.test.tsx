import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ThemeToggle from "./components/ThemeToggle";

it("offers the opposite mode with an accessible label", () => {
  const light = renderToStaticMarkup(
    <ThemeToggle theme="light" onToggle={() => {}} />,
  );
  expect(light).toContain('aria-label="切换到深色模式"');
  expect(light).toContain('title="切换到深色模式"');
  expect(light).toContain('aria-pressed="false"');

  const dark = renderToStaticMarkup(
    <ThemeToggle theme="dark" onToggle={() => {}} />,
  );
  expect(dark).toContain('aria-label="切换到浅色模式"');
  expect(dark).toContain('aria-pressed="true"');
});
