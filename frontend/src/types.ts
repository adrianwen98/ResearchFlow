export interface Metrics {
  requests?: number;
  model_calls?: number;
  tool_calls?: number;
  errors?: number;
  retries?: number;
  tool_attempts?: number;
  input_tokens?: number;
  output_tokens?: number;
  limits?: {
    model_requests?: number;
    tool_calls?: number;
    timeout_seconds?: number;
  };
}
export interface Task {
  delivery_outcome?: "completed" | "completed_with_warnings" | "partial" | "needs_review" | "failed";
  task_id: string;
  title?: string;
  task: string;
  status: string;
  status_label: string;
  created_at: string;
  metrics: Metrics;
  elapsed_seconds: number | null;
  read_only: boolean;
  execution_label: string;
  archived?: boolean;
}

export interface DeletionPreview {
  task_id: string;
  groups: {
    records: { files: number; bytes: number };
    files: { files: number; bytes: number };
  };
  checkpoint: { checkpoints: number; writes: number };
  active: boolean;
  locked: boolean;
  can_stop: boolean;
  can_delete: boolean;
  cleanup_pending: boolean;
}
export interface TraceEvent {
  id: number;
  time?: string;
  event: string;
  detail?: string;
  call_id?: string;
  arguments?: unknown;
  output_preview?: string;
  elapsed_seconds?: number;
  error_code?: string;
  [key: string]: unknown;
}
export interface Requirement {
  id: string;
  description: string;
  passed: boolean;
  status: string;
  reason?: string;
}
export interface Pending {
  tool?: string;
  approval_id?: string;
  reason?: string;
  [key: string]: unknown;
}
export interface Source {
  id: string;
  title: string;
  url: string;
  fact?: string;
  quote?: string;
}
export interface ModelBudget {
  used: number;
  limit: number;
  remaining: number;
  exhausted: boolean;
  revision: number;
  binding: string;
  max_addition: number;
  max_total: number;
  can_add: boolean;
  blocked_reason: string;
  other_limits: {
    tool_calls: number;
    timeout_seconds: number;
    graph_steps: number;
    tool_timeout: number;
  };
  tool_used: number;
  time_used: number;
  grants: {
    operation_id: string;
    added: number;
    old_limit: number;
    new_limit: number;
    confirmed_at: string;
  }[];
}
export interface Workspace extends Task {
  delivery?: {
    outcome: "completed" | "completed_with_warnings" | "partial" | "needs_review" | "failed";
    usable: boolean;
    label: string;
    headline: string;
    warning_count: number;
    execution_warning_count: number;
    recovered_execution_warning_count?: number;
    warnings: { kind: string; message: string; blocking: boolean; call_id?: string }[];
    semantic_accepted: boolean;
  } | null;
  task_brief?: TaskBrief | null;
  model_budget?: ModelBudget;
  completion_mode?: string;
  review_reason?: string;
  failure?: {
    code: string;
    label: string;
    next_action: string;
    retryable: boolean;
    detail: string;
  } | null;
  report: string;
  report_format?: "pdf" | "markdown" | "both";
  answer: string;
  error?: string;
  plan: string[];
  events: TraceEvent[];
  sources: Source[];
  pending_review: Pending[];
  requirements: {
    requirements: Requirement[];
    requirements_supported: boolean;
    quality_warnings?: string[];
  };
  artifacts: { artifacts_valid: boolean };
  review_binding: string;
  files: { name: string; bytes: number; url: string }[];
  capabilities: {
    can_cancel: boolean;
    can_resume: boolean;
    can_restart?: boolean;
    can_review: boolean;
    can_accept: boolean;
    has_checkpoint: boolean;
    locked: boolean;
    can_request_revision?: boolean;
  };
}
export interface StorageSummary {
  tasks: number;
  outputs_bytes: number;
  checkpoint_bytes: number;
  work_bytes: number;
  logs_bytes: number;
  freed_bytes?: number;
  warning?: string;
}
export interface Settings {
  model: string;
  model_requests: number;
  tool_calls: number;
  timeout_seconds: number;
  api_key_configured: boolean;
  allowed_domains: string[];
}
export interface Workbook {
  sheets: {
    name: string;
    row_count: number;
    column_count: number;
    columns: string[];
    rows: (string | number | boolean | null)[][];
    truncated: boolean;
  }[];
  max_rows: number;
  max_columns: number;
}

export type DeliveryTab = "pdf" | "report" | "data" | "sources" | "requirements";
export type WorkbenchAction = {
  kind: "create" | "related" | "resume" | "restart" | "review" | "accept" | "revision" | "unlock";
  title: string;
  description: string;
  body?: unknown;
  path?: string;
};


export interface TaskPreparation {
  version: number;
  task: string;
  report_format: "pdf" | "markdown" | "both";
  scope: string;
  defaults: string[];
  questions: { id: string; prompt: string; options: string[] }[];
  answers: Record<string, string>;
  ready: boolean;
  unanswered: string[];
  model_requests: number;
}
export interface TaskBrief {
  scope: string;
  user_clarifications: { question: string; answer: string }[];
  report_format: string;
}
