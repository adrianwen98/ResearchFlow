import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { observedPhases, reportExcerpt, outcomeText } from "./ux";
import ApprovalDetails from "./components/ApprovalDetails";
import TaskOverview from "./components/TaskOverview";
import TaskProgress from "./components/TaskProgress";
import ExecutionPanel from "./components/ExecutionPanel";
import DeliveryPanel from "./components/DeliveryPanel";
import ReportMarkdown from "./components/ReportMarkdown";
import type { Workspace } from "./types";
const view: Workspace = {
  task_id: "test",
  title: "Stored task",
  task: "Real task text",
  status: "needs_review",
  status_label: "交付待复核",
  created_at: "",
  metrics: { requests: 2 },
  elapsed_seconds: 1,
  read_only: false,
  execution_label: "",
  report:
    "# Stored report\n\n## Findings\nA real paragraph from stored report.\n\n## More\nOther text.",
  answer: "Saved backend reply",
  plan: [],
  events: [],
  sources: [{ id: "1", title: "Source", url: "https://example.org" }],
  pending_review: [],
  requirements: {
    requirements: [
      {
        id: "r1",
        description: "Check evidence",
        passed: false,
        status: "missing",
      },
    ],
    requirements_supported: false,
  },
  artifacts: { artifacts_valid: false },
  review_binding: "a".repeat(64),
  files: [
    {
      name: "task_result.xlsx",
      bytes: 1234,
      url: "/tasks/test/files/task_result.xlsx",
    },
  ],
  capabilities: {
    can_cancel: false,
    can_resume: false,
    can_review: false,
    can_accept: false,
    has_checkpoint: false,
    locked: false,
  },
};
describe("task-first UX uses observable data", () => {
  it("does not mark missing phases complete for a file-only task", () => {
    const phases = observedPhases(
      [
        {
          id: 1,
          event: "tool_start",
          detail: "inspect_downloaded_table",
          call_id: "a",
        },
        {
          id: 2,
          event: "tool_end",
          call_id: "a",
          output_preview: "Actual file",
        },
      ],
      "needs_review",
    );
    expect(phases[1].calls).toBe(0);
    expect(phases[1].state).toBe("none");
    expect(phases[2].calls).toBe(1);
    expect(phases[3].note).toBe("已交付 · 待复核");
  });
  it("retains failed tool counts without inventing successful completion", () => {
    const phases = observedPhases(
      [
        {
          id: 1,
          event: "tool_start",
          detail: "browser_navigate",
          call_id: "a",
        },
        {
          id: 2,
          event: "tool_error",
          call_id: "a",
          detail: "Permission denied",
        },
        {
          id: 3,
          event: "tool_end",
          call_id: "a",
          output_preview: "ERROR: denied",
        },
      ],
      "permission_denied",
    );
    expect(phases[1].state).toBe("error");
    expect(phases[1].note).toContain("1 次失败");
  });
  it("shows successful returned retry as observed, with errors still in trace", () => {
    const p = observedPhases(
      [
        {
          id: 1,
          event: "tool_start",
          detail: "browser_snapshot",
          call_id: "a",
        },
        { id: 2, event: "tool_error", call_id: "a", detail: "timeout" },
        {
          id: 3,
          event: "tool_end",
          call_id: "a",
          output_preview: "Actual returned page",
        },
      ],
      "running",
    );
    expect(p[1].state).toBe("observed");
  });
  it("extracts a verbatim first paragraph even without blank line after heading", () => {
    const e = reportExcerpt(view.report);
    expect(e.text).toBe("A real paragraph from stored report.");
    expect(view.report).toContain(e.text);
    expect(e.truncated).toBe(false);
  });
  it("labels truncation and does not invent summary for list-only report", () => {
    expect(reportExcerpt("# Report\n\n- only list\n\n|a|b|")).toEqual({
      text: "",
      truncated: false,
    });
    const text = "actual ".repeat(100);
    const e = reportExcerpt(text);
    expect(e.truncated).toBe(true);
    expect(text.startsWith(e.text)).toBe(true);
  });
  it("keeps needs_review and completed outcome language distinct", () => {
    expect(outcomeText("needs_review")).toBe("交付已生成，等待你核对");
    expect(outcomeText("completed")).toBe("任务已完成");
  });
  it("renders actual files before excerpt with real URLs and requirement counts", () => {
    const html = renderToStaticMarkup(
      <TaskOverview data={view} onOpenTab={() => {}} />,
    );
    expect(html).toContain("/tasks/test/files/task_result.xlsx");
    expect(html).toContain("0 / 1");
    expect(html.indexOf("task_result.xlsx")).toBeLessThan(
      html.indexOf("Saved backend reply"),
    );
    expect(html).not.toContain("交付已通过人工验收");
    expect(html).toContain("Agent 交付说明");
    expect(html).not.toContain("A real paragraph");
  });
  it("does not offer a non-existing delivery file", () => {
    const html = renderToStaticMarkup(
      <TaskOverview
        data={{ ...view, files: [], report: "", answer: "" }}
        onOpenTab={() => {}}
      />,
    );
    expect(html).not.toContain('download=""');
    expect(html).toContain("尚无可下载的交付文件");
  });
  it("exposes cancellation independently from the inspector and explains observed phases", () => {
    const html = renderToStaticMarkup(
      <TaskProgress
        data={{
          ...view,
          status: "running",
          capabilities: { ...view.capabilities, can_cancel: true },
        }}
        events={[]}
        busy={false}
        onCancel={() => {}}
        onDetails={() => {}}
        detailsOpen={false}
      />,
    );
    expect(html).toContain("中断任务");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("尚无工具调用记录");
    expect(html).not.toContain("02 / 执行进展");
  });
  it("shares the hardened report renderer for both full report and excerpt", () => {
    const html = renderToStaticMarkup(
      <ReportMarkdown>
        {
          "<script>alert(1)</script>\n\n![pixel](https://evil.example/pixel)\n\n[bad](javascript:alert)\n\nActual -0.487"
        }
      </ReportMarkdown>,
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).not.toContain('href="javascript:');
    expect(html).toContain("-0.487");
  });
});

it("keeps sensitive operation preview visible while raw approval JSON defaults closed", () => {
  const html = renderToStaticMarkup(
    <ApprovalDetails
      pending={{
        tool: "submit_public_form",
        approval_id: "a".repeat(64),
        preview: {
          action_url: "https://example.org/feedback",
          method: "POST",
          purpose: "Send requested feedback",
          fields: { message: "Actual submitted content" },
        },
      }}
    />,
  );
  expect(html).toContain("Actual submitted content");
  expect(html).toContain("https://example.org/feedback");
  expect(html).toContain("POST");
  expect(html).toContain('<details class="approval-json">');
  expect(html).not.toContain("<details open");
});
it("delivery phase never marks needs_review as human accepted", () => {
  const p = observedPhases([], "needs_review").at(-1)!;
  expect(p.state).toBe("review");
  expect(p.note).not.toContain("人工验收");
  expect(observedPhases([], "completed").at(-1)!.state).toBe("observed");
});

it("distinguishes automatic completion from a human acceptance receipt", () => {
  const html = renderToStaticMarkup(
    <TaskOverview
      data={{
        ...view,
        status: "completed",
        completion_mode: "automatic_objective_checks",
      }}
      onOpenTab={() => {}}
    />,
  );
  expect(html).toContain("客观检查通过");
  expect(html).toContain("未记录人工验收");
  expect(html).not.toContain("交付已通过人工验收");
});
it("read-only recorded demonstrations do not demand an unavailable acceptance action", () => {
  const html = renderToStaticMarkup(
    <TaskOverview data={{ ...view, read_only: true }} onOpenTab={() => {}} />,
  );
  expect(html).toContain("演示任务");
  expect(html).toContain("无需也不能再次验收");
  expect(html).not.toContain("逐项核对");
});
it("shows real unsatisfied requirements and remaining cumulative budget", () => {
  const html = renderToStaticMarkup(
    <ExecutionPanel
      data={{
        ...view,
        metrics: {
          requests: 8,
          tool_attempts: 14,
          limits: { model_requests: 12, tool_calls: 30 },
        },
      }}
      events={[]}
      busy={false}
      onCancel={() => {}}
      selected="test"
      connection="saved"
      elapsed={1}
    />,
  );
  expect(html).toContain("剩余请求");
  expect(html).toContain("<strong>4</strong>");
  expect(html).toContain("14 / 30");
  const overview = renderToStaticMarkup(
    <TaskOverview data={view} onOpenTab={() => {}} />,
  );
  expect(overview).toContain("尚未满足的要求");
  expect(overview).toContain("Check evidence");
});
it("separates auto-corrected failures from unresolved warnings", () => {
  const events: Workspace["events"] = [
    { id: 1, event: "tool_start", call_id: "a", detail: "record_requirement_result", arguments: { requirement_id: "r1" } },
    { id: 2, event: "tool_error", call_id: "a", detail: 'ERROR: {"code":"evidence_mismatch","message":"missing source"}' },
    { id: 3, event: "tool_end", call_id: "a", detail: "record_requirement_result", output_preview: 'ERROR: {"code":"evidence_mismatch","message":"missing source"}' },
    { id: 4, event: "tool_start", call_id: "b", detail: "record_requirement_result", arguments: { requirement_id: "r1" } },
    { id: 5, event: "tool_end", call_id: "b", detail: "record_requirement_result", output_preview: '{"id":"r1"}' },
    { id: 6, event: "tool_start", call_id: "c", detail: "record_source", arguments: {} },
    { id: 7, event: "tool_error", call_id: "c", detail: 'ERROR: {"code":"evidence_mismatch","message":"bad quote"}' },
    { id: 8, event: "tool_end", call_id: "c", detail: "record_source", output_preview: 'ERROR: {"code":"evidence_mismatch","message":"bad quote"}' },
  ];
  const html = renderToStaticMarkup(
    <ExecutionPanel
      data={{
        ...view,
        status: "needs_review",
        delivery: {
          outcome: "needs_review",
          usable: true,
          label: "结果待确认",
          headline: "交付已生成，等待你核对",
          warning_count: 1,
          execution_warning_count: 1,
          recovered_execution_warning_count: 1,
          warnings: [],
          semantic_accepted: false,
        },
      }}
      events={events}
      busy={false}
      onCancel={() => {}}
      selected="test"
      connection="saved"
      elapsed={1}
    />,
  );
  expect(html).toContain("1 未修正 / 1 已修正");
  expect(html).toContain("错误（未修正 / 已修正）");
  expect(html).toContain("后续已修正");
  expect(html).toContain("错误与返回（已修正）");
  expect(html).toContain("bad quote");
});


it("limits the main activity feed to four observed actions and never labels an unfinished call successful",()=>{
 const events:Workspace['events']=[];
 for(let i=0;i<7;i++){
  events.push({id:i*2+1,event:'tool_start',time:'',call_id:String(i),detail:'observed_'+i});
  if(i<6)events.push({id:i*2+2,event:'tool_end',time:'',call_id:String(i),detail:'observed_'+i,output_preview:'Actual tool result'});
 }
 const html=renderToStaticMarkup(<TaskProgress data={{...view,status:'running'}} events={events} busy={false} onCancel={()=>{}} onDetails={()=>{}} detailsOpen={false}/>);
 expect(html).not.toContain('observed_2');expect(html).toContain('observed_3');expect(html).toContain('observed_6');
 expect(html.match(/<li>/g)?.length).toBe(4);expect(html).toContain('进行中');expect(html).not.toContain('01 /');
});
it("separates preview controls from real downloads and renders no duplicate report body",()=>{
 const html=renderToStaticMarkup(<TaskOverview data={view} onOpenTab={()=>{}}/>);
 expect(html).toContain('aria-label="预览业务数据表"');expect(html).toContain('aria-label="下载业务数据表"');
 expect(html).toContain('/tasks/test/files/task_result.xlsx');expect(html).not.toContain('A real paragraph from stored report');
});


it.each([{status:'completed',read_only:false},{status:'needs_review',read_only:true}])("does not present fake acceptance controls for %s",({status,read_only})=>{
 const html=renderToStaticMarkup(<DeliveryPanel data={{...view,status,read_only}} selected="test" tab="requirements" setTab={()=>{}} bookError="" sheet={0} setSheet={()=>{}} checked={[]} setChecked={()=>{}} busy={false} setAction={()=>{}} onDetails={()=>{}} detailsOpen={false}/>);
 expect(html).not.toContain('确认验收完成');expect(html).not.toContain('type="checkbox"');
 expect(html).toContain('Check evidence');
});
it("retains actual user acceptance only where backend permits it",()=>{
 const data={...view,requirements:{requirements:[{id:'r1',description:'Verified requirement',passed:true,status:'satisfied'}],requirements_supported:true},capabilities:{...view.capabilities,can_accept:true}};
 const html=renderToStaticMarkup(<DeliveryPanel data={data} selected="test" tab="requirements" setTab={()=>{}} bookError="" sheet={0} setSheet={()=>{}} checked={['r1']} setChecked={()=>{}} busy={false} setAction={()=>{}} onDetails={()=>{}} detailsOpen={false}/>);
 expect(html).toContain('确认验收完成');expect(html).toContain('type="checkbox"');expect(html).toContain('checked=""');
});
