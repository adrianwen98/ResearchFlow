import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TaskComposer from "./components/TaskComposer";
import type { Settings } from "./types";

const settings: Settings = { model: "offline-test", model_requests: 30, tool_calls: 80, timeout_seconds: 60, api_key_configured: true, allowed_domains: [] };
it.each([
  ["", settings, false, "至少输入 5 个字", true],
  ["测试", settings, false, "请补充任务描述，至少 5 个字", true],
  ["调查官方数据并生成报告", settings, false, "开始执行", false],
  ["分析[请替换为文件网址]", settings, false, "请将示例占位符替换为实际文件链接", true],
  ["调查官方数据并生成报告", undefined, false, "正在检查运行配置", true],
  ["调查官方数据并生成报告", { ...settings, api_key_configured: false }, false, "请先配置模型密钥", true],
  ["调查官方数据并生成报告", settings, true, "正在提交任务", true],
] as const)("explains the submit gate for %s", (goal, cfg, busy, reason, disabled) => {
  const html = renderToStaticMarkup(<TaskComposer goal={goal} criteria="" onGoal={() => {}} onCriteria={() => {}} onSubmit={() => { throw new Error("Must not execute"); }} busy={busy} settings={cfg} />);
  expect(html).toContain(reason);
  const button = html.match(/<button type="submit"[^>]*>/)?.[0];
  expect(button).toBeDefined();
  expect(button!.includes('disabled=""')).toBe(disabled);
});
