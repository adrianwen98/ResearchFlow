import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ErrorBoundary, { ErrorFallback } from "./components/ErrorBoundary";

it("renders a recoverable fallback instead of a blank page", () => {
  const html = renderToStaticMarkup(<ErrorFallback message="boom" />);
  expect(html).toContain("页面出现异常");
  expect(html).toContain("刷新页面");
  expect(html).toContain("boom");
});

it("captures a render error", () => {
  expect(ErrorBoundary.getDerivedStateFromError(new Error("x"))).toEqual({
    error: expect.any(Error),
  });
});
