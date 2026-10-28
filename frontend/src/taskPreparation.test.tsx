import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TaskComposer from "./components/TaskComposer";
import type { Settings, TaskPreparation } from "./types";

const settings: Settings = { model: "offline-test", model_requests: 30, tool_calls: 80, timeout_seconds: 60, api_key_configured: true, allowed_domains: [] };
const preparation: TaskPreparation = {
  version: 1, task: "比较 Notion 和飞书的免费版", report_format: "both", scope: "核对主要差异与限制。",
  defaults: ["优先官方来源"], questions: [{id: "use_case", prompt: "主要用于个人使用，还是团队协作？", options: ["个人使用", "小团队协作"]}],
  answers: {}, ready: false, unanswered: ["use_case"], model_requests: 0,
};

const answerCases: Record<string, string>[] = [{}, {use_case: "个人使用"}, {use_case: "供五人研究小组使用"}];
it.each(answerCases)("requires an explicit choice or custom answer before starting: %j", answers => {
  const html=renderToStaticMarkup(<TaskComposer goal={preparation.task} criteria="" onGoal={()=>{}} onCriteria={()=>{}}
    onSubmit={()=>{throw new Error("Must not execute");}} busy={false} settings={settings} preparation={preparation} clarifications={answers} />);
  const button=html.match(/<button type="submit"[^>]*>/)![0];
  expect(button.includes('disabled=""')).toBe(!("use_case" in answers));
  expect(html).toContain("确认并开始");
  expect(html).toContain("本步免费，不调用模型");
  expect(html).toContain('aria-label="任务澄清"');
});

it("never preselects a clarification and keeps the output format controls",()=>{
  const html=renderToStaticMarkup(<TaskComposer goal={preparation.task} criteria="" onGoal={()=>{}} onCriteria={()=>{}} onSubmit={()=>{}}
    busy={false} settings={settings} preparation={preparation} />);
  expect(html.match(/<input[^>]*name="clarify-use_case"[^>]*>/g)!.every(s=>!s.includes('checked=""'))).toBe(true);
  expect(html).toContain("PDF + Markdown（推荐）");
});
