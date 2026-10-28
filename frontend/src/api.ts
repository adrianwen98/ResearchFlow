import type {
  Task,
  Workspace,
  Workbook,
  Settings,
  DeletionPreview,
  StorageSummary,
} from "./types";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(
      response.status,
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return response.json() as Promise<T>;
}
export const api = {
  tasks: (archived = false) =>
    request<{ tasks: Task[] }>(`/tasks?archived=${archived}`),
  deletion: (id: string) =>
    request<DeletionPreview>(`/tasks/${encodeURIComponent(id)}/deletion`),
  deleteTask: (
    id: string,
    options: {
      delete_records: boolean;
      delete_checkpoint: boolean;
      delete_files: boolean;
    },
  ) =>
    request(`/tasks/${encodeURIComponent(id)}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation: id, ...options }),
    }),
  settings: () => request<Settings>("/settings"),
  storage: () => request<StorageSummary>("/storage"),
  cleanup: () =>
    request<StorageSummary>("/maintenance/cleanup", { method: "POST" }),
  workspace: (id: string) =>
    request<Workspace>(`/tasks/${encodeURIComponent(id)}/workspace`),
  workbook: (id: string, filename = "task_result.xlsx") =>
    request<Workbook>(`/tasks/${encodeURIComponent(id)}/workbook?filename=${encodeURIComponent(filename)}`),
  post: <T = unknown>(path: string, body: unknown = {}, key?: string) =>
    request<T>(path, {
      method: "POST",
      body: JSON.stringify(body),
      headers: key ? { "Idempotency-Key": key } : {},
    }),
};
