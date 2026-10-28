"""Offline web/API integration: real runner and checkpoints, scripted model, zero network/paid API."""
import asyncio
import json
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from web_agent import api, web_views as web
from web_agent.persistence import atomic_json
from web_agent.runner import run_task
from test_memory_completion import prepared_workspace
from test_graph import ScriptModel,call,empty_browser

@pytest.fixture
def client(tmp_path,monkeypatch):
    cfg=replace(api.Settings.load(),output_dir=tmp_path/'out',checkpoint_path=tmp_path/'check.sqlite',api_key='',max_model_calls=8,task_timeout=10)
    cfg.output_dir.mkdir()
    monkeypatch.setattr(api.Settings,'load',lambda:cfg)
    monkeypatch.setattr(api,'jobs',{})
    monkeypatch.setattr(api,'semaphore',asyncio.Semaphore(1))
    with TestClient(api.app) as c:yield c,cfg

def test_existing_real_artifacts_history_and_workspace(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    ws.log('tool_start','browser_snapshot',call_id='c1',arguments={'Cookie':'session=fake-secret','url':'https://example.com/docs'})
    ws.log('tool_end','browser_snapshot',call_id='c1',output_preview='Actual observed text',elapsed_seconds=.2)
    atomic_json(ws.path/'usage.json',{'requests':2,'input_tokens':77,'output_tokens':9})
    data=c.get('/tasks/study/workspace').json()
    assert c.get('/tasks').json()['tasks'][0]['task_id']=='study'
    assert data['status']=='needs_review' and data['capabilities']['can_accept']
    assert not data['capabilities']['can_resume'] # no actual checkpoint fabricated
    assert data['metrics']['requests']==2 and data['metrics']['input_tokens']==77
    assert data['report'].startswith('# Finding') and len(data['sources'])==1
    assert 'fake-secret' not in json.dumps(data)
    assert data['events'][-1]['output_preview']=='Actual observed text'
    assert c.get('/tasks/study/files/research_sources.xlsx').content[:2]==b'PK'
    assert c.get('/tasks/study/files/trace.jsonl').status_code==200
    assert 'fake-secret' not in c.get('/tasks/study/files/trace.jsonl').text

def test_read_only_demonstration_cannot_execute(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    atomic_json(ws.path/'demo_meta.json',{'read_only':True,'actual_api_requests':0})
    view=c.get('/tasks/study/workspace').json()
    assert not view['capabilities']['can_accept'] and view['metrics']['requests']==0
    assert c.post('/tasks/study/resume',json={}).status_code==409
    assert c.post('/tasks/study/review',json={'decision':'handled'}).status_code==409
    assert not api.jobs

def test_sse_replays_actual_events_once_and_supports_cursor(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    ws.log('tool_start','export_report',call_id='x')
    ws.log('tool_end','export_report',call_id='x',output_preview='written')
    data=c.get('/tasks/study/events').text
    assert 'event: trace' in data and 'event: settled' in data
    ids=[int(line[4:]) for line in data.splitlines() if line.startswith('id: ')]
    assert ids==sorted(set(ids))
    remaining=c.get('/tasks/study/events',headers={'Last-Event-ID':str(ids[-1])}).text
    assert 'event: trace' not in remaining and 'event: state' in remaining
    assert c.get('/tasks/study/events',headers={'Last-Event-ID':'bad'}).status_code==400

def test_idempotent_creation_reaches_actual_runner_and_checkpoint(client,monkeypatch):
    c,cfg=client
    async def local_runner(task,task_id,**kwargs):
        model=ScriptModel([call('update_plan',{'steps':['inspect actual task state']}),AIMessage(content='No external data requested.'),AIMessage(content='Incomplete.')])
        return await run_task(task,task_id,cfg,model=model,browser_factory=empty_browser,**kwargs)
    monkeypatch.setattr(api,'run_task',local_runner)
    headers={'Idempotency-Key':'offline-web-task-0001'}
    a=c.post('/tasks',json={'task':'Offline integration task'},headers=headers)
    b=c.post('/tasks',json={'task':'Offline integration task'},headers=headers)
    assert a.status_code==b.status_code==202 and a.json()['task_id']==b.json()['task_id']
    key=a.json()['task_id']
    # TestClient portal waits for the actual task; no arbitrary sleep or paid request.
    async def finish():return await api.jobs[key]
    result=c.portal.call(finish)
    assert result.task_id==key and result.status=='incomplete'
    view=c.get(f'/tasks/{key}/workspace').json()
    assert view['capabilities']['has_checkpoint'] and view['capabilities']['can_resume']
    assert view['plan']==['inspect actual task state'] and view['metrics']['requests']==3
    assert c.post('/tasks',json={'task':'Changed integration task'},headers=headers).status_code==409
    # Emulate API ownership loss/restart, reload disk records; do not reset request count.
    api.jobs.clear()
    assert c.get('/tasks').json()['tasks'][0]['task_id']==key
    assert c.get(f'/tasks/{key}/workspace').json()['metrics']['requests']==3

def test_actual_interrupt_review_and_resume_via_existing_api(client,monkeypatch):
    c,cfg=client
    models=iter([ScriptModel([call('request_human_review',{'action':'Manual verification','reason':'User must inspect target'})]),ScriptModel([AIMessage(content='User rejected.'),AIMessage(content='Nothing submitted.')])])
    async def local_runner(task,task_id,**kwargs):return await run_task(task,task_id,cfg,model=next(models),browser_factory=empty_browser,**kwargs)
    monkeypatch.setattr(api,'run_task',local_runner)
    key=c.post('/tasks',json={'task':'Offline human interrupt'}).json()['task_id']
    async def finish():return await api.jobs[key]
    c.portal.call(finish)
    view=c.get(f'/tasks/{key}/workspace').json()
    assert view['status']=='awaiting_review' and view['capabilities']['can_review']
    assert view['pending_review'] and not view['capabilities']['can_resume']
    assert c.post(f'/tasks/{key}/review',json={'decision':'reject'}).status_code==202
    c.portal.call(finish)
    view=c.get(f'/tasks/{key}/workspace').json()
    assert view['status']=='incomplete' and not view['pending_review']
    assert any(e['event']=='human_decision' for e in view['events'])
    assert view['metrics']['requests']==3

def test_workbook_preview_reads_actual_cells_and_limits(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    from openpyxl import Workbook
    book=Workbook();sheet=book.active;sheet.title='Actual data';sheet.append(['value','label'])
    for i in range(80):sheet.append([-i,'<script>alert(1)</script>'])
    book.save(ws.path/'task_result.xlsx');book.close()
    result=c.get('/tasks/study/workbook').json()['sheets'][0]
    assert result['rows'][1][0]==-1 and result['row_count']==80
    assert result['truncated'] and len(result['rows'])==50
    assert '<script>' in result['rows'][0][1] # React renders text; not injected HTML

def test_unsafe_paths_and_cross_origin_mutations_rejected(client):
    c,cfg=client;prepared_workspace(cfg.output_dir)
    assert c.get('/tasks/study/files/.env').status_code==404
    assert c.get('/tasks/study/files/checkpoints.sqlite').status_code==404
    assert c.get('/tasks/not-valid!').status_code==400
    assert c.post('/tasks',json={'task':'Must not execute'},headers={'Origin':'https://evil.example'}).status_code==403
    assert not api.jobs
    assert c.get('/health',headers={'Host':'evil.example'}).status_code==403
    allowed=c.options('/tasks',headers={'Origin':'http://127.0.0.1:5173','Access-Control-Request-Method':'POST'})
    assert allowed.headers['access-control-allow-origin']=='http://127.0.0.1:5173'
    denied=c.options('/tasks',headers={'Origin':'https://evil.example','Access-Control-Request-Method':'POST'})
    assert 'access-control-allow-origin' not in denied.headers

def test_sanitizer_preserves_public_urls_token_metrics_and_negatives():
    data=web.clean({'url':'https://user:secret@example.com/docs?q=csv&access_token=secret','Cookie':'abc','access_token':'private','nested':{'api_key':'key'},'input_tokens':100,'number':-.5,'path':r'D:\private\secret.txt','result':'Cookie: session=private'})
    assert data['input_tokens']==100 and data['number']==-.5
    assert 'example.com/docs' in data['url'] and 'secret' not in data['url']
    assert data['Cookie']==data['access_token']=='[REDACTED]'
    assert data['path']=='[LOCAL_PATH]' and 'session=private' not in data['result']
    assert web.clean('https://www.python.org/')=='https://www.python.org/'

def test_active_lock_blocks_resume_and_exposes_interrupted_state(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    atomic_json(ws.path/'.running',{'pid':999999})
    view=c.get('/tasks/study/workspace').json()
    assert view['status']=='interrupted' and view['capabilities']['locked']
    assert not view['capabilities']['can_resume']
    assert c.post('/tasks/study/resume',json={}).status_code==409


def test_startup_failure_is_durable_visible_and_costs_no_requests(client):
    c,cfg=client
    key=c.post('/tasks',json={'task':'Must fail without a server model key'}).json()['task_id']
    async def finish():return await api.jobs[key]
    c.portal.call(finish)
    api.jobs.clear() # after restart a failed queued job must remain visible
    view=c.get(f'/tasks/{key}/workspace').json()
    assert view['status']=='error' and 'DEEPSEEK_API_KEY' in view['error']
    assert view['metrics'].get('requests',0)==0 and not view['capabilities']['can_resume']
    assert any(e['event']=='task_error' for e in view['events'])
    assert c.get(f'/tasks/{key}').json()['status']=='error'
    assert c.get('/tasks').json()['tasks'][0]['status']=='error'
