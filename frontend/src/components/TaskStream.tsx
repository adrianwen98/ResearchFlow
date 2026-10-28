import { useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ChevronRight,
  FileSpreadsheet,
  FileText,
  Sparkles,
} from "lucide-react";
import ExecutionActivity from "./ExecutionActivity";
import { outcomeText, reportExcerpt } from "../ux";
import type { DeliveryTab, TraceEvent, Workspace } from "../types";

export { activityEvents as streamEvents } from "../activity";
const clock = (time?: string) =>
  time && !Number.isNaN(Date.parse(time))
    ? new Date(time).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
function RequestContent({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className={`request-content ${text.length > 200 ? "collapsible" : ""} ${expanded ? "expanded" : ""}`}
    >
      <p>{expanded || text.length <= 200 ? text : text.slice(0, 200) + "…"}</p>
      {text.length > 200 && (
        <button
          className="request-expand"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "收起任务要求" : "展开完整任务要求"}
        </button>
      )}
    </div>
  );
}
export default function TaskStream({
  data,
  events,
  onOpenTab,
  onFile,
  onDetails,
  children,
}: {
  data: Workspace;
  events: TraceEvent[];
  onOpenTab: (tab: DeliveryTab) => void;
  onFile: (file: Workspace["files"][number]) => void;
  onDetails: () => void;
  children?: React.ReactNode;
}) {
  const active = ["running", "queued"].includes(data.status);
  const excerpt = reportExcerpt(data.report);
  const summary = data.failure ? "" : data.answer || excerpt.text;
  const summaryPreview = summary.split(/\n\s*\n/)[0].slice(0, 180);
  const delivered = Boolean(data.delivery?.usable);
  const execution = <div className="stream-execution"><ExecutionActivity
    key={data.task_id} data={data} events={events} onDetails={onDetails}
  /></div>;
  return (
    <div className="task-stream" aria-label="连续任务交互">
      <article className="request-message">
        <div className="message-meta">
          <span className="person-avatar">你</span>
          <strong>任务要求</strong>
          <time>{clock(data.created_at)}</time>
        </div>
        <RequestContent key={data.task_id} text={data.task} />
        {data.task_brief && <details className="task-brief">
          <summary>任务范围{data.task_brief.user_clarifications.length ? "与补充要求" : ""}</summary>
          <p>{data.task_brief.scope}</p>
          {data.task_brief.user_clarifications.map(item => <p key={item.question}><strong>{item.question}</strong><br />{item.answer}</p>)}
        </details>}
      </article>
      <article className="agent-message">
        <div className="message-meta">
          <span className="agent-avatar">
            <Sparkles size={17} />
          </span>
          <strong>ResearchFlow Agent</strong>
        </div>
        {data.execution_label && (
          <p className="record-label">{data.execution_label}</p>
        )}
        <div className={delivered ? "delivery-first" : "execution-first"}>
        {!delivered && execution}
        {!delivered && <div className="stream-delivery-notices">{children}</div>}
        {!active && data.status !== "awaiting_review" && (
          <section className="stream-result" aria-label="任务结果">
            <div className="result-heading">
              <div>
                <h2>{data.delivery?.headline || outcomeText(data.status)}</h2>
              </div>
              <span className="delivery-count">{data.files.length} 个文件</span>
            </div>
            {delivered && data.status === "budget_exceeded" && <p className="delivery-budget-note">预算已用尽，已保存阶段性结果；请查看未完成需求。</p>}
            {summary && (
              <p className="result-summary">
                {summaryPreview}
                {summary.length > summaryPreview.length ? "…" : ""}
              </p>
            )}
            {summary.length > summaryPreview.length && (
              <details className="saved-answer">
                <summary>展开交付说明</summary>
                <p>{summary.slice(summaryPreview.length)}</p>
              </details>
            )}
            {data.status === "completed" && (
              <p className="completion-receipt">
                {data.completion_mode === "automatic_objective_checks"
                  ? "客观检查已通过 · 自动完成"
                  : data.completion_mode === "human_acceptance"
                    ? "已记录人工验收"
                    : "完成状态来自已保存记录"}
              </p>
            )}
            {data.files.length > 0 ? (
              <div className="stream-files">
                {data.files.map((f) => (
                  <div className="stream-file" key={f.name}>
                    <button
                      onClick={() => onFile(f)}
                      aria-label={`预览 ${f.name}`}
                    >
                      {f.name.endsWith(".xlsx") ? (
                        <FileSpreadsheet size={24} />
                      ) : (
                        <FileText size={24} />
                      )}
                      <span>
                        <em className="file-format">
                          {f.name.endsWith(".xlsx")
                            ? "Excel"
                            : f.name.endsWith(".pdf") ? "PDF" : f.name.endsWith(".md")
                              ? "Markdown"
                              : f.name.endsWith(".json") ? "JSON" : "文件"}
                        </em>
                        <strong>
                          {f.name === "research_report.pdf" ? "PDF 研究报告" : f.name === "research_report.md"
                            ? "研究报告"
                            : f.name === "task_result.xlsx"
                              ? "业务数据表"
                              : f.name === "research_sources.xlsx"
                                ? "来源证据表"
                                : f.name}
                        </strong>
                        <small>
                          {(f.bytes / 1024).toFixed(1)} KB
                        </small>
                      </span>
                      <ChevronRight size={15} />
                    </button>
                    <a href={f.url} download aria-label={`下载 ${f.name}`}>
                      <ArrowDownToLine size={16} />
                    </a>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted small">尚无交付文件。</p>
            )}
            <div className="result-actions">
              <button onClick={() => onOpenTab("sources")}>
                {data.sources.length} 条来源证据 <ArrowRight size={13} />
              </button>
              <button onClick={() => onOpenTab("requirements")}>
                逐项验收 ·{" "}
                {data.requirements.requirements.filter((r) => r.passed).length}/
                {data.requirements.requirements.length} <ArrowRight size={13} />
              </button>
              <button onClick={onDetails}>
                执行详情 <ArrowRight size={13} />
              </button>
            </div>
          </section>
        )}
        {delivered && <div className="stream-delivery-notices">{children}</div>}
        {delivered && execution}
        </div>
        {active && data.files.length > 0 && (
          <div className="result-actions">
            {data.files.map((f) => (
              <button key={f.name} onClick={() => onFile(f)}>
                <FileText size={14} />
                {f.name}
              </button>
            ))}
          </div>
        )}
      </article>
    </div>
  );
}
