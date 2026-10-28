"""Zero paid/network calls: real SQLite checkpoints and runner, scripted decisions."""
import asyncio
from dataclasses import replace
import hashlib
import json
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient
from web_agent import api, task_budget
from web_agent.persistence import atomic_json, read_json
from web_agent.runner import run_task
from web_agent.workspace import Workspace
from test_graph import ScriptModel, call, empty_browser

ORIGIN={'Origin':'http://127.0.0.1:5173'}

@pytest.fixture
def exhausted(tmp_path,monkeypatch):
    cfg=replace(api.Settings.load(),api_key='offline-test-only',output_dir=tmp_path/'out',checkpoint_path=tmp_path/'checkpoint.sqlite',
                max_model_calls=30,max_tool_calls=90,task_timeout=420,max_graph_steps=85)
    ws=Workspace(cfg.output_dir,'budget-case')
    ws.observe('https://example.com/specs','Screen: 6.3 inches')
    ws.record('Offline fixture','https://example.com/specs','Screen observed','Screen: 6.3 inches',require_observation=True)
    (ws.path/'public_fixture.csv').write_text('value\n6.3\n',encoding='utf-8')
    initial=ScriptModel([call('update_plan',{'steps':[f'step {i}']},f'initial-{i}') for i in range(30)])
    # Simulate a historical task created before forced delivery existed. Budget
    # grants must still support its real checkpoint and all 30 durable debits.
    from web_agent.execution_budget import handoff
    with monkeypatch.context() as legacy:
        legacy.setattr('web_agent.graph.handoff',lambda *args:{**handoff(*args),'phase':'research','closing':False,'local_only':False})
        result=asyncio.run(run_task('Offline existing source comparison','budget-case',cfg,model=initial,browser_factory=empty_browser))
    assert result.status=='budget_exceeded' and result.metrics['requests']==30
    monkeypatch.setattr(api.Settings,'load',lambda:cfg)
    monkeypatch.setattr(api,'jobs',{})
    monkeypatch.setattr(api,'semaphore',asyncio.Semaphore(1))
    with TestClient(api.app) as client:
        # API execution is forbidden by default in these tests.
        async def forbidden(*args,**kwargs):raise AssertionError('No model execution is authorized in this test')
        monkeypatch.setattr(api,'run_task',forbidden)
        yield client,cfg,ws


def proposal(client,task_id='budget-case',addition=20,operation='offline-operation-00001'):
    view=client.get(f'/tasks/{task_id}/workspace').json()
    return {'task_id':task_id,'additional_requests':addition,'binding':view['model_budget']['binding'],
            'operation_id':operation,'confirm_paid_execution':True}


def test_30_to_50_persist_restart_resume_and_replay_safe(exhausted,monkeypatch):
    c,cfg,ws=exhausted
    gate=asyncio.Event();entries=[]
    model=ScriptModel([
        # An already successful call ID must reuse its journal entry.
        call('update_plan',{'steps':['step 0']},'initial-0'),
        call('export_report',{'markdown_report':'# Offline recovered comparison\nScreen: 6.3 inches.'},'new-report'),
    ])
    async def local_runner(task,task_id,**kwargs):
        entries.append(task_id)
        await gate.wait()
        return await run_task(task,task_id,api.Settings.load(),model=model,browser_factory=empty_browser,**kwargs)
    monkeypatch.setattr(api,'run_task',local_runner)
    untouched=['task.json','usage.json','tool_usage.json','tool_journal.json','time_budget.json','sources.json','public_fixture.csv']
    before={name:(ws.path/name).read_bytes() for name in untouched}
    body=proposal(c)
    first=c.post('/tasks/budget-case/budget-resume',json=body,headers=ORIGIN)
    assert first.status_code==202,first.text
    assert first.json()['budget']['used']==30 and first.json()['budget']['limit']==50
    assert first.json()['budget']['remaining']==20
    assert {name:(ws.path/name).read_bytes() for name in untouched}==before
    duplicate=c.post('/tasks/budget-case/budget-resume',json=body,headers=ORIGIN)
    assert duplicate.status_code==202 and duplicate.json()['reused']
    assert len(read_json(ws.path/'model_budget.json')['grants'])==1
    assert read_json(ws.path/'model_budget.json')['grants'][0]['paid_execution_confirmed']
    altered={**body,'additional_requests':10}
    assert c.post('/tasks/budget-case/budget-resume',json=altered,headers=ORIGIN).status_code==409
    # A fresh Python process reads the granted limit without launching an Agent.
    code="""import json,sys
from pathlib import Path
from dataclasses import replace
from web_agent import api
cfg=replace(api.Settings.load(),output_dir=Path(sys.argv[1]),checkpoint_path=Path(sys.argv[2]),max_model_calls=3)
api.Settings.load=lambda:cfg
v=api.get_workspace('budget-case')
print(json.dumps({'limit':v['model_budget']['limit'],'used':v['model_budget']['used'],'status':v['status']}))
"""
    fresh=json.loads(subprocess.check_output([sys.executable,'-c',code,str(cfg.output_dir),str(cfg.checkpoint_path)],text=True,timeout=15))
    assert fresh=={'limit':50,'used':30,'status':'interrupted'}
    current=c.get('/tasks/budget-case/workspace').json()
    assert current['metrics']['limits']['model_requests']==50 and current['status']=='queued'
    # Simulate service ownership loss before runner starts: no automatic paid recovery.
    c.post('/tasks/budget-case/cancel')
    api.jobs.clear()
    restarted=c.get('/tasks/budget-case/workspace').json()
    assert restarted['status']=='interrupted' and restarted['model_budget']['remaining']==20
    assert c.post('/tasks/budget-case/budget-resume',json=body,headers=ORIGIN).json()['reused']
    assert len(entries)==1 and model.calls==0
    # Even changed global limits must not overwrite this task's frozen budgets.
    monkeypatch.setattr(api.Settings,'load',lambda:replace(cfg,max_model_calls=8,max_tool_calls=140,task_timeout=900,max_graph_steps=99))
    c.portal.call(gate.set)
    assert c.post('/tasks/budget-case/resume',json={}).status_code==202
    async def finish():return await api.jobs['budget-case']
    resumed=c.portal.call(finish)
    assert resumed.metrics['requests']==32 and resumed.metrics['limits']['model_requests']==50
    assert resumed.metrics['limits']['tool_calls']==90 and resumed.metrics['limits']['timeout_seconds']==420
    assert resumed.metrics['limits']['graph_steps']==85
    assert read_json(ws.path/'tool_usage.json')['attempts']==31 # 30 original + one new export; replay costs zero
    assert len(read_json(ws.path/'tool_journal.json'))==31
    assert (ws.path/'sources.json').read_bytes()==before['sources.json']
    assert (ws.path/'public_fixture.csv').read_bytes()==before['public_fixture.csv']
    assert c.get('/tasks/budget-case/workspace').json()['model_budget']['limit']==50
    assert (ws.path/'trace.jsonl').read_text(encoding='utf-8').count('"event": "model_budget_added"')==1


@pytest.mark.parametrize('value',[0,-1,51,1.5,'20',True])
def test_input_range_and_integer_types(exhausted,value):
    c,cfg,ws=exhausted
    assert c.post('/tasks/budget-case/budget-resume',json=proposal(c,addition=value),headers=ORIGIN).status_code==422
    assert not (ws.path/'model_budget.json').exists() and not api.jobs


@pytest.mark.parametrize('case',['no-consent','wrong-task','wrong-binding','foreign-origin','missing-origin','extra-policy','read-only','archived','lock','no-checkpoint','tool-exhausted','time-exhausted'])
def test_security_boundaries_do_not_change_budget_or_launch(exhausted,case):
    c,cfg,ws=exhausted;body=proposal(c);headers=ORIGIN.copy()
    if case=='no-consent':body['confirm_paid_execution']=False
    if case=='wrong-task':body['task_id']='other-task'
    if case=='wrong-binding':body['binding']='0'*64
    if case=='foreign-origin':headers['Origin']='https://untrusted.example'
    if case=='missing-origin':headers={}
    if case=='extra-policy':body['tool_calls']=999
    if case=='read-only':atomic_json(ws.path/'demo_meta.json',{'read_only':True})
    if case=='archived':api.task_management.archive(cfg.output_dir,'budget-case',True)
    if case=='lock':(ws.path/'.running').write_text('offline fixture')
    if case=='no-checkpoint':cfg.checkpoint_path.unlink()
    if case=='tool-exhausted':atomic_json(ws.path/'tool_usage.json',{'attempts':90})
    if case=='time-exhausted':atomic_json(ws.path/'time_budget.json',{'spent':420})
    response=c.post('/tasks/budget-case/budget-resume',json=body,headers=headers)
    assert response.status_code in (403,409,422),response.text
    assert not (ws.path/'model_budget.json').exists() and not api.jobs
    assert read_json(ws.path/'usage.json')['requests']==30


def test_opening_views_and_selections_never_launch(exhausted):
    c,cfg,ws=exhausted
    for _ in range(3):
        data=c.get('/tasks/budget-case/workspace').json()
        assert data['model_budget']['can_add'] and data['model_budget']['remaining']==0
        assert c.get('/settings').status_code==200
    assert not api.jobs and not (ws.path/'model_budget.json').exists()


def test_full_queue_and_cross_task_bindings_cannot_grant(exhausted,monkeypatch):
    c,cfg,ws=exhausted
    body=proposal(c)
    other=Workspace(cfg.output_dir,'another-task')
    atomic_json(other.path/'task.json',read_json(ws.path/'task.json'))
    atomic_json(other.path/'result.json',read_json(ws.path/'result.json'))
    atomic_json(other.path/'usage.json',{'requests':30})
    atomic_json(other.path/'run_limits.json',read_json(ws.path/'run_limits.json'))
    # Even with the same quota/usage, a source task's binding cannot approve another task.
    body['task_id']='another-task'
    monkeypatch.setattr(api.web,'checkpoint_exists',lambda task:True)
    assert c.post('/tasks/another-task/budget-resume',json=body,headers=ORIGIN).status_code==409
    assert not (other.path/'model_budget.json').exists()
    class Active:
        def done(self):return False
    api.jobs.update({f'busy-{i}':Active() for i in range(4)})
    body=proposal(c)
    assert c.post('/tasks/budget-case/budget-resume',json=body,headers=ORIGIN).status_code==429
    assert not (ws.path/'model_budget.json').exists()
    api.jobs.clear()


def test_total_cap_and_task_bound_record(exhausted):
    c,cfg,ws=exhausted
    current=task_budget.snapshot(ws.path,cfg)
    record={'task_id':'budget-case','limit':190,'revision':1,'grants':[],'other_limits':current['other_limits']}
    atomic_json(ws.path/'model_budget.json',record)
    atomic_json(ws.path/'usage.json',{'requests':190})
    assert c.get('/tasks/budget-case/workspace').json()['model_budget']['max_addition']==10
    assert c.post('/tasks/budget-case/budget-resume',json=proposal(c,addition=20),headers=ORIGIN).status_code==409
    assert read_json(ws.path/'model_budget.json')['limit']==190
    record['task_id']='different-task';atomic_json(ws.path/'model_budget.json',record)
    with pytest.raises(task_budget.BudgetConflict):task_budget.effective_settings(cfg,ws.path)
