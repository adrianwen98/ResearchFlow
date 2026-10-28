import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TaskStream, { streamEvents } from "./components/TaskStream";
import type { Workspace } from "./types";
const data: Workspace = {
  task_id: "stored",
  task: "Actual user request",
  title: "Title",
  status: "completed",
  status_label: "已完成",
  created_at: "",
  metrics: {},
  elapsed_seconds: 1,
  read_only: false,
  execution_label: "",
  report: "# Full report\n\nPrivate full report body",
  answer: "Actual saved answer",
  plan: [],
  events: [],
  sources: [],
  pending_review: [],
  requirements: { requirements: [], requirements_supported: true },
  artifacts: { artifacts_valid: true },
  review_binding: "x",
  files: [
    {
      name: "research_sources.xlsx",
      bytes: 123,
      url: "/tasks/stored/files/research_sources.xlsx",
    },
  ],
  capabilities: {
    can_cancel: false,
    can_resume: false,
    can_review: false,
    can_accept: false,
    has_checkpoint: false,
    locked: false,
  },
};
const events = [
  { id: 3, event: "tool_end", detail: "browser_snapshot", call_id: "a" },
  { id: 1, event: "tool_start", detail: "browser_snapshot", call_id: "a" },
  { id: 2, event: "plan_updated", detail: "[Actual saved plan]" },
  { id: 4, event: "model_start" },
];
it("keeps actual event order and excludes low-level model traffic from the main stream", () => {
  expect(streamEvents(events).map((e) => e.id)).toEqual([2, 3]);
  const html = renderToStaticMarkup(
    <TaskStream
      data={{ ...data, status: "running" }}
      events={events}
      onOpenTab={() => {}}
      onFile={() => {}}
      onDetails={() => {}}
    />,
  );
  expect(html.indexOf('data-event-id="2"')).toBeLessThan(
    html.indexOf('data-event-id="3"'),
  );
  expect(html).not.toContain("model_start");
});
it("shows stored task, result and real files without embedding the report", () => {
  const html = renderToStaticMarkup(
    <TaskStream
      data={data}
      events={[]}
      onOpenTab={() => {}}
      onFile={() => {}}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain("Actual user request");
  expect(html).toContain("Actual saved answer");
  expect(html).toContain('aria-label="预览 research_sources.xlsx"');
  expect(html).toContain('href="/tasks/stored/files/research_sources.xlsx"');
  expect(html).not.toContain("Private full report body");
  expect(html).not.toContain('class="markdown"');
});
it("does not invent a plan or mark a pending task complete", () => {
  const html = renderToStaticMarkup(
    <TaskStream
      data={{ ...data, status: "running", answer: "", files: [] }}
      events={[]}
      onOpenTab={() => {}}
      onFile={() => {}}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain("Agent 正在执行任务");
  expect(html).not.toContain("更新任务计划");
  expect(html).not.toContain("任务已完成");
});
it("keeps failed tool results visible in the chronological feed", () => {
  const html = renderToStaticMarkup(
    <TaskStream
      data={{ ...data, status: "permission_denied" }}
      events={[
        {
          id: 1,
          event: "tool_start",
          call_id: "denied",
          detail: "browser_navigate",
        },
        {
          id: 2,
          event: "tool_error",
          call_id: "denied",
          detail: "Permission denied",
        },
      ]}
      onOpenTab={() => {}}
      onFile={() => {}}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain("Permission denied");
  expect(html).toContain('class="failed"');
  expect(html).toContain("权限受限，任务已停止");
});
