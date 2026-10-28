import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  HardDrive,
  Layers3,
  ListChecks,
  LoaderCircle,
  PanelLeftClose,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  TriangleAlert,
  X,
} from "lucide-react";
import ApprovalDetails from "./components/ApprovalDetails";
import TaskHistoryItem, {
  type HistoryAction,
} from "./components/TaskHistoryItem";
import TaskHistoryDialog from "./components/TaskHistoryDialog";
import StorageDialog from "./components/StorageDialog";
import TaskContinuation from "./components/TaskContinuation";
import { parentTask, relatedGoal } from "./related";
import TaskBudget from "./components/TaskBudget";
import TaskComposer from "./components/TaskComposer";
import TaskStream from "./components/TaskStream";
import PreviewPane, { type PreviewTab } from "./components/PreviewPane";
import ThemeToggle from "./components/ThemeToggle";

import DeliveryPanel from "./components/DeliveryPanel";
import ExecutionPanel from "./components/ExecutionPanel";
import { Badge, Empty } from "./components/Shared";
import { api, ApiError } from "./api";
import { mergeEvents } from "./model";
import { RequestScope } from "./requestScope";
import {
  readTaskView,
  saveTaskView,
  readSidebarCollapsed,
  saveSidebarCollapsed,
} from "./viewState";
import { applyTheme, initialTheme, saveTheme, type Theme } from "./theme";
import type {
  Settings,
  Task,
  TraceEvent,
  Workbook,
  Workspace,
  DeliveryTab,
  WorkbenchAction,
  TaskPreparation,
} from "./types";

const isActive = (s: string) => s === "running" || s === "queued";

export default function App() {
  const centerRef = useRef<HTMLElement>(null);
  const autoFollow = useRef(true);
  const [initialId] = useState(
    () => new URLSearchParams(location.search).get("task") ?? "",
  );
  const [initialView] = useState(() => readTaskView(initialId));
  const workspaceScope = useRef(new RequestScope());
  const workbookScope = useRef(new RequestScope());
  const [selected, setSelected] = useState(initialId),
    [tasks, setTasks] = useState<Task[]>([]),
    [settings, setSettings] = useState<Settings>();
  const [theme, setTheme] = useState<Theme>(() => initialTheme());
  const [workspaceSnapshot, setData] = useState<Workspace>(),
    [workbookSnapshot, setWorkbook] = useState<{
      taskId: string;
      file: string;
      value: Workbook;
    }>(),
    [events, setEvents] = useState<TraceEvent[]>([]);
  const [tab, setTab] = useState<DeliveryTab>(
      initialView.tab === "execution" ? "report" : initialView.tab,
    ),
    [sheet, setSheet] = useState(initialView.sheet),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all");
  const [reportFormat, setReportFormat] = useState<"pdf" | "markdown" | "both">(
    "both",
  );
  const [preparation, setPreparation] = useState<TaskPreparation>();
  const [clarifications, setClarifications] = useState<Record<string, string>>(
    {},
  );
  const [preparing, setPreparing] = useState(false);
  const intakeRevision = useRef(0);
  const [goal, setGoal] = useState(""),
    [criteria, setCriteria] = useState(""),
    [followup, setFollowup] = useState("");
  useEffect(() => {
    setPreparation(undefined);
    setClarifications({});
    intakeRevision.current += 1;
  }, [goal, criteria, reportFormat, selected]);
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  const [error, setError] = useState(""),
    [syncError, setSyncError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false),
    [bookError, setBookError] = useState("");
  const [connection, setConnection] = useState<
      "saved" | "live" | "reconnecting"
    >("saved"),
    [action, setAction] = useState<WorkbenchAction>(),
    [checked, setChecked] = useState<string[]>([]),
    [mobileNav, setMobileNav] = useState(false);
  const [historyArchived, setHistoryArchived] = useState(
    () => new URLSearchParams(location.search).get("history") === "archived",
  );
  const [storageOpen, setStorageOpen] = useState(false);
  const [historyDialog, setHistoryDialog] = useState<{
    task: Task;
    kind: "rename" | "delete";
  }>();
  const historyRequest = useRef(0);
  const historyModeRef = useRef(historyArchived);
  historyModeRef.current = historyArchived;
  const closeHistoryDialog = useCallback(() => setHistoryDialog(undefined), []);
  const setHistoryView = useCallback((archived: boolean) => {
    historyModeRef.current = archived;
    setHistoryArchived(archived);
    const url = new URL(location.href);
    if (archived) url.searchParams.set("history", "archived");
    else url.searchParams.delete("history");
    history.replaceState(null, "", url);
  }, []);
  const [runNow, setRunNow] = useState(Date.now());
  const [sidebarCollapsed, setSidebarCollapsed] =
    useState(readSidebarCollapsed);
  const [detailsOpen, setDetailsOpen] = useState(initialView.open);
  const [previewTab, setPreviewTab] = useState<PreviewTab>(initialView.tab);
  const [previewFile, setPreviewFile] = useState(initialView.file);
  const data =
    workspaceSnapshot?.task_id === selected ? workspaceSnapshot : undefined;
  const book =
    workbookSnapshot?.taskId === selected &&
    workbookSnapshot.file === previewFile
      ? workbookSnapshot.value
      : undefined;
  useEffect(
    () =>
      saveTaskView(selected, {
        open: detailsOpen,
        tab: previewTab,
        file: previewFile,
        sheet,
      }),
    [selected, detailsOpen, previewTab, previewFile, sheet],
  );
  useEffect(() => saveSidebarCollapsed(sidebarCollapsed), [sidebarCollapsed]);
  const [streamRevision, setStreamRevision] = useState(0);
  const lastSelected = useRef<string | null>(null);
  const modalRef = useRef<HTMLElement>(null);
  const inFlight = useRef(false),
    createKey = useRef(""),
    selectedRef = useRef(selected),
    lastCursor = useRef(0);
  selectedRef.current = selected;
  const choose = useCallback((id: string) => {
    if (id === selectedRef.current) {
      setMobileNav(false);
      return;
    }
    workspaceScope.current.invalidate();
    workbookScope.current.invalidate();
    selectedRef.current = id;
    const view = readTaskView(id);
    setData(undefined);
    setWorkbook(undefined);
    setEvents([]);
    setBookError("");
    setLoading(Boolean(id));
    setPreviewTab(view.tab);
    setPreviewFile(view.file);
    setSheet(view.sheet);
    setSelected(id);
    setError("");
    setSyncError("");
    setNotice("");
    setChecked([]);
    setTab(view.tab === "execution" ? "report" : view.tab);
    setMobileNav(false);
    setDetailsOpen(view.open);
    setFollowup("");
    const url = new URL(location.href);
    if (id) url.searchParams.set("task", id);
    else url.searchParams.delete("task");
    history.replaceState(null, "", url);
  }, []);
  const historyLoad = useCallback(async () => {
    const request = ++historyRequest.current;
    const response = await api.tasks(historyArchived);
    if (
      request === historyRequest.current &&
      historyModeRef.current === historyArchived
    )
      setTasks(response.tasks);
    return response.tasks;
  }, [historyArchived]);
  useEffect(() => {
    void api
      .settings()
      .then(setSettings)
      .catch((e) => setError(String(e.message)));
    void historyLoad().catch((e) => setError(e.message));
    const id = setInterval(() => {
      void historyLoad().catch(() => {});
    }, 10000);
    return () => clearInterval(id);
  }, [historyLoad]);
  const reload = useCallback(
    async (id: string, ownsRequest = workspaceScope.current.capture()) => {
      const value = await api.workspace(id);
      if (selectedRef.current === id && ownsRequest()) {
        setData(value);
        setSyncError("");
        setEvents((old) => mergeEvents(old, value.events));
        lastCursor.current = Math.max(
          lastCursor.current,
          ...value.events.map((e) => e.id),
          0,
        );
      }
      return value;
    },
    [],
  );
  useEffect(() => {
    workspaceScope.current.invalidate();
    const ownsRequest = workspaceScope.current.capture();
    if (lastSelected.current !== selected) {
      if (centerRef.current) centerRef.current.scrollTop = 0;
      autoFollow.current = true;
      setData(undefined);
      setWorkbook(undefined);
      setEvents([]);
      setChecked([]);
      setBookError("");
      lastCursor.current = 0;
      lastSelected.current = selected;
    }
    if (!selected) {
      setLoading(false);
      return;
    }
    let disposed = false;
    let stream: EventSource | undefined;
    let poll: ReturnType<typeof setInterval> | undefined;
    setLoading(true);
    const refresh = () =>
      reload(selected, ownsRequest).catch((e) => {
        if (!disposed && ownsRequest()) setSyncError(e.message);
      });
    const connect = (cursor: number) => {
      if (disposed || !ownsRequest() || selectedRef.current !== selected)
        return;
      stream = new EventSource(
        `/tasks/${encodeURIComponent(selected)}/events?after=${cursor}`,
      );
      stream.onopen = () => {
        if (!disposed && ownsRequest()) setConnection("live");
      };
      stream.addEventListener("trace", (e) => {
        if (disposed || !ownsRequest() || selectedRef.current !== selected)
          return;
        try {
          const event = JSON.parse((e as MessageEvent).data) as TraceEvent;
          lastCursor.current = Math.max(lastCursor.current, event.id);
          setEvents((old) => mergeEvents(old, [event]));
          if (
            ["plan_updated", "task_end", "task_interrupted"].includes(
              event.event,
            )
          )
            void refresh();
        } catch {
          setConnection("reconnecting");
        }
      });
      stream.addEventListener("state", (e) => {
        if (disposed || !ownsRequest() || selectedRef.current !== selected)
          return;
        try {
          const value = JSON.parse((e as MessageEvent).data) as Task;
          setData((old) => (old ? { ...old, ...value } : old));
          if (!isActive(value.status)) void refresh();
        } catch {
          setConnection("reconnecting");
        }
      });
      stream.addEventListener("settled", () => {
        stream?.close();
        if (!disposed && ownsRequest()) {
          setConnection("saved");
          void refresh();
          void historyLoad().catch(() => {});
        }
      });
      stream.onerror = () => {
        if (!disposed && ownsRequest()) setConnection("reconnecting");
      };
    };
    void reload(selected, ownsRequest)
      .then((value) => {
        if (!disposed && ownsRequest())
          connect(Math.max(0, ...value.events.map((e) => e.id)));
      })
      .catch((e) => {
        if (!disposed && ownsRequest()) setSyncError(e.message);
      })
      .finally(() => {
        if (!disposed && ownsRequest()) setLoading(false);
      });
    poll = setInterval(() => {
      void refresh();
    }, 5000);
    return () => {
      disposed = true;
      if (ownsRequest()) workspaceScope.current.invalidate();
      stream?.close();
      if (poll) clearInterval(poll);
    };
  }, [selected, reload, historyLoad, streamRevision]);
  useEffect(() => {
    if (!data || !selected || !detailsOpen || previewTab !== "data") return;
    workbookScope.current.invalidate();
    const ownsRequest = workbookScope.current.capture();
    setWorkbook(undefined);
    let gone = false;
    setBookError("");
    void api
      .workbook(selected, previewFile)
      .then((v) => {
        if (!gone && ownsRequest() && selectedRef.current === selected) {
          setWorkbook({ taskId: selected, file: previewFile, value: v });
          setSheet((old) => (v.sheets[old] ? old : 0));
        }
      })
      .catch((e) => {
        if (!gone && ownsRequest() && selectedRef.current === selected)
          setBookError(
            e instanceof ApiError && e.status === 404
              ? `此任务没有可预览的 ${previewFile}。`
              : e.message,
          );
      });
    return () => {
      gone = true;
      if (ownsRequest()) workbookScope.current.invalidate();
    };
  }, [
    selected,
    detailsOpen,
    previewTab,
    previewFile,
    data?.files.map((f) => f.name + f.bytes).join("|"),
  ]);
  useEffect(() => {
    if (!data || !isActive(data.status)) return;
    const id = setInterval(() => setRunNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [data?.status]);
  useEffect(() => {
    if (!action) return;
    const previous = document.activeElement as HTMLElement | null;
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Tab") {
        const items = modalRef.current?.querySelectorAll<HTMLButtonElement>(
          "button:not(:disabled)",
        );
        if (items?.length) {
          const first = items[0],
            last = items[items.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
      if (e.key === "Escape" && !inFlight.current) setAction(undefined);
    };
    window.addEventListener("keydown", handle);
    return () => {
      window.removeEventListener("keydown", handle);
      previous?.focus();
    };
  }, [action]);
  const status = data?.status ?? "idle",
    metrics = data?.metrics;
  const limit = metrics?.limits?.model_requests,
    used = metrics?.requests;
  const visible = tasks.filter(
    (t) =>
      (filter === "all" ||
        (filter === "active"
          ? isActive(t.status) || t.status === "awaiting_review"
          : t.status === filter)) &&
      (t.task + t.task_id + (t.title ?? ""))
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const lastStart = [...events]
    .reverse()
    .find((e) => e.event === "task_start" || e.event === "task_resume");
  const startMs = lastStart?.time ? Date.parse(lastStart.time) : NaN;
  const elapsed =
    isActive(status) && !Number.isNaN(startMs)
      ? Math.max(0, (runNow - startMs) / 1000)
      : data?.elapsed_seconds;
  async function create() {
    if (inFlight.current) return;
    if (goal.trim().length < 5) {
      setError("请至少用 5 个字符描述任务目标。");
      return;
    }
    const requirements = criteria
      .split("\n")
      .filter((x) => x.trim())
      .map((description) => ({
        description: description.trim(),
        kind: "research",
      }));
    if (requirements.length > 16) {
      setError("验收标准最多 16 项。");
      return;
    }
    const revision = ++intakeRevision.current;
    let body: Record<string, unknown> | undefined;
    inFlight.current = true;
    setBusy(true);
    setPreparing(true);
    setError("");
    try {
      const result = await api.post<TaskPreparation>("/tasks/prepare", {
        task: goal.trim(),
        report_format: reportFormat,
        clarifications,
      });
      if (revision !== intakeRevision.current || selectedRef.current) return;
      if (!result.ready) {
        setPreparation(result);
        setClarifications(result.answers);
      } else {
        body = {
          task: result.task,
          report_format: reportFormat,
          clarifications: result.answers,
          requirements,
        };
      }
    } catch (e) {
      if (revision === intakeRevision.current)
        setError(e instanceof Error ? e.message : "需求检查失败，请重试。");
    } finally {
      inFlight.current = false;
      setBusy(false);
      setPreparing(false);
    }
    if (body)
      void perform({
        kind: "create",
        title: "开始执行 Agent 任务",
        description: "按已确认范围执行，可能产生模型费用。",
        body,
      });
  }
  async function perform(nextAction: WorkbenchAction | undefined = action) {
    if (!nextAction || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      if (nextAction.kind === "create" || nextAction.kind === "related") {
        createKey.current ||= crypto.randomUUID();
        const v = await api.post<{ task_id: string }>(
          "/tasks",
          nextAction.kind === "related"
            ? nextAction.body
            : (nextAction.body ?? {
                task: goal.trim(),
                report_format: reportFormat,
                requirements: criteria
                  .split("\n")
                  .filter((x) => x.trim())
                  .map((description) => ({
                    description: description.trim(),
                    kind: "research",
                  })),
              }),
          createKey.current,
        );
        createKey.current = "";
        setHistoryView(false);
        choose(v.task_id);
        setGoal("");
        setCriteria("");
        setNotice("任务已提交。下方将持续更新进展与交付。");
      } else {
        await api.post(nextAction.path!, nextAction.body ?? {});
        setNotice(
          nextAction.kind === "accept"
            ? "最终验收已保存。"
            : nextAction.kind === "unlock"
              ? "残留锁已解除，可以继续任务。"
              : "决定已提交，后续状态会在下方更新。",
        );
        await reload(selected); // Reopen SSE for resumed tasks, whose previous stream settled.
        if (
          nextAction.kind === "resume" ||
          nextAction.kind === "restart" ||
          nextAction.kind === "review"
        ) {
          setStreamRevision((value) => value + 1);
        }
      }
      setAction(undefined);
      void historyLoad().catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败，请检查服务连接。");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function manageHistory(task: Task, action: HistoryAction) {
    if (inFlight.current || busy) return;
    if (action === "rename" || action === "delete") {
      setHistoryDialog({ task, kind: action });
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await api.post(`/tasks/${task.task_id}/archive`, {
        archived: action === "archive",
      });
      if (action === "restore") {
        setHistoryView(false);
        choose(task.task_id);
      } else if (selectedRef.current === task.task_id) choose("");
      setNotice(
        action === "archive"
          ? "任务已归档，可在“已归档”中恢复。"
          : "任务已恢复到主列表。",
      );
      await historyLoad();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function historySaved(kind: "rename" | "delete", task: Task) {
    closeHistoryDialog();
    if (kind === "delete" && selectedRef.current === task.task_id) choose("");
    if (kind === "rename" && selectedRef.current === task.task_id)
      void reload(task.task_id).catch((e) => setSyncError(e.message));
    setNotice(
      kind === "rename"
        ? "任务名称已保存。"
        : "任务已移出历史，按确认的选项处理本地数据。",
    );
    await historyLoad().catch((e) => setSyncError(e.message));
  }
  async function cancel() {
    if (inFlight.current || !data?.capabilities.can_cancel) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await api.post(`/tasks/${selected}/cancel`);
      await reload(selected);
      setNotice("已请求中断；已提交的 Checkpoint 和工具记录保留。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function review(decision: "approve" | "reject" | "handled") {
    const pending = data?.pending_review[0];
    if (!pending) return;
    setAction({
      kind: "review",
      title:
        decision === "approve"
          ? "批准这一次敏感操作"
          : decision === "reject"
            ? "拒绝操作并恢复 Agent"
            : "确认已完成人工处理",
      description:
        "将把此次决定提交给后端 interrupt，并从原 Checkpoint 恢复；后续执行可能继续调用付费模型。批准仅绑定当前审批 ID，不会授予永久权限。",
      path: `/tasks/${selected}/review`,
      body: {
        decision,
        approval_id: pending.approval_id ?? "",
        note: followup,
      },
    });
  }
  useEffect(() => {
    if (
      data &&
      isActive(data.status) &&
      autoFollow.current &&
      centerRef.current
    ) {
      centerRef.current.scrollTop = centerRef.current.scrollHeight;
    }
  }, [events.length, data?.status]);
  function selectWorkbook(filename: string) {
    if (filename === previewFile) return;
    workbookScope.current.invalidate();
    setWorkbook(undefined);
    setBookError("");
    setSheet(0);
    setPreviewFile(filename);
  }
  function openDeliveryTab(next: DeliveryTab) {
    setTab(next);
    setPreviewTab(next);
    if (next === "data" && !data?.files.some((f) => f.name === previewFile))
      selectWorkbook(
        data?.files.find((f) => f.name.endsWith(".xlsx"))?.name ??
          "task_result.xlsx",
      );
    setDetailsOpen(true);
  }
  function openExecution() {
    setPreviewTab("execution");
    setDetailsOpen(true);
  }
  function openFile(file: Workspace["files"][number]) {
    const scroll = centerRef.current?.scrollTop ?? 0;
    if (file.name.endsWith(".xlsx")) {
      selectWorkbook(file.name);
      setTab("data");
      setPreviewTab("data");
      setDetailsOpen(true);
    } else
      openDeliveryTab(
        file.name.endsWith(".pdf")
          ? "pdf"
          : file.name.endsWith(".md")
            ? "report"
            : "sources",
      );
    requestAnimationFrame(() => {
      if (centerRef.current) centerRef.current.scrollTop = scroll;
    });
  }
  return (
    <div
      className={`app-shell task-first product-workbench agent-workspace ${sidebarCollapsed ? "history-collapsed" : ""}`}
    >
      <aside id="task-history" className={`sidebar ${mobileNav ? "open" : ""}`}>
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            choose("");
          }}
        >
          <span className="brand-mark">
            <Layers3 size={21} />
          </span>
          <span>
            ResearchFlow<span className="brand-sub">RESEARCH AGENT</span>
          </span>
        </a>
        <button
          className="new-task"
          disabled={busy}
          onClick={() => {
            choose("");
          }}
        >
          <Plus size={18} />
          创建新任务<span>↗</span>
        </button>
        <div className="nav-label">
          任务中心 <span>{tasks.length}</span>
        </div>
        <label className="search-box">
          <Search size={16} />
          <input
            aria-label="搜索历史任务"
            placeholder="搜索任务与记录"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          className="task-filter"
          aria-label="筛选任务状态"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">全部任务</option>
          <option value="active">执行中 / 待审批</option>
          <option value="needs_review">结果待确认</option>
          <option value="completed">已完成</option>
          <option value="partial">部分完成</option>
          <option value="budget_exceeded">预算耗尽</option>
        </select>
        <div
          className="history-view-switch"
          role="group"
          aria-label="历史任务视图"
        >
          <button
            aria-pressed={!historyArchived}
            onClick={() => setHistoryView(false)}
          >
            任务列表
          </button>
          <button
            aria-pressed={historyArchived}
            onClick={() => setHistoryView(true)}
          >
            已归档
          </button>
        </div>
        <div className="task-list">
          {visible.map((t) => (
            <TaskHistoryItem
              key={t.task_id}
              task={t}
              selected={t.task_id === selected}
              busy={busy}
              onChoose={() => choose(t.task_id)}
              onAction={(action) => void manageHistory(t, action)}
            />
          ))}
          {!visible.length && (
            <p className="small muted">
              {historyArchived ? "暂无匹配的归档任务" : "暂无匹配任务"}
            </p>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="local-indicator">
            <i />
            本地工作空间
            <ShieldCheck size={15} />
          </div>
          <p>LangGraph · Playwright MCP</p>
          <button
            className="text-button"
            onClick={() => setStorageOpen(true)}
          >
            <HardDrive size={14} />
            数据与存储
          </button>
          <button
            className="text-button"
            onClick={() => {
              void historyLoad().catch((e) => setError(e.message));
            }}
          >
            <RefreshCw size={14} />
            刷新任务列表
          </button>
        </div>
      </aside>
      {mobileNav && (
        <button
          className="nav-scrim"
          aria-label="关闭任务列表"
          onClick={() => setMobileNav(false)}
        />
      )}
      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="打开任务列表"
              onClick={() => setMobileNav(!mobileNav)}
            >
              <PanelLeftClose size={20} />
            </button>
            <button
              className="icon-button desktop-menu"
              aria-label={sidebarCollapsed ? "展开历史任务" : "收起历史任务"}
              aria-expanded={!sidebarCollapsed}
              aria-controls="task-history"
              onClick={() => setSidebarCollapsed((v) => !v)}
            >
              <PanelLeftClose size={19} />
            </button>
            <span>工作空间</span>
            <ChevronRight size={14} />
            <strong
              title={selected ? data?.title || "任务交互" : "新建任务"}
            >
              {selected ? data?.title || "任务交互" : "新建任务"}
            </strong>
          </div>
          <div className="topbar-right">
            {data?.capabilities.can_cancel && (
              <button
                className="secondary"
                disabled={busy}
                onClick={() => {
                  void cancel();
                }}
              >
                中断任务
              </button>
            )}
            {selected && (
              <button
                className="secondary top-new-task"
                disabled={busy}
                onClick={() => choose("")}
              >
                <Plus size={15} />
                新建任务
              </button>
            )}
            {selected && (
              <button
                id="details-trigger"
                className={`secondary details-toggle ${detailsOpen ? "selected" : ""}`}
                onClick={openExecution}
                aria-expanded={detailsOpen && previewTab === "execution"}
                aria-controls="work-preview"
              >
                <ListChecks size={16} />
                执行详情
              </button>
            )}
            <span className="local-tag">
              <i />
              LOCAL
            </span>
            <Badge status={data?.delivery?.usable ? data.delivery.outcome : status} demo={Boolean(data?.read_only)} />
            <ThemeToggle
              theme={theme}
              onToggle={() => {
                const next = theme === "dark" ? "light" : "dark";
                setTheme(next);
                saveTheme(next);
              }}
            />
          </div>
        </header>
        <div
          className={`workspace-layout ${detailsOpen && selected ? "details-open" : ""}`}
        >
          <section
            className="center-panel"
            ref={centerRef}
            onScroll={(e) => {
              const el = e.currentTarget;
              autoFollow.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < 100;
            }}
          >
            {error && (
              <div className="alert error" role="alert">
                <TriangleAlert size={18} />
                <span>{error}</span>
                <button aria-label="关闭错误" onClick={() => setError("")}>
                  <X size={16} />
                </button>
              </div>
            )}
            {syncError && (
              <div className="alert warning" role="status">
                <RefreshCw size={18} />
                <span>连接暂不可用，正在自动重试：{syncError}</span>
                <button
                  aria-label="关闭连接提示"
                  onClick={() => setSyncError("")}
                >
                  <X size={16} />
                </button>
              </div>
            )}
            {notice && (
              <div className="alert notice" role="status">
                <Check size={18} />
                <span>{notice}</span>
                <button aria-label="关闭提示" onClick={() => setNotice("")}>
                  <X size={16} />
                </button>
              </div>
            )}
            {!selected ? (
              <TaskComposer
                goal={goal}
                criteria={criteria}
                reportFormat={reportFormat}
                preparation={preparation}
                preparing={preparing}
                clarifications={clarifications}
                onClarifications={(value) => {
                  setClarifications(value);
                  createKey.current = "";
                }}
                onReportFormat={(value) => {
                  setReportFormat(value);
                  createKey.current = "";
                }}
                busy={busy}
                settings={settings}
                onGoal={(value) => {
                  setGoal(value);
                  createKey.current = "";
                }}
                onCriteria={(value) => {
                  setCriteria(value);
                  createKey.current = "";
                }}
                onSubmit={create}
              />
            ) : loading && !data ? (
              <Empty
                icon={<LoaderCircle className="spin" />}
                title="正在读取任务"
              >
                从后端加载持久化状态与交付文件。
              </Empty>
            ) : data ? (
              <div className="task-workspace-content">
                {parentTask(data.task) && (
                  <button
                    className="parent-task-link"
                    onClick={() => choose(parentTask(data.task)!)}
                  >
                    关联的原任务 ↗
                  </button>
                )}
                <TaskStream
                  key={`stream:${selected}`}
                  data={data}
                  events={events}
                  onOpenTab={openDeliveryTab}
                  onFile={openFile}
                  onDetails={openExecution}
                >
                  {data.delivery?.usable && data.delivery.warnings.some(w => w.kind !== "execution") && (
  <details
    className="delivery-warnings"
    style={
      data.delivery.warnings.every(w => w.kind === "execution")
        ? {
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            boxShadow: "none",
            color: "#64748b",
            borderRadius: 10,
          }
        : undefined
    }
  >
    <summary
      style={
        data.delivery.warnings.every(w => w.kind === "execution")
          ? { color: "#475569", fontWeight: 500 }
          : undefined
      }
    >
      {data.delivery.warnings.every(w => w.kind === "execution")
        ? <span aria-hidden="true">ⓘ</span>
        : <TriangleAlert size={16} />
      }

      {data.delivery.warnings.every(w => w.kind === "execution")
        ? `历史执行记录 · ${data.delivery.execution_warning_count} 条未修正${
            data.delivery.recovered_execution_warning_count
              ? ` · ${data.delivery.recovered_execution_warning_count} 条已自动修正`
              : ""
          }`
        : "有待核对事项"
      }
    </summary>

    <ul>
      {data.delivery.warnings.filter(w => w.kind !== "execution").map((warning, i) => (
        <li key={i} style={
          warning.kind === "execution"
            ? { color: "#64748b" }
            : undefined
        }>
          {warning.message}
        </li>
      ))}
    </ul>

    <button onClick={openExecution}>
      查看完整执行轨迹
    </button>
  </details>
)}
                  {!data.delivery?.usable && !!data.requirements.quality_warnings?.length && (
                    <div className="alert" role="status" aria-label="交付说明">
                      <TriangleAlert size={18} />
                      <div>
                        <strong>文件已交付，请核对未核实信息</strong>
                        <p>报告已保留缺失说明。请核对文件和以下限制，再决定是否确认结果。</p>
                        <details>
                          <summary>查看未核实项</summary>
                          <ul>{data.requirements.quality_warnings.map((item) => <li key={item}>{item}</li>)}</ul>
                        </details>
                      </div>
                    </div>
                  )}
                  {!data.delivery?.usable && (data.failure ? (
                    <div className="alert error" role="alert">
                      <TriangleAlert size={18} />
                      <div>
                        <strong>{data.failure.label}</strong>
                        <p>
                          {status === "error" &&
                          !data.capabilities.has_checkpoint
                            ? "尚未生成可恢复状态，请根据原因检查后新建任务；不会自动重试。"
                            : data.failure.next_action}
                        </p>
                        {data.failure.detail && (
                          <p>
                            {data.failure.detail.slice(0, 240)}
                            {data.failure.detail.length > 240 ? "…" : ""}
                          </p>
                        )}
                        <p className="small">
                          {data.failure.retryable
                            ? "临时故障可有限重试，所有尝试计入预算。"
                            : "不自动重复此错误，请先按提示处理。"}
                        </p>
                        {data.failure.detail && (
                          <details>
                            <summary>查看保存的原因</summary>
                            <p>{data.failure.detail}</p>
                          </details>
                        )}
                      </div>
                    </div>
                  ) : (
                    data.error && (
                      <div className="alert error" role="alert">
                        <TriangleAlert size={18} />
                        <span>{data.error}</span>
                      </div>
                    )
                  ))}
                  {data.pending_review.length > 0 && (
                    <section className="approval-card card">
                      <div className="section-heading">
                        <span>
                          <ShieldCheck size={18} />
                          Agent 正在等待你的决定
                        </span>
                        <Badge status="awaiting_review" />
                      </div>
                      <p>
                        以下操作尚未执行，请核对目标与参数。审批会绑定当前操作。
                      </p>
                      <ApprovalDetails pending={data.pending_review[0]} />
                      <input
                        aria-label="审批备注"
                        placeholder="审批备注或人工处理说明（可选）"
                        value={followup}
                        onChange={(e) => setFollowup(e.target.value)}
                        maxLength={1000}
                      />
                      <div className="action-row">
                        <button
                          className="secondary"
                          disabled={!data.capabilities.can_review || busy}
                          onClick={() => review("reject")}
                        >
                          拒绝并继续
                        </button>
                        {data.pending_review[0].tool ===
                        "submit_public_form" ? (
                          <button
                            className="primary"
                            disabled={!data.capabilities.can_review || busy}
                            onClick={() => review("approve")}
                          >
                            <ShieldCheck size={16} />
                            批准本次操作
                          </button>
                        ) : (
                          <button
                            className="primary"
                            disabled={!data.capabilities.can_review || busy}
                            onClick={() => review("handled")}
                          >
                            已人工处理，继续
                          </button>
                        )}
                      </div>
                    </section>
                  )}
                </TaskStream>
                <TaskBudget
                  key={`budget:${selected}`}
                  data={data}
                  settings={settings}
                  busy={busy}
                  onResumed={(id) => {
                    if (selectedRef.current === id) {
                      setNotice(
                        "追加额度已确认，正在从原 Checkpoint 恢复任务。",
                      );
                      setStreamRevision((value) => value + 1);
                      void reload(id).catch((e) => setSyncError(e.message));
                    }
                    void historyLoad().catch(() => {});
                  }}
                >
                  {data.capabilities.can_resume &&
                    !data.model_budget?.exhausted && (
                      <div className="budget-resume-footer">
                        <div className="budget-resume-copy">
                          <p id="resume-description">
                            保留已有进度与文件，继续执行可能产生 API 费用。
                          </p>
                          <details className="budget-resume-note">
                            <summary>补充执行要求（可选）</summary>
                            <input
                              aria-describedby="resume-description"
                              aria-label="恢复补充说明"
                              placeholder="输入本次继续执行的补充要求"
                              value={followup}
                              maxLength={6000}
                              disabled={busy}
                              onChange={(e) => setFollowup(e.target.value)}
                            />
                          </details>
                        </div>
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() =>
                            setAction({
                              kind: "resume",
                              title: "继续执行任务？",
                              description:
                                typeof used === "number" &&
                                typeof limit === "number"
                                  ? `剩余 ${Math.max(0, limit - used)} 次模型请求，继续执行可能产生 API 费用。`
                                  : "将从已有进度继续，可能产生 API 费用。",
                              path: `/tasks/${selected}/resume`,
                              body: { task: followup },
                            })
                          }
                        >
                          <Play size={15} />
                          继续执行
                        </button>
                      </div>
                    )}
                  {data.capabilities.can_restart && (
                    <div className="budget-resume-footer">
                      <div className="budget-resume-copy">
                        <p>
                          任务在服务重启前尚未开始，没有可恢复的进度。重新开始会按原目标执行，可能产生 API 费用。
                        </p>
                      </div>
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={() =>
                          setAction({
                            kind: "restart",
                            title: "重新开始任务？",
                            description:
                              "该任务尚未开始，没有可恢复的进度；重新开始会从原目标执行，可能产生模型费用。",
                            path: `/tasks/${selected}/resume`,
                            body: { task: "" },
                          })
                        }
                      >
                        <Play size={15} />
                        重新开始
                      </button>
                    </div>
                  )}
                </TaskBudget>
                {!isActive(status) &&
                  !data.capabilities.can_resume &&
                  !data.capabilities.can_restart &&
                  status !== "awaiting_review" && (
                    <TaskContinuation
                      key={selected}
                      busy={busy}
                      onEdit={() => {
                        createKey.current = "";
                      }}
                      onSubmit={(note) => {
                        if (!settings?.api_key_configured) {
                          setError(
                            "后端未配置模型密钥，暂时不能执行后续任务。",
                          );
                          return;
                        }
                        try {
                          setAction({
                            kind: "related",
                            title: "创建关联的后续任务",
                            description: `这是独立新任务，不继续原Checkpoint，不自动读取原文件。新任务最多${settings.model_requests}次模型请求，可能产生费用；原任务与预算不变。`,
                            body: { task: relatedGoal(data, note) },
                          });
                        } catch (e) {
                          setError((e as Error).message);
                        }
                      }}
                    />
                  )}
                {data.capabilities.locked && !isActive(status) && (
                  <div className="alert warning">
                    <TriangleAlert size={18} />
                    <span>
                      任务仍有进程锁。确认原进程已经退出后，可以解除残留锁再继续。
                    </span>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        setAction({
                          kind: "unlock",
                          title: "解除残留锁？",
                          description:
                            "仅在确认原 Python/Agent 进程已经关闭时使用。解除后，已有进度和文件保留，可重新继续任务。",
                          path: `/tasks/${selected}/unlock`,
                          body: { confirm: true },
                        })
                      }
                    >
                      解除残留锁
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <Empty icon={<TriangleAlert />} title="任务暂不可用">
                请从左侧重新选择，或检查后端连接。
              </Empty>
            )}
          </section>
          {selected && data && detailsOpen && (
            <PreviewPane
              key={selected}
              tab={previewTab}
              hasPdf={data.files.some((f) => f.name.endsWith(".pdf"))}
              onTab={(next) => {
                if (next === "execution") openExecution();
                else openDeliveryTab(next);
              }}
              onClose={() => {
                setDetailsOpen(false);
                requestAnimationFrame(() =>
                  document.getElementById("details-trigger")?.focus(),
                );
              }}
            >
              {previewTab === "execution" ? (
                <ExecutionPanel
                  key={selected}
                  data={data}
                  events={events}
                  settings={settings}
                  connection={connection}
                  selected={selected}
                  busy={busy}
                  elapsed={elapsed}
                  onCancel={() => {
                    void cancel();
                  }}
                />
              ) : (
                <>
                  {(previewTab === "report" ||
                    previewTab === "pdf" ||
                    previewTab === "data") &&
                    (() => {
                      const file = data.files.find(
                        (f) =>
                          f.name ===
                          (previewTab === "pdf"
                            ? "research_report.pdf"
                            : previewTab === "report"
                              ? "research_report.md"
                              : previewFile),
                      );
                      return file ? (
                        <div className="preview-file-meta">
                          <div>
                            <strong>{file.name}</strong>
                            <span>
                              {(file.bytes / 1024).toFixed(1)} KB · 已生成
                            </span>
                          </div>
                          <a
                            href={file.url}
                            download
                            className="secondary"
                            aria-label={`下载预览文件 ${file.name}`}
                          >
                            下载文件
                          </a>
                        </div>
                      ) : null;
                    })()}
                  {previewTab === "data" && (
                    <label className="workbook-picker">
                      工作簿
                      <select
                        aria-label="预览工作簿"
                        value={previewFile}
                        onChange={(e) => selectWorkbook(e.target.value)}
                      >
                        {data.files
                          .filter((f) => f.name.endsWith(".xlsx"))
                          .map((f) => (
                            <option value={f.name} key={f.name}>
                              {f.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  )}
                  <DeliveryPanel
                    data={data}
                    selected={selected}
                    tab={tab}
                    setTab={setTab}
                    book={book}
                    bookError={bookError}
                    sheet={sheet}
                    setSheet={setSheet}
                    checked={checked}
                    setChecked={setChecked}
                    busy={busy}
                    setAction={setAction}
                    onDetails={openExecution}
                    detailsOpen={detailsOpen}
                    embedded
                  />
                </>
              )}
            </PreviewPane>
          )}
        </div>
      </main>
      {storageOpen && <StorageDialog onClose={() => setStorageOpen(false)} />}
      {historyDialog && (
        <TaskHistoryDialog
          key={historyDialog.task.task_id + historyDialog.kind}
          task={historyDialog.task}
          kind={historyDialog.kind}
          onClose={closeHistoryDialog}
          onSaved={historySaved}
        />
      )}
      {action && (
        <div
          className="modal-overlay"
          onClick={() => {
            if (!busy) setAction(undefined);
          }}
        >
          <section
            className="modal"
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="modal-icon">
              <ShieldCheck size={24} />
            </span>
            <h2 id="modal-title">{action.title}</h2>
            <p>{action.description}</p>
            {error && <p className="warning-text">{error}</p>}
            <div className="action-row">
              <button
                autoFocus
                className="secondary"
                disabled={busy}
                onClick={() => setAction(undefined)}
              >
                取消
              </button>
              <button
                className="primary"
                disabled={busy}
                onClick={() => {
                  void perform();
                }}
              >
                {busy ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <Check size={16} />
                )}
                确认
                {action.kind === "create"
                  ? "执行"
                  : action.kind === "accept"
                    ? "验收"
                    : action.kind === "resume"
                      ? "继续"
                      : action.kind === "restart"
                        ? "重新开始"
                        : action.kind === "unlock"
                          ? "解除锁"
                          : "提交"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
