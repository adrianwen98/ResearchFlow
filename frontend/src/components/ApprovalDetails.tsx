import type { Pending } from "../types";
const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export default function ApprovalDetails({ pending }: { pending: Pending }) {
  const args = record(pending.arguments),
    preview = record(pending.preview),
    fields = record(preview.fields);
  const text = (value: unknown) =>
    typeof value === "string" ? value : JSON.stringify(value);
  const rows = [
    [
      "操作",
      pending.tool === "submit_public_form"
        ? "提交公开表单"
        : (text(args.action) ?? pending.tool ?? "人工核对"),
    ],
    ["原因 / 用途", text(preview.purpose ?? args.reason ?? args.purpose)],
    ["目标地址", text(preview.action_url ?? args.url)],
    ["请求方法", text(preview.method)],
  ];
  return (
    <>
      <dl className="approval-preview">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        {Object.entries(fields).map(([key, value]) => (
          <div key={"field:" + key}>
            <dt>提交字段 · {key}</dt>
            <dd>{text(value)}</dd>
          </div>
        ))}
      </dl>
      {typeof pending.policy === "string" && (
        <p className="approval-policy">{pending.policy}</p>
      )}
      <details className="approval-json">
        <summary>查看完整审批参数</summary>
        <pre>{JSON.stringify(pending, null, 2)}</pre>
      </details>
    </>
  );
}
