import { expect,it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TaskStream from "./components/TaskStream";
import { stateInfo } from "./model";
import type { Workspace } from "./types";

const data: Workspace={task_id:"real",task:"Real task",status:"budget_exceeded",status_label:"预算耗尽",created_at:"",metrics:{},elapsed_seconds:1,read_only:false,execution_label:"",report:"# Report",answer:"",plan:[],events:[],sources:[],pending_review:[],requirements:{requirements:[],requirements_supported:false},artifacts:{artifacts_valid:true},review_binding:"",files:[{name:"task_result.xlsx",bytes:1024,url:"/tasks/real/files/task_result.xlsx"}],capabilities:{can_cancel:false,can_resume:true,can_review:false,can_accept:false,has_checkpoint:true,locked:false},delivery:{outcome:"completed_with_warnings",usable:true,label:"已完成（有告警）",headline:"交付已生成，等待你核对",warning_count:1,execution_warning_count:1,warnings:[{kind:"execution",message:"old validation failure",blocking:false}],semantic_accepted:false}};
const events=[{id:1,event:"tool_start",detail:"export_business_excel",call_id:"x"},{id:2,event:"tool_error",detail:"ERROR: old validation failure",call_id:"x"}];
const show=(value:Workspace)=>renderToStaticMarkup(<TaskStream data={value} events={events} onOpenTab={()=>{}} onFile={()=>{}} onDetails={()=>{}}/>);

it("puts real deliverables before collapsed execution and keeps budget truth",()=>{
 const html=show(data);
 expect(html).toContain("预算已用尽，已保存阶段性结果");
 expect(html).toContain("交付已生成，等待你核对");
 expect(html.indexOf('aria-label="预览 task_result.xlsx"')).toBeLessThan(html.indexOf("执行记录"));
 expect(html).toContain('aria-expanded="false"');
 expect(html).not.toContain("old validation failure");
 expect(html).not.toContain("已记录人工验收");
});

it("keeps blocking errors visible when core delivery is unavailable",()=>{
 const html=show({...data,delivery:{...data.delivery!,usable:false,outcome:"failed",headline:"核心交付尚未完成",warnings:[{kind:"execution",message:"old validation failure",blocking:true,call_id:"x"}]},files:[]});
 expect(html).toContain("核心交付尚未完成");
 expect(html).toContain("old validation failure");
 expect(html).toContain("阻塞错误");
});

it("has distinct labels for successful, warning and failed delivery",()=>{
 expect(stateInfo("completed").label).toBe("已完成");
 expect(stateInfo("completed_with_warnings")).toEqual({label:"已完成（有告警）",tone:"warning"});
 expect(stateInfo("failed").label).toBe("失败");
 expect(stateInfo("partial").label).toBe("部分完成");
 expect(stateInfo("needs_review").label).toBe("结果待确认");
});

it("keeps usable files and incomplete requirements visibly partial",()=>{
 const html=show({...data,delivery:{...data.delivery!,outcome:"partial",label:"部分完成",headline:"已保存阶段性交付，仍有需求未完成"}});
 expect(html).toContain("仍有需求未完成");
 expect(html).not.toContain("已完成（有告警）");
 expect(html).not.toContain("已记录人工验收");
});
