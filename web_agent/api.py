"""Single-process local demo API. Bind to 127.0.0.1; not a multi-user service."""
import asyncio
import os
import re
import subprocess
import uuid
import json
import hashlib
import sqlite3
from pathlib import Path
from dataclasses import asdict
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field,model_validator
from .config import Settings
from .runner import run_task
from .persistence import read_json, atomic_json
from . import web_views as web
from .workspace import Workspace
from .completion import accept_delivery,define_requirements,review_binding,evaluate_requirements,current_result,verify_existing_delivery,request_delivery_revision
from .memory import MemoryStore
from . import task_history as task_management
from . import task_budget
from . import backup as backup_tools
from .task_intake import prepare_task

app = FastAPI(title='ResearchFlow',version='0.3.0')
jobs: dict[str,asyncio.Task] = {}
semaphore = asyncio.Semaphore(1)
LOCAL_ORIGINS = {f'http://{host}:{port}' for host in ('127.0.0.1','localhost') for port in (5173,8000)}
app.add_middleware(CORSMiddleware,allow_origins=sorted(LOCAL_ORIGINS),allow_methods=['GET','POST','DELETE'],allow_headers=['Content-Type','Idempotency-Key'],allow_credentials=False)

@app.middleware('http')
async def local_browser_boundary(request:Request,call_next):
    origin=request.headers.get('origin')
    if request.method not in {'GET','HEAD','OPTIONS'} and origin and origin not in LOCAL_ORIGINS:
        return JSONResponse({'detail':'Only the local workbench may change tasks'},status_code=403)
    if request.url.hostname not in {'127.0.0.1','localhost','testserver'}:
        return JSONResponse({'detail':'Local host required'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


class TaskInput(BaseModel):
    task: str = Field(min_length=5,max_length=6000)
    requirements:list[dict]=Field(default_factory=list,max_length=16)
    review_mode:Literal['auto','manual']='auto'
    report_format:Literal['markdown','pdf','both']='both'
    clarifications:dict[str,str]=Field(default_factory=dict,max_length=3)


class PrepareInput(BaseModel):
    model_config = {'extra': 'forbid'}
    task:str=Field(min_length=5,max_length=6000)
    report_format:Literal['markdown','pdf','both']='both'
    clarifications:dict[str,str]=Field(default_factory=dict,max_length=3)


class ResumeInput(BaseModel):
    task:str=Field(default='',max_length=6000)
    requirements:list[dict]=Field(default_factory=list,max_length=16)


class BudgetResumeInput(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: str = Field(min_length=1,max_length=56,pattern=r'^[A-Za-z0-9_-]+$')
    additional_requests: int = Field(strict=True,ge=1,le=task_budget.MAX_ADDITION)
    binding: str = Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]+$')
    operation_id: str = Field(min_length=16,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    confirm_paid_execution: bool = Field(strict=True)


class ReviewInput(BaseModel):
    decision: Literal['reject','handled','approve']
    note: str = Field(default='',max_length=1000)
    approval_id:str=Field(default='',max_length=64)
    @model_validator(mode='after')
    def require_binding(self):
        if self.decision=='approve' and len(self.approval_id)!=64:raise ValueError('approve必须提供本次approval_id')
        return self


class AcceptanceInput(BaseModel):
    accept_limitations:bool=False
    requirement_ids:list[str]=Field(min_length=1,max_length=16)
    binding:str=Field(min_length=64,max_length=64)
    note:str=Field(default='',max_length=1000)


class RevisionInput(BaseModel):
    binding:str=Field(min_length=64,max_length=64)
    note:str=Field(min_length=3,max_length=1000)


class MemoryInput(BaseModel):
    content:str=Field(min_length=2,max_length=1500)


class UnlockInput(BaseModel):
    model_config = {'extra': 'forbid'}
    confirm: bool


class RenameTaskInput(BaseModel):
    title:str=Field(min_length=1,max_length=160)


class ArchiveTaskInput(BaseModel):
    archived:bool


class DeleteTaskInput(BaseModel):
    confirmation:str=Field(min_length=1,max_length=56)
    delete_records:bool=False
    delete_checkpoint:bool=False
    delete_files:bool=False


def task_path(task_id):
    folder=web.safe_folder(task_id)
    if task_management.deleted(folder.parent,task_id):raise HTTPException(404,'任务已删除')
    return folder

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                                 capture_output=True, text=True, timeout=5)
            return str(pid) in out.stdout
        except (OSError, subprocess.SubprocessError):
            return True  # unknown: refuse to remove a lock we cannot verify
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def execute(task_id, task='', **kwargs):
    async with semaphore:
        try:return await run_task(task,task_id,**kwargs)
        except asyncio.CancelledError:raise
        except Exception as exc:
            folder=task_path(task_id)
            folder.mkdir(parents=True,exist_ok=True)
            result={'task_id':task_id,'status':'error','error':web.clean(str(exc))[:1000],'answer':'执行未能启动，请检查服务端配置。'}
            Workspace(folder.parent,task_id).log('task_error',result['error'])
            atomic_json(folder/'result.json',result)
            return result

@app.get('/health')
def health(): return {'status':'ok','app':'webtask-agent','ui':'react','mode':'local-single-process','external_writes':'approval_required','approved_public_forms':True}


@app.get('/settings')
def public_settings():
    cfg=Settings.load()
    return {'model':cfg.model,'model_requests':cfg.max_model_calls,'tool_calls':cfg.max_tool_calls,
        'timeout_seconds':cfg.task_timeout,'api_key_configured':bool(cfg.api_key),'allowed_domains':list(cfg.allowed_domains),
        'network_mode':cfg.network_mode,'public_dns_mode':cfg.public_dns_mode,
        'mode':'local-single-process','paid_execution':'explicit_user_action'}

def _active_jobs():
    return any(not job.done() for job in jobs.values())


@app.get('/storage')
def storage():
    return backup_tools.storage_summary(Settings.load())


@app.get('/backup')
def download_backup():
    if _active_jobs():
        raise HTTPException(409, '有任务正在执行，请等任务结束后再备份')
    path = backup_tools.create_backup(Settings.load())
    filename = 'webtask-backup-' + __import__('datetime').datetime.now().strftime('%Y%m%d-%H%M') + '.zip'
    return FileResponse(path, media_type='application/zip', filename=filename,
                        background=BackgroundTask(path.unlink, missing_ok=True))


@app.post('/maintenance/cleanup')
def maintenance_cleanup():
    if _active_jobs():
        raise HTTPException(409, '有任务正在执行，请等任务结束后再清理')
    cfg = Settings.load()
    result = backup_tools.cleanup_temp(cfg)
    try:
        compact = backup_tools.compact_checkpoints(cfg)
    except sqlite3.Error:
        result['warning'] = '数据库正忙，临时文件已清理，检查点未压缩'
    else:
        result['freed_bytes'] += compact['freed_bytes']
        result.update({k: v for k, v in compact.items() if k != 'freed_bytes'})
    return result


@app.get('/tasks')
def list_tasks(archived:bool=False):
    root=Settings.load().output_dir
    items=[]
    if root.exists():
        for folder in root.iterdir():
            if folder.is_dir() and not folder.is_symlink() and ((folder/'task.json').is_file() or (folder/'queued.json').is_file()):
                try:
                    web.safe_folder(folder.name)
                    meta=task_management.metadata(root,folder.name)
                    if meta.get('deleted') or bool(meta.get('archived'))!=archived:continue
                    items.append(( ((folder/'task.json') if (folder/'task.json').exists() else (folder/'queued.json')).stat().st_mtime, web.overview(folder,jobs)))
                except (HTTPException,ValueError,OSError):continue
    return {'tasks':[row for _,row in sorted(items,key=lambda x:x[0],reverse=True)[:200]]}


def managed_folder(task_id,*,allow_deleted=False):
    folder=web.safe_folder(task_id)
    meta=task_management.metadata(folder.parent,task_id)
    if meta.get('deleted') and not allow_deleted:raise HTTPException(404,'任务已删除')
    if not meta.get('deleted') and not (folder/'task.json').is_file() and not (folder/'queued.json').is_file():
        raise HTTPException(404,'Task not found')
    return folder


@app.post('/tasks/{task_id}/rename')
async def rename_task(task_id:str,body:RenameTaskInput):
    folder=managed_folder(task_id)
    try:task_management.rename(folder.parent,task_id,body.title)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return web.overview(folder,jobs)


@app.post('/tasks/{task_id}/archive')
async def archive_task(task_id:str,body:ArchiveTaskInput):
    folder=managed_folder(task_id)
    try:task_management.archive(folder.parent,task_id,body.archived)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return web.overview(folder,jobs)


@app.get('/tasks/{task_id}/deletion')
async def deletion_preview(task_id:str):
    folder=managed_folder(task_id,allow_deleted=True)
    try:return task_management.preview(Settings.load(),folder,jobs)
    except (ValueError,OSError,sqlite3.Error) as exc:raise HTTPException(409,str(exc)) from exc


@app.delete('/tasks/{task_id}')
async def delete_task(task_id:str,body:DeleteTaskInput):
    folder=managed_folder(task_id,allow_deleted=True)
    try:return await task_management.delete_task(Settings.load(),folder,jobs,**body.model_dump())
    except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc

@app.post('/tasks/prepare')
def prepare_task_input(body:PrepareInput):
    try:return prepare_task(body.task,body.clarifications,body.report_format)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/tasks',status_code=202)
async def create_task(body: TaskInput, idempotency_key:str|None=Header(default=None)):
    try:brief=prepare_task(body.task,body.clarifications,body.report_format)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    if not brief['ready']:raise HTTPException(409,{'code':'clarification_required','brief':brief})
    request_hash=hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    task_id=uuid.uuid4().hex[:12]
    if idempotency_key:
        if not __import__('re').fullmatch(r'[A-Za-z0-9_-]{16,80}',idempotency_key):raise HTTPException(400,'Invalid idempotency key')
        task_id=hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
        if task_management.deleted(Settings.load().output_dir,task_id):raise HTTPException(409,'此创建请求对应的任务已删除，请创建新任务，不会重置旧预算')
        manifest=web.load(task_path(task_id),'queued.json',{})
        if manifest:
            if manifest.get('request_hash')!=request_hash:raise HTTPException(409,'Idempotency key reused with another task')
            return {'task_id':task_id,'status':web.state(task_path(task_id),jobs),'reused':True}
    if sum(not j.done() for j in jobs.values()) >= 4: raise HTTPException(429,'Queue full')
    for key in list(jobs):
        if jobs[key].done(): jobs.pop(key)
    folder=task_path(task_id);folder.mkdir(parents=True,exist_ok=True)
    from datetime import datetime,timezone
    # Durable queue record; run_task owns task.json and must create it itself.
    atomic_json(folder/'queued.json',{'task':body.task,'task_id':task_id,'created_at':datetime.now(timezone.utc).isoformat(),'request_hash':request_hash,'report_format':body.report_format,'requirements':body.requirements,'review_mode':body.review_mode})
    atomic_json(folder/'task_brief.json',brief)
    jobs[task_id]=asyncio.create_task(execute(task_id,body.task,requirements=body.requirements,review_mode=body.review_mode,report_format=body.report_format))
    return {'task_id':task_id,'status':'queued'}

@app.get('/tasks/{task_id}')
def get_task(task_id:str):
    folder=task_path(task_id)
    if task_id in jobs and jobs[task_id].cancelled():return {'task_id':task_id,'status':'interrupted'}
    if task_id in jobs and not jobs[task_id].done(): return {'task_id':task_id,'status':'running' if (folder/'.running').exists() else 'queued'}
    result=current_result(Workspace(folder.parent,task_id)) if (folder/'result.json').exists() else None
    if result: return {**web.clean(result),'status':web.state(folder,jobs)}
    if task_id in jobs and jobs[task_id].done():
        try:
            item=jobs[task_id].result()
            return web.clean(item if isinstance(item,dict) else asdict(item))
        except Exception: return {'task_id':task_id,'status':'error','error':'Job failed; inspect local logs'}
    if (folder/'task.json').exists() or (folder/'queued.json').exists():return web.overview(folder,jobs)
    raise HTTPException(404,'Task not found')


async def continue_task(task_id, decision=None,task='',requirements=None):
    folder=task_path(task_id)
    queued=web.load(folder,'queued.json',{}) if not (folder/'task.json').exists() else {}
    if not (folder/'task.json').exists() and not queued: raise HTTPException(404,'Task not found')
    if web.load(folder,'demo_meta.json',{}).get('read_only'):raise HTTPException(409,'Read-only recorded demo; cannot execute')
    if task_id in jobs and not jobs[task_id].done(): raise HTTPException(409,'Task active')
    if (folder/'.running').exists(): raise HTTPException(409,'Stale/active lock; inspect before unlocking')
    if sum(not j.done() for j in jobs.values()) >= 4: raise HTTPException(429,'Queue full')
    if queued:
        if decision: raise HTTPException(409,'该任务尚未开始，没有可审批的操作')
        if web.checkpoint_exists(task_id): raise HTTPException(409,'发现遗留检查点，不能按新任务重开')
        queued_task=task or queued.get('task','')
        if len(queued_task.strip())<5: raise HTTPException(422,'原任务描述不完整，无法重新开始')
        jobs[task_id]=asyncio.create_task(execute(task_id,queued_task,requirements=requirements or queued.get('requirements') or [],
            review_mode=queued.get('review_mode','auto'),report_format=queued.get('report_format','both')))
        return {'task_id':task_id,'status':'queued','restarted':True}
    if decision and decision.get('decision')=='approve':
        pending=read_json(folder/'result.json',{}).get('pending_review',[])
        if not any(p.get('approval_id')==decision.get('approval_id') and p.get('tool')=='submit_public_form' for p in pending):
            raise HTTPException(409,'审批内容已变化或ID不匹配')
    jobs[task_id]=asyncio.create_task(execute(task_id,task,resume=True,decision=decision,requirements=requirements))
    return {'task_id':task_id,'status':'queued'}


@app.post('/tasks/{task_id}/resume',status_code=202)
async def resume_task(task_id:str,body:ResumeInput|None=None):
    return await continue_task(task_id,task=body.task if body else '',requirements=body.requirements if body else None)


@app.post('/tasks/{task_id}/budget-resume',status_code=202)
async def add_budget_and_resume(task_id:str,body:BudgetResumeInput,request:Request):
    # Single-user local app: retain its host/origin boundary, plus explicit consent
    # and a revision binding scoped to exactly one task. No GET launches a job.
    if request.headers.get('origin') not in LOCAL_ORIGINS or request.headers.get('sec-fetch-site')=='cross-site':
        raise HTTPException(403,'请从本地工作台确认付费执行')
    if body.task_id != task_id:raise HTTPException(409,'路径与确认的任务不一致')
    if not body.confirm_paid_execution:raise HTTPException(422,'必须明确确认可能产生模型 API 费用')
    folder=managed_folder(task_id)
    cfg=Settings.load();payload=body.model_dump()
    try:
        with task_budget.mutation_lock(folder):
            previous=task_budget.previous_grant(folder,body.operation_id,payload)
            if previous:
                return {'task_id':task_id,'status':web.state(folder,jobs),'reused':True,
                        'budget':task_budget.snapshot(folder,cfg)}
            current=task_budget.view(folder,cfg,status=web.state(folder,jobs),checkpoint=web.checkpoint_exists(task_id),
                read_only=bool(web.load(folder,'demo_meta.json',{}).get('read_only')),
                locked=(folder/'.running').exists(),
                archived=bool(task_management.metadata(folder.parent,task_id).get('archived')))
            if not current['can_add']:raise task_budget.BudgetConflict(current['blocked_reason'])
            if not cfg.api_key:raise HTTPException(409,'模型密钥未配置，尚未追加预算或启动任务')
            if sum(not j.done() for j in jobs.values())>=4:raise HTTPException(429,'Queue full')
            receipt=task_budget.grant(folder,cfg,payload,current)
            Workspace(folder.parent,task_id).log('model_budget_added','用户确认追加模型请求并恢复原任务',**receipt)
            result=await continue_task(task_id)
            return {**result,'reused':False,'budget':task_budget.snapshot(folder,cfg)}
    except task_budget.BudgetConflict as exc:raise HTTPException(409,str(exc)) from exc


@app.post('/tasks/{task_id}/review',status_code=202)
async def review_task(task_id:str, body:ReviewInput): return await continue_task(task_id,body.model_dump())


@app.post('/tasks/{task_id}/unlock')
async def unlock_task(task_id: str, body: UnlockInput):
    if not body.confirm:
        raise HTTPException(422, '必须明确确认已关闭原进程')
    folder = task_path(task_id)
    lock = folder / '.running'
    if not lock.is_file():
        raise HTTPException(409, '该任务没有残留进程锁')
    if task_id in jobs and not jobs[task_id].done():
        raise HTTPException(409, '任务正在本进程运行，不能解除锁')
    try:
        data = json.loads(lock.read_text(encoding='utf-8'))
        pid = int(data.get('pid', 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pid = 0
    if _pid_alive(pid):
        raise HTTPException(409, f'进程 {pid} 仍在运行；确认关闭后再解除')
    lock.unlink(missing_ok=True)
    Workspace(folder.parent, task_id).log('lock_released', '用户确认原进程已退出，解除残留锁', pid=pid)
    return {'task_id': task_id, 'unlocked': True}


@app.post('/tasks/{task_id}/cancel')
async def cancel_task(task_id:str):
    task_path(task_id)
    job=jobs.get(task_id)
    if not job or job.done():raise HTTPException(409,'任务未在本进程执行')
    job.cancel()
    try:await job
    except asyncio.CancelledError:pass
    return {'task_id':task_id,'status':'interrupted'}


@app.get('/tasks/{task_id}/acceptance')
def acceptance_preview(task_id:str):
    folder=task_path(task_id)
    if not (folder/'task.json').exists():raise HTTPException(404,'Task not found')
    ws=Workspace(folder.parent,task_id)
    return {'binding':review_binding(ws),**evaluate_requirements(ws),**ws.verify_delivery()}


@app.post('/tasks/{task_id}/accept')
def accept_task(task_id:str,body:AcceptanceInput):
    folder=task_path(task_id)
    if not (folder/'task.json').exists():raise HTTPException(404,'Task not found')
    try:return accept_delivery(Workspace(folder.parent,task_id),body.requirement_ids,body.binding,body.note,accept_limitations=body.accept_limitations)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.post('/tasks/{task_id}/revision')
def revise_delivery(task_id:str,body:RevisionInput):
    folder=task_path(task_id)
    if not (folder/'task.json').is_file():raise HTTPException(404,'Task not found')
    try:return request_delivery_revision(Workspace(folder.parent,task_id),body.binding,body.note)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.post('/tasks/{task_id}/verify')
def verify_task(task_id:str):
    folder=task_path(task_id)
    if not (folder/'result.json').exists():raise HTTPException(404,'Task not found')
    try:return verify_existing_delivery(Workspace(folder.parent,task_id))
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.get('/memories')
def list_memories():return MemoryStore(Settings.load().output_dir/'memory.sqlite').list()


@app.post('/memories')
def save_memory(body:MemoryInput):
    try:return {'id':MemoryStore(Settings.load().output_dir/'memory.sqlite').save(body.content)}
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.delete('/memories/{memory_id}')
def forget_memory(memory_id:str):return {'deleted':MemoryStore(Settings.load().output_dir/'memory.sqlite').delete(memory_id)}


@app.get('/tasks/{task_id}/workspace')
def get_workspace(task_id:str):return web.workspace_view(task_path(task_id),jobs)

@app.get('/tasks/{task_id}/workbook')
def preview_workbook(task_id:str,filename:str="task_result.xlsx"):return web.workbook(task_path(task_id),filename)

@app.get('/tasks/{task_id}/events')
async def task_events(task_id:str,request:Request,after:int=0):
    folder=task_path(task_id)
    if not (folder/'task.json').is_file() and not (folder/'queued.json').is_file():raise HTTPException(404,'Task not found')
    try:cursor=max(0,after,int(request.headers.get('last-event-id','0')))
    except ValueError:raise HTTPException(400,'Invalid event cursor')
    async def stream():
        nonlocal cursor
        yield 'retry: 2000\n\n'
        last_state=''
        for _ in range(600):
            if await request.is_disconnected():break
            rows=await asyncio.to_thread(web.events,folder)
            for row in rows:
                if row['id']>cursor:
                    cursor=row['id']
                    yield f"id: {cursor}\nevent: trace\ndata: {json.dumps(row,ensure_ascii=False)}\n\n"
            view=await asyncio.to_thread(web.overview,folder,jobs)
            payload=json.dumps(view,ensure_ascii=False,sort_keys=True)
            if payload!=last_state:
                yield f'event: state\ndata: {payload}\n\n';last_state=payload
            if view['status'] not in {'running','queued'}:
                yield 'event: settled\ndata: {}\n\n';break
            yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@app.get('/tasks/{task_id}/report-pdf')
def pdf_metadata(task_id:str):
    import pymupdf
    path=web.safe_file(task_path(task_id),'research_report.pdf')
    if path.stat().st_size>30*1024*1024:raise HTTPException(413,'PDF too large')
    with pymupdf.open(path) as doc:
        if not 1<=len(doc)<=80:raise HTTPException(413,'PDF page limit exceeded')
        return {'pages':len(doc),'url':f'/tasks/{task_id}/files/research_report.pdf'}

@app.get('/tasks/{task_id}/report-pdf/pages/{page}')
def pdf_page(task_id:str,page:int):
    import pymupdf
    path=web.safe_file(task_path(task_id),'research_report.pdf')
    if path.stat().st_size>30*1024*1024:raise HTTPException(413,'PDF too large')
    with pymupdf.open(path) as doc:
        if not 1<=page<=min(80,len(doc)):raise HTTPException(404,'Page not found')
        item=doc[page-1]
        scale=min(1.6,1400/max(item.rect.width,item.rect.height))
        return Response(item.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).tobytes('png'),media_type='image/png',headers={'Cache-Control':'no-cache'})

@app.get('/tasks/{task_id}/files/{filename}')
def download(task_id:str,filename:str,inline:bool=False):
    if filename not in {'research_report.md','research_report.pdf','research_sources.xlsx','task_result.xlsx','business_artifact.json','trace.jsonl','sources.json','result.json','requirements.json','delivery_check.json','acceptance.json','delivery_finalization.json','business_validation.json','business_batch_validation.json'}:
        raise HTTPException(404,'File not exposed')
    folder=task_path(task_id)
    path=web.safe_file(folder,filename)
    if filename=='trace.jsonl':
        content=''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in web.events(folder))
        return Response(content,media_type='application/x-ndjson',headers={'Content-Disposition':'attachment; filename="trace.jsonl"'})
    if filename.endswith('.json'):
        return JSONResponse(web.clean(web.load(folder,filename,{})),headers={'Content-Disposition':f'attachment; filename="{filename}"'})
    if filename.endswith('.md'):
        return Response(web.clean(path.read_text(encoding='utf-8')),media_type='text/markdown',headers={'Content-Disposition':f'attachment; filename="{filename}"'})
    return FileResponse(path,filename=filename,media_type='application/pdf' if filename.endswith('.pdf') else None,content_disposition_type='inline' if inline and filename.endswith('.pdf') else 'attachment')

# Built React assets are served on the API origin; Vite proxies the same paths in development.
FRONTEND = Path(__file__).resolve().parent.parent/'frontend'/'dist'
if (FRONTEND/'assets').is_dir():app.mount('/assets',StaticFiles(directory=FRONTEND/'assets'),name='web-assets')

@app.get('/',include_in_schema=False)
def workbench():
    if not (FRONTEND/'index.html').is_file():return JSONResponse({'detail':'Build frontend first: cd frontend && npm ci && npm run build'},status_code=503)
    return FileResponse(FRONTEND/'index.html')
