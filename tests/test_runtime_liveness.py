"""Offline storage, model-deadline and challenge regressions. No live model/network."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from test_graph import settings, ScriptModel, call, empty_browser
from test_web_workbench import client
from web_agent import persistence, browser, web_views
from web_agent.harness import ToolFailure, classify_error
from web_agent.persistence import atomic_json, read_json, read_trace, PersistenceError
from web_agent.runner import run_task
from web_agent.workspace import Workspace


def test_atomic_replace_retries_only_local_write(tmp_path, monkeypatch):
    target = tmp_path/'usage.json'
    atomic_json(target, {'requests':30})
    real_replace = persistence.os.replace
    attempts = []
    def locked_once(source, destination):
        attempts.append(source)
        if len(attempts)==1:
            raise PermissionError(13, 'Windows destination temporarily locked', str(destination))
        return real_replace(source, destination)
    monkeypatch.setattr(persistence.os, 'replace', locked_once)
    atomic_json(target, {'requests':31})
    assert read_json(target)=={'requests':31}
    assert len(attempts)==2 and not list(tmp_path.glob('*.tmp'))


def test_permanent_storage_error_keeps_old_valid_budget(tmp_path, monkeypatch):
    target = tmp_path/'usage.json'
    atomic_json(target, {'requests':30})
    def denied(*args):raise PermissionError(13, 'persistent lock', str(target))
    monkeypatch.setattr(persistence.os, 'replace', denied)
    monkeypatch.setattr(persistence.time, 'sleep', lambda _: None)
    with pytest.raises(PersistenceError) as failure:atomic_json(target, {'requests':31})
    assert classify_error(failure.value).code=='storage_error'
    assert read_json(target)=={'requests':30} and not list(tmp_path.glob('*.tmp'))
    assert classify_error(PermissionError('policy denied')).code=='permission_denied'


def test_concurrent_atomic_writes_never_mix_or_tear_json(tmp_path):
    target=tmp_path/'budget.json'
    atomic_json(target, {'id':-1, 'payload':'-1'*1000})
    def write(index):
        atomic_json(target, {'id':index, 'payload':str(index)*1000})
        value=read_json(target)
        assert value['payload']==str(value['id'])*1000
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(write, range(64)))
    assert not list(tmp_path.glob('*.tmp'))


def test_unreadable_budget_is_not_reset_to_default(tmp_path,monkeypatch):
    from pathlib import Path
    target=tmp_path/'usage.json'
    atomic_json(target,{'requests':30})
    original=Path.read_text
    attempts=[]
    def locked(path,*args,**kwargs):
        if path==target:
            attempts.append(True)
            raise PermissionError('injected read lock')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',locked)
    monkeypatch.setattr(persistence.time,'sleep',lambda _:None)
    with pytest.raises(PersistenceError):read_json(target,{'requests':0})
    assert len(attempts)==6
    assert json.loads(original(target,encoding='utf-8'))['requests']==30


class WaitingModel(ScriptModel):
    async def ainvoke(self, messages):
        self.calls+=1
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_model_total_deadline_persists_terminal_state(settings):
    cfg=replace(settings, model_timeout=.04)
    model=WaitingModel([])
    started=time.monotonic()
    result=await run_task('offline hung response', 'hung', cfg, model=model, browser_factory=empty_browser)
    folder=cfg.output_dir/'hung'
    assert result.status=='timeout' and result.failure_code=='model_timeout'
    assert time.monotonic()-started<3 and model.calls==1
    assert read_json(folder/'usage.json')['requests']==1
    assert read_json(folder/'result.json')['failure_code']=='model_timeout'
    assert not (folder/'.running').exists()
    assert read_json(folder/'time_budget.json')['active_since'] is None
    assert any(row['event']=='model_timeout' for row in read_trace(folder/'trace.jsonl'))
    assert web_views.state(folder,{})=='timeout'


@pytest.mark.asyncio
async def test_task_deadline_remains_stricter_than_model_deadline(settings):
    result=await run_task('offline total task deadline','global-timeout',replace(settings,task_timeout=.5,model_timeout=10),
                          model=WaitingModel([]),browser_factory=empty_browser)
    folder=settings.output_dir/'global-timeout'
    assert result.status=='timeout' and result.failure_code!='model_timeout'
    assert read_json(folder/'usage.json')['requests']==1
    assert not (folder/'.running').exists()


@pytest.mark.asyncio
async def test_mcp_exception_group_retains_model_timeout_reason(settings):
    @asynccontextmanager
    async def grouped_browser(cfg,ws):
        try:yield []
        except Exception as exc:raise ExceptionGroup('MCP session exit', [exc])
    result=await run_task('wrapped MCP model timeout','grouped-timeout',replace(settings,model_timeout=.04),
                          model=WaitingModel([]),browser_factory=grouped_browser)
    assert result.status=='timeout' and result.failure_code=='model_timeout'


@pytest.mark.asyncio
async def test_timeout_resume_keeps_completed_operations_and_usage(settings):
    class PlanThenWait(WaitingModel):
        async def ainvoke(self,messages):
            if not self.calls:
                self.calls+=1
                return call('update_plan', {'steps':['saved step']}, 'plan-before-timeout')
            return await super().ainvoke(messages)
    cfg=replace(settings,model_timeout=.04)
    result=await run_task('preserve progress on model timeout', 'resume-timeout', cfg,
                          model=PlanThenWait([]), browser_factory=empty_browser)
    folder=cfg.output_dir/'resume-timeout'
    assert result.status=='timeout' and read_json(folder/'usage.json')['requests']==2
    result=await run_task('', 'resume-timeout',cfg,resume=True,
                          model=ScriptModel([AIMessage(content='No verified facts'), AIMessage(content='Incomplete')]),
                          browser_factory=empty_browser)
    assert result.status=='incomplete'
    assert read_json(folder/'usage.json')['requests']==4
    assert sum(row['event']=='plan_updated' for row in read_trace(folder/'trace.jsonl'))==1


@pytest.mark.asyncio
async def test_storage_debit_failure_stops_before_dispatch_or_another_model(settings,monkeypatch):
    import web_agent.graph as graph
    original=graph.atomic_json
    def fail_debit(path,value):
        if path.name=='tool_usage.json':raise PersistenceError('injected storage lock')
        return original(path,value)
    monkeypatch.setattr(graph,'atomic_json',fail_debit)
    model=ScriptModel([call('update_plan',{'steps':['must not execute']})])
    result=await run_task('storage debit failure task','debit-fail',settings,model=model,browser_factory=empty_browser)
    folder=settings.output_dir/'debit-fail'
    assert result.status=='error' and result.failure_code=='storage_error'
    assert model.calls==1 and read_json(folder/'usage.json')['requests']==1
    assert not (folder/'plan.json').exists() and not (folder/'.running').exists()


@pytest.mark.asyncio
async def test_transient_budget_lock_does_not_repeat_tool(settings,monkeypatch):
    original=persistence.os.replace
    failures=[]
    def locked_once(source,destination):
        if destination.name=='tool_usage.json' and not failures:
            failures.append(True)
            raise PermissionError('injected Windows rename lock')
        return original(source,destination)
    monkeypatch.setattr(persistence.os,'replace',locked_once)
    model=ScriptModel([call('update_plan',{'steps':['one operation']})])
    result=await run_task('transient counter file lock', 'one-tool',replace(settings,max_model_calls=1),
                          model=model,browser_factory=empty_browser)
    folder=settings.output_dir/'one-tool'
    assert result.status=='budget_exceeded' and result.metrics['errors']==0
    assert read_json(folder/'tool_usage.json')['attempts']==1 and model.calls==1
    assert sum(row['event']=='plan_updated' for row in read_trace(folder/'trace.jsonl'))==1


@pytest.mark.asyncio
async def test_final_timer_write_failure_always_releases_lock(settings,monkeypatch):
    import web_agent.runner as runner
    original=runner.atomic_json
    def failed_final_timer(path,value):
        if path.name=='time_budget.json' and value.get('active_since') is None:
            raise PersistenceError('cannot finalize timer')
        return original(path,value)
    monkeypatch.setattr(runner,'atomic_json',failed_final_timer)
    with pytest.raises(PersistenceError):
        await run_task('timer failure cleanup','timer-fail',replace(settings,max_model_calls=1),
                       model=ScriptModel([call('update_plan',{'steps':['step']})]),browser_factory=empty_browser)
    assert not (settings.output_dir/'timer-fail'/'.running').exists()


@pytest.mark.asyncio
async def test_user_cancel_preserves_spent_request_and_unlocks(settings):
    task=asyncio.create_task(run_task('cancel waiting model','cancel-wait',settings,
                                    model=WaitingModel([]),browser_factory=empty_browser))
    folder=settings.output_dir/'cancel-wait'
    for _ in range(100):
        if (folder/'usage.json').exists():break
        await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert read_json(folder/'result.json')['status']=='interrupted'
    assert read_json(folder/'usage.json')['requests']==1 and not (folder/'.running').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('rendered_only',[False,True])
async def test_challenge_is_never_saved_as_source(settings,monkeypatch,rendered_only):
    async def navigate(args):
        return '- Page URL: https://example.com/data\n- Page Title: '+('Data' if rendered_only else 'Just a moment...')+'\n```yaml\n- generic: Loading\n```'
    async def evaluate(args):return '### Result\n"Verify you are human"'
    async def load(session):
        return [SimpleNamespace(name=n,ainvoke=f) for n,f in [('browser_navigate',navigate),('browser_snapshot',navigate),('browser_evaluate',evaluate)]]
    monkeypatch.setattr(browser,'load_mcp_tools',load)
    ws=Workspace(settings.output_dir,'challenge-rendered')
    tools={t.name:t for t in await browser.load_safe_browser_tools(None,settings,ws)}
    with pytest.raises(ToolFailure) as failure:await tools['browser_navigate'].ainvoke({'url':'https://example.com/data'})
    assert failure.value.code=='site_challenge'
    assert not list((ws.path/'observations').glob('*.json'))


def test_article_mentioning_captcha_is_not_a_verification_page():
    browser.check_site_challenge('- Page Title: CAPTCHA research overview\nA tutorial explaining how to verify you are human.')


@pytest.mark.asyncio
async def test_challenge_can_switch_source_but_repeated_challenge_pauses(settings):
    invoked=[]
    @asynccontextmanager
    async def fake_browser(cfg, ws):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Offline challenge fixture."""
            invoked.append(url)
            if 'blocked.example.com' in url:raise ToolFailure('site_challenge','Verify you are human')
            return 'Other public source'
        yield [navigate]
    first=ScriptModel([call('browser_navigate',{'url':'https://blocked.example.com/data'},'one'),
                       call('browser_navigate',{'url':'https://public.example.com/data'},'two')])
    result=await run_task('switch to public source','switch-source',replace(settings,max_model_calls=2),
                          model=first,browser_factory=fake_browser)
    assert result.status=='budget_exceeded' and len(invoked)==2
    invoked.clear()
    batched=AIMessage(content='',tool_calls=[{'name':'browser_navigate','args':{'url':'https://blocked.example.com/'+suffix},'id':suffix} for suffix in ['a','b']])
    result=await run_task('blocked source must pause','pause-challenge',settings,model=ScriptModel([batched]),browser_factory=fake_browser)
    folder=settings.output_dir/'pause-challenge'
    assert result.status=='interrupted' and len(invoked)==1
    assert read_json(folder/'usage.json')['requests']==1
    assert '人工验证' in result.answer and not (folder/'.running').exists()
    assert read_json(folder/'result.json')['status']=='interrupted'


def test_workspace_uses_model_timeout_instead_of_unrelated_old_tool_error(client):
    c,cfg=client
    ws=Workspace(cfg.output_dir,'timeout-view')
    atomic_json(ws.path/'task.json',{'task':'offline timeout view'})
    atomic_json(ws.path/'result.json',{'status':'timeout','failure_code':'model_timeout','answer':'模型响应超时'})
    atomic_json(ws.path/'tool_journal.json',{'old':{'error_code':'permission_denied'}})
    data=c.get('/tasks/timeout-view/workspace').json()
    assert data['status']=='timeout' and data['failure']['code']=='model_timeout'
    assert '模型' in data['failure']['label'] and not data['capabilities']['can_cancel']
