import type { Dispatch, SetStateAction } from "react";
import { useState, useEffect } from "react";
import {
  ArrowUpRight,
  Check,
  CheckCheck,
  Globe2,
  FileSpreadsheet,
  FileText,
  Link2,
  LoaderCircle,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import DataTable from "./DataTable";
import PdfPreview from "./PdfPreview";
import ReportMarkdown from "./ReportMarkdown";
import { safeLink } from "../model";
import type {
  Workbook,
  Workspace,
  DeliveryTab,
  WorkbenchAction,
} from "../types";
import { Empty } from "./Shared";
interface Props {
  data: Workspace;
  selected: string;
  tab: DeliveryTab;
  setTab: Dispatch<SetStateAction<DeliveryTab>>;
  book?: Workbook;
  bookError: string;
  sheet: number;
  setSheet: Dispatch<SetStateAction<number>>;
  checked: string[];
  setChecked: Dispatch<SetStateAction<string[]>>;
  busy: boolean;
  setAction: Dispatch<SetStateAction<WorkbenchAction | undefined>>;
  onDetails: () => void;
  detailsOpen: boolean;
  embedded?: boolean;
}
export default function DeliveryPanel({
  data,
  selected,
  tab,
  setTab,
  book,
  bookError,
  sheet,
  setSheet,
  checked,
  setChecked,
  busy,
  setAction,
  embedded = false,
}: Props) {
  const [revisionNote, setRevisionNote] = useState("");
  const [acceptLimitations, setAcceptLimitations] = useState(false);
  useEffect(() => setAcceptLimitations(false), [selected, data.review_binding]);
  useEffect(() => setRevisionNote(""), [selected]);
  const status = data.status,
    passed = data.requirements.requirements.filter((r) => r.passed).length;
  return (
    <section className="delivery-section" id="delivery-details">
      {!embedded && (
        <div className="delivery-navigation">
          <div className="tabs" role="tablist" aria-label="交付内容">
            {(
              [
                {
                  id: "report",
                  label: "完整报告",
                  icon: <FileText size={16} />,
                },
                {
                  id: "data",
                  label: "数据预览",
                  icon: <FileSpreadsheet size={16} />,
                },
                {
                  id: "sources",
                  label: "来源证据",
                  icon: <Link2 size={16} />,
                },
                {
                  id: "requirements",
                  label: "逐项验收",
                  icon: <CheckCheck size={16} />,
                },
              ] as const
            ).map((t) => (
              <button
                role="tab"
                aria-selected={tab === t.id}
                key={t.id}
                onClick={() => setTab(t.id)}
                className={tab === t.id ? "active" : ""}
              >
                {t.icon}
                {t.label}
                {t.id === "sources" && <span>{data.sources.length}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
      <div
        className="tab-content"
        role="tabpanel"
        aria-label={
          {
            report: "完整报告",
            pdf: "PDF报告",
            data: "数据预览",
            sources: "来源证据",
            requirements: "需求验收",
          }[tab]
        }
      >
        {tab === "pdf" && <PdfPreview key={selected} taskId={selected} available={data.files.some(f => f.name === "research_report.pdf")} />}
        {tab === "report" &&
          (data.report ? (
            <article className="markdown">
              <ReportMarkdown>{data.report}</ReportMarkdown>
            </article>
          ) : (
            <Empty icon={<FileText />} title="报告将在执行后生成">
              {data.answer || "Agent 仍在收集与处理信息。"}
            </Empty>
          ))}
        {tab === "data" &&
          (bookError ? (
            <Empty icon={<FileSpreadsheet />} title="暂无可预览的业务表格">
              {bookError}
            </Empty>
          ) : !book ? (
            <Empty
              icon={<LoaderCircle className="spin" />}
              title="正在读取真实工作簿"
            />
          ) : (
            <>
              <div className="sheet-tabs">
                {book.sheets.map((s, i) => (
                  <button
                    key={s.name}
                    className={sheet === i ? "active" : ""}
                    onClick={() => setSheet(i)}
                  >
                    <FileSpreadsheet size={14} />
                    {s.name}
                  </button>
                ))}
              </div>
              {book.sheets[sheet] && (
                <>
                  <p className="table-description">
                    {book.sheets[sheet].row_count} 行 ·{" "}
                    {book.sheets[sheet].column_count} 列
                    {book.sheets[sheet].truncated
                      ? ` · 预览前 ${book.max_rows} 行 / ${book.max_columns} 列，完整数据见下载文件`
                      : ""}
                  </p>
                  <DataTable
                    columns={book.sheets[sheet].columns}
                    rows={book.sheets[sheet].rows}
                  />
                </>
              )}
            </>
          ))}
        {tab === "sources" &&
          (data.sources.length ? (
            <div className="source-list">
              {data.sources.map((s) => (
                <article className="source-card" key={s.id}>
                  <div className="source-label">
                    <span>来源 {s.id}</span>
                    <Globe2 size={15} />
                  </div>
                  <h3>{s.title}</h3>
                  <p>{s.fact}</p>
                  {s.quote && <blockquote>{s.quote}</blockquote>}
                  <a
                    href={safeLink(s.url)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {s.url}
                    <ArrowUpRight size={13} />
                  </a>
                </article>
              ))}
            </div>
          ) : (
            <Empty icon={<Link2 />} title="尚无来源证据">
              只展示实际保存的页面或文件来源。
            </Empty>
          ))}
        {tab === "requirements" && (
          <>
            <div className="verification-summary">
              <ShieldCheck size={20} />
              <div>
                <strong>
                  {passed} / {data.requirements.requirements.length}{" "}
                  项证据检查通过
                </strong>
                <p>
                  文件核验：
                  {data.artifacts.artifacts_valid ? "通过" : "未通过或尚未生成"}
                  。程序检查不替代对事实和任务含义的人工判断。
                </p>
              </div>
            </div>
            {data.requirements.requirements.map((r) => (
              <label
                className={`requirement ${r.passed ? "passed" : ""}`}
                key={r.id}
              >
                {data.capabilities.can_accept && (
                  <input
                    type="checkbox"
                    disabled={!data.capabilities.can_accept || !r.passed}
                    checked={checked.includes(r.id)}
                    onChange={(e) =>
                      setChecked((old) =>
                        e.target.checked
                          ? [...old, r.id]
                          : old.filter((x) => x !== r.id),
                      )
                    }
                  />
                )}
                <span>
                  <strong>
                    {r.id} · {r.description}
                  </strong>
                  <small>
                    {r.passed
                      ? r.status === "not_disclosed"
                        ? "已如实说明未公布"
                        : "证据检查通过"
                      : r.status === "pending"
                        ? "等待补充"
                        : "需求未满足"}
                    {r.reason ? ` · ${r.reason}` : ""}
                  </small>
                </span>
                {r.passed ? <Check size={18} /> : <TriangleAlert size={18} />}
              </label>
            ))}
            {!data.requirements.requirements.length && (
              <p className="muted">尚未定义可验收需求。</p>
            )}
            {!!data.requirements.quality_warnings?.length && status === "needs_review" && (
              <div className="accept-limitations">
                <ul>{data.requirements.quality_warnings.map((item) => <li key={item}>{item}</li>)}</ul>
                <label>
                  <input type="checkbox" checked={acceptLimitations} onChange={(e) => setAcceptLimitations(e.target.checked)} />
                  我已了解并接受以上未核实信息
                </label>
              </div>
            )}
            <div className="accept-footer">
              <span>
                {status === "completed"
                  ? data.completion_mode === "automatic_objective_checks"
                    ? "客观检查已自动通过，未冒称人工验收。"
                    : "查看已保存的完成记录。"
                  : data.read_only
                    ? "只读演示，保留原始验收状态。"
                    : "请核对报告、计算与每项需求后确认。"}
              </span>
              {status === "needs_review" && !data.read_only && (
                <button
                  className="primary"
                  disabled={
                    busy ||
                    !data.capabilities.can_accept ||
                    checked.length !== data.requirements.requirements.length ||
                    (!!data.requirements.quality_warnings?.length && !acceptLimitations)
                  }
                  onClick={() =>
                    setAction({
                      kind: "accept",
                      title: "确认最终交付",
                      description:
                        "你已逐项核对需求。后端将再次检查来源、报告、计算和文件，并绑定本次交付版本后标记已完成。此操作不调用模型。",
                      path: `/tasks/${selected}/accept`,
                      body: {
                        requirement_ids: checked,
                        accept_limitations: acceptLimitations,
                        binding: data.review_binding,
                      },
                    })
                  }
                >
                  <CheckCheck size={16} />
                  确认验收完成
                </button>
              )}
            </div>
            {data.capabilities.can_request_revision && (
              <div className="revision-box">
                <label htmlFor="revision-note">
                  结果不符合预期？说明修改要求或拒绝原因
                </label>
                <textarea
                  id="revision-note"
                  aria-label="交付修改要求"
                  value={revisionNote}
                  onChange={(e) => setRevisionNote(e.target.value)}
                  maxLength={1000}
                  placeholder="例如：缺少第二个网站的来源，请补充后重新交付。"
                />
                <button
                  className="secondary"
                  disabled={busy || revisionNote.trim().length < 3}
                  onClick={() =>
                    setAction({
                      kind: "revision",
                      title: "要求修改这次交付",
                      description:
                        "保存原因并将任务标记为部分完成；不会调用模型。之后可从原任务显式继续，累计预算不会重置。",
                      path: `/tasks/${selected}/revision`,
                      body: {
                        binding: data.review_binding,
                        note: revisionNote.trim(),
                      },
                    })
                  }
                >
                  要求修改 / 拒绝交付
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
