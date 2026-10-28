import {
  ArrowDownToLine,
  ArrowRight,
  CheckCheck,
  FileSpreadsheet,
  FileText,
  ShieldCheck,
  Link2,
} from "lucide-react";
import type { DeliveryTab, Workspace } from "../types";
import { outcomeText } from "../ux";
const fileLabels: Record<string, string> = {
  "task_result.xlsx": "业务数据表",
  "research_sources.xlsx": "来源证据表",
  "research_report.md": "研究报告",
};
export default function TaskOverview({
  data,
  onOpenTab,
}: {
  data: Workspace;
  onOpenTab: (tab: DeliveryTab) => void;
}) {
  const passed = data.requirements.requirements.filter((r) => r.passed).length;
  const primary = data.files.filter(
    (f) => f.name.endsWith(".md") || f.name.endsWith(".xlsx"),
  );
  const other = data.files.filter((f) => !primary.includes(f));
  const fileCard = (file: Workspace["files"][number]) => (
    <div className="file-card" key={file.name}>
      <button
        className="file-preview"
        onClick={() =>
          onOpenTab(
            file.name === "task_result.xlsx"
              ? "data"
              : file.name.endsWith(".md")
                ? "report"
                : "sources",
          )
        }
        aria-label={`预览${fileLabels[file.name] ?? file.name}`}
      >
        {file.name.endsWith(".xlsx") ? (
          <FileSpreadsheet size={23} />
        ) : (
          <FileText size={23} />
        )}
        <span>
          <strong>{fileLabels[file.name] ?? file.name}</strong>
          <small>
            {file.name} · {(file.bytes / 1024).toFixed(1)} KB
          </small>
        </span>
      </button>
      <a
        className="file-download"
        href={file.url}
        download
        aria-label={`下载${fileLabels[file.name] ?? file.name}`}
        title={`下载 ${file.name}`}
      >
        <ArrowDownToLine size={17} />
      </a>
    </div>
  );
  return (
    <section
      className="result-overview"
      id="task-results"
      aria-label="任务结果"
    >
      <div className="overview-title">
        <div>
          <h2>
            {data.read_only
              ? "演示任务 · 已保存的交付结果"
              : outcomeText(data.status)}
          </h2>
          {data.status === "completed" && (
            <p className="muted">
              {data.completion_mode === "automatic_objective_checks"
                ? "全部指定的客观检查通过，自动完成；未记录人工验收。"
                : data.completion_mode === "human_acceptance"
                  ? "已记录用户逐项验收。"
                  : "请以保存的完成记录为准。"}
            </p>
          )}
        </div>
      </div>
      <div className="result-facts">
        <span>
          <CheckCheck size={16} />
          需求证据{" "}
          <strong>
            {passed} / {data.requirements.requirements.length}
          </strong>
        </span>
        <span>
          <Link2 size={16} />
          <strong>{data.sources.length}</strong> 条来源
        </span>
        <span>
          <ShieldCheck size={16} />
          已有文件结构{" "}
          <strong>
            {data.artifacts.artifacts_valid ? "通过" : "未通过 / 待生成"}
          </strong>
        </span>
      </div>
      {data.status === "needs_review" && !data.read_only && (
        <div className="review-prompt">
          <p>
            {data.review_reason ||
              "请核对内容；未满足的必要需求不能通过人工勾选绕过。"}
          </p>
          <button
            className="secondary"
            onClick={() => onOpenTab("requirements")}
          >
            逐项核对
            <ArrowRight size={15} />
          </button>
        </div>
      )}
      {data.read_only && (
        <p className="muted">
          这是只读历史演示，保留原始执行与确认状态，无需也不能再次验收。
        </p>
      )}
      {data.requirements.requirements.some((r) => !r.passed) && (
        <div className="review-prompt">
          <div>
            <strong>尚未满足的要求</strong>
            <ul>
              {data.requirements.requirements
                .filter((r) => !r.passed)
                .map((r) => (
                  <li key={r.id}>
                    {r.description}：
                    {r.reason ||
                      (r.status === "pending" ? "尚未验证" : "未通过检查")}
                  </li>
                ))}
            </ul>
          </div>
        </div>
      )}
      {primary.length > 0 ? (
        <div className="overview-files">
          <div className="section-heading">
            <span>文件</span>
            <span className="small muted">点击直接下载</span>
          </div>
          <div className="file-grid">{primary.map(fileCard)}</div>
        </div>
      ) : (
        <p className="muted">尚无可下载的交付文件。</p>
      )}
      {other.length > 0 && (
        <details className="other-files">
          <summary>其他数据文件（{other.length}）</summary>
          <div className="file-grid">{other.map(fileCard)}</div>
        </details>
      )}
      {data.answer && (
        <div className="outcome-note">
          <span>Agent 交付说明</span>
          <p>
            {data.answer.slice(0, 280)}
            {data.answer.length > 280 ? "…" : ""}
          </p>
          {data.answer.length > 280 && (
            <details>
              <summary>展开其余说明</summary>
              <p>{data.answer.slice(280)}</p>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
