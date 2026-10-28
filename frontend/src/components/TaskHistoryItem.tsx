import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Archive,
  ArchiveRestore,
  FileText,
  MoreHorizontal,
  Pencil,
  Trash2,
} from "lucide-react";
import type { Task } from "../types";
import { stateInfo } from "../model";

export type HistoryAction = "rename" | "archive" | "restore" | "delete";
export default function TaskHistoryItem({
  task,
  selected,
  busy,
  onChoose,
  onAction,
}: {
  task: Task;
  selected: boolean;
  busy: boolean;
  onChoose: () => void;
  onAction: (action: HistoryAction) => void;
}) {
  const [position, setPosition] = useState<{ top: number; left: number }>();
  const row = useRef<HTMLDivElement>(null),
    trigger = useRef<HTMLButtonElement>(null),
    menu = useRef<HTMLDivElement>(null);
  const title = task.title || task.task.split("\n")[0] || task.task_id;
  const state = task.read_only
    ? { label: "演示任务", tone: "neutral" }
    : stateInfo(task.delivery_outcome || task.status);
  useEffect(() => {
    if (!position) return;
    const outside = (e: PointerEvent) => {
      if (
        !row.current?.contains(e.target as Node) &&
        !menu.current?.contains(e.target as Node)
      )
        setPosition(undefined);
    };
    const close = () => setPosition(undefined);
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    menu.current
      ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
      ?.focus();
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [position]);
  const actions: [HistoryAction, string, React.ReactNode][] = [
    ["rename", "重命名", <Pencil size={15} />],
    [
      task.archived ? "restore" : "archive",
      task.archived ? "恢复到任务列表" : "归档",
      task.archived ? <ArchiveRestore size={15} /> : <Archive size={15} />,
    ],
    ["delete", "删除任务", <Trash2 size={15} />],
  ];
  return (
    <div ref={row} className={`history-row ${selected ? "selected" : ""}`}>
      <button
        disabled={busy}
        className={`task-item ${selected ? "selected" : ""}`}
        onClick={() => {
          setPosition(undefined);
          onChoose();
        }}
        title={title}
        aria-current={selected ? "page" : undefined}
      >
        <span className="task-glyph">
          <FileText size={17} />
        </span>
        <span className="task-copy">
          <strong>{title}</strong>
          <span className="task-status-line">
            <span className={`task-status ${state.tone}`} title={state.label}>
              {state.label}
            </span>
          </span>
        </span>
      </button>
      <button
        ref={trigger}
        className="history-more"
        aria-label={`管理任务：${title}`}
        aria-haspopup="menu"
        aria-expanded={Boolean(position)}
        disabled={busy}
        onClick={() => {
          if (position) {
            setPosition(undefined);
            return;
          }
          const rect = trigger.current!.getBoundingClientRect();
          setPosition({
            top: Math.max(
              8,
              Math.min(rect.bottom + 6, window.innerHeight - 152),
            ),
            left: Math.max(
              8,
              Math.min(rect.right - 184, window.innerWidth - 192),
            ),
          });
        }}
      >
        <MoreHorizontal size={18} />
      </button>
      {position &&
        createPortal(
          <div
            ref={menu}
            role="menu"
            aria-label="任务管理"
            className="history-menu"
            style={position}
            onKeyDown={(e) => {
              const items = [
                ...menu.current!.querySelectorAll<HTMLButtonElement>(
                  '[role="menuitem"]',
                ),
              ];
              const index = items.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              if (e.key === "Escape") {
                e.preventDefault();
                setPosition(undefined);
                trigger.current?.focus();
              } else if (
                ["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)
              ) {
                e.preventDefault();
                const next =
                  e.key === "Home"
                    ? 0
                    : e.key === "End"
                      ? items.length - 1
                      : (index +
                          (e.key === "ArrowDown" ? 1 : -1) +
                          items.length) %
                        items.length;
                items[next]?.focus();
              } else if (e.key === "Tab") setPosition(undefined);
            }}
          >
            {actions.map(([action, label, icon]) => (
              <button
                role="menuitem"
                key={action}
                className={action === "delete" ? "danger-text" : ""}
                onClick={() => {
                  setPosition(undefined);
                  onAction(action);
                }}
              >
                {icon}
                {label}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
