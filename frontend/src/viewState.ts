import type { DeliveryTab } from "./types";

export type PreviewTab = DeliveryTab | "execution";
export interface TaskViewState {
  open: boolean;
  tab: PreviewTab;
  file: string;
  sheet: number;
}
type ViewStorage = Pick<Storage, "getItem" | "setItem">;
const defaults: TaskViewState = { open: false, tab: "report", file: "task_result.xlsx", sheet: 0 };
const tabs: PreviewTab[] = ["report", "pdf", "data", "sources", "execution", "requirements"];
const files = ["task_result.xlsx", "research_sources.xlsx"];

export function readTaskView(taskId: string, storage?: ViewStorage): TaskViewState {
  try {
    if (!taskId) return { ...defaults };
    const value = JSON.parse((storage ?? sessionStorage).getItem(`webtask:view:v1:${taskId}`) ?? "null");
    if (!value || typeof value !== "object") return { ...defaults };
    return {
      open: value.open === true,
      tab: tabs.includes(value.tab) ? value.tab : defaults.tab,
      file: files.includes(value.file) ? value.file : defaults.file,
      sheet: Number.isInteger(value.sheet) && value.sheet >= 0 && value.sheet < 8 ? value.sheet : 0,
    };
  } catch { return { ...defaults }; }
}
export function saveTaskView(taskId: string, view: TaskViewState, storage?: ViewStorage) {
  if (!taskId) return;
  try { (storage ?? sessionStorage).setItem(`webtask:view:v1:${taskId}`, JSON.stringify(view)); } catch { /* Viewing still works when browser storage is unavailable. */ }
}
export function readSidebarCollapsed() {
  try { return sessionStorage.getItem("webtask:sidebar-collapsed") === "true"; } catch { return false; }
}
export function saveSidebarCollapsed(value: boolean) {
  try { sessionStorage.setItem("webtask:sidebar-collapsed", String(value)); } catch { /* Optional presentation state. */ }
}
export function readPreviewWidth() {
  try {
    const width = Number(localStorage.getItem("webtask-preview-width"));
    return Number.isFinite(width) && width >= 300 && width <= 900 ? width : 520;
  } catch { return 520; }
}
export function savePreviewWidth(width: number) {
  try { localStorage.setItem("webtask-preview-width", String(width)); } catch { /* Optional presentation state. */ }
}
