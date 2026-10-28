"""Real persistence and targeted deletion; all data is disposable pytest data."""
import asyncio
import hashlib
import itertools
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from test_web_workbench import client
from test_memory_completion import prepared_workspace
from test_graph import ScriptModel,empty_browser
from web_agent import api,task_history as history
from web_agent.memory import MemoryStore
from web_agent.persistence import atomic_json,read_json
from web_agent.runner import run_task


async def seed_checkpoint(cfg,task_id):
    async with AsyncSqliteSaver.from_conn_string(str(cfg.checkpoint_path)) as saver:
        config=await saver.aput({'configurable':{'thread_id':task_id,'checkpoint_ns':''}},empty_checkpoint(),{'source':'input','step':0,'parents':{}},{})
        await saver.aput_writes(config,[('audit','persisted fixture')],'writer')


def remove(c,key,**options):
    return c.request('DELETE',f'/tasks/{key}',json={'confirmation':key,**options})


def test_rename_persists_and_never_rewrites_goal_report_or_checkpoint(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    c.portal.call(seed_checkpoint,cfg,'study')
    before={p.name:p.read_bytes() for p in ws.path.iterdir() if p.is_file()}
    checkpoints=history.checkpoint_counts(cfg,'study')
    title='自定义名称 <script>inert</script> '+('完整名称'*20)
    response=c.post('/tasks/study/rename',json={'title':title})
    assert response.status_code==200 and response.json()['title']==title
    assert c.get('/tasks').json()['tasks'][0]['title']==title
    assert c.get('/tasks/study/workspace').json()['title']==title
    assert {p.name:p.read_bytes() for p in ws.path.iterdir() if p.is_file()}==before
    assert history.checkpoint_counts(cfg,'study')==checkpoints
    assert history.metadata(cfg.output_dir,'study')['title']==title
    assert c.post('/tasks/study/rename',json={'title':'   '}).status_code==409
    assert c.post('/tasks/study/rename',json={'title':'x'*161}).status_code==422


def test_archive_restore_persistent_and_checkpoint_available(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    c.portal.call(seed_checkpoint,cfg,'study')
    before=history.checkpoint_counts(cfg,'study')
    assert c.post('/tasks/study/archive',json={'archived':True}).status_code==200
    assert c.get('/tasks').json()['tasks']==[]
    assert c.get('/tasks?archived=true').json()['tasks'][0]['task_id']=='study'
    api.jobs.clear() # emulate loss of in-memory ownership; metadata remains on disk
    assert c.get('/tasks/study/workspace').json()['archived']
    assert history.checkpoint_counts(cfg,'study')==before
    assert c.post('/tasks/study/archive',json={'archived':False}).status_code==200
    assert not c.get('/tasks?archived=true').json()['tasks']
    assert c.get('/tasks').json()['tasks'][0]['task_id']=='study'


@pytest.mark.parametrize('records,checkpoint,files',list(itertools.product([False,True],repeat=3)))
def test_all_delete_scopes_preserve_other_tasks_and_shared_resources(client,records,checkpoint,files):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir);other=prepared_workspace(cfg.output_dir,'other')
    c.portal.call(seed_checkpoint,cfg,'study');c.portal.call(seed_checkpoint,cfg,'other')
    public=cfg.output_dir.parent/'demo_bundle';public.mkdir();(public/'shared.csv').write_text('untouched')
    store=MemoryStore(cfg.output_dir/'memory.sqlite')
    store.save('Global language preference')
    store.save('Accepted study summary',kind='accepted_task',task_id='study')
    store.save('Other accepted summary',kind='accepted_task',task_id='other')
    other_bytes={p.name:p.read_bytes() for p in other.path.iterdir() if p.is_file()}
    before_other_cp=history.checkpoint_counts(cfg,'other')
    before_files={p.name:p.read_bytes() for p in ws.path.iterdir() if p.is_file()}
    preview=c.get('/tasks/study/deletion').json()
    assert preview['can_delete'] and preview['groups']['files']['files']>=2 and preview['checkpoint']['checkpoints']==1
    options={'delete_records':records,'delete_checkpoint':checkpoint,'delete_files':files}
    response=remove(c,'study',**options)
    assert response.status_code==200,response.text
    assert c.get('/tasks/study/workspace').status_code==404
    assert [row['task_id'] for row in c.get('/tasks').json()['tasks']]==['other']
    assert not c.get('/tasks?archived=true').json()['tasks']
    for name,data in before_files.items():
        cleaned=files if history.is_artifact(Path(name)) else records
        assert not (ws.path/name).exists() if cleaned else (ws.path/name).read_bytes()==data
    assert bool(history.checkpoint_counts(cfg,'study')['checkpoints']) is not checkpoint
    assert history.checkpoint_counts(cfg,'other')==before_other_cp
    assert {p.name:p.read_bytes() for p in other.path.iterdir() if p.is_file()}==other_bytes
    assert (public/'shared.csv').read_text()=='untouched'
    memories=store.list()
    assert any(m['kind']=='preference' for m in memories)
    assert any(m['task_id']=='other' for m in memories)
    assert any(m['task_id']=='study' for m in memories) is not records
    assert remove(c,'study',**options).status_code==200 # safe identical retry
    assert history.metadata(cfg.output_dir,'study')['deleted']


def test_confirmation_required_and_deleted_id_never_resets_budget(client):
    c,cfg=client
    idem='history-creation-request-0001';key=hashlib.sha256(idem.encode()).hexdigest()[:24]
    ws=prepared_workspace(cfg.output_dir,key)
    atomic_json(ws.path/'usage.json',{'requests':20})
    assert c.request('DELETE',f'/tasks/{key}',json={}).status_code==422
    assert c.request('DELETE',f'/tasks/{key}',json={'confirmation':'another-task'}).status_code==409
    assert c.get(f'/tasks/{key}/workspace').status_code==200
    assert remove(c,key).status_code==200
    assert read_json(ws.path/'usage.json')['requests']==20
    assert c.post('/tasks',json={'task':'Attempt to recreate deleted task'},headers={'Idempotency-Key':idem}).status_code==409
    with pytest.raises(ValueError,match='已删除'):
        c.portal.call(lambda:run_task('Try reuse deleted task',key,cfg,model=ScriptModel([]),browser_factory=empty_browser))
    assert c.post(f'/tasks/{key}/resume',json={}).status_code==404


def test_active_job_requires_safe_stop_before_delete(client,monkeypatch):
    c,cfg=client
    started=None
    async def slow_runner(task,key,**kwargs):
        return await run_task(task,key,cfg,model=WaitingModel(),browser_factory=empty_browser,**kwargs)
    class WaitingModel(ScriptModel):
        def __init__(self):pass
        async def ainvoke(self,messages):await asyncio.Event().wait()
    monkeypatch.setattr(api,'run_task',slow_runner)
    key=c.post('/tasks',json={'task':'Disposable offline active task'}).json()['task_id']
    assert remove(c,key).status_code==409
    preview=c.get(f'/tasks/{key}/deletion').json()
    assert preview['can_stop'] and not preview['can_delete']
    assert c.post(f'/tasks/{key}/rename',json={'title':'Renamed during execution'}).status_code==200
    assert c.post(f'/tasks/{key}/cancel').status_code==200
    assert c.get(f'/tasks/{key}/deletion').json()['can_delete']
    assert remove(c,key).status_code==200


def test_stale_lock_never_force_deleted_and_unsafe_tree_refused(client,monkeypatch):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    (ws.path/'.running').write_text('Other process owns this task')
    assert remove(c,'study',delete_records=True,delete_files=True,delete_checkpoint=True).status_code==409
    assert (ws.path/'task.json').exists()
    (ws.path/'.running').unlink()
    original=history._reparse
    monkeypatch.setattr(history,'_reparse',lambda p:p.name=='research_report.md' or original(p))
    assert remove(c,'study',delete_records=True,delete_files=True).status_code==409
    assert (ws.path/'task.json').exists() and not history.deleted(cfg.output_dir,'study')


def test_partial_failure_is_hidden_retryable_and_cannot_expand_cleanup(client,monkeypatch):
    c,cfg=client;prepared_workspace(cfg.output_dir)
    original=Path.unlink
    failed=False
    def fail_once(path,*args,**kwargs):
        nonlocal failed
        if path.name=='research_report.md' and not failed:
            failed=True;raise PermissionError('Injected locked file')
        return original(path,*args,**kwargs)
    with patch.object(Path,'unlink',fail_once):
        assert remove(c,'study',delete_files=True).status_code==409
    assert not c.get('/tasks').json()['tasks']
    assert history.metadata(cfg.output_dir,'study')['cleanup_pending']
    assert remove(c,'study',delete_records=True,delete_files=True).status_code==409
    assert remove(c,'study',delete_files=True).status_code==200
    assert not history.metadata(cfg.output_dir,'study')['cleanup_pending']


def test_management_respects_existing_origin_and_path_boundaries(client):
    c,cfg=client;prepared_workspace(cfg.output_dir)
    assert c.post('/tasks/study/rename',json={'title':'Unwanted'},headers={'Origin':'https://evil.example'}).status_code==403
    assert remove(c,'missing',delete_records=True).status_code==404
    assert c.request('DELETE','/tasks/%2E%2E',json={'confirmation':'..','delete_records':True}).status_code in {400,404}
    assert c.get('/tasks/study/workspace').status_code==200


def test_demo_restore_never_resurrects_deleted_task(client,tmp_path):
    from scripts.restore_demo_tasks import restore
    c,cfg=client
    bundle=tmp_path/'bundle'
    prepared_workspace(bundle/'demo_bundle','live_A')
    history.update_metadata(cfg.output_dir,'live_A',deleted=True,cleanup_pending=False)
    outcome=c.portal.call(lambda:restore(cfg,bundle))
    assert outcome['restored']==[] and outcome['checkpoint_threads_imported']==[]
    assert not (cfg.output_dir/'live_A').exists()
    assert history.deleted(cfg.output_dir,'live_A')
