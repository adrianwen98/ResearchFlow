import { useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronDown,
  Circle,
  ListChecks,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import {
  activityEvents,
  keyActivities,
  activitySummary,
  eventFailed,
  eventRecovered,
  eventIssue,
  eventTitle,
  toolTarget,
} from "../activity";
import { names } from "../model";
import type { TraceEvent, Workspace } from "../types";
const time = (value?: string) =>
  value && !Number.isNaN(Date.parse(value))
    ? new Date(value).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "";
export default function ExecutionActivity({
  data,
  events,
  onDetails,
}: {
  data: Workspace;
  events: TraceEvent[];
  onDetails: () => void;
}) {
  const active = ["queued", "running"].includes(data.status);
  const delivered = Boolean(data.delivery?.usable);
  const [expanded, setExpanded] = useState(false);
  const rows = activityEvents(events),
    summary = activitySummary(events);
  const visible = expanded ? rows : keyActivities(events);
  const unresolvedWarnings = data.delivery?.execution_warning_count ?? 0;
  const recoveredWarnings =
    data.delivery?.recovered_execution_warning_count ?? 0;
  const warningSummary =
    unresolvedWarnings > 0
      ? `${unresolvedWarnings} 条未修正告警${
          recoveredWarnings ? ` · ${recoveredWarnings} 条已自动修正` : ""
        }`
      : recoveredWarnings
        ? `执行告警均已自动修正（${recoveredWarnings} 条）`
        : "";
  const resumeId = Math.max(
    0,
    ...events.filter((e) => e.event === "task_resume").map((e) => e.id),
  );
  const notable = rows
    .filter(
      (e) =>
        (eventFailed(e) && e.id > resumeId && !eventRecovered(e, events)) ||
        [
          "tool_retry",
          "task_resume",
          "plan_updated",
          "human_decision",
        ].includes(e.event),
    )
    .slice(-2);
  return (
    <section
      className={`execution-activity ${active ? "is-running" : ""}`}
      aria-label="任务执行进展"
    >
      <div className="activity-heading">
        <Activity size={18} />
        <strong>{active ? "Agent 正在执行任务" : delivered ? "执行记录" : "Agent 执行回顾"}</strong>
        {active && <span className="activity-live-state">
          {data.status === "queued" ? "排队中" : "运行中"}
        </span>}
        <button aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          {delivered && warningSummary && (
            <span style={{ color: "var(--ink-subtle)", fontSize: 12, fontWeight: 400, marginRight: 12, whiteSpace: "nowrap" }}>
              {warningSummary}
            </span>
          )}
          {expanded ? "收起记录" : delivered ? "展开记录" : "更多进展"}
          <ChevronDown size={16} />
        </button>
      </div>
      {(!delivered || expanded) && <>
        {active && (
          <div className="current-operation" role="status" aria-live="polite">
            <span className="operation-icon">
              <LoaderCircle className="spin" size={20} />
            </span>
            <div>
              <small>当前操作</small>
              <strong>
                {data.status === "queued"
                  ? "任务已提交，等待执行"
                  : summary.current
                    ? (names[summary.current.name] ?? summary.current.name)
                    : "Agent 正在执行任务"}
              </strong>
              {summary.current && <p>{toolTarget(summary.current.start)}</p>}
            </div>
          </div>
        )}
        {(active || expanded) && data.plan.length > 0 && (
          <details className="activity-plan">
            <summary>
              <ListChecks size={16} /> 任务计划{" "}
              <span>{data.plan.length} 项 · 不代表完成进度</span>
            </summary>
            <ol>
              {data.plan.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </details>
        )}
        {expanded && notable.length > 0 && (
          <div className="activity-notices">
            {notable.map((e) => {
              const failed = eventFailed(e);
              const recovered = failed && eventRecovered(e, events);
              const blocking =
                data.delivery?.warnings.find((w) => w.call_id === e.call_id)?.blocking ?? !delivered;
              const title = eventTitle(e, events, delivered);

              return (
                <div key={e.id} className={failed ? "attention" : "update"}>
                  {failed ? <TriangleAlert size={15} /> : <Activity size={15} />}
                  <span>
                    {failed
                      ? blocking
                        ? "阻塞错误"
                        : "历史非阻塞告警"
                      : "执行动态"}
                    {" · "}
                    {title}
                    {recovered && " · 后续已修正"}
                  </span>
                  <button onClick={onDetails}>
                    查看记录
                    <ArrowRight size={13} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
        {expanded && <div className="activity-subheading">
          <span>全部进展 · {summary.returned} 次工具正常返回</span>
        </div>}
        <ol className="activity-timeline">
          {visible.map((e) => {
            const recovered = eventRecovered(e, events),
              failed = eventFailed(e) && !recovered,
              blocking = data.delivery?.warnings.find((w) => w.call_id === e.call_id)?.blocking ?? !delivered,
              start = events.find(
                (s) => s.event === "tool_start" && s.call_id === e.call_id,
              );
            const target = start ? toolTarget(start) : "";
            const title = eventTitle(e, events, delivered);
            return (
              <li
                key={e.id}
                data-event-id={e.id}
                className={failed ? blocking ? "failed" : "warning" : recovered ? "recovered" : ""}
              >
                <span className="timeline-dot">
                  {failed ? (
                    <TriangleAlert size={14} />
                  ) : recovered || e.event === "tool_end" ? (
                    <Check size={14} />
                  ) : (
                    <Circle size={11} />
                  )}
                </span>
                <div>
                  <strong>{title}</strong>
                  {recovered && (
                    <span className="recovery-badge">后续已修正</span>
                  )}
                  {(expanded || recovered || failed || e.event === "tool_retry") && <small>
                    {recovered
                      ? "该次操作曾失败，后续调用已成功"
                      : failed
                        ? !blocking ? "非阻塞告警" : "该次操作未通过 · 阻塞错误"
                        : e.event === "tool_end"
                          ? "已返回"
                          : e.event === "tool_retry"
                            ? delivered
                              ? "工具曾重试（后续已恢复）"
                              : "工具正在重试"
                            : "已记录"}
                    {target ? ` · ${target}` : ""}
                  </small>}
                  {eventFailed(e) && (
                    <small>
                      {recovered ? "原错误：" : ""}
                      {eventIssue(e)}
                    </small>
                  )}
                </div>
                {expanded && <time>{time(e.time)}</time>}
              </li>
            );
          })}
        </ol>
        {!rows.length && (
          <p className="activity-empty">
            {active ? "等待首条工具进展…" : "该任务没有保存可展示的过程事件。"}
          </p>
        )}
        <div className="activity-footer">
          <button onClick={onDetails}>
            完整执行轨迹
            <ArrowRight size={14} />
          </button>
        </div>
      </>}
    </section>
  );
}
