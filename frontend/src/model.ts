import type { TraceEvent } from "./types";
export const states: Record<string, { label: string; tone: string }> = {
  idle: { label: "准备就绪", tone: "neutral" },
  queued: { label: "等待执行", tone: "neutral" },
  running: { label: "执行中", tone: "active" },
  awaiting_review: { label: "等待操作审批", tone: "warning" },
  needs_review: { label: "结果待确认", tone: "warning" },
  completed: { label: "已完成", tone: "success" },
  completed_with_warnings: { label: "已完成（有告警）", tone: "warning" },
  failed: { label: "失败", tone: "error" },
  partial: { label: "部分完成", tone: "warning" },
  error: { label: "执行失败", tone: "error" },
  budget_exceeded: { label: "预算耗尽", tone: "warning" },
  permission_denied: { label: "权限拒绝", tone: "error" },
  timeout: { label: "执行超时", tone: "warning" },
  stalled: { label: "重复失败，已停止", tone: "error" },
  interrupted: { label: "已中断", tone: "warning" },
  incomplete: { label: "未完成", tone: "warning" },
};
export const stateInfo = (status: string) =>
  states[status] ?? { label: status, tone: "neutral" };
export function mergeEvents(old: TraceEvent[], incoming: TraceEvent[]) {
  const m = new Map(old.map((e) => [e.id, e]));
  incoming.forEach((e) => m.set(e.id, e));
  return [...m.values()].sort((a, b) => a.id - b.id).slice(-1200);
}
export function stage(status: string, events: TraceEvent[]) {
  if (status === "completed") return 4;
  if (status === "needs_review") return 3;
  if (events.some((e) => e.event === "verification")) return 2;
  if (events.some((e) => e.event === "tool_start")) return 1;
  return 0;
}
export function safeLink(url: string) {
  try {
    const u = new URL(url);
    return ["http:", "https:"].includes(u.protocol) &&
      !u.username &&
      !u.password
      ? url
      : undefined;
  } catch {
    return undefined;
  }
}
export const names: Record<string, string> = {
  browser_navigate: "浏览网页",
  browser_search: "搜索公开网页",
  browser_type: "输入搜索内容",
  browser_click: "点击页面控件",
  browser_follow_link: "打开已观察链接",
  browser_snapshot: "读取页面",
  browser_download: "浏览器下载",
  browser_select_option: "选择页面选项",
  load_skill: "加载任务技能",
  request_human_review: "等待人工处理",
  set_requirements: "明确验收要求",
  record_requirement_result: "核对交付需求",
  list_recorded_sources: "查看已收集来源",
  analyze_recorded_sources: "统计来源",
  update_plan: "调整执行计划",
  inspect_downloaded_table: "检查数据文件",
  aggregate_downloaded_table: "计算表格数据",
  export_business_excel: "生成业务 Excel",
  repair_business_excel: "修正业务表单元格",
  export_excel: "生成来源 Excel",
  export_report: "生成研究报告",
  record_source: "记录来源证据",
  read_public_pdf: "读取 PDF",
  download_public_file: "下载公开文件",
};
export interface ToolCall {
  id: string;
  name: string;
  start: TraceEvent;
  end?: TraceEvent;
  error?: TraceEvent;
  retry: number;
}
export function toolCalls(events: TraceEvent[]) {
  const calls = new Map<string, ToolCall>();
  for (const e of events) {
    if (!e.call_id) continue;
    if (e.event === "tool_start")
      calls.set(e.call_id, {
        id: e.call_id,
        name: String(e.detail ?? ""),
        start: e,
        retry: 0,
      });
    else {
      const c = calls.get(e.call_id);
      if (!c) continue;
      if (e.event === "tool_end") c.end = e;
      if (e.event === "tool_error") c.error = e;
      if (e.event === "tool_retry") c.retry++;
    }
  }
  return [...calls.values()];
}

export function callFailed(call: ToolCall) {
  return call.end
    ? String(call.end.output_preview ?? "").startsWith("ERROR:")
    : Boolean(call.error);
}
