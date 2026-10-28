"""Regression evidence for host enforcement; scripted model, never a paid API."""
import asyncio
import json
from dataclasses import replace
from contextlib import asynccontextmanager

import httpx
import pandas as pd
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from test_graph import ScriptModel, call, empty_browser, settings
from web_agent.context import CONTEXT_CHAR_LIMIT, prompt_size
from web_agent.graph import context_messages
from web_agent.runner import run_task
from web_agent.harness import classify_error
from web_agent.tools import build_local_tools
from web_agent.workspace import Workspace
from web_agent.persistence import read_json, read_trace
from web_agent.persistence import atomic_json


@pytest.mark.asyncio
async def test_denied_domain_different_paths_never_dispatch_and_stop(settings):
    invoked = []
    @asynccontextmanager
    async def browser(config, ws):
        @tool('browser_navigate')
        async def navigate(url: str) -> str:
            """Dispatch spy; must never be called for a denied domain."""
            invoked.append(url)
            return 'should not run'
        yield [navigate]
    responses = [call('browser_navigate', {'url':'https://nodejs.org/'+path}, str(i))
                 for i,path in enumerate(['en/about','en/download','en/docs',''])]
    result = await run_task('denied domain regression', 'denied_paths',
                            replace(settings, allowed_domains=('python.org',)),
                            model=ScriptModel(responses), browser_factory=browser)
    assert result.status == 'permission_denied' and invoked == []
    journal = read_json(settings.output_dir/'denied_paths'/'tool_journal.json')
    assert len(journal) == result.metrics['tool_calls'] == 2
    assert all(row['error_code']=='permission_denied' for row in journal.values())
    assert result.metrics['retries'] == 0
    from web_agent.dashboard import trace_html
    rendered=trace_html({'trace':read_trace(settings.output_dir/'denied_paths'/'trace.jsonl'),'plan':[]})
    assert 'permission_denied' in rendered and 'nodejs.org/en/about' in rendered


@pytest.mark.asyncio
async def test_successful_read_loop_is_also_bounded(settings):
    invoked = []
    @asynccontextmanager
    async def browser(config, ws):
        @tool('browser_snapshot')
        async def snapshot() -> str:
            """Repeated successful snapshot without new progress."""
            invoked.append(1)
            return 'unchanged observation'
        yield [snapshot]
    model = ScriptModel([call('browser_snapshot', id=str(i)) for i in range(7)])
    result = await run_task('repeated successful read', 'success_loop', settings, model=model, browser_factory=browser)
    assert len(invoked)==3 and model.calls==6 and result.status=='stalled'
    assert result.metrics['tool_calls']==6
    assert 'loop_detected' in (settings.output_dir/'success_loop'/'tool_journal.json').read_text(encoding='utf-8')


@pytest.mark.asyncio
async def test_snapshot_loop_scope_changes_with_actual_page(settings):
    observed=[]
    @asynccontextmanager
    async def browser(config,ws):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Set observed page for loop-scope regression."""
            atomic_json(ws.path/'browser_state.json',{'url':url})
            return url
        @tool('browser_snapshot')
        async def snapshot()->str:
            """Observe a different page on each iteration."""
            observed.append(read_json(ws.path/'browser_state.json')['url'])
            return observed[-1]
        yield [navigate,snapshot]
    responses=[]
    for i in range(4):
        responses.extend([call('browser_navigate',{'url':f'https://example.com/{i}'},'n'+str(i)),call('browser_snapshot',id='s'+str(i))])
    responses.extend([AIMessage(content='No sources'),AIMessage(content='Incomplete')])
    result=await run_task('different pages need different snapshots','scope',replace(settings,allowed_domains=(),max_model_calls=30),
                          model=ScriptModel(responses),browser_factory=browser)
    assert len(observed)==4 and result.status=='incomplete' and result.metrics['errors']==0


@pytest.mark.parametrize('status,retryable', [(404,False),(403,False),(429,True),(503,True)])
def test_http_error_classification(status, retryable):
    request = httpx.Request('GET','https://example.com/data.csv')
    response = httpx.Response(status, request=request)
    failure = classify_error(httpx.HTTPStatusError('injected',request=request,response=response))
    assert failure.code=='http_'+str(status) and failure.retryable is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize('status,attempts', [(404,1),(503,2)])
async def test_retry_policy_enforced_in_graph(settings,status,attempts):
    invoked=[]
    @asynccontextmanager
    async def browser(config,ws):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Injected HTTP failure; actual graph retry policy under test."""
            invoked.append(url)
            request=httpx.Request('GET',url)
            raise httpx.HTTPStatusError('injected',request=request,response=httpx.Response(status,request=request))
        yield [navigate]
    model=ScriptModel([call('browser_navigate',{'url':'https://example.com'}),AIMessage(content='No source'),AIMessage(content='Incomplete')])
    result=await run_task('retry classification task','http_'+str(status),replace(settings,allowed_domains=()),model=model,browser_factory=browser)
    assert len(invoked)==attempts and result.metrics['retries']==attempts-1
    assert result.status=='incomplete'


@pytest.mark.asyncio
async def test_human_gate_obeys_tool_budget(settings):
    model=ScriptModel([AIMessage(content='',tool_calls=[
        {'name':'update_plan','args':{'steps':['research']},'id':'p','type':'tool_call'},
        {'name':'request_human_review','args':{'action':'send','reason':'external'},'id':'r','type':'tool_call'}])])
    result=await run_task('review after budget exhausted','review_budget',replace(settings,max_tool_calls=1),model=model,browser_factory=empty_browser)
    assert result.status=='budget_exceeded' and not result.pending_review and result.metrics['tool_calls']==1


def test_context_bounds_include_huge_arguments_and_complete_tool_groups(tmp_path):
    ws=Workspace(tmp_path,'context')
    original=AIMessage(content='x'*100000,tool_calls=[{'name':'export_report','args':{'markdown_report':'y'*100000},'id':'big','type':'tool_call'}])
    messages=[HumanMessage('original task')]
    for i in range(10):
        messages.extend([AIMessage(content='',tool_calls=[{'name':'browser_snapshot','args':{},'id':str(i),'type':'tool_call'}]),
                         ToolMessage(content='page'*20000,tool_call_id=str(i))])
    messages.extend([original,ToolMessage(content='large output'*20000,tool_call_id='big')])
    result=context_messages(messages,ws,'2026-10-15')
    assert prompt_size(result)<=CONTEXT_CHAR_LIMIT
    assert len(original.content)==100000 and len(original.tool_calls[0]['args']['markdown_report'])==100000
    assert result[2].content=='original task'
    for index,message in enumerate(result):
        if message.type=='tool':
            before=next(m for m in reversed(result[:index]) if m.type!='tool')
            assert before.type=='ai' and message.tool_call_id in {c['id'] for c in before.tool_calls}


def test_huge_multi_tool_group_uses_bounded_summary(tmp_path):
    ws=Workspace(tmp_path,'large_batch')
    ai=AIMessage(content='',tool_calls=[{'name':'browser_snapshot','args':{},'id':str(i),'type':'tool_call'} for i in range(500)])
    messages=[HumanMessage('keep original goal'),ai,*[ToolMessage(content='a'*1000,tool_call_id=str(i)) for i in range(500)]]
    result=context_messages(messages,ws,'2026-10-15')
    assert prompt_size(result)<=CONTEXT_CHAR_LIMIT
    assert not any(m.type=='tool' for m in result)
    assert any('历史调用过长' in m.content for m in result)


@pytest.mark.asyncio
async def test_skills_loaded_only_on_demand_and_invalid_name_fails(settings,tmp_path):
    ws=Workspace(tmp_path,'skills')
    tools={t.name:t for t in build_local_tools(ws,settings)}
    initial=context_messages([HumanMessage('study products')],ws,'2026-10-15')
    assert not any('避免将不同存储容量' in m.content for m in initial)
    loaded=await tools['load_skill'].ainvoke({'skill_name':'product_research'})
    assert '避免将不同存储容量' in loaded
    with pytest.raises(ValueError):await tools['load_skill'].ainvoke({'skill_name':'../../.env'})
    class Spy(ScriptModel):
        async def ainvoke(self,messages):
            if self.calls==1:
                assert any(m.type=='tool' and '避免将不同存储容量' in m.content for m in messages)
            return await super().ainvoke(messages)
    model=Spy([call('load_skill',{'skill_name':'product_research'}),AIMessage(content='No data'),AIMessage(content='Incomplete')])
    result=await run_task('skills loop integration','skill_loop',settings,model=model,browser_factory=empty_browser)
    assert result.status=='incomplete' and model.calls==3


def verified_workspace(tmp_path):
    ws=Workspace(tmp_path,'verify')
    ws.observe('https://example.com','Actual observed source text.')
    ws.record('Example','https://example.com','Fact','Actual observed source text.',require_observation=True)
    ws.export_excel();ws.export_report('# Findings\nFact based on observed text.')
    return ws


def test_delivery_verifies_workbook_cells_and_report_hash(tmp_path):
    ws=verified_workspace(tmp_path)
    check=ws.verify_delivery()
    assert check['artifacts_valid'] and not check['all_requirements_verified']
    frame=pd.read_excel(ws.path/'research_sources.xlsx');frame.loc[0,'fact']='tampered'
    frame.to_excel(ws.path/'research_sources.xlsx',index=False)
    assert 'excel_content_mismatch' in ws.verify_delivery()['issues']
    ws.export_excel()
    with (ws.path/'research_report.md').open('a',encoding='utf-8') as f:f.write('unauthorized modification')
    assert 'report_hash_missing_or_mismatched' in ws.verify_delivery()['issues']


def test_corrupt_or_stale_delivery_is_rejected(tmp_path):
    ws=verified_workspace(tmp_path)
    (ws.path/'research_sources.xlsx').write_bytes(b'not a workbook')
    assert not ws.verify_delivery()['artifacts_valid']
    ws.export_excel()
    ws.observe('https://example.com/other','New actual observation.')
    ws.record('Other','https://example.com/other','Other fact','New actual observation.',require_observation=True)
    check=ws.verify_delivery()
    assert 'report_sources_stale' in check['issues'] and 'excel_content_mismatch' in check['issues']


def test_evidence_verification_uses_recorded_observation_id(tmp_path):
    ws=verified_workspace(tmp_path)
    row=ws.sources()[0]
    ws.observe(row['url'],'Additional text '+row['quote'])
    assert ws.verify_delivery()['artifacts_valid']
    # Another observation with a different URL cannot stand in for the recorded one.
    other=ws.observe('https://example.com/elsewhere',row['quote'])
    records=ws.sources();records[0]['observation_id']=other
    atomic_json(ws.data_file,records)
    assert not ws.verify_delivery()['artifacts_valid']


@pytest.mark.asyncio
async def test_graceful_cancellation_cleans_lock_and_can_resume(settings):
    entered=asyncio.Event()
    class Slow(ScriptModel):
        async def ainvoke(self,messages):
            entered.set()
            await asyncio.sleep(60)
    job=asyncio.create_task(run_task('cancel checkpoint task','cancel',settings,model=Slow([]),browser_factory=empty_browser))
    await asyncio.wait_for(entered.wait(),5)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):await job
    folder=settings.output_dir/'cancel'
    assert not (folder/'.running').exists() and read_json(folder/'result.json')['status']=='interrupted'
    result=await run_task('', 'cancel',settings,resume=True,model=ScriptModel([AIMessage(content='No source'),AIMessage(content='Incomplete')]),browser_factory=empty_browser)
    assert result.status=='incomplete' and result.metrics['requests']==3


@pytest.mark.asyncio
async def test_torn_trace_does_not_break_resume(settings):
    await run_task('torn trace recovery','torn',replace(settings,max_model_calls=1),
                   model=ScriptModel([call('update_plan',{'steps':['saved']})]),browser_factory=empty_browser)
    with (settings.output_dir/'torn'/'trace.jsonl').open('a',encoding='utf-8') as f:f.write('{"event":')
    result=await run_task('', 'torn',settings,resume=True,
                          model=ScriptModel([AIMessage(content='No data'),AIMessage(content='Incomplete')]),browser_factory=empty_browser)
    assert result.status=='incomplete' and result.metrics['requests']==3


@pytest.mark.asyncio
async def test_followup_is_appended_when_resuming_and_tasks_are_isolated(settings):
    await run_task('original specific goal','follow',replace(settings,max_model_calls=1),
                   model=ScriptModel([call('update_plan',{'steps':['original plan']})]),browser_factory=empty_browser)
    class FollowupSpy(ScriptModel):
        async def ainvoke(self,messages):
            assert any(m.type=='human' and m.content=='additional user requirement' for m in messages)
            assert any(m.type=='human' and m.content=='original specific goal' for m in messages)
            return await super().ainvoke(messages)
    result=await run_task('additional user requirement','follow',settings,resume=True,
                          model=FollowupSpy([AIMessage(content='No data'),AIMessage(content='Incomplete')]),browser_factory=empty_browser)
    assert result.status=='incomplete'
    class OtherSpy(ScriptModel):
        async def ainvoke(self,messages):
            assert all('original specific goal' not in str(m.content) for m in messages)
            return await super().ainvoke(messages)
    await run_task('separate task must be isolated','other',settings,
                   model=OtherSpy([AIMessage(content='No data'),AIMessage(content='Incomplete')]),browser_factory=empty_browser)
