import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Coins, LoaderCircle, Plus, X } from "lucide-react";
import { api } from "../api";
import { stateInfo } from "../model";
import type { ModelBudget, Settings, Workspace } from "../types";

export function validAddition(value: string, maximum: number) {
  return (
    /^\d+$/.test(value) &&
    Number.isSafeInteger(Number(value)) &&
    Number(value) >= 1 &&
    Number(value) <= maximum
  );
}

function BudgetDialog({
  budget,
  taskId,
  settings,
  onClose,
  onResumed,
}: {
  budget: ModelBudget;
  taskId: string;
  settings?: Settings;
  onClose: () => void;
  onResumed: (id: string) => void;
}) {
  const [choice, setChoice] = useState(
      budget.max_addition >= 20
        ? "20"
        : budget.max_addition >= 10
          ? "10"
          : "custom",
    ),
    [custom, setCustom] = useState(String(Math.min(20, budget.max_addition))),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const dialog = useRef<HTMLElement>(null),
    inFlight = useRef(false),
    operation = useRef(crypto.randomUUID());
  const value = choice === "custom" ? custom : choice,
    valid = validAddition(value, budget.max_addition),
    addition = valid ? Number(value) : 0;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  async function submit() {
    if (
      inFlight.current ||
      !valid ||
      !confirmed ||
      !settings?.api_key_configured
    )
      return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await api.post(`/tasks/${encodeURIComponent(taskId)}/budget-resume`, {
        task_id: taskId,
        additional_requests: addition,
        binding: budget.binding,
        operation_id: operation.current,
        confirm_paid_execution: true,
      });
      onResumed(taskId);
      onClose();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "追加失败，请刷新任务状态后重试。",
      );
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return createPortal(
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <section
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="budget-title"
        aria-describedby="budget-fees"
        className="budget-dialog"
        onKeyDown={(e) => {
          if (e.key === "Escape" && !busy) {
            e.stopPropagation();
            onClose();
          }
          if (e.key === "Tab") {
            const nodes = Array.from(
              dialog.current!.querySelectorAll<HTMLElement>(
                'button:not(:disabled),input:not(:disabled),summary,[tabindex="0"]',
              ),
            );
            if (e.shiftKey && document.activeElement === nodes[0]) {
              e.preventDefault();
              nodes.at(-1)?.focus();
            } else if (!e.shiftKey && document.activeElement === nodes.at(-1)) {
              e.preventDefault();
              nodes[0]?.focus();
            }
          }
        }}
      >
        <header>
          <div>
            <h2 id="budget-title">继续执行任务</h2>
          </div>
          <button
            className="icon-button"
            aria-label="关闭追加预算"
            disabled={busy}
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>
        <p>额度已用完，追加后从当前进度继续。</p>
        <fieldset disabled={busy}>
          <legend>追加次数</legend>
          <div className="budget-choices">
            {["10", "20", "custom"].map((x) => (
              <label key={x}>
                <input
                  type="radio"
                  name="budget-addition"
                  value={x}
                  disabled={x !== "custom" && Number(x) > budget.max_addition}
                  checked={choice === x}
                  onChange={() => {
                    setChoice(x);
                    setConfirmed(false);
                    operation.current = crypto.randomUUID();
                  }}
                />
                {x === "custom" ? "自定义" : `+${x} 次`}
              </label>
            ))}
          </div>
          {choice === "custom" && (
            <label className="budget-custom">
              追加次数（1–{budget.max_addition}）
              <input
                aria-label="自定义追加次数"
                type="number"
                min={1}
                max={budget.max_addition}
                step={1}
                value={custom}
                onChange={(e) => {
                  setCustom(e.target.value);
                  setConfirmed(false);
                  operation.current = crypto.randomUUID();
                }}
              />
            </label>
          )}
        </fieldset>
        <div className="budget-projection">
          <span>已用 / 总额度</span>
          <strong>
            {budget.used}/{budget.limit} <span>→</span> {budget.used}/
            {budget.limit + addition}
          </strong>
          <small>
            追加后可用 {Math.max(0, budget.limit + addition - budget.used)} 次
          </small>
        </div>
        <details className="budget-execution-details">
          <summary>执行详情</summary>
          <p>
            任务 ID：<code>{taskId}</code>
          </p>
          <p>模型：{settings?.model || "DeepSeek"}</p>
          <p>
            工具调用：{budget.tool_used} / {budget.other_limits.tool_calls} 次；
            已用时间：{Math.floor(budget.time_used)} /{" "}
            {budget.other_limits.timeout_seconds} 秒。
          </p>
          <p>其他预算和安全限制保持不变。总额度最多 {budget.max_total} 次。</p>
        </details>
        <label id="budget-fees" className="budget-consent">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>我确认追加 {addition} 次并继续，可能产生 API 费用。</span>
        </label>
        {!settings?.api_key_configured && (
          <p className="warning-text">模型服务尚未配置，暂时无法继续。</p>
        )}
        {!valid && (
          <p role="alert" className="warning-text">
            请输入 1–{budget.max_addition} 的整数追加次数。
          </p>
        )}
        {error && (
          <p role="alert" className="warning-text">
            {error}
          </p>
        )}
        <footer>
          <button className="secondary" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className="primary"
            disabled={
              busy || !valid || !confirmed || !settings?.api_key_configured
            }
            onClick={submit}
          >
            {busy && <LoaderCircle className="spin" size={16} />}确认并继续
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

export default function TaskBudget({
  data,
  settings,
  busy,
  onResumed,
  children,
}: {
  children?: ReactNode;
  data: Workspace;
  settings?: Settings;
  busy: boolean;
  onResumed: (id: string) => void;
}) {
  const [proposal, setProposal] = useState<ModelBudget>();
  const saved = data.model_budget;
  const used = data.metrics.requests ?? saved?.used,
    limit = data.metrics.limits?.model_requests ?? saved?.limit;
  const budget =
    saved && typeof used === "number" && typeof limit === "number"
      ? {
          ...saved,
          used,
          limit,
          remaining: Math.max(0, limit - used),
          exhausted: used >= limit,
          can_add:
            saved.can_add &&
            ["budget_exceeded", "partial"].includes(data.status),
        }
      : saved;
  if (!budget)
    return children ? (
      <section className="task-budget" aria-label="继续当前任务">
        {children}
      </section>
    ) : null;
  if (!Number.isFinite(budget.limit))
    return <p className="warning-text">{budget.blocked_reason}</p>;
  if (
    !(
      data.capabilities.can_resume ||
      data.capabilities.can_restart ||
      ["running", "queued", "budget_exceeded"].includes(data.status) ||
      budget.revision > 0
    )
  )
    return null;
  return (
    <section className="task-budget" aria-label="当前任务模型请求预算">
      <header>
        <span>
          <Coins size={18} />
          模型请求预算
        </span>
        <span>{data.delivery?.usable ? "当前交付已保留" : stateInfo(data.status).label}</span>
      </header>
      <div className="budget-numbers">
        <strong>
          {budget.used} <small>/ {budget.limit} 次</small>
        </strong>
        <span>
          剩余 <b>{budget.remaining}</b> 次
        </span>
      </div>
      <progress
        aria-label="累计模型请求使用情况"
        max={budget.limit}
        value={Math.min(budget.used, budget.limit)}
      />
      {children && !budget.exhausted ? (
        children
      ) : (
        <div className="budget-bottom">
          <p>
            {budget.exhausted
              ? data.delivery?.usable ? "预算已用尽，已输出当前最佳结果。" : "请求额度已用完，已有资料与任务进度保留。"
              : "累计请求计数跨恢复保留，不会重新开始整个任务。"}
          </p>
          {budget.exhausted && (
            <button
              className="primary"
              disabled={busy || !budget.can_add}
              onClick={() => setProposal({ ...budget })}
            >
              <Plus size={16} />
              追加额度并继续
            </button>
          )}
        </div>
      )}
      {budget.exhausted && !budget.can_add && (
        <p className="budget-boundary">{budget.blocked_reason}</p>
      )}
      {budget.grants.length > 0 && (
        <details className="budget-history">
          <summary>已确认的追加记录 · {budget.grants.length}</summary>
          {budget.grants.map((g) => (
            <p key={g.operation_id}>
              +{g.added} 次 · 上限 {g.old_limit} → {g.new_limit} ·{" "}
              {new Date(g.confirmed_at).toLocaleString("zh-CN")}
            </p>
          ))}
        </details>
      )}
      {proposal && (
        <BudgetDialog
          budget={proposal}
          taskId={data.task_id}
          settings={settings}
          onClose={() => setProposal(undefined)}
          onResumed={onResumed}
        />
      )}
    </section>
  );
}
