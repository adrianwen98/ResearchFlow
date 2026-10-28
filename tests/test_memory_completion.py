import json
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import pytest
from langchain_core.messages import AIMessage,HumanMessage
from test_graph import settings,ScriptModel,call,empty_browser
from web_agent.workspace import Workspace
from web_agent.memory import MemoryStore,memory_for
from web_agent.completion import define_requirements,record_result,evaluate_requirements,review_binding,accept_delivery,current_result
from web_agent.graph import context_messages
from web_agent.runner import run_task
from web_agent.persistence import atomic_json,read_json
from langchain_core.tools import tool
from web_agent.harness import preflight,ToolFailure


def prepared_workspace(root,task_id='study'):
    ws=Workspace(root,task_id)
    atomic_json(ws.path/'task.json',{'task':'Python official project research','task_id':task_id})
    ws.observe('https://example.com','Actual observed project text.')
    ws.record('Example','https://example.com','Finding','Actual observed project text.',require_observation=True)
    ws.export_excel();ws.export_report('# Finding\nActual research finding.')
    define_requirements(ws,[{'description':'Explain official project purpose','kind':'research'}],origin='user')
    record_result(ws,'r1',['1'],'Actual research finding.')
    atomic_json(ws.path/'result.json',{'task_id':task_id,'status':'needs_review','completion':{}})
    return ws


def test_cross_task_memory_persists_retrieves_and_deletes(tmp_path):
    store=MemoryStore(tmp_path/'memory.sqlite')
    pref=store.save('报告使用中文，优先官方来源')
    receipt=store.save('Python official project research summary',kind='accepted_task',task_id='prior')
    other=store.save('Unrelated football fixture',kind='accepted_task',task_id='unrelated')
    loaded=MemoryStore(tmp_path/'memory.sqlite')
    found=loaded.search('Python project research')
    assert {r['id'] for r in found}=={pref,receipt}
    assert loaded.delete(receipt) and all(r['id']!=receipt for r in loaded.search('Python project research'))
    assert loaded.save('报告使用中文，优先官方来源')==pref
    with pytest.raises(ValueError):loaded.save('password: secret-test-123')


def test_memory_enters_next_task_as_data_not_tool_authorization(tmp_path):
    first=Workspace(tmp_path,'first')
    memory_for(first).save('Python official project research summary',kind='accepted_task',task_id='prior')
    second=Workspace(tmp_path,'second')
    messages=context_messages([HumanMessage('Research Python official project')],second,'2026-10-15')
    assert 'Python official project research summary' in messages[1].content
    assert 'cross_task_memory' in messages[1].content


def test_requirements_append_cannot_remove_user_contract_and_validate_sources(tmp_path):
    ws=prepared_workspace(tmp_path)
    rows=define_requirements(ws,[{'description':'Export report','kind':'artifact','artifact':'research_report.md'}])
    assert len(rows)==2 and rows[0]['origin']=='user'
    assert len(define_requirements(ws,[{'description':'Export report','kind':'artifact','artifact':'research_report.md'}]))==2
    with pytest.raises(ValueError):record_result(ws,'r1',['invented'],'quote')
    with pytest.raises(ValueError):record_result(ws,'r1',['1'],'quote',calculation_id='',status='unknown')
    record_result(ws,'r2',[])
    assert evaluate_requirements(ws)['requirements_supported']


def test_final_acceptance_binds_files_and_all_requirements_and_creates_memory(tmp_path):
    ws=prepared_workspace(tmp_path)
    binding=review_binding(ws)
    with pytest.raises(ValueError):accept_delivery(ws,[] ,binding)
    (ws.path/'research_report.md').write_text('modified',encoding='utf-8')
    with pytest.raises(ValueError):accept_delivery(ws,['r1'],binding)
    ws.export_report('# Finding\nActual research finding.')
    result=accept_delivery(ws,['r1'],review_binding(ws),'Reviewed source and interpretation')
    assert result['status']=='completed' and result['completion']['all_requirements_verified']
    assert result['completion']['acceptance']['verification_mode']=='human_acceptance'
    assert memory_for(ws).search('Python official project')
    assert read_json(ws.path/'acceptance.json')['requirement_ids']==['r1']


def test_acceptance_cannot_race_running_task(tmp_path):
    ws=prepared_workspace(tmp_path)
    (ws.path/'.running').write_text('active')
    with pytest.raises(ValueError):accept_delivery(ws,['r1'],review_binding(ws))


def test_changed_accepted_artifact_invalidates_completion_and_memory(tmp_path):
    ws=prepared_workspace(tmp_path)
    accept_delivery(ws,['r1'],review_binding(ws))
    assert memory_for(ws).search('Python official project')
    (ws.path/'research_report.md').write_text('Modified after acceptance',encoding='utf-8')
    result=current_result(ws)
    assert result['status']=='partial' and result['completion']['acceptance_stale']
    assert not result['completion']['all_requirements_verified']
    assert not memory_for(ws).search('Python official project')


def test_requirement_excerpt_ignores_markdown_but_rejects_rewritten_fact(tmp_path):
    ws=prepared_workspace(tmp_path)
    ws.export_report('# Finding\n1. **Actual** research finding.\n\nRows: **7**.')
    record_result(ws,'r1',['1'],'1. Actual research finding. Rows: 7.')
    assert evaluate_requirements(ws)['requirements_supported']
    with pytest.raises(ValueError):record_result(ws,'r1',['1'],'Rows: 8.')


def test_binary_url_returns_format_guidance_instead_of_permission_error():
    with pytest.raises(ToolFailure) as failure:preflight('browser_navigate',{'url':'https://example.com/data.xlsx'},{},('example.com',))
    assert failure.value.code=='file_tool_required' and not failure.value.retryable
    assert preflight('download_public_file',{'url':'https://example.com/data.xlsx'},{},('example.com',))


@pytest.mark.asyncio
async def test_graph_checks_each_requirement_before_final_review(settings):
    ws=Workspace(settings.output_dir,'requirements')
    ws.observe('https://example.com','Actual observed project text.')
    ws.record('Example','https://example.com','Finding','Actual observed project text.',require_observation=True)
    model=ScriptModel([
        call('set_requirements',{'items':[{'description':'Explain project purpose','kind':'research'}]},'req'),
        call('record_requirement_result',{'requirement_id':'r1','source_ids':['1'],'report_excerpt':'Actual finding.'},'assessment'),
        call('export_excel',id='excel'),call('export_report',{'markdown_report':'# Result\nActual finding.'},'report')])
    result=await run_task('Research project purpose','requirements',settings,model=model,browser_factory=empty_browser)
    assert result.status=='needs_review' and result.completion['requirements_supported']
    assert not result.completion['all_requirements_verified']
    accepted=accept_delivery(ws,['r1'],review_binding(ws))
    assert accepted['completion']['outcome']=='completed'


@pytest.mark.asyncio
async def test_missing_requirement_produces_partial_instead_of_complete(settings):
    ws=prepared_workspace(settings.output_dir,'partial')
    # Existing manifest needs explicit resume, so create a fresh graph under a separate fixture ID.
    (ws.path/'task.json').unlink()
    record_result(ws,'r1',[],status='missing',reason='Official evidence unavailable')
    model=ScriptModel([AIMessage(content='Partial report'),AIMessage(content='Still missing official evidence')])
    result=await run_task('Research missing project evidence','partial',settings,model=model,browser_factory=empty_browser)
    assert result.status=='partial' and result.completion['missing_requirements']==['r1']


@pytest.mark.asyncio
async def test_reused_tool_id_with_different_args_stops_without_replaying(settings):
    model=ScriptModel([call('update_plan',{'steps':['original']},'same-id'),call('update_plan',{'steps':['changed']},'same-id')])
    result=await run_task('Tool identity conflict regression','conflict',settings,model=model,browser_factory=empty_browser)
    assert result.status=='stalled'
    assert read_json(settings.output_dir/'conflict'/'plan.json')==['original']


@pytest.mark.asyncio
async def test_followup_waits_until_interrupted_tool_group_is_completed(settings):
    entered=asyncio.Event();slow=True
    @asynccontextmanager
    async def browser(config,ws):
        @tool('browser_snapshot')
        async def snapshot()->str:
            """Injected read interrupted before its journal is committed."""
            entered.set()
            if slow:await asyncio.sleep(60)
            return 'Recovered read'
        yield [snapshot]
    job=asyncio.create_task(run_task('Original research task','pending_tool',settings,
                            model=ScriptModel([call('browser_snapshot',id='pending')]),browser_factory=browser))
    await asyncio.wait_for(entered.wait(),5);job.cancel()
    with pytest.raises(asyncio.CancelledError):await job
    slow=False
    class Spy(ScriptModel):
        async def ainvoke(self,messages):
            assert any(m.type=='human' and m.content=='New followup after restart' for m in messages)
            assert any(m.type=='tool' and m.tool_call_id=='pending' for m in messages)
            return await super().ainvoke(messages)
    result=await run_task('New followup after restart','pending_tool',settings,resume=True,
                          model=Spy([AIMessage(content='No source'),AIMessage(content='Incomplete')]),browser_factory=browser)
    assert result.status=='incomplete' and result.metrics['requests']==3


def test_ui_callbacks_expose_real_memory_and_acceptance(monkeypatch,tmp_path):
    import app
    current=replace(app.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(app.Settings,'load',lambda:current)
    rows,message=app.save_preference('Use official sources and Chinese reports')
    assert len(rows)==1
    rows,message=app.delete_memory(rows[0]['id'])
    assert rows==[]
