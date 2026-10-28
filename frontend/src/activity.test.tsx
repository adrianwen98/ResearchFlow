import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  activityEvents,
  activitySummary,
  toolTarget,
  keyActivities,
  eventRecovered,
  eventIssue,
  eventTitle,
} from "./activity";
import ExecutionActivity from "./components/ExecutionActivity";
import TaskComposer from "./components/TaskComposer";
import type { Workspace } from "./types";
import type { TraceEvent } from "./types";
const excerptFailure =
  'ERROR: {"code":"evidence_mismatch","message":"需求摘录与已导出报告不匹配"}';
function requirementTrace(): TraceEvent[] {
  return [
    {
      id: 1,
      event: "tool_start",
      call_id: "a",
      detail: "record_requirement_result",
      arguments: { requirement_id: "r1" },
    },
    { id: 2, event: "tool_error", call_id: "a", detail: excerptFailure },
    { id: 3, event: "tool_end", call_id: "a", output_preview: excerptFailure },
    {
      id: 4,
      event: "tool_start",
      call_id: "b",
      detail: "record_requirement_result",
      arguments: { requirement_id: "r1" },
    },
    { id: 5, event: "tool_end", call_id: "b", output_preview: "Recorded" },
  ];
}
it("deduplicates the product error without altering the raw trace", () => {
  const events = requirementTrace();
  const original = JSON.stringify(events);
  expect(activityEvents(events).map((e) => e.id)).toEqual([2, 5]);
  expect(JSON.stringify(events)).toBe(original);
});
it("recognizes only a later successful registration of the same requirement", () => {
  const events = requirementTrace();
  expect(eventRecovered(events[1], events)).toBe(true);
  events[3].arguments = { requirement_id: "r2" };
  expect(eventRecovered(events[1], events)).toBe(false);
  events[3].arguments = { requirement_id: "r1" };
  events[4].output_preview = excerptFailure;
  expect(eventRecovered(events[1], events)).toBe(false);
  events[4].output_preview = "Recorded";
  events[4].id = 0;
  expect(eventRecovered(events[1], events)).toBe(false);
});
it("recognizes a successful retry only after its failed attempt", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "export_report" },
    { id: 2, event: "tool_error", call_id: "a", detail: "Temporary error" },
    { id: 3, event: "tool_retry", call_id: "a" },
    { id: 4, event: "tool_end", call_id: "a", output_preview: "Saved" },
  ];
  expect(eventRecovered(events[1], events)).toBe(true);
  events[3].output_preview = "ERROR: failed again";
  expect(eventRecovered(events[1], events)).toBe(false);
});
it("marks a failed discovery call as corrected after a later successful same tool", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "browser_search", arguments: { query: "x" } },
    { id: 2, event: "tool_error", call_id: "a", detail: 'ERROR: {"code":"search_empty","message":"empty"}' },
    { id: 3, event: "tool_start", call_id: "b", detail: "browser_search", arguments: { query: "y", engine: "duckduckgo" } },
    { id: 4, event: "tool_end", call_id: "b", output_preview: "results" },
  ];
  expect(eventRecovered(events[1], events)).toBe(true);
});

it("recognizes search_empty on the tool_end preview as external", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "browser_search", arguments: { query: "Nobel 2030" } },
    { id: 2, event: "tool_end", call_id: "a", detail: "browser_search", output_preview: 'ERROR: {"code":"search_empty","message":"no results"}' },
  ];
  expect(eventRecovered(events[1], events)).toBe(true);
});

it("treats an unavailable external page as resolved rather than a data warning", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "browser_navigate", arguments: { url: "https://example.org/missing" } },
    { id: 2, event: "tool_end", call_id: "a", detail: "browser_navigate", output_preview: 'ERROR: {"code":"http_404","message":"not found"}' },
  ];
  expect(eventRecovered(events[1], events)).toBe(true);
});

it("marks an empty search as superseded after a direct page path", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "browser_search", arguments: { query: "Nobel 2030" } },
    { id: 2, event: "tool_error", call_id: "a", code: "search_empty", detail: 'ERROR: {"code":"search_empty","message":"no results"}' },
    { id: 3, event: "tool_start", call_id: "b", detail: "browser_navigate", arguments: { url: "https://www.nobelprize.org/prizes/literature/" } },
    { id: 4, event: "tool_end", call_id: "b", output_preview: "page" },
  ];
  expect(eventRecovered(events[1], events)).toBe(true);
  events.pop();
  events.pop();
  expect(eventRecovered(events[1], events)).toBe(true);
});

it("does not treat a different tool success as recovery", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "browser_search" },
    { id: 2, event: "tool_error", call_id: "a", detail: 'ERROR: {"code":"transport","message":"failed"}' },
    { id: 3, event: "tool_start", call_id: "b", detail: "browser_navigate" },
    { id: 4, event: "tool_end", call_id: "b", output_preview: "page" },
  ];
  expect(eventRecovered(events[1], events)).toBe(false);
});

it("marks a stale draft inspection as corrected after inspect_delivery succeeds", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "inspect_business_draft", arguments: { draft_id: "stale" } },
    { id: 2, event: "tool_error", call_id: "a", detail: 'ERROR: {"code":"invalid_input","message":"draft missing"}' },
    { id: 3, event: "tool_start", call_id: "b", detail: "inspect_delivery", arguments: {} },
    { id: 4, event: "tool_end", call_id: "b", output_preview: "current state" },
  ];
  expect(eventRecovered(events[1], events)).toBe(true);
});

it("recovers a source quote through another source tool only for the same URL", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "record_source", arguments: { url: "https://example.org/data.csv" } },
    { id: 2, event: "tool_error", call_id: "a", detail: 'ERROR: {"code":"evidence_mismatch","message":"quote not found"}' },
    { id: 3, event: "tool_start", call_id: "b", detail: "record_source_from_observation", arguments: { observation_id: "a".repeat(20) } },
    { id: 4, event: "tool_end", call_id: "b", output_preview: '{"source_id":"1","url":"https://example.org/data.csv"}' },
  ];
  expect(eventRecovered(events[1], events)).toBe(true);
  events[3].output_preview = '{"source_id":"1","url":"https://example.org/other.csv"}';
  events.push(
    { id: 5, event: "tool_start", call_id: "c", detail: "record_source", arguments: { url: "https://example.org/other.csv" } },
    { id: 6, event: "tool_end", call_id: "c", output_preview: "已记录来源 #2\n实际来源URL：https://example.org/other.csv" },
  );
  expect(eventRecovered(events[1], events)).toBe(false);
  events[3].output_preview = '{"source_id":"1","url":"https://example.org/data.csv"}';
  expect(eventRecovered(events[1], events)).toBe(true);
});

it("uses past tense for a historical retry after delivery", () => {
  const event = { id: 5, event: "tool_retry", call_id: "b" } as TraceEvent;
  expect(eventTitle(event, [], true)).toBe("曾重试工具");
  expect(eventTitle(event, [], false)).toBe("正在重试工具");
});

it("keeps repaired errors in history while compact progress shows the valid result", () => {
  const events = requirementTrace();
  expect(keyActivities(events).map((e) => e.id)).toEqual([5]);
  expect(activityEvents(events).some((e) => e.id === 2)).toBe(true);
  expect(eventIssue(events[1])).toBe("需求摘录与已导出报告不匹配");
  expect(
    eventIssue({
      id: 6,
      event: "tool_end",
      output_preview: "ERROR: Permission denied",
    }),
  ).toBe("Permission denied");
});
const data = {
  status: "running",
  plan: ["Collect sources", "Verify deliverables"],
} as Workspace;
it("shows a bounded set of distinct real work steps by default, including recovery", () => {
  const events = [
    { id: 1, event: "plan_updated" },
    { id: 2, event: "tool_end", detail: "download_public_file" },
    { id: 3, event: "tool_end", detail: "inspect_downloaded_table" },
    { id: 4, event: "tool_end", detail: "aggregate_downloaded_table" },
    { id: 5, event: "tool_end", detail: "export_business_excel" },
    { id: 6, event: "tool_end", detail: "export_report" },
    { id: 7, event: "task_resume" },
    { id: 8, event: "tool_end", detail: "record_requirement_result" },
  ];
  expect(keyActivities(events).map((e) => e.id)).toEqual([1, 4, 6, 7]);
  const html = renderToStaticMarkup(
    <ExecutionActivity
      data={{ ...data, status: "completed" }}
      events={events}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain('data-event-id="4"');
  expect(html).toContain('data-event-id="6"');
  expect(html).not.toContain('data-event-id="8"');
  expect(html).not.toContain("当前操作");
});
it("never promotes a failed download to a successful capability step", () => {
  const events = [
    {
      id: 1,
      event: "tool_end",
      detail: "download_public_file",
      output_preview: "ERROR: denied",
    },
  ];
  const html = renderToStaticMarkup(
    <ExecutionActivity
      data={{ ...data, status: "permission_denied" }}
      events={events}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain('class="failed"');
  expect(html).toContain("该次操作未通过");
  expect(html).not.toContain("已获取官方数据");
});
it("separates returned tools, error outputs, retries and unfinished operations", () => {
  const events = [
    { id: 1, event: "tool_start", call_id: "a", detail: "browser_navigate" },
    { id: 2, event: "tool_end", call_id: "a", output_preview: "ERROR: denied" },
    { id: 3, event: "tool_start", call_id: "b", detail: "export_excel" },
    { id: 4, event: "tool_retry", call_id: "b" },
    { id: 5, event: "tool_end", call_id: "b", output_preview: "Saved" },
    { id: 6, event: "tool_start", call_id: "c", detail: "export_report" },
  ];
  const summary = activitySummary(events);
  expect(summary.returned).toBe(1);
  expect(summary.failed).toBe(1);
  expect(summary.current?.name).toBe("export_report");
  expect(activityEvents(events).map((e) => e.id)).toEqual([2, 4, 5]);
});
it("does not call a verified record or a plan a completed task", () => {
  const html = renderToStaticMarkup(
    <ExecutionActivity
      data={data}
      events={[
        { id: 1, event: "verification" },
        { id: 2, event: "plan_updated" },
      ]}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain("不代表完成进度");
  expect(html).toContain("交付检查已记录");
  expect(html).not.toContain("任务已完成");
});
it("renders only public tool activity, not model messages or private reasoning", () => {
  const html = renderToStaticMarkup(
    <ExecutionActivity
      data={data}
      events={[
        { id: 1, event: "model_end", detail: "PRIVATE_REASONING" },
        {
          id: 2,
          event: "tool_start",
          call_id: "a",
          detail: "browser_navigate",
          arguments: { url: "https://example.com/docs?token=hidden" },
        },
      ]}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain("浏览网页");
  expect(html).toContain("example.com/docs");
  expect(html).not.toContain("PRIVATE_REASONING");
  expect(html).not.toContain("token=hidden");
});
it("never labels stale incomplete tools as live operations after a task settles", () => {
  const html = renderToStaticMarkup(
    <ExecutionActivity
      data={{ ...data, status: "completed" }}
      events={[
        {
          id: 1,
          event: "tool_start",
          call_id: "a",
          detail: "browser_navigate",
        },
      ]}
      onDetails={() => {}}
    />,
  );
  expect(html).toContain("执行回顾");
  expect(html).not.toContain("当前操作");
  expect(html).not.toContain("spin");
});
it("preserves recovery and retry events in chronological order", () => {
  expect(
    activityEvents([
      { id: 9, event: "tool_retry" },
      { id: 3, event: "task_resume" },
      { id: 7, event: "human_decision" },
      { id: 10, event: "model_start" },
    ]).map((e) => e.id),
  ).toEqual([3, 7, 9]);
});
it("does not expose credentials embedded in observed URL targets", () => {
  expect(
    toolTarget({
      id: 1,
      event: "tool_start",
      arguments: {
        url: "https://name:password@example.org/page?secret=yes#token",
      },
    }),
  ).toBe("example.org/page");
});
it("keeps unsupported file placeholders and missing model configuration non-submittable", () => {
  const html = renderToStaticMarkup(
    <TaskComposer
      goal="[请替换为文件网址]"
      criteria=""
      onGoal={() => {}}
      onCriteria={() => {}}
      onSubmit={() => {}}
      busy={false}
    />,
  );
  expect(html).toContain('type="submit" class="primary" disabled');
  expect(html).toContain("公开文件分析");
  expect(html).not.toContain('type="file"');
});

it("marks an earlier business export as corrected only after a successful business file export", () => {
  const events: TraceEvent[] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "export_business_excel" },
    { id: 2, event: "tool_error", call_id: "a", detail: 'ERROR: {"code":"business_validation","message":"invalid cell"}' },
    { id: 3, event: "tool_start", call_id: "b", detail: "export_excel" },
    { id: 4, event: "tool_end", call_id: "b", output_preview: "Saved sources" },
  ];
  expect(eventRecovered(events[1], events)).toBe(false);
  events.push({id: 5,event:"tool_start",call_id:"c",detail:"repair_business_excel"},
              {id: 6,event:"tool_end",call_id:"c",output_preview:"Saved business workbook"});
  expect(eventRecovered(events[1], events)).toBe(true);
  expect(activityEvents(events).some((e) => e.id === 2)).toBe(true);
  expect(keyActivities(events).some((e) => e.id === 2)).toBe(false);
});

it("shows the human message from a truncated structured error without dumping JSON", () => {
  expect(eventIssue({id: 1,event:"tool_error",detail:'ERROR: {"code":"business_validation","message":"请核对原文","diagnostics": {'})).toBe("请核对原文");
  expect(eventIssue({id: 2,event:"tool_error",detail:'ERROR: {"code":"business_validation","mess'})).not.toContain('"code"');
});
