"""ResearchFlow: local research workspace with live, factual execution views."""
from __future__ import annotations
import asyncio
import time
import uuid
import os
import re
from dataclasses import replace
from urllib.parse import urlsplit
from web_agent.security import validate_public_url
from pathlib import Path
import gradio as gr
from web_agent.config import ROOT, Settings
from web_agent.runner import run_task
from web_agent.persistence import redact
from web_agent.workspace import Workspace
from web_agent.completion import accept_delivery
from web_agent.memory import MemoryStore
from web_agent.dashboard import snapshot, summary_html, stage_html, sources_html, trace_html, empty_html, history_choices, requirements_html, result_summary_html, workbook_preview_html, recommended_demo

UI_CSS=(ROOT/'assets'/'dashboard.css').read_text(encoding='utf-8')
UI_THEME=gr.themes.Base(primary_hue='teal',neutral_hue='slate',radius_size='lg',
                        font=['Segoe UI','Microsoft YaHei','Arial','sans-serif'],
                        font_mono=['Consolas','monospace'])
EXAMPLES=[
    ['访问 https://www.python.org/about/ 和 https://pandas.pydata.org/about/，分别调查两个项目的定位与主要用途。每站记录至少一条官方来源，生成对比 Excel 和简短中文 Markdown 报告。'],
    ['下载 https://raw.githubusercontent.com/mwaskom/seaborn-data/master/iris.csv，检查真实列名，按 species 统计数量，记录数据来源，生成 Excel 和中文研究报告。'],
    ['访问 https://www.python.org/about/，调查 Python 的定位和主要用途，记录至少一条官方原文来源，导出 Excel 和简短中文报告。']]
_JOBS={}

# Preserve the existing programmatic helpers; the UI uses streaming views below.
def display(result):
    brief=f'**状态：{result.status}**　任务 ID：`{result.task_id}`\n\n'+result.answer
    rows=[[r.get('id'),r.get('title'),r.get('fact'),r.get('url')] for r in result.sources]
    return brief,rows,result.files,result.task_id

async def submit(task):
    try: return display(await run_task(task,task_id=uuid.uuid4().hex[:12]))
    except Exception as exc: return redact(str(exc)),[],[],''

async def review(task_id,decision):
    try:
        result=await run_task('',task_id,resume=True,decision={'decision':decision} if decision else None)
        return display(result)
    except Exception as exc: return redact(str(exc)),[],[],task_id

async def resume_ui(task_id): return await review(task_id,None)
async def reject_ui(task_id): return await review(task_id,'reject')
async def handled_ui(task_id): return await review(task_id,'handled')


def build_run_settings(request_limit=None, domains=None):
    current=Settings.load()
    count=current.max_model_calls if request_limit is None else int(request_limit)
    if not 1<=count<=50:raise ValueError('模型请求预算必须在 1–50 次之间。')
    allowed=current.allowed_domains
    if domains is not None:
        hosts=[]
        for value in re.split(r'[,，\s]+',domains.strip()):
            if not value:continue
            url=value if '://' in value else 'https://'+value
            validate_public_url(url)
            host=(urlsplit(url).hostname or '').encode('idna').decode().lower().rstrip('.')
            if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*[a-z0-9]',host):raise ValueError('请输入有效域名，如 nodejs.org。')
            if host not in hosts:hosts.append(host)
        allowed=tuple(hosts)
        if not allowed and os.getenv('ALLOW_FAKE_IP_PROXY')=='true':
            raise ValueError('本机代理兼容模式需要明确的允许站点，请至少填写目标域名。')
    return replace(current,max_model_calls=count,max_graph_steps=min(100,max(current.max_graph_steps,2*count+5)),
                   max_tool_calls=max(current.max_tool_calls,count*3),allowed_domains=allowed)


def budget_hint(value):
    count=int(value) if value is not None else Settings.load().max_model_calls
    return f'<div class="budget-note">本任务最多 {count} 次模型请求 · 恢复时累计计数</div>'


def present(data,error=''):
    running=data['status']=='running'
    report=data['report'] or (data['answer'] if data['status'] not in {'idle','running'} else '')
    pending=data['pending']
    notice=error
    if data['status']=='partial' and not notice:notice='**部分完成**：'+data['answer']
    if data['status']=='budget_exceeded' and not notice:
        notice='**任务因执行预算暂停**：'+data['answer']+' 在左侧运行设置中调整后，可恢复原任务继续处理。' 
    if pending:
        actions=[str(item.get('arguments',{}).get('action','需要人工处理')) for item in pending]
        notice='**等待人工审批**：'+ '；'.join(actions)+'。核对下面的目标与字段后再批准；也可拒绝或人工处理。'
    return (summary_html(data),stage_html(data),gr.update(value=report,visible=bool(report)),
            gr.update(visible=not bool(report)),sources_html(data),trace_html(data),data['files'],data['task_id'],
            gr.update(visible=bool(pending)),gr.update(value=notice,visible=bool(notice)),gr.update(interactive=not running),
            {'task_id':data['task_id'],'binding':data.get('review_binding',''),'requirement_ids':[r['id'] for r in data.get('requirements',[])],
             'approval_id':pending[0].get('approval_id','') if pending else ''},
            requirements_html(data),gr.update(value=pending[0].get('preview',pending[0].get('arguments',{})) if pending else {},visible=bool(pending)),
            result_summary_html(data),workbook_preview_html(data),gr.update(interactive=data.get('can_accept',False)))


def load_task(task_id):
    try:
        if not task_id: return present(snapshot(),error='请先选择一条已保存的任务。')
        data=snapshot(task_id)
        if not data['task']: raise ValueError('未找到该任务，请检查任务 ID。')
        return present(data)
    except Exception as exc: return present(snapshot(),error=redact(str(exc)))


def show_demo():
    task_id=recommended_demo()
    if not task_id:return present(snapshot(),error='尚无本地演示记录；请先运行免费演示准备脚本。')
    return present(snapshot(task_id),error='正在查看已保存演示 '+task_id+'；查看不会调用模型。执行方式在结果摘要中明确标注。')


def refresh_history(): return gr.update(choices=history_choices())

def example_research(): return EXAMPLES[0][0]
def example_data(): return EXAMPLES[1][0]
def example_single(): return EXAMPLES[2][0]


async def stream_task(task='',task_id='',decision=None,resume=False,request_limit=None,domains=None,requirements_text=''):
    if not resume:
        if not 5<=len(task.strip())<=6000:
            yield present(snapshot(),error='请填写 5–6000 字符的研究任务。')
            return
        task_id=uuid.uuid4().hex[:12]
    elif not task_id:
        yield present(snapshot(),error='请先选择任务，或输入需要恢复的任务 ID。')
        return
    if resume:
        try:
            existing=snapshot(task_id)
            if not existing['task']: raise ValueError('未找到该任务，请检查任务 ID。')
        except Exception as exc:
            yield present(snapshot(),error=redact(str(exc)))
            return
    if task_id in _JOBS and not _JOBS[task_id].done():
        yield present(snapshot(task_id),error='该任务仍在执行，请等待完成后再恢复。')
        return
    try: runtime_settings=build_run_settings(request_limit,domains)
    except (ValueError,TypeError) as exc:
        yield present(snapshot(),error=redact(str(exc)))
        return
    started=time.monotonic()
    criteria=[{'description':line.strip(),'kind':'research'} for line in requirements_text.splitlines() if line.strip()]
    job=asyncio.create_task(run_task(task,task_id,settings=runtime_settings,resume=resume,decision=decision,requirements=criteria))
    _JOBS[task_id]=job
    def finished(future):
        _JOBS.pop(task_id,None)
        if not future.cancelled(): future.exception()  # consume exceptions if the browser disconnects
    job.add_done_callback(finished)
    try:
        while not job.done():
            data=snapshot(task_id,started=started)
            data['status']='running'
            yield present(data)
            await asyncio.wait({job},timeout=0.8)
        result=await job
        yield present(snapshot(task_id,result=result))
    except asyncio.CancelledError:
        yield present(snapshot(task_id),error='任务已停止；已保存的状态可以恢复。')
    except Exception as exc:
        data=snapshot(task_id)
        data['status']='error'
        yield present(data,error=redact(f'{type(exc).__name__}: {exc}'))

async def start_stream(task,request_limit=None,domains=None,requirements_text=''):
    async for view in stream_task(task=task,request_limit=request_limit,domains=domains,requirements_text=requirements_text): yield view
async def resume_stream(task_id,request_limit=None,domains=None,followup='',requirements_text=''):
    from web_agent.persistence import read_json
    from web_agent.dashboard import task_folder
    if read_json(task_folder(task_id)/'demo_meta.json',{}).get('read_only'):
        yield present(snapshot(task_id),error='这是脱敏演示副本，仅供查看；继续执行请创建新任务。');return
    async for view in stream_task(task=followup,task_id=task_id,resume=True,request_limit=request_limit,domains=domains,requirements_text=requirements_text): yield view
async def reject_stream(task_id,request_limit=None,domains=None):
    async for view in stream_task(task_id=task_id,resume=True,decision={'decision':'reject'},request_limit=request_limit,domains=domains): yield view
async def handled_stream(task_id,request_limit=None,domains=None):
    async for view in stream_task(task_id=task_id,resume=True,decision={'decision':'handled'},request_limit=request_limit,domains=domains): yield view

async def approve_stream(task_id,review_state,request_limit=None,domains=None):
    if review_state.get('task_id')!=task_id or not review_state.get('approval_id'):
        yield present(snapshot(task_id),error='请选择并核对当前表单审批；旧预览不能用于新操作。');return
    async for view in stream_task(task_id=task_id,resume=True,decision={'decision':'approve','approval_id':review_state['approval_id']},request_limit=request_limit,domains=domains):yield view

async def cancel_ui(task_id):
    job=_JOBS.get(task_id)
    if not job or job.done():return present(snapshot(task_id),error='该任务当前没有在本页面执行。')
    job.cancel()
    try:await job
    except asyncio.CancelledError:pass
    return present(snapshot(task_id),error='已停止执行，Checkpoint与已有资料保留。')

def accept_ui(task_id,review_state):
    try:
        if review_state.get('task_id')!=task_id:raise ValueError('请重新打开并核对任务')
        from web_agent.dashboard import task_folder
        folder=task_folder(task_id)
        accept_delivery(Workspace(folder.parent,task_id),review_state.get('requirement_ids',[]),review_state.get('binding',''))
        return present(snapshot(task_id),error='全部需求已验收；摘要已写入可删除的跨任务记忆。')
    except ValueError as exc:return present(snapshot(task_id),error=str(exc))

def memory_list():return MemoryStore(Settings.load().output_dir/'memory.sqlite').list()
def save_preference(content):
    try:
        MemoryStore(Settings.load().output_dir/'memory.sqlite').save(content)
        return memory_list(),'已保存，下一个任务会按需读取。'
    except ValueError as exc:return memory_list(),str(exc)
def delete_memory(key):
    removed=MemoryStore(Settings.load().output_dir/'memory.sqlite').delete(key.strip())
    return memory_list(),'已删除。' if removed else '未找到该记忆ID。'

initial=snapshot()
settings=Settings.load()
with gr.Blocks(title='ResearchFlow · 研究工作台',analytics_enabled=False,fill_width=True) as demo:
    review_state=gr.State({})
    gr.HTML('''<header class="app-header"><div class="brand"><div class="brand-symbol">R<span>↗</span></div><div><strong>ResearchFlow</strong><span class="brand-subtitle">RESEARCH WORKSPACE</span></div></div><div class="header-right"><span class="local-indicator"><i></i>本地工作区</span><span class="header-divider"></span><span>公开网页研究</span></div></header>''',elem_id='topbar')
    with gr.Column(elem_id='workspace-shell'):
        with gr.Row(elem_id='page-heading'):
            gr.HTML('<div class="page-title"><span class="section-eyebrow">从任务到交付</span><h1>网页研究工作台<span class="title-dot">.</span></h1><p>描述目标，跟踪过程，核对证据，交付结果。</p></div>')
            demo_button=gr.Button('查看真实演示 ↗',size='md',elem_id='demo-button',scale=0,min_width=155)
        metrics=gr.HTML(summary_html(initial),elem_id='metrics')
        with gr.Row(elem_id='main-grid',equal_height=False):
            with gr.Column(scale=4,min_width=310,elem_id='composer-card'):
                gr.HTML('<div class="card-heading"><span class="section-number">01</span><div><h2>创建研究任务</h2><p>告诉 Agent 你想了解什么</p></div></div>')
                task_input=gr.Textbox(label='研究目标',show_label=False,lines=8,max_lines=10,placeholder='例如：比较 Python 与 pandas 的项目定位和用途，优先使用官方来源，并生成 Excel 和研究报告。',elem_id='task-input')
                gr.HTML('<div class="input-hint">建议写明研究对象、关注字段和交付格式。</div>')
                requirements_input=gr.Textbox(label='验收标准（可选，每行一项）',lines=2,placeholder='留空由 Agent 拆解；研究时可继续补充。')
                run_button=gr.Button('开始执行任务  →',variant='primary',elem_id='run-button')
                budget_note=gr.HTML(budget_hint(settings.max_model_calls))
                gr.HTML('<div class="example-label">从一个小任务开始</div>')
                with gr.Row(elem_id='example-buttons'):
                    research_example=gr.Button('跨站研究',size='sm',elem_classes='example-chip',min_width=85)
                    data_example=gr.Button('数据分析',size='sm',elem_classes='example-chip',min_width=85)
                    single_example=gr.Button('单站调查',size='sm',elem_classes='example-chip',min_width=85)
                with gr.Accordion('运行设置 · 请求预算与允许站点',open=False,elem_id='runtime-settings'):
                    request_limit=gr.Number(value=settings.max_model_calls,precision=0,minimum=1,maximum=50,label='模型请求总预算',
                                            info='一次模型请求可能调用多个工具；恢复任务时不会重置已用次数。')
                    domain_input=gr.Textbox(value=','.join(settings.allowed_domains),lines=3,label='本任务允许访问的站点',
                                            info='逗号分隔，如 python.org,nodejs.org；父域名自动包含子域。新增网站可在这里补充。')
                gr.HTML('<div class="panel-rule"></div><div class="subsection-title">已保存的任务</div>')
                history=gr.Dropdown(choices=history_choices(),value=None,label='任务历史',show_label=False,interactive=True,elem_id='history-select')
                with gr.Row(elem_id='history-actions'):
                    open_button=gr.Button('打开任务',size='sm',min_width=100)
                    refresh_button=gr.Button('刷新列表',size='sm',min_width=90)
                with gr.Accordion('恢复与人工处理',open=False,elem_id='recovery-panel'):
                    current_id=gr.Textbox(label='任务 ID',placeholder='选择历史任务后自动填写',max_lines=1)
                    followup_input=gr.Textbox(label='追加要求 / 纠正',lines=2,placeholder='继续同一任务时填写，历史与已用预算保留')
                    resume_button=gr.Button('恢复任务 / 查看待处理项',size='sm')
                    cancel_button=gr.Button('停止当前任务',size='sm')
                    with gr.Column(visible=False) as approval_controls:
                        gr.Markdown('批准仅适用于预览中的一次普通表单POST；账户、支付等仍由人处理。')
                        approve_button=gr.Button('批准这一次表单提交',size='sm',variant='primary')
                        reject_button=gr.Button('拒绝外部操作，继续研究',size='sm')
                        handled_button=gr.Button('已人工处理，继续读取',size='sm')
                with gr.Accordion('跨任务记忆 · 偏好与已验收摘要',open=False):
                    preference_input=gr.Textbox(label='明确保存的长期偏好',lines=2)
                    save_memory_button=gr.Button('保存偏好',size='sm')
                    memories=gr.JSON(value=memory_list(),label='已保存记忆')
                    delete_memory_id=gr.Textbox(label='要删除的记忆 ID')
                    delete_memory_button=gr.Button('删除这条记忆',size='sm')
                    memory_notice=gr.Markdown('网页内容不会自动写入长期记忆。')
            with gr.Column(scale=8,min_width=480,elem_id='results-card'):
                with gr.Row(elem_id='results-heading'):
                    gr.HTML('<div class="card-heading"><span class="section-number">02</span><div><h2>研究与交付</h2><p>结果、依据和执行过程，集中核对</p></div></div>')
                stages=gr.HTML(stage_html(initial),elem_id='stages')
                notice=gr.Markdown('',visible=False,elem_id='notice')
                approval_preview=gr.JSON(label='待批准操作：目标与完整字段',visible=False)
                result_summary=gr.HTML(result_summary_html(initial),elem_id='result-summary')
                with gr.Accordion('逐项需求与最终验收',open=True):
                    requirement_view=gr.HTML(requirements_html(initial),elem_id='requirement-view')
                    accept_button=gr.Button('我已逐项核对，确认全部需求完成',size='sm',interactive=False,elem_id='accept-button')
                with gr.Tabs(elem_id='result-tabs'):
                    with gr.Tab('研究报告',id='report'):
                        blank=gr.HTML(empty_html(),elem_id='report-empty')
                        report=gr.Markdown('',visible=False,elem_id='report-body',sanitize_html=True)
                    with gr.Tab('执行过程',id='trace'):
                        trace=gr.HTML(trace_html(initial),elem_id='trace-body')
                    with gr.Tab('来源证据',id='sources'):
                        sources=gr.HTML(sources_html(initial),elem_id='sources-body')
                    with gr.Tab('数据预览',id='data'):
                        workbook_preview=gr.HTML(workbook_preview_html(initial),elem_id='workbook-preview')
                    with gr.Tab('交付文件',id='files'):
                        gr.HTML('<div class="files-intro"><h3>可下载的任务产物</h3><p>报告、Excel 和执行轨迹均来自当前任务的实际文件。</p></div>')
                        files=gr.File(label='交付清单',show_label=False,file_count='multiple',interactive=False,elem_id='delivery-files')
                        gr.HTML('<div class="file-note">Markdown · 研究报告 &nbsp;&nbsp; XLSX · 来源证据 &nbsp;&nbsp; JSONL · 执行轨迹</div>')
        gr.HTML('<footer class="workspace-footer"><span><i></i>所有状态与数据来自真实执行记录</span><span>公开网页研究 · 结论需人工复核 · 文件保存在本机</span></footer>')
    ui_outputs=[metrics,stages,report,blank,sources,trace,files,current_id,approval_controls,notice,run_button,review_state,requirement_view,approval_preview,result_summary,workbook_preview,accept_button]
    run_event=run_button.click(start_stream,[task_input,request_limit,domain_input,requirements_input],ui_outputs,concurrency_id='agent_execution',concurrency_limit=1,show_progress='hidden')
    run_event.then(refresh_history,None,history,queue=False)
    request_limit.change(budget_hint,request_limit,budget_note,queue=False)
    open_button.click(load_task,history,ui_outputs,queue=False,show_progress='hidden')
    demo_button.click(show_demo,None,ui_outputs,queue=False,show_progress='hidden')
    refresh_button.click(refresh_history,None,history,queue=False)
    research_example.click(example_research,None,task_input,queue=False)
    data_example.click(example_data,None,task_input,queue=False)
    single_example.click(example_single,None,task_input,queue=False)
    resume_button.click(resume_stream,[current_id,request_limit,domain_input,followup_input,requirements_input],ui_outputs,concurrency_id='agent_execution',concurrency_limit=1,show_progress='hidden')
    reject_button.click(reject_stream,[current_id,request_limit,domain_input],ui_outputs,concurrency_id='agent_execution',concurrency_limit=1,show_progress='hidden')
    approve_button.click(approve_stream,[current_id,review_state,request_limit,domain_input],ui_outputs,concurrency_id='agent_execution',concurrency_limit=1,show_progress='hidden')
    cancel_button.click(cancel_ui,current_id,ui_outputs,queue=False)
    accept_button.click(accept_ui,[current_id,review_state],ui_outputs,queue=False)
    save_memory_button.click(save_preference,preference_input,[memories,memory_notice],queue=False)
    delete_memory_button.click(delete_memory,delete_memory_id,[memories,memory_notice],queue=False)
    handled_button.click(handled_stream,[current_id,request_limit,domain_input],ui_outputs,concurrency_id='agent_execution',concurrency_limit=1,show_progress='hidden')

if __name__=='__main__':
    demo.queue(default_concurrency_limit=1).launch(server_name='127.0.0.1',server_port=7860,share=False,
        inbrowser=True,show_error=False,theme=UI_THEME,css=UI_CSS,footer_links=[])
