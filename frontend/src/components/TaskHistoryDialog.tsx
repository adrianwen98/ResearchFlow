import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Pencil, Trash2 } from "lucide-react";
import { api } from "../api";
import type { DeletionPreview, Task } from "../types";

export const retainedOptions = {
  delete_records: false,
  delete_checkpoint: false,
  delete_files: false,
};
export function CleanupOptions({
  value,
  onChange,
  disabled,
  preview,
}: {
  value: typeof retainedOptions;
  onChange: (value: typeof retainedOptions) => void;
  disabled: boolean;
  preview?: DeletionPreview;
}) {
  return (
    <fieldset className="cleanup-options" disabled={disabled}>
      <legend>同时永久清理（可选）</legend>
      <label>
        <input
          type="checkbox"
          checked={value.delete_records}
          onChange={(e) =>
            onChange({ ...value, delete_records: e.target.checked })
          }
        />
        <span>
          <strong>任务记录</strong>
          <small>
            任务描述、执行过程和本任务记忆
            {preview ? ` · ${preview.groups.records.files} 个文件` : ""}
            。
          </small>
        </span>
      </label>
      <label>
        <input
          type="checkbox"
          checked={value.delete_checkpoint}
          onChange={(e) =>
            onChange({ ...value, delete_checkpoint: e.target.checked })
          }
        />
        <span>
          <strong>恢复进度</strong>
          <small>
            删除后无法从上次进度继续
            {preview
              ? ` · ${preview.checkpoint.checkpoints} 个保存点`
              : ""}
            。
          </small>
        </span>
      </label>
      <label>
        <input
          type="checkbox"
          checked={value.delete_files}
          onChange={(e) =>
            onChange({ ...value, delete_files: e.target.checked })
          }
        />
        <span>
          <strong>任务文件</strong>
          <small>
            报告、表格和下载文件
            {preview ? ` · ${preview.groups.files.files} 个文件` : ""}
            。
          </small>
        </span>
      </label>
      <small>仅清理当前任务，不影响其他任务和通用记忆。</small>
    </fieldset>
  );
}

export default function TaskHistoryDialog({
  task,
  kind,
  onClose,
  onSaved,
}: {
  task: Task;
  kind: "rename" | "delete";
  onClose: () => void;
  onSaved: (kind: "rename" | "delete", task: Task) => Promise<void>;
}) {
  const [name, setName] = useState(
    (task.title || task.task.split("\n")[0] || task.task_id).slice(0, 160),
  );
  const [options, setOptions] = useState(retainedOptions),
    [preview, setPreview] = useState<DeletionPreview>(),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(kind === "delete"),
    [error, setError] = useState("");
  const dialog = useRef<HTMLElement>(null),
    inFlight = useRef(false);
  const fetchPreview = async () => {
    setLoading(true);
    try {
      setPreview(await api.deletion(task.task_id));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    let gone = false;
    if (kind === "delete")
      void api
        .deletion(task.task_id)
        .then((v) => {
          if (!gone) setPreview(v);
        })
        .catch((e) => {
          if (!gone) setError(e.message);
        })
        .finally(() => {
          if (!gone) setLoading(false);
        });
    return () => {
      gone = true;
    };
  }, [kind, task.task_id]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const focus = dialog.current?.querySelector<HTMLInputElement>(
      kind === "rename" ? "input" : "button",
    );
    focus?.focus();
    if (kind === "rename") focus?.select?.();
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !inFlight.current) {
        e.preventDefault();
        onClose();
      }
      if (e.key === "Tab") {
        const items = [
          ...dialog.current!.querySelectorAll<HTMLElement>(
            "button:not(:disabled),input:not(:disabled)",
          ),
        ];
        if (!items.length) return;
        if (e.shiftKey && document.activeElement === items[0]) {
          e.preventDefault();
          items.at(-1)?.focus();
        } else if (!e.shiftKey && document.activeElement === items.at(-1)) {
          e.preventDefault();
          items[0].focus();
        }
      }
    };
    window.addEventListener("keydown", handle);
    return () => {
      window.removeEventListener("keydown", handle);
      if (previous?.isConnected) previous.focus();
    };
  }, [kind, onClose]);
  const submit = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      if (kind === "rename")
        await api.post(`/tasks/${task.task_id}/rename`, { title: name.trim() });
      else await api.deleteTask(task.task_id, options);
      await onSaved(kind, task);
    } catch (e) {
      setError((e as Error).message);
      if (kind === "delete")
        void api
          .deletion(task.task_id)
          .then(setPreview)
          .catch(() => {});
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  const stop = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await api.post(`/tasks/${task.task_id}/cancel`);
      await fetchPreview();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <div
      className="modal-overlay"
      onClick={() => {
        if (!inFlight.current) onClose();
      }}
    >
      <section
        className="modal history-dialog"
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`modal-icon ${kind === "delete" ? "danger-text" : ""}`}>
          {kind === "rename" ? <Pencil size={22} /> : <Trash2 size={22} />}
        </div>
        <h2 id="history-dialog-title">
          {kind === "rename" ? "重命名任务" : "删除这个任务？"}
        </h2>
        <p className="history-target">
          {task.title || task.task.split("\n")[0]}
        </p>
        {kind === "rename" ? (
          <>
            <label htmlFor="history-task-title">任务名称</label>
            <input
              id="history-task-title"
              aria-label="任务名称"
              value={name}
              maxLength={160}
              disabled={busy}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim()) void submit();
              }}
            />
            <p>仅修改显示名称，原始目标、报告和 Checkpoint 保留。</p>
          </>
        ) : (
          <>
            <p>
              默认只从历史列表移除，保留本地数据。勾选下方项目会永久删除对应数据，无法恢复。
            </p>
            {loading ? (
              <p role="status">正在检查任务…</p>
            ) : (
              <>
                {preview && !preview.can_delete && (
                  <div className="history-blocked">
                    <strong>
                      {preview.can_stop
                        ? "任务正在执行或排队，请先安全停止。"
                        : "任务仍被占用，暂时不能删除。请稍后重试。"}
                    </strong>
                    <div>
                      {preview.can_stop && (
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() => void stop()}
                        >
                          先安全停止任务
                        </button>
                      )}
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => void fetchPreview()}
                      >
                        重新检查
                      </button>
                    </div>
                  </div>
                )}
                <CleanupOptions
                  value={options}
                  onChange={setOptions}
                  disabled={busy || !preview?.can_delete}
                  preview={preview}
                />
              </>
            )}
          </>
        )}
        {error && (
          <p className="warning-text" role="alert">
            {error}
          </p>
        )}
        <div className="action-row">
          <button className="secondary" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className={kind === "delete" ? "danger-button" : "primary"}
            disabled={
              busy ||
              loading ||
              (kind === "rename" ? !name.trim() : !preview?.can_delete)
            }
            onClick={() => void submit()}
          >
            {busy && <LoaderCircle size={15} className="spin" />}
            {busy
              ? "正在处理"
              : kind === "rename"
                ? "保存名称"
                : "确认删除任务"}
          </button>
        </div>
      </section>
    </div>
  );
}
