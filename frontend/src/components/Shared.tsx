import type { ReactNode } from "react";
import { stateInfo } from "../model";
export function Badge({
  status,
  demo = false,
}: {
  status: string;
  demo?: boolean;
}) {
  const s = demo ? { label: "演示任务", tone: "neutral" } : stateInfo(status);
  return (
    <span className={`badge ${s.tone}`}>
      <i />
      {s.label}
    </span>
  );
}
export function Empty({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
