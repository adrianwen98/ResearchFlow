import { it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TaskBudget, { validAddition } from "./components/TaskBudget";
import type { Workspace } from "./types";
it("rejects fractional, zero, negative and excessive custom additions", () => {
  for (const value of ["0", "-1", "1.5", "51", "1e1", "", "Infinity"])
    expect(validAddition(value, 50)).toBe(false);
  expect(validAddition("10", 50)).toBe(true);
  expect(validAddition("20", 50)).toBe(true);
  expect(validAddition("20", 10)).toBe(false);
});
it("shows real cumulative usage and an explicit budget action without opening consent or executing", () => {
  const data = {
    task_id: "original",
    status: "budget_exceeded",
    metrics: { requests: 30, limits: { model_requests: 30 } },
    capabilities: { can_resume: true },
    model_budget: {
      used: 30,
      limit: 30,
      remaining: 0,
      revision: 0,
      exhausted: true,
      can_add: true,
      grants: [],
    },
  } as unknown as Workspace;
  const html = renderToStaticMarkup(
    <TaskBudget data={data} busy={false} onResumed={() => {}} />,
  );
  expect(html).toContain("追加额度并继续");
  expect(html).toContain("/ 30 次");
  expect(html).not.toContain('role="dialog"');
  const running = renderToStaticMarkup(
    <TaskBudget
      data={{
        ...data,
        status: "running",
        metrics: { requests: 31, limits: { model_requests: 50 } },
      }}
      busy={false}
      onResumed={() => {}}
    />,
  );
  expect(running).toContain("/ 50 次");
  expect(running).toContain("<b>19</b>");
  expect(running).not.toContain("追加额度并继续");
});
