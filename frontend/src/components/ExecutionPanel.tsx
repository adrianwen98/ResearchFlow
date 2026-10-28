import {
  Activity,
  X,
  Check,
  ChevronDown,
  Clock3,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Square,
  TriangleAlert,
} from "lucide-react";
import type { Settings, TraceEvent, Workspace } from "../types";
import { callFailed, names, toolCalls, type ToolCall } from "../model";
import { eventRecovered } from "../activity";
import { Badge } from "./Shared";
const fmt = (n: number | undefined) =>
  n === undefined ? "—" : n.toLocaleString("zh-CN");
const pretty = (v: unknown) =>
  typeof v === "string" ? v : JSON.stringify(v, null, 2);
const isActive = (s: string) => s === "running" || s === "queued";
interface Props {
  data?: Workspace;
  events: TraceEvent[];
  settings?: Settings;
  connection: string;
  selected: string;
  busy: boolean;
  elapsed: number | null | undefined;
  onCancel: () => void;
  onClose?: () => void;
}
export default function ExecutionPanel({
  data,
  events,
  settings,
  connection,
  selected,
  busy,
  elapsed,
  onCancel,
  onClose,
}: Props) {
  const status = data?.status ?? "idle",
    metrics = data?.metrics,
    used = metrics?.requests,
    limit = metrics?.limits?.model_requests;
  const calls = toolCalls(events),
    current = calls.filter((c) => !c.end).at(-1);
  const isRecovered = (call: ToolCall) => {
    // Prefer the structured tool_error event: a tool_end preview may carry the
    // tool name in detail, so recovery classification must see the error JSON.
    const evidence = call.error ?? call.end;
    return Boolean(
      callFailed(call) && evidence && eventRecovered(evidence, events),
    );
  };
  const failedCalls = calls.filter(callFailed),
    recoveredCalls = failedCalls.filter(isRecovered),
    unresolvedCalls = failedCalls.length - recoveredCalls.length;
  const delivery = data?.delivery,
    unresolvedWarnings = delivery?.execution_warning_count,
    recoveredWarnings = delivery?.recovered_execution_warning_count ?? 0,
    hasDeliveryCounts = typeof unresolvedWarnings === "number";
  const waitingModel =
    (events.filter((e) => e.event === "model_start").at(-1)?.id ?? 0) >
    (events.filter((e) => e.event === "model_end").at(-1)?.id ?? 0);
  return (
    <aside
      className="execution-panel"
      role="region"
      id="execution-details"
      aria-label="Agent 执行详情"
    >
      <div className="execution-title">
        <span>
          <Activity size={18} />
          执行观察
        </span>
        <span className={`connection ${connection === "live" ? "live" : ""}`}>
          <i />
          {!selected
            ? "就绪"
            : connection === "live"
              ? "实时连接"
              : connection === "reconnecting"
                ? "重连中 · 定时同步"
                : "已保存记录"}
        </span>
        {onClose && (
          <button
            id="close-execution-details"
            className="icon-button"
            aria-label="收起执行详情"
            onClick={onClose}
          >
            <X size={17} />
          </button>
        )}
      </div>
      {data?.plan.length ? (
        <details className="inspector-plan">
          <summary>当前保存的计划 · {data.plan.length} 步</summary>
          <ol>
            {data.plan.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </details>
      ) : null}
      <div className="runtime-summary">
        <div>
          <span>当前状态</span>
          <Badge status={status} />
        </div>
        <div>
          <span>模型请求</span>
          <strong>
            {fmt(used)}
            <small> / {limit === undefined ? "未记录" : fmt(limit)}</small>
          </strong>
        </div>
        {limit !== undefined && used !== undefined && (
          <div className="budget-track">
            <span
              style={{ width: `${Math.min(100, (100 * used) / limit)}%` }}
            />
          </div>
        )}
        <div className="runtime-grid">
          <div>
            <span>工具调用</span>
            <strong>{fmt(metrics?.tool_calls)}</strong>
          </div>
          <div>
            <span>
              {hasDeliveryCounts ? "错误（未修正 / 已修正）" : "错误 / 重试"}
            </span>
            <strong>
              {hasDeliveryCounts
                ? `${fmt(unresolvedWarnings)} / ${fmt(recoveredWarnings)}`
                : `${fmt(metrics?.errors)} / ${fmt(metrics?.retries)}`}
            </strong>
          </div>
          <div>
            <span>{isActive(status) ? "本次已运行" : "最近执行耗时"}</span>
            <strong>{elapsed == null ? "—" : `${elapsed.toFixed(1)}s`}</strong>
          </div>
          <div>
            <span>剩余请求</span>
            <strong>
              {limit !== undefined && used !== undefined
                ? Math.max(0, limit - used)
                : "—"}
            </strong>
          </div>
        </div>
        <details className="token-details">
          <summary>
            用量与执行边界
            <ChevronDown size={13} />
          </summary>
          <p>
            工具执行（含重试）：
            {`${fmt(metrics?.tool_attempts)} / ${fmt(metrics?.limits?.tool_calls)}`}
            <br />
            输入 Token：{fmt(metrics?.input_tokens)}
            <br />
            输出 Token：{fmt(metrics?.output_tokens)}
            <br />
            后端配置模型：{settings?.model ?? "未读取"}
            <br />
            来源：持久化用量与运行限制
            <br />
            费用：API 未记录实际金额
          </p>
        </details>
      </div>
      {current && isActive(status) && (
        <div className="current-tool">
          <LoaderCircle size={16} className="spin" />
          <div>
            <span>正在执行</span>
            <strong>{names[current.name] ?? current.name}</strong>
          </div>
        </div>
      )}
      {!current && waitingModel && isActive(status) && (
        <div className="current-tool">
          <LoaderCircle size={16} className="spin" />
          <div>
            <span>模型请求中</span>
            <strong>正在获取下一步执行决策</strong>
          </div>
        </div>
      )}
      <div className="trace-heading">
        <span>工具调用轨迹</span>
        <span>
          {calls.length} 条已加载
          {failedCalls.length
            ? ` · ${unresolvedCalls} 未修正 / ${recoveredCalls.length} 已修正`
            : ""}
        </span>
      </div>
      <p className="trace-scope">显示后端保存的最近 1200 条事件；工具返回为已保存的预览（最多 900 字符），未保存的内容无法还原。</p>
      <div className="trace-feed">
        {calls.length ? (
          calls.map((c) => {
            const recovered = isRecovered(c);
            return (
              <details
                className={`tool-card ${
                  recovered
                    ? "recovered"
                    : callFailed(c)
                      ? "failed"
                      : c.end
                        ? "done"
                        : "pending"
                }`}
                key={c.id}
              >
                <summary>
                  <span className="tool-dot">
                    {recovered ? (
                      <Check size={13} />
                    ) : callFailed(c) ? (
                      <TriangleAlert size={13} />
                    ) : c.end ? (
                      <Check size={13} />
                    ) : (
                      <Clock3 size={13} />
                    )}
                  </span>
                  <span className="tool-heading">
                    <strong>{names[c.name] ?? c.name}</strong>
                    <small>{c.name}</small>
                    {recovered && (
                      <span className="recovery-badge">后续已修正</span>
                    )}
                  </span>
                  <span className="tool-time">
                    {c.end?.elapsed_seconds !== undefined
                      ? `${c.end.elapsed_seconds.toFixed(2)}s`
                      : c.end
                        ? "已返回"
                        : isActive(status)
                          ? "执行中"
                          : status === "awaiting_review"
                            ? "等待审批"
                            : "未保存返回"}
                    <ChevronDown size={12} />
                  </span>
                </summary>
                <div className="tool-body">
                  <div className="tool-meta">
                    {c.start.time
                      ? new Date(c.start.time).toLocaleString("zh-CN")
                      : "时间未记录"}
                    {c.retry ? ` · 重试 ${c.retry} 次` : ""}
                  </div>
                  <span className="code-label">输入参数</span>
                  <pre>{pretty(c.start.arguments ?? "此公开轨迹未包含参数")}</pre>
                  <span className="code-label">
                    {callFailed(c)
                      ? recovered
                        ? "错误与返回（已修正）"
                        : "错误与返回"
                      : c.error
                        ? "重试后返回"
                        : "实际返回"}
                  </span>
                  <pre className={c.error && !recovered ? "error-code" : ""}>
                    {pretty(
                      c.end?.output_preview ??
                        c.error?.detail ??
                        "尚未保存返回内容",
                    )}
                  </pre>
                  {Boolean(c.error?.error_code || c.error?.code) && (
                    <span className="warning-text">
                      分类：{String(c.error?.error_code ?? c.error?.code)}
                      {recovered ? " · 后续已修正" : ""}
                    </span>
                  )}
                </div>
              </details>
            );
          })
        ) : (
          <div className="trace-empty">
            <Activity size={27} />
            <p>执行过程会在这里展开</p>
            <small>
              展示已保存的工具行为、返回和调整；不展示模型内部思考。
            </small>
          </div>
        )}
      </div>
      <details className="system-events">
        <summary><RefreshCw size={14}/>Harness / MCP 原始事件 · {events.length}<ChevronDown size={13}/></summary>
        {events.map(e=><details key={e.id} className="raw-event"><summary>{e.id} · {e.time?.slice(11,19)} · {e.event}</summary><pre>{pretty(e)}</pre></details>)}
        {selected && <a href={`/tasks/${encodeURIComponent(selected)}/files/trace.jsonl`} download>下载已保存轨迹</a>}
      </details>
      {data?.capabilities.can_cancel && (
        <button className="stop-button" disabled={busy} onClick={onCancel}>
          <Square size={13} />
          中断执行并保留状态
        </button>
      )}
      <div className="execution-foot">
        <ShieldCheck size={14} />
        <span>权限、预算与审批由后端 Harness 执行。</span>
      </div>
    </aside>
  );
}
