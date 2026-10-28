import { callFailed, toolCalls } from "./model";
import type { TraceEvent } from "./types";

/** Display observed actions, never infer that every task requires each phase. */
export function observedPhases(events: TraceEvent[], status: string) {
  const calls = toolCalls(events);
  const groups = [
    {
      id: "plan",
      label: "任务规划",
      test: (name: string) =>
        ["load_skill", "update_plan", "set_requirements"].includes(name),
    },
    {
      id: "web",
      label: "网页操作",
      test: (name: string) => name.startsWith("browser_"),
    },
    {
      id: "files",
      label: "文件处理",
      test: (name: string) =>
        [
          "read_public_pdf",
          "download_public_file",
          "inspect_downloaded_table",
          "aggregate_downloaded_table",
          "analyze_recorded_sources",
        ].includes(name),
    },
    {
      id: "delivery",
      label: "结果交付",
      test: (name: string) =>
        [
          "export_excel",
          "export_report",
          "export_business_excel",
          "repair_business_excel",
          "record_requirement_result",
        ].includes(name),
    },
  ];
  return groups.map((group) => {
    const found = calls.filter((c) => group.test(c.name));
    const failed = found.filter(callFailed).length;
    const returned = found.filter((c) => c.end).length;
    const waiting = found.some((c) => !c.end);
    let phaseState =
      waiting && status === "running"
        ? "active"
        : failed
          ? "error"
          : returned
            ? "observed"
            : "none";
    let note = found.length
      ? `${returned} 次返回${failed ? ` · ${failed} 次失败` : ""}`
      : "未记录调用";
    if (waiting)
      note =
        status === "awaiting_review"
          ? "等待人工决定"
          : status === "running"
            ? "正在执行"
            : `${returned} 次返回 · 有未返回调用`;
    if (group.id === "delivery" && status === "needs_review") {
      note = "已交付 · 待复核";
      phaseState = "review";
    }
    if (group.id === "delivery" && status === "completed") {
      note = "已完成";
      phaseState = "observed";
    }
    return {
      id: group.id,
      label: group.label,
      state: phaseState,
      note,
      calls: found.length,
    };
  });
}

/** An explicitly labelled verbatim paragraph, not a generated or fact-checked summary. */
export function reportExcerpt(report: string) {
  const block = report
    .split(/\r?\n\s*\r?\n/)
    .map((x) => x.replace(/^(?:#{1,6}[^\r\n]*\r?\n)+/, "").trim())
    .find((x) => x && !/^(?:#|>|\||[-*+]\s|\d+[.)]\s|```|---)/.test(x));
  if (!block) return { text: "", truncated: false };
  return { text: block.slice(0, 360), truncated: block.length > 360 };
}
export function outcomeText(status: string) {
  const labels: Record<string, string> = {
    completed: "任务已完成",
    needs_review: "交付已生成，等待你核对",
    partial: "任务部分完成，已有阶段性结果",
    incomplete: "任务尚未完成，请检查缺失内容",
    error: "任务执行失败，已保留现有记录",
    budget_exceeded: "预算已耗尽，请核对已生成的内容",
    permission_denied: "权限受限，任务已停止",
    timeout: "执行超时，已保留现有结果",
    stalled: "重复失败，任务已停止",
    interrupted: "任务已中断，可查看已保存结果",
    awaiting_review: "等待你的决定，以下为当前文件",
    running: "已生成阶段性文件，任务仍在执行",
    queued: "任务正在排队",
  };
  return labels[status] ?? "当前任务结果";
}
