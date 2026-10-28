"""Bounded, credential-free view models for the independent local web UI."""
from __future__ import annotations
import json
import hashlib
import re
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from fastapi import HTTPException
from .persistence import read_json, redact
from .config import Settings
from .completion import current_result, evaluate_requirements, review_binding
from .workspace import Workspace
from .business_excel import verify_business_excel
from .status import STATUS
from .harness import error_policy
from .task_history import metadata
from . import task_budget
from .task_intake import context_brief

SENSITIVE = re.compile(r"(?i)^(?:.*[_-])?(?:api[_-]?key|authorization|proxy-authorization|cookie|set-cookie|password|secret|access[_-]?token|refresh[_-]?token|token|credential)s?$")
FILES = ('research_report.md','research_report.pdf','research_sources.xlsx','task_result.xlsx','sources.json')

def clean(value):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if SENSITIVE.search(str(k)) and not (str(k) in {'input_tokens','output_tokens','total_tokens','input_cache_hit_tokens','input_cache_miss_tokens'} and isinstance(v,(int,float))) else clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):return [clean(v) for v in value]
    if not isinstance(value,str):return value
    text=redact(value)
    text=re.sub(r"(?i)((?:set-cookie|cookie)\s*[:=]\s*)[^\r\n]+",r"\1[REDACTED]",text)
    text=re.sub(r"(?i)(?:(?<![A-Za-z0-9])[A-Z]:[\\/]|/home/|/Users/)[^\s\"<>]+",'[LOCAL_PATH]',text)
    # Signed query values, credentials and fragments never belong in browser links/logs.
    def safe_url(m):
        try:
            u=urlsplit(m.group(0)); host=u.hostname or ''
            if u.port:host+=':'+str(u.port)
            query=urlencode([(k,'[REDACTED]' if SENSITIVE.search(k) or re.search(r'(?i)signature|x-amz-|sas',k) else v) for k,v in parse_qsl(u.query,keep_blank_values=True)])
            return urlunsplit((u.scheme,host,u.path,query,''))
        except ValueError:return '[INVALID_URL]'
    return re.sub(r'https?://[^\s<>\"]+',safe_url,text)

def safe_folder(task_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,56}',task_id):raise HTTPException(400,'Invalid task id')
    root=Settings.load().output_dir.resolve();folder=root/task_id
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):raise HTTPException(404,'Unsafe task path')
    return folder

def safe_file(folder, name):
    path=folder/name
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():raise HTTPException(404,'File not available')
    return path

def load(folder,name,default=None):
    p=folder/name
    if p.is_symlink():return default
    try:return read_json(p,default)
    except (ValueError,OSError):return default

def events(folder):
    curated=load(folder,'public_trace.json')
    if isinstance(curated,list):rows=curated
    else:
        p=folder/'trace.jsonl';rows=[]
        if p.is_file() and not p.is_symlink():
            # Bound bytes read, preserve monotonic file line offsets for event IDs.
            with p.open('rb') as f:
                for i,line in enumerate(f):
                    if len(line)>262144:continue
                    try:
                        row=json.loads(line)
                        if isinstance(row,dict) and 'event' in row:rows.append((i+1,row))
                    except (ValueError,UnicodeDecodeError):pass
            return [{'id':i,**clean(row)} for i,row in rows[-1200:]]
    return [{'id':i+1,**clean(row)} for i,row in enumerate(rows[-1200:],max(0,len(rows)-1200)) if isinstance(row,dict)]

def checkpoint_exists(task_id):
    p=Settings.load().checkpoint_path
    if not p.is_file():return False
    try:
        with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            return bool(db.execute('SELECT 1 FROM checkpoints WHERE thread_id=? LIMIT 1',(task_id,)).fetchone())
    except sqlite3.Error:return False

def state(folder, jobs):
    task_id=folder.name;job=jobs.get(task_id);saved=load(folder,'result.json',{})
    if job and not job.done():return 'running' if (folder/'.running').exists() else 'queued'
    if job and job.cancelled():return 'interrupted'
    if (folder/'.running').exists():return 'interrupted' # another process/stale lock; resume remains blocked
    try:
        intent=task_budget.ledger(folder).get('last_resume',{})
        if intent and intent.get('result_digest')==task_budget.result_digest(folder):return 'interrupted'
    except task_budget.BudgetConflict:pass
    if saved.get('status')=='completed':saved=current_result(Workspace(folder.parent,task_id))
    if saved:return saved.get('status','incomplete')
    return 'interrupted' # durable manifest but no active owner after restart

def overview(folder,jobs):
    manifest=load(folder,'task.json',{}) or load(folder,'queued.json',{});saved=load(folder,'result.json',{});meta=load(folder,'demo_meta.json',{})
    usage={**saved.get('metrics',{}),**load(folder,'usage.json',{})}
    usage['limits']={**usage.get('limits',{}),**load(folder,'run_limits.json',{})}
    try:
        grant=task_budget.ledger(folder)
        if grant:usage['limits']['model_requests']=grant['limit']
    except task_budget.BudgetConflict:pass
    if 'actual_api_requests' in meta:usage['requests']=meta['actual_api_requests']
    trace=events(folder)
    usage['tool_calls']=len(load(folder,'tool_journal.json',{})) or sum(e['event']=='tool_start' for e in trace)
    usage['errors']=sum(e['event']=='tool_error' for e in trace)
    usage['retries']=sum(e['event']=='tool_retry' for e in trace)
    spent=load(folder,'tool_usage.json',{})
    if 'attempts' in spent:usage['tool_attempts']=spent['attempts']
    title=manifest.get('task','').split('\n')[0][:100]
    try:
        with safe_file(folder,'research_report.md').open(encoding='utf-8') as report_file:
            heading=report_file.readline(512).strip()
        if heading.startswith('# ') and not heading.startswith('# 部分执行结果'):title=heading[2:].strip()[:100]
    except (HTTPException,OSError):pass
    elapsed=saved.get('elapsed_seconds')
    status=state(folder,jobs)
    management=metadata(folder.parent,folder.name)
    delivery=saved.get('completion',{}).get('delivery') if status not in {'running','queued','awaiting_review'} else None
    # Older saved summaries equated usable files with completion. Correct their
    # presentation without rewriting historical results or accepting any facts.
    if delivery and status!='completed' and delivery.get('outcome') in {'completed','completed_with_warnings'}:
        outcome='needs_review' if status=='needs_review' and not saved.get('completion',{}).get('missing_requirements') else 'partial'
        delivery={**delivery,'outcome':outcome,'label':STATUS[outcome][0],'semantic_accepted':False}
    if management.get('title'):title=management['title']
    return clean({'task_id':folder.name,'task':manifest.get('task',''),'title':title,'created_at':manifest.get('created_at',''),
        'status':status,'status_label':delivery['label'] if delivery else STATUS.get(status,(status,''))[0],
        'delivery_outcome':delivery.get('outcome') if delivery else None,'metrics':usage,'archived':bool(management.get('archived')),
        'elapsed_seconds':elapsed,'read_only':bool(meta.get('read_only')),'execution_label':meta.get('execution_label','')})

def workspace_view(folder,jobs):
    if not (folder/'task.json').is_file() and not (folder/'queued.json').is_file():raise HTTPException(404,'Task not found')
    view=overview(folder,jobs);saved=load(folder,'result.json',{});ws=Workspace(folder.parent,folder.name)
    queued_only=not (folder/'task.json').is_file() and (folder/'queued.json').is_file()
    checks=evaluate_requirements(ws);artifact=ws.verify_delivery()
    from .delivery_status import aggregate_delivery
    delivery=aggregate_delivery(ws,view['status'],artifact,checks)
    trace=events(folder);pending=saved.get('pending_review',[]) if view['status']=='awaiting_review' else []
    active=view['status'] in {'running','queued'}
    checkpoint=checkpoint_exists(folder.name)
    lock=(folder/'.running').exists()
    report=''
    try:
        p=safe_file(folder,'research_report.md')
        if p.stat().st_size<=2*1024*1024:report=p.read_text(encoding='utf-8')
    except HTTPException:pass
    receipt=load(folder,'acceptance.json',{})
    mode=receipt.get('verification_mode','') if view['status']=='completed' else ''
    review_reason=saved.get('completion',{}).get('review_reason','')
    if view['status']=='needs_review' and not review_reason:
        review_reason='来源与文件检查不替代对事实、解释和任务含义的判断，请核对后确认或提出修改。'
    failure=None
    if view['status'] in {'error','timeout','permission_denied','stalled','budget_exceeded','incomplete','partial','interrupted'}:
        journal=load(folder,'tool_journal.json',{})
        failed=next((row for row in reversed(list(journal.values())) if row.get('error_code')), {})
        code=saved.get('failure_code') or {'timeout':'timeout','permission_denied':'permission_denied','budget_exceeded':'budget_exceeded','stalled':'loop_detected','interrupted':'interrupted'}.get(view['status'],failed.get('error_code',''))
        if view['status']=='interrupted' and failed.get('error_code')=='site_challenge':code='site_challenge'
        label,action,retryable=error_policy(code) if code else ('任务未完整交付','查看未满足需求；修正目标或配置后显式继续',False)
        failure={'code':code,'label':label,'next_action':action,'retryable':retryable,
                 'detail':clean(saved.get('error') or saved.get('answer') or failed.get('output',''))[:1400]}
        if view['status']=='stalled' and failed.get('name')=='record_source' and failed.get('error_code')=='evidence_mismatch':
            failure.update(code='evidence_mismatch',root_code='evidence_mismatch',label='来源网址与摘录未匹配',
                           next_action='核对实际最终网址与连续原文；修复后可从原进度继续，已有资料和累计请求保留。')
        if view['status']=='stalled' and failed.get('name') in {'export_business_excel','repair_business_excel'} and failed.get('error_code') in {'business_validation','evidence_mismatch'}:
            try:
                error_data=json.loads(failed.get('output','')[7:])
                diagnostics=error_data.get('diagnostics',{})
                issues=diagnostics.get('issues',[])
                locations='；'.join(f"{r['sheet']}第{r['row']}行「{r['column']}」：{r['message']}" for r in issues[:3])
                label,action,_=error_policy('business_validation')
                if not issues:action='旧版日志未保存单元格定位。使用对应原文值，无法核实的值标为缺失；修复后可显式继续，仍使用原剩余额度并可能产生模型费用。'
                failure.update(label=label,next_action=action,root_code='business_validation',
                               detail=clean(locations or error_data.get('message') or saved.get('answer',''))[:1400])
            except (ValueError,KeyError,TypeError):pass
            repair=load(folder,'business_repair_receipt.json',{})
            business=load(folder,'business_artifact.json',{})
            if (repair.get('task_id')==folder.name and repair.get('business_sha256')==business.get('sha256')
                    and repair.get('requests')==load(folder,'usage.json',{}).get('requests')
                    and repair.get('resolved_failure_sha256')==hashlib.sha256(failed.get('output','').encode()).hexdigest()
                    and (folder/'task_result.xlsx').is_file() and not verify_business_excel(ws)):
                failure.update(label='业务表格已修正，任务仍暂停',
                               next_action='可以先预览或下载业务数据表；其他未核实事项仍需核对。继续执行需要确认 API 费用。',
                               detail=f"已通过离线校验并修正 {repair.get('patched_cells',0)} 个单元格，未新增模型请求。历史失败记录保留，未将任务标记为完成。")
        if view['status']=='interrupted' and queued_only:
            failure.update(code='queued_restart',label='任务在服务重启前尚未开始',
                           next_action='没有可恢复的进度；重新开始会按原目标执行，可能产生模型费用。',
                           detail='服务重启时任务仍在队列中，尚未开始执行。',retryable=False)
        if view['status']=='partial' and artifact['artifacts_valid'] and not (folder/'business_draft.json').exists():
            failure.update(code='requirements_incomplete',label='已有阶段性交付，仍有需求未完成',
                           next_action='查看未满足的需求与未核实数据；文件已生成不代表研究任务全部完成。',
                           detail=clean(saved.get('answer') or '已有文件通过结构检查，需求与事实核验尚未全部完成。'),retryable=False)
        revision=load(folder,'task.json',{}).get('revision_note')
        if revision and view['status']=='partial':
            failure.update(code='user_revision',label='用户要求修改交付',next_action=clean(revision),retryable=False)
    view.update({'delivery':clean(delivery),'error':clean(saved.get('error','')),'answer':clean(saved.get('answer','')),'report':clean(report),'report_format':load(folder,'task.json',{}).get('report_format','markdown'),'plan':clean(load(folder,'plan.json',[])),
        'sources':clean(load(folder,'sources.json',[])),'events':trace,'pending_review':clean(pending),
        'requirements':checks,'artifacts':artifact,'review_binding':review_binding(ws),
        'task_brief':context_brief(load(folder,'task_brief.json',{}),view['task']),
        'completion_mode':mode,'review_reason':review_reason,'failure':failure,
        'execution_finished':view['status'] not in {'running','queued','idle','awaiting_review'},
        'confirmation_state':('automatic' if mode=='automatic_objective_checks' else 'human' if mode=='human_acceptance' else 'pending' if view['status']=='needs_review' else 'not_recorded'),
        'files':[{'name':name,'bytes':(folder/name).stat().st_size,'url':f'/tasks/{folder.name}/files/{name}'} for name in FILES if (name!='research_report.md' or load(folder,'task.json',{}).get('report_format')!='pdf') and (folder/name).is_file() and not (folder/name).is_symlink()],
        'capabilities':{'can_cancel':bool(folder.name in jobs and not jobs[folder.name].done()),
            'can_resume':not active and not lock and not view['read_only'] and checkpoint and view['status'] not in {'completed','awaiting_review','needs_review'},
            'can_restart':queued_only and not active and not lock and not view['read_only'] and not checkpoint and view['status']=='interrupted',
            'can_review':not active and not lock and not view['read_only'] and bool(pending) and checkpoint,
            'can_accept':view['status']=='needs_review' and not view['read_only'] and artifact['artifacts_valid'] and checks['requirements_supported'],
            'can_request_revision':view['status']=='needs_review' and not view['read_only'] and not active and not lock,
            'has_checkpoint':checkpoint,'locked':lock}})
    try:
        view['model_budget']=task_budget.view(folder,Settings.load(),status=view['status'],checkpoint=checkpoint,
            read_only=view['read_only'],locked=lock,archived=view.get('archived',False))
    except (task_budget.BudgetConflict,ValueError,OSError) as exc:
        view['model_budget']={'can_add':False,'blocked_reason':str(exc)}
    return clean(view)

def workbook(folder, filename="task_result.xlsx"):
    if filename not in {"task_result.xlsx", "research_sources.xlsx"}:
        raise HTTPException(400, "Workbook filename is not exposed")
    from openpyxl import load_workbook
    from datetime import datetime, date, time
    p=safe_file(folder,filename)
    if p.stat().st_size>16*1024*1024:raise HTTPException(413,'Workbook too large for preview')
    try:
        with zipfile.ZipFile(p) as z:
            if sum(i.file_size for i in z.infolist())>64*1024*1024:raise HTTPException(413,'Workbook expands beyond preview limit')
        book=load_workbook(p,read_only=True,data_only=True);sheets=[]
        try:
            for sheet in list(book.worksheets)[:8]:
                rows=list(sheet.iter_rows(max_row=min(sheet.max_row or 1,51),max_col=min(sheet.max_column or 1,40),values_only=True))
                def cell(v):
                    if isinstance(v,datetime):return v.date().isoformat() if v.time()==time() else v.isoformat(sep=" ")
                    if isinstance(v,date):return v.isoformat()
                    if v is None or isinstance(v,(str,int,float,bool)):return clean(v)
                    return str(v)
                sheets.append({'name':sheet.title,'row_count':max(0,(sheet.max_row or 1)-1),'column_count':sheet.max_column or 0,
                    'columns':[str(cell(v) or '') for v in rows[0]] if rows else [],
                    'rows':[[cell(v) for v in row] for row in rows[1:]],'truncated':(sheet.max_row or 1)>51 or (sheet.max_column or 1)>40})
        finally:book.close()
        return {'sheets':sheets,'max_rows':50,'max_columns':40}
    except HTTPException:raise
    except Exception as exc:raise HTTPException(422,'Workbook cannot be safely previewed') from exc
