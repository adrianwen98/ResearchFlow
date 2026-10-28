import { names, toolCalls, callFailed } from "./model";
import type { TraceEvent } from "./types";
export const activityLabels: Record<string, string> = {
  plan_updated: "任务计划已调整",
  task_resume: "从检查点恢复",
  human_decision: "人工决定已记录",
  verification: "交付检查已记录",
  task_error: "执行失败",
  task_limit: "达到执行限制",
  task_interrupted: "执行已中断",
  loop_guard: "重复操作已停止",
  tool_retry: "正在重试工具",
};
export function activityEvents(events: TraceEvent[]) {
  // The harness records both tool_error and tool_end(ERROR) for one failed call.
  // Keep the original error once in the product feed; raw trace stays untouched.
  const failedCalls = new Set(
    events
      .filter((e) => e.event === "tool_error" && e.call_id)
      .map((e) => e.call_id),
  );
  return [...events]
    .sort((a, b) => a.id - b.id)
    .filter(
      (e) =>
        e.event in activityLabels ||
        (e.event === "tool_end" &&
          !(eventFailed(e) && e.call_id && failedCalls.has(e.call_id))) ||
        e.event === "tool_error",
    );
}

const sourceTools = new Set(["record_source", "record_source_from_observation"]);
function sameSourceUrl(left: string, right: string) {
  try {
    const a = new URL(left),
      b = new URL(right);
    return a.origin === b.origin && a.pathname === b.pathname && a.search === b.search;
  } catch {
    return left === right;
  }
}
function recordedSourceUrl(event: TraceEvent, start?: TraceEvent) {
  const args = start?.arguments as Record<string, unknown> | undefined;
  if (typeof args?.url === "string" && args.url) return args.url;
  const text = String(event.output_preview ?? "");
  const match = text.match(/实际来源URL：\s*(\S+)/);
  if (match) return match[1];
  if (text.trim().startsWith("{")) {
    try {
      const data = JSON.parse(text) as { url?: unknown };
      if (typeof data.url === "string") return data.url;
    } catch {
      /* Raw error/diagnostic text is not a source URL. */
    }
  }
  return "";
}
export function eventRecovered(event: TraceEvent, events: TraceEvent[]) {
  if (!eventFailed(event) || !event.call_id) return false;
  const starts = new Map(
    events.filter((e) => e.event === "tool_start").map((e) => [e.call_id, e]),
  );
  const start = starts.get(event.call_id);
  const args = start?.arguments as Record<string, unknown> | undefined;
  const requirement =
    start?.detail === "record_requirement_result" &&
    typeof args?.requirement_id === "string"
      ? args.requirement_id
      : undefined;
  const failedText = [event.detail, event.output_preview]
    .filter((value): value is string => typeof value === "string")
    .join("\n");
  const eventCode = String(
    (event as TraceEvent & { code?: unknown }).code ??
      failedText.match(/"code"\s*:\s*"([^"]+)"/)?.[1] ??
      "",
  );
  if (
    ["search_empty", "http_404", "http_403", "http_410", "image_document"]
      .includes(eventCode)
  ) {
    // Unavailable/unsupported external sources are not data-integrity errors.
    // Missing data still surfaces through requirements/budget/report warnings.
    return true;
  }
  return events.some((e) => {
    if (e.id <= event.id || e.event !== "tool_end" || eventFailed(e))
      return false;
    if (e.call_id === event.call_id) return true;
    const next = starts.get(e.call_id);
    const tool = String(start?.detail ?? "");
    const nextTool = String(next?.detail ?? "");
    const business = new Set(["export_business_excel", "repair_business_excel"]);
    if (business.has(tool) && business.has(nextTool)) return true;
    const inspection = new Set(["inspect_business_draft", "inspect_delivery"]);
    if (inspection.has(tool) && inspection.has(nextTool)) return true;
    // 逐项验收仍必须匹配同一个 requirement_id，不能用同工具成功放宽。
    if (requirement) {
      return (
        nextTool === "record_requirement_result" &&
        (next?.arguments as Record<string, unknown> | undefined)
          ?.requirement_id === requirement
      );
    }
    // 来源工具互换时，只有同一个 URL 才算修正；不能拿另一个来源顶替。
    if (sourceTools.has(tool)) {
      if (!sourceTools.has(nextTool)) return false;
      const failedUrl = recordedSourceUrl(event, start);
      const laterUrl = recordedSourceUrl(e, next);
      return Boolean(
        failedUrl && laterUrl && sameSourceUrl(failedUrl, laterUrl),
      );
    }
    // 后续同工具成功 = 模型已自行修正；与后端计数规则一致。
    return Boolean(tool && nextTool === tool);
  });
}

export function eventIssue(event: TraceEvent) {
  const text = String(event.output_preview || event.detail || "");
  if (text.startsWith("ERROR:")) {
    try {
      return String(
        JSON.parse(text.slice(6)).message || "该次调用未通过校验",
      ).slice(0, 200);
    } catch {
      const raw = text.slice(6).trim();
      if (raw.startsWith("{")) {
        const message = raw.match(/"message"\s*:\s*"((?:\\.|[^"\\])*)"/);
        if (message) {
          try { return String(JSON.parse(`"${message[1]}"`)).slice(0, 200); } catch { /* Keep raw payload in detailed trace. */ }
        }
        return "该次整理未通过校验，详细原因可在执行轨迹中查看。";
      }
      return raw.slice(0, 200);
    }
  }
  return text.slice(0, 200);
}
export function eventFailed(event: TraceEvent) {
  return (
    [
      "tool_error",
      "task_error",
      "task_limit",
      "loop_guard",
      "task_interrupted",
    ].includes(event.event) ||
    String(event.output_preview ?? "").startsWith("ERROR:")
  );
}
// Only display tool targets already present in persisted arguments; never infer a completed plan step.
export function toolTarget(event: TraceEvent) {
  if (!event.arguments || typeof event.arguments !== "object") return "";
  const args = event.arguments as Record<string, unknown>;
  const value = args.url ?? args.filename ?? args.path ?? args.query;
  if (typeof value !== "string") return "";
  try {
    const url = new URL(value);
    return url.hostname + url.pathname;
  } catch {
    return value.slice(0, 160);
  }
}
export function activitySummary(events: TraceEvent[]) {
  const calls = toolCalls([...events].sort((a, b) => a.id - b.id));
  return {
    returned: calls.filter((c) => c.end && !callFailed(c)).length,
    failed: calls.filter(callFailed).length,
    current: calls.filter((c) => !c.end && !c.error).at(-1),
  };
}
export function eventTitle(
  event: TraceEvent,
  events: TraceEvent[],
  delivered = false,
) {
  if (event.event === "tool_retry" && delivered) return "曾重试工具";
  const start = events.find(
    (e) => e.call_id === event.call_id && e.event === "tool_start",
  );
  const name = String(start?.detail ?? event.detail ?? "");
  return activityLabels[event.event] ?? names[name] ?? name;
}

// A compact account of real work: one event per capability, plus the latest
// exception/recovery. IDs and ordering remain those of the persisted trace.
export function keyActivities(events: TraceEvent[]) {
  const rows = activityEvents(events);
  const groups = new Map<string, TraceEvent>();
  const calls = new Map(
    events.filter((e) => e.event === "tool_start").map((e) => [e.call_id, e]),
  );
  const categories: Record<string, string> = {
    update_plan: "plan",
    browser_navigate: "research",
    browser_search: "research",
    browser_snapshot: "research",
    browser_follow_link: "research",
    record_source: "research",
    browser_download: "download",
    download_public_file: "download",
    read_public_pdf: "analysis",
    inspect_downloaded_table: "analysis",
    aggregate_downloaded_table: "analysis",
    export_business_excel: "delivery",
    export_excel: "delivery",
    export_report: "delivery",
    record_requirement_result: "verification",
  };
  for (const e of rows) {
    if (eventFailed(e) || e.event === "tool_retry") continue;
    const name = String(calls.get(e.call_id)?.detail ?? e.detail ?? "");
    const group =
      e.event === "plan_updated"
        ? "plan"
        : e.event === "verification"
          ? "verification"
          : e.event === "tool_end"
            ? categories[name]
            : undefined;
    if (group) groups.set(group, e);
  }
  const plan = groups.get("plan");
  const resumeId = Math.max(
    0,
    ...events.filter((r) => r.event === "task_resume").map((r) => r.id),
  );
  const lastIssue = rows
    .filter(
      (e) =>
        (eventFailed(e) && e.id > resumeId && !eventRecovered(e, events)) ||
        ["tool_retry", "task_resume", "human_decision"].includes(e.event),
    )
    .at(-1);
  // Verification is accessible in the result area. Prefer visible work over
  // repeated requirement bookkeeping when other capabilities were executed.
  const work = [...groups.entries()]
    .filter(([key]) => key !== "plan" && key !== "verification")
    .map(([, e]) => e)
    .sort((a, b) => a.id - b.id);
  const preferred = [
    groups.get("analysis"),
    groups.get("delivery"),
    ...work.slice().reverse(),
  ].filter((e): e is TraceEvent => Boolean(e));
  const candidates = work.length
    ? [...new Map(preferred.map((e) => [e.id, e])).values()]
    : rows.filter((e) => !eventFailed(e) && e !== plan).reverse();
  const recent = candidates.slice(0, 4 - Number(!!plan) - Number(!!lastIssue));
  return [
    ...new Map(
      [
        ...(plan ? [plan] : []),
        ...recent,
        ...(lastIssue ? [lastIssue] : []),
      ].map((e) => [e.id, e]),
    ).values(),
  ].sort((a, b) => a.id - b.id);
}
