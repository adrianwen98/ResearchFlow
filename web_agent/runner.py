"""Task lifecycle, durable graph recovery, time budgets and actual artifacts."""
from __future__ import annotations
import asyncio
import json
import time
import uuid
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langgraph.errors import GraphRecursionError,NodeCancelledError
from .browser import browser_client, load_safe_browser_tools
from .config import Settings
from .tools import build_local_tools
from .workspace import Workspace
from .graph import build_graph, ModelResponseTimeout
from .persistence import atomic_json, read_json, read_trace, redact, PersistenceError
from .completion import define_requirements,evaluate_requirements,completion_decision,automatic_receipt,reconcile_final_answer
from .memory import memory_for
from .status import completion_outcome
from .task_budget import effective_settings


def error_leaves(exc):
    """MCP/AnyIO wraps failures in ExceptionGroup; preserve useful root errors."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for child in exc.exceptions for leaf in error_leaves(child)]
    return [exc]


@dataclass
class TaskResult:
    task_id: str
    answer: str
    status: str
    sources: list = field(default_factory=list)
    files: list = field(default_factory=list)
    elapsed_seconds: float = 0
    error: str = ""
    metrics: dict = field(default_factory=dict)
    pending_review: list = field(default_factory=list)
    completion: dict = field(default_factory=dict)
    failure_code: str = ''


@asynccontextmanager
async def real_browser(settings, workspace):
    client = browser_client(settings, workspace)
    async with client.session("playwright") as session:
        yield await load_safe_browser_tools(session, settings, workspace)


async def run_task(task: str = "", task_id: str | None = None, settings: Settings | None = None,
                   *, resume=False, decision: dict | None = None, model=None, browser_factory=None, requirements:list | None=None,review_mode='auto',report_format='markdown') -> TaskResult:
    if report_format not in {'markdown','pdf','both'}:raise ValueError('无效报告格式')
    settings = settings or Settings.load()
    if model is None:
        settings.validate()
    if not resume and len(task.strip()) < 5:
        raise ValueError("请描述明确的网页任务，至少五个字符。")
    if len(task)>6000: raise ValueError('任务描述最多 6000 字符。')
    task_id = task_id or uuid.uuid4().hex[:12]
    from .task_history import deleted
    if deleted(settings.output_dir,task_id):raise ValueError('任务已删除，不能重建或恢复同一ID；请新建任务，旧预算不会重置')
    workspace = Workspace(settings.output_dir, task_id, settings.allowed_domains)
    if resume:settings=effective_settings(settings,workspace.path)
    manifest = read_json(workspace.path / 'task.json')
    if manifest and not resume:
        raise ValueError('任务 ID 已存在；继续请显式使用 --resume。')
    if resume and not manifest:
        raise ValueError('找不到该任务，无法恢复。')
    # Read durable budgets before acquiring ownership; a locked/unreadable file
    # must not strand a .running marker or turn an unreadable budget into zero.
    timer_path=workspace.path/'time_budget.json'
    timer=read_json(timer_path,{'spent':read_json(workspace.path/'result.json',{}).get('elapsed_seconds',0) if resume else 0})
    spent=float(timer.get('spent',0))
    if timer.get('active_since'):
        spent+=max(0,time.time()-timer['active_since'])
    remaining_time=max(0,settings.task_timeout-spent)
    # Cross-process exclusion: do not overwrite checkpoints for the same task.
    lock = workspace.path / ".running"
    try:
        handle = lock.open("x")
    except FileExistsError:
        raise RuntimeError("任务正在运行，或上次进程异常退出。确认无运行进程后使用 --unlock 恢复。")
    with handle:
        json.dump({"pid":os.getpid(),"task_id":task_id},handle)
    started = time.monotonic()
    result = TaskResult(workspace.task_id, "", "error")
    output = {}
    try:
        try:
            atomic_json(timer_path,{'spent':spent,'active_since':time.time()})
            manifest = read_json(workspace.path / "task.json")
            if manifest and not resume:
                raise ValueError("任务 ID 已存在；继续请显式使用 --resume。")
            if resume and not manifest:
                raise ValueError("找不到该任务，无法恢复。")
            if not manifest:
                manifest = {"task": task, "task_id": task_id, 'review_mode':review_mode,'report_format':report_format,"created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
                atomic_json(workspace.path / "task.json", manifest)
            workspace.log("task_resume" if resume else "task_start", manifest["task"][:500])
            if resume:
                memory_for(workspace).invalidate_task(workspace.task_id)
                (workspace.path/'acceptance.json').unlink(missing_ok=True)
            if requirements:define_requirements(workspace,requirements,origin='user')
            atomic_json(workspace.path/'run_limits.json',{'model_requests':settings.max_model_calls,'tool_calls':settings.max_tool_calls,'timeout_seconds':settings.task_timeout})
            if model is None:
                model = ChatOpenAI(model=settings.model, api_key=settings.api_key, base_url=settings.base_url,
                                   temperature=0, timeout=60, max_retries=0, max_tokens=2200,
                                   extra_body={"thinking": {"type": "disabled"}})
            settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
                async with asyncio.timeout(remaining_time):
                    async with (browser_factory or real_browser)(settings, workspace) as browser_tools:
                        graph = build_graph(model, browser_tools + build_local_tools(workspace, settings), workspace,
                                            settings, saver, datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                                            form_controller=getattr(browser_tools,'form_controller',None),resuming=resume)
                        config = {"configurable": {"thread_id": workspace.task_id}, "recursion_limit": settings.max_graph_steps}
                        snapshot = await graph.aget_state(config)
                        if resume:
                            if not snapshot.values:
                                raise ValueError("Checkpoint 丢失；不能冒充恢复成功。")
                            pending = [i for t in snapshot.tasks for i in t.interrupts]
                            if pending and decision is None:
                                result.status = "awaiting_review"
                                result.pending_review = [i.value for i in pending]
                                output = snapshot.values
                            else:
                                if decision is not None and not pending:
                                    raise ValueError("该任务没有待处理的人工审核。")
                                if decision is not None and decision.get("decision") not in {"handled", "reject",'approve'}:
                                    raise ValueError("审核决策只支持 approve/handled/reject。")
                                if decision is not None and decision.get('decision')=='approve':
                                    if not pending or pending[0].value.get('tool')!='submit_public_form' or decision.get('approval_id')!=pending[0].value.get('approval_id'):
                                        raise ValueError('approve必须绑定当前表单审批ID')
                                if task.strip() and not pending and snapshot.next:
                                    await graph.aupdate_state(config, {'pending_followup':task, 'verification_rounds':0, 'answer':''})
                                payload = Command(resume=decision) if pending else None if snapshot.next else {
                                    'messages':[HumanMessage(task or manifest.get('revision_note') or '继续任务：核对现有证据和交付，说明缺失内容。')],
                                    'verification_rounds':0, 'answer':''}
                                output = await graph.ainvoke(payload, config)
                        else:
                            output = await graph.ainvoke({"messages": [{"role": "user", "content": task}], "model_calls":0,
                                                        "tool_calls":0, "errors":0, "verification_rounds":0}, config)
                        # Durable phase markers locate a slow graph/checkpoint/browser
                        # teardown without changing deadlines or skipping safe cleanup.
                        workspace.log('graph_invoke_returned','',status=output.get('status',''))
                        if output.get("__interrupt__"):
                            result.status = "awaiting_review"
                            result.pending_review = [i.value for i in output["__interrupt__"]]
                        elif result.status != "awaiting_review":
                            result.status = output.get("status", "incomplete")
                        result.answer = output.get("answer", "等待人工审核；外部操作尚未执行。")
                        result.metrics = {k:output.get(k,0) for k in ("model_calls","tool_calls","errors")}
                    workspace.log('browser_context_closed','')
                workspace.log('run_async_context_closed','')
        except ModelResponseTimeout as exc:
            result.status, result.failure_code = 'timeout', 'model_timeout'
            result.answer = str(exc)
            workspace.log('task_limit', str(exc), code='model_timeout')
        except (TimeoutError, GraphRecursionError) as exc:
            result.status = "timeout" if isinstance(exc, TimeoutError) else "budget_exceeded"
            result.answer = (f"达到任务时间上限 {settings.task_timeout} 秒。" if isinstance(exc,TimeoutError)
                             else f"达到 LangGraph 图步数上限 {settings.max_graph_steps} 步。")+"中间结果已保留，可调整预算后显式恢复。"
            workspace.log("task_limit", type(exc).__name__)
        except Exception as exc:
            leaves=error_leaves(exc)
            result.status = 'interrupted' if any(isinstance(item,NodeCancelledError) for item in leaves) else 'timeout' if any(isinstance(item,TimeoutError) for item in leaves) else 'error'
            if any(isinstance(item,PersistenceError) for item in leaves):result.failure_code='storage_error'
            elif any(isinstance(item,ModelResponseTimeout) for item in leaves):result.failure_code='model_timeout'
            result.error = redact('; '.join(f'{type(item).__name__}: {item}' for item in leaves[:3]))[:1000]
            result.answer = ('执行节点已中断，Checkpoint 和累计预算保留。' if result.status=='interrupted' else "任务失败：") + result.error
            workspace.log("task_error", result.error)
        # Independent local exports survive model/transport/budget failures.
        # Approval pauses retain pending requirements and their checkpoint.
        try:
            if result.status!='awaiting_review' and not output.get('host_finalized'):
                from .delivery_pipeline import finalize_delivery
                finalize_delivery(workspace,result.answer or '执行结束，保存现有结果')
        except Exception as exc:
            result.status, result.error = "error", "交付文件生成失败：" + redact(str(exc))[:500]
        result.sources, result.files = workspace.sources(), workspace.list_artifacts()
        result.elapsed_seconds = round(time.monotonic() - started, 2)
        result.metrics.update(read_json(workspace.path / "usage.json", {}))
        result.metrics['tool_attempts']=read_json(workspace.path/'tool_usage.json',{}).get('attempts',len(read_json(workspace.path/'tool_journal.json',{})))
        trace_path=workspace.path/'trace.jsonl'
        if trace_path.exists():
            trace=read_trace(trace_path)
            result.metrics['tool_calls']=len(read_json(workspace.path/'tool_journal.json',{}))
            result.metrics['errors']=sum(row['event']=='tool_error' for row in trace)
            result.metrics['retries']=sum(row['event']=='tool_retry' for row in trace)
        result.metrics['limits']={'model_requests':settings.max_model_calls,'tool_calls':settings.max_tool_calls,
                                  'graph_steps':settings.max_graph_steps,'timeout_seconds':settings.task_timeout}
        result.metrics['execution_seconds_used']=round(spent+time.monotonic()-started,3)
        result.metrics['budget_scope']='per task including resume; model/tool attempts and execution time never reset'  
        check = workspace.verify_delivery()
        from .workflow_invariants import cleared_delivery_failure
        clean_answer, clean_requirements = cleared_delivery_failure(
            result.answer, read_json(workspace.path/'requirements.json',[]), check)
        if clean_answer != result.answer:
            result.answer = clean_answer
            atomic_json(workspace.path/'requirements.json',clean_requirements)
            workspace.log('delivery_error_reconciled',
                          '重新导出后文件校验已通过；清理之前的过时错误，不变更研究需求为已完成。')
        if result.status in {'completed','needs_review','partial','incomplete'}:
            final=completion_decision(workspace)
            result.status=final['status']
            result.answer=reconcile_final_answer(
                result.answer, result.status, evaluate_requirements(workspace))
            if result.status=='completed':automatic_receipt(workspace)
        journal = read_json(workspace.path / 'tool_journal.json', {})

        model_report_delivered = (
                check['artifacts_valid']
                and any(
            entry.get('name') == 'export_report'
            and not entry.get('error_code')
            and not str(entry.get('output', '')).startswith('ERROR:')
            for entry in journal.values()
        )
        )

        budget_exhausted = (
                result.metrics.get('requests', 0) >= settings.max_model_calls
                or result.metrics.get('tool_attempts', 0) >= settings.max_tool_calls
        )

        if result.status in {'needs_review', 'partial', 'incomplete'} and budget_exhausted:
            if not (result.status == 'needs_review' and model_report_delivered):
                result.status = 'budget_exceeded'
        outcome = completion_outcome(result.status,bool(result.sources))
        result.completion = {'outcome':outcome, **check,**evaluate_requirements(workspace)}
        from .delivery_status import aggregate_delivery
        result.completion['delivery']=aggregate_delivery(workspace,result.status,check,result.completion)
        if result.status=='completed':
            result.completion.update(all_requirements_verified=True,semantic_review_required=False,
                                     acceptance=read_json(workspace.path/'acceptance.json',{}))
        elif result.status=='needs_review':result.completion['review_reason']=completion_decision(workspace)['reason']
        atomic_json(workspace.path/'delivery_check.json', result.completion)
        workspace.log("task_end", result.status, metrics=result.metrics, completion=result.completion)
        if result.status == "budget_exceeded":
            used = result.metrics.get("requests")
            limit = result.metrics.get("limits", {}).get("model_requests")

            if isinstance(used, int) and isinstance(limit, int) and 0 < limit <= used:
                budget_text = f"{used}/{limit}"
                if budget_text not in (result.answer or ""):
                    result.answer = f"模型请求预算已用尽（{budget_text}）。\n{result.answer or ''}"
        atomic_json(workspace.path / "result.json", asdict(result))
        return result
    except asyncio.CancelledError:
        workspace.log('task_interrupted', '任务协程被取消；已提交的 Checkpoint 可恢复。')
        atomic_json(workspace.path/'result.json', {'task_id':task_id,'status':'interrupted',
                    'answer':'执行被中断，已提交的状态和工具日志保留，可显式恢复。',
                    'metrics':read_json(workspace.path/'usage.json',{})})
        raise
    finally:
        try:
            atomic_json(timer_path,{'spent':round(spent+time.monotonic()-started,3),'active_since':None})
        finally:
            # A failed storage write must not leave a permanent 'running' lock.
            lock.unlink(missing_ok=True)
