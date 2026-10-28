import {
  Check,
  ChevronRight,
  LoaderCircle,
  Square,
  TriangleAlert,
} from "lucide-react";
import type { TraceEvent, Workspace } from "../types";
import { callFailed, names, toolCalls } from "../model";
interface Props {
  data: Workspace;
  events: TraceEvent[];
  busy: boolean;
  onCancel: () => void;
  onDetails: () => void;
  detailsOpen: boolean;
}
export default function TaskProgress({
  data,
  events,
  busy,
  onCancel,
  onDetails,
  detailsOpen,
}: Props) {
  const running = ["running", "queued"].includes(data.status);
  const calls = toolCalls(events),
    recent = calls.slice(-4);
  const current = calls.filter((c) => !c.end && !c.error).at(-1);
  const waiting = data.status === "awaiting_review";
  return (
    <section
      className="activity-stream"
      aria-label="任务执行进展"
      aria-live="polite"
    >
      <div className="activity-heading">
        <span className="agent-avatar">W</span>
        <div>
          <strong>
            {waiting
              ? "需要你确认后继续"
              : current && running
                ? (names[current.name] ?? current.name)
                : data.status === "queued"
                  ? "正在等待执行"
                  : running
                    ? "Agent 正在处理任务"
                    : "已保存的操作记录"}
          </strong>
          <p>
            {waiting
              ? "待审批操作尚未执行。"
              : running
                ? "进展来自实际工具调用，完整过程可随时展开。"
                : "以下记录来自这个任务。"}
          </p>
        </div>
        {running && <LoaderCircle size={18} className="spin" />}
      </div>
      {recent.length ? (
        <ol className="recent-actions">
          {recent.map((c) => (
            <li key={c.id}>
              {callFailed(c) ? (
                <TriangleAlert size={15} />
              ) : c.end ? (
                <Check size={15} />
              ) : running ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Square size={13} />
              )}
              <span>{names[c.name] ?? c.name}</span>
              <small>
                {callFailed(c)
                  ? "遇到问题"
                  : c.end
                    ? "已返回"
                    : running
                      ? "进行中"
                      : "未返回结果"}
              </small>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">尚无工具调用记录，等待后端更新。</p>
      )}
      <div className="activity-controls">
        <button
          className="text-button"
          onClick={onDetails}
          aria-expanded={detailsOpen}
        >
          查看完整执行过程
          <ChevronRight size={14} />
        </button>
        {data.capabilities.can_cancel && (
          <button className="text-button" disabled={busy} onClick={onCancel}>
            <Square size={12} />
            中断任务
          </button>
        )}
      </div>
    </section>
  );
}
