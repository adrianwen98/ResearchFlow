import { useRef } from "react";
import {
  ArrowUp,
  ArrowUpRight,
  FileSearch,
  Globe2,
  Table2,
  ListChecks,
  LoaderCircle,
} from "lucide-react";
import TaskPreparation from "./TaskPreparation";
import type { TaskPreparation as Preparation } from "../types";
import type { Settings } from "../types";

const starters = [
  {
    icon: Globe2,
    title: "网页调查",
    detail: "跨站研究 · 可追溯来源",
    goal: "调查 Python 和 Node.js 官方网站，比较主要用途、文档和下载入口，生成有来源的 Excel 与中文报告。",
  },
  {
    icon: Table2,
    title: "数据处理",
    detail: "真实计算 · Excel 交付",
    goal: "读取 Palmer Penguins 公开数据，统计不同物种的样本数和平均体重，说明缺失值并输出 Excel 与报告。",
  },
  {
    icon: FileSearch,
    title: "公开文件分析",
    detail: "PDF / CSV / Excel 链接",
    goal: "分析这个公开文件链接：[请替换为文件网址]。检查实际内容，记录来源，说明数据缺失或文件局限，整理中文报告。",
  },
];
export default function TaskComposer({
  goal,
  criteria,
  onGoal,
  onCriteria,
  onSubmit,
  busy,
  settings,
  reportFormat = "both",
  onReportFormat,
  preparation,
  clarifications = {},
  onClarifications = () => {},
  preparing = false,
}: {
  preparation?: Preparation;
  clarifications?: Record<string, string>;
  onClarifications?: (answers: Record<string, string>) => void;
  preparing?: boolean;
  goal: string;
  criteria: string;
  onGoal: (value: string) => void;
  onCriteria: (value: string) => void;
  onSubmit: () => void;
  busy: boolean;
  settings?: Settings;
  reportFormat?: "pdf" | "markdown" | "both";
  onReportFormat?: (value: "pdf" | "markdown" | "both") => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const blockedReason = busy
    ? preparing ? "正在免费检查需求…" : "正在提交任务…"
    : !settings
      ? "正在检查运行配置…"
      : !settings.api_key_configured
        ? "请先配置模型密钥"
        : !goal.trim()
          ? "至少输入 5 个字"
          : goal.trim().length < 5
            ? "请补充任务描述，至少 5 个字"
            : goal.includes("[请替换为文件网址]")
              ? "请将示例占位符替换为实际文件链接"
              : preparation?.questions.some(q => !clarifications[q.id]?.trim())
                ? "补充关键信息后即可开始"
                : "";
  const ready = !blockedReason;
  return (
    <div className="task-home">
      <header className="home-heading">
        <h1>
          把目标交给 Agent，
          <br />
          <span>把时间留给更重要的事。</span>
        </h1>
      </header>
      <form
        className="task-composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) onSubmit();
        }}
      >
        <label htmlFor="task-goal">
          你想完成什么？
        </label>
        <textarea
          id="task-goal"
          ref={input}
          aria-label="任务目标"
          aria-describedby="task-submit-hint"
          maxLength={6000}
          disabled={busy}
          placeholder="例如：比较两款产品的官方参数，生成 Excel 和中文报告。"
          value={goal}
          onChange={(e) => onGoal(e.target.value)}
          onKeyDown={(e) => {
            if (
              (e.ctrlKey || e.metaKey) &&
              e.key === "Enter" &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              if (ready) onSubmit();
            }
          }}
        />
        <details className="criteria-input">
          <summary>
            <ListChecks size={16} /> 添加验收标准 <span>可选</span>
          </summary>
          <textarea
            aria-label="验收标准"
            value={criteria}
            onChange={(e) => onCriteria(e.target.value)}
            disabled={busy}
            maxLength={5000}
            placeholder="每行一项，例如：每个结论附来源；表格保留原始数据。"
          />
        </details>
        <fieldset className="report-format-choice" disabled={busy}>
          <legend>报告格式</legend>
          {([['both', 'PDF + Markdown（推荐）'], ['pdf', 'PDF'], ['markdown', 'Markdown']] as const).map(([value,label]) => (
            <label key={value}><input type="radio" name="report-format" value={value} checked={reportFormat===value} onChange={() => onReportFormat?.(value)} />{label}</label>
          ))}
        </fieldset>
        {preparation && <TaskPreparation value={preparation} answers={clarifications} onAnswers={onClarifications} disabled={busy} />}
        <div className="composer-controls">
          <span id="task-submit-hint" role="status" aria-live="polite">
            {blockedReason}
          </span>
          <button type="submit" className="primary" disabled={!ready} title={blockedReason || "开始执行任务（Ctrl / ⌘ + Enter）"} aria-describedby="task-submit-hint">
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <ArrowUp size={17} />
            )}{" "}
            {busy ? preparing ? "检查需求中" : "正在提交" : !goal.trim() ? "输入任务后开始" : preparation ? "确认并开始" : "开始执行"}
          </button>
        </div>
      </form>
      <div className="composer-note">
        <span>
          {settings && !settings.api_key_configured
            ? "模型密钥未配置 · 可浏览历史任务与交付文件"
            : `执行可能产生 API 费用${settings ? ` · 上限 ${settings.model_requests} 次请求` : ""}`}
        </span>
      </div>
      <section className="capability-starters" aria-label="任务能力入口">
        <div>
          {starters.map(({ icon: Icon, ...x }) => (
            <button
              type="button"
              key={x.title}
              disabled={busy}
              onClick={() => {
                onGoal(x.goal);
                input.current?.focus();
              }}
            >
              <Icon size={22} />
              <strong>{x.title}</strong>
              <small>{x.detail}</small>
              <ArrowUpRight size={16} />
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
