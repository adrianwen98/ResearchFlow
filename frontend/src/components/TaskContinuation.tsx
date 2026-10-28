import { useState } from "react";
import { ArrowUpRight, Plus } from "lucide-react";
export default function TaskContinuation({
  busy,
  onSubmit,
  onEdit,
}: {
  busy: boolean;
  onSubmit: (note: string) => void;
  onEdit: () => void;
}) {
  const [open, setOpen] = useState(false),
    [note, setNote] = useState("");
  return (
    <section className="task-continuation" aria-label="后续任务">
      {!open ? (
        <button className="continuation-trigger" onClick={() => setOpen(true)}>
          <Plus size={17} />
          补充要求，创建后续任务
          <ArrowUpRight size={15} />
        </button>
      ) : (
        <>
          <label htmlFor="related-goal">接下来想做什么？</label>
          <textarea
            id="related-goal"
            aria-label="后续任务要求"
            value={note}
            maxLength={3000}
            disabled={busy}
            onChange={(e) => {
              setNote(e.target.value);
              onEdit();
            }}
            placeholder="例如：补充第二个地区的数据，并重新生成对比表。"
            autoFocus
          />
          <div className="continuation-actions">
            <small>
              创建独立任务并关联当前任务；不会继续原Checkpoint或自动读取原文件。执行会另行计费。
            </small>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              收起
            </button>
            <button
              className="primary"
              disabled={busy || note.trim().length < 5}
              onClick={() => onSubmit(note)}
            >
              创建并执行
              <ArrowUpRight size={15} />
            </button>
          </div>
        </>
      )}
    </section>
  );
}
