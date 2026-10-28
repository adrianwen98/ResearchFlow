"""Observed export failure regressions. Scripted graph decisions; zero model API."""
import copy
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage

from test_graph import settings,ScriptModel,call
from test_web_workbench import client
from web_agent.business_excel import BusinessExcelValidationError,export_business_excel,verify_business_excel
from web_agent.context import bounded_context,prompt_size,CONTEXT_CHAR_LIMIT
from web_agent.harness import classify_error,error_output
from web_agent.persistence import atomic_json,read_json
from web_agent.runner import run_task
from web_agent.workspace import Workspace


def fixture_workspace(root,key='fixture'):
    ws=Workspace(root,key)
    text='示例单位 招聘公告\n岗位：研究助理\n专业不限\n薪资 -12.5 元'
    ws.observe('https://example.com/jobs',text)
    ws.record('招聘信息来源网站','https://example.com/jobs','Public fixture',text,require_observation=True)
    return ws


def sheet(value='研究助理',quote='岗位：研究助理'):
    return [{'name':'机会对比','mode':'comparison','columns':['岗位'],
             'rows':[{'label':'示例单位','cells':{'岗位':{'value':value,'source_id':'1','quote':quote}}}]}]


def invalid_source_sheet(value):
    # Source-linked interpretations now export with review flags. An unknown
    # source remains a real hard error in the current production tool.
    spec=sheet(value)
    spec[0]['rows'][0]['cells']['岗位']['source_id']='missing'
    return spec


def test_all_cell_errors_are_located_and_previous_workbook_is_preserved(tmp_path):
    ws=fixture_workspace(tmp_path);export_business_excel(ws,sheet())
    prior=(ws.path/'task_result.xlsx').read_bytes();manifest=(ws.path/'business_artifact.json').read_bytes()
    specs=sheet('综合管理（研究方向）')
    specs[0]['columns']+=['来源','缺失项']
    specs[0]['rows'][0]['cells'].update({'来源':{'value':'招聘信息来源网站','source_id':'1','quote':'岗位：研究助理'},'缺失项':{'value':None}})
    with pytest.raises(BusinessExcelValidationError) as error:export_business_excel(ws,specs)
    data=json.loads(error_output(classify_error(error.value))[7:])
    assert data['code']=='business_validation' and not data['retryable']
    assert data['diagnostics']['issues_total']==3
    assert {i['column'] for i in data['diagnostics']['issues']}=={'岗位','来源','缺失项'}
    assert all(i['sheet']=='机会对比' and i['row']==2 and i['repair'] for i in data['diagnostics']['issues'])
    assert 'Provenance' in data['diagnostics']['issues'][0]['repair']
    assert (ws.path/'task_result.xlsx').read_bytes()==prior
    assert (ws.path/'business_artifact.json').read_bytes()==manifest


def test_whitespace_only_difference_is_accepted_but_inference_is_not(tmp_path):
    ws=fixture_workspace(tmp_path)
    export_business_excel(ws,sheet('研究助理 专业不限','研究助理\n专业不限'))
    assert not verify_business_excel(ws)
    for value in ['研究助理（专业不限）','不限专业','招聘信息来源网站']:
        with pytest.raises(BusinessExcelValidationError):export_business_excel(ws,sheet(value,'研究助理\n专业不限'))


def test_invalid_quote_is_precise_and_diagnostics_are_bounded(tmp_path):
    ws=fixture_workspace(tmp_path);spec=sheet('研究助理','未观察过的网页正文')
    # Unique identities are now enforced before cell checks. This test isolates
    # the original 30-cell diagnostic bound, rather than duplicate-row errors.
    spec[0]['rows']=[{**spec[0]['rows'][0],'label':f'示例单位 {i}'} for i in range(30)]
    with pytest.raises(BusinessExcelValidationError) as error:export_business_excel(ws,spec)
    payload=json.loads(error_output(classify_error(error.value))[7:])
    assert payload['diagnostics']['issues_total']==30 and len(payload['diagnostics']['issues'])==20
    assert payload['diagnostics']['issues_omitted']==10
    assert len(json.dumps(payload,ensure_ascii=False))<20000
    assert not (ws.path/'task_result.xlsx').exists()


@pytest.mark.parametrize('legacy_placeholder',[False,True])
def test_long_call_history_never_exposes_callable_fake_arguments(tmp_path,legacy_placeholder):
    ws=fixture_workspace(tmp_path)
    args={'context_omitted':'旧Checkpoint中的历史占位'} if legacy_placeholder else {'sheets':[{'large':'x'*16000}]}
    ai=call('export_business_excel',args,'large');before=copy.deepcopy(ai.tool_calls)
    result=bounded_context([HumanMessage('Test task'),ai,ToolMessage(content='ERROR: precise cell diagnostic',tool_call_id='large',name='export_business_excel')],ws,'system rules')
    assert ai.tool_calls==before and prompt_size(result)<=CONTEXT_CHAR_LIMIT
    assert not any(m.type=='tool' for m in result)
    assert not any(m.type=='ai' and m.tool_calls for m in result)
    assert any('precise cell diagnostic' in str(m.content) for m in result)
    assert not any('context_omitted' in str(m.content) for m in result)


@asynccontextmanager
async def fixture_browser(cfg,ws):
    if not ws.sources():fixture_workspace(cfg.output_dir,ws.task_id)
    yield []


@pytest.mark.asyncio
async def test_graph_uses_diagnostics_to_correct_export_and_deliver(settings):
    class CorrectingModel(ScriptModel):
        async def ainvoke(self,messages):
            if self.calls==1:
                response=next(m for m in reversed(messages) if m.type=='tool')
                error=json.loads(response.content[7:])
                assert error['code']=='business_validation'
                assert error['diagnostics']['issues'][0]['column']=='岗位'
            return await super().ainvoke(messages)
    model=CorrectingModel([call('export_business_excel',{'sheets':invalid_source_sheet('综合管理类')},'bad'),
                           call('export_business_excel',{'sheets':sheet()},'correct'),call('export_excel',id='sources'),
                           call('export_report',{'markdown_report':'# 任务结果\n原文岗位：研究助理。'},'report')])
    result=await run_task('Export a grounded workbook','corrected',settings,model=model,browser_factory=fixture_browser)
    assert result.status=='needs_review' and result.metrics['requests']==4
    assert result.metrics['errors']==1 and result.metrics['tool_attempts']==4
    assert (settings.output_dir/'corrected/task_result.xlsx').exists()
    assert not verify_business_excel(Workspace(settings.output_dir,'corrected'))


@pytest.mark.asyncio
async def test_three_failed_exports_stop_changed_arguments_and_resume_keeps_budget(settings):
    cfg=replace(settings,max_model_calls=6)
    failed=ScriptModel([call('export_business_excel',{'sheets':invalid_source_sheet('未核实岗位'+str(i))},'bad'+str(i)) for i in range(3)])
    result=await run_task('Bounded correction test','bounded_exports',cfg,model=failed,browser_factory=fixture_browser)
    assert result.status=='stalled' and failed.calls==3 and result.metrics['requests']==3
    assert result.metrics['tool_attempts']==3 and '业务表格' in result.answer
    assert not (cfg.output_dir/'bounded_exports/task_result.xlsx').exists()
    resumed=ScriptModel([call('export_business_excel',{'sheets':sheet()},'fixed'),call('export_excel',id='sources'),
                         call('export_report',{'markdown_report':'# Corrected\n研究助理。'},'report')])
    result=await run_task('', 'bounded_exports',cfg,resume=True,model=resumed,browser_factory=fixture_browser)
    assert result.status=='needs_review' and result.metrics['requests']==6
    assert result.metrics['tool_attempts']==6 and len(read_json(cfg.output_dir/'bounded_exports/tool_journal.json'))==6


@pytest.mark.parametrize('historical',[False,True])
def test_stalled_workbench_exposes_actual_export_cause(client,historical):
    c,cfg=client;ws=fixture_workspace(cfg.output_dir,'export_error')
    try:export_business_excel(ws,sheet('综合管理类'))
    except BusinessExcelValidationError as exc:failure=classify_error(exc)
    output=error_output(failure)
    code='business_validation'
    if historical:
        code='evidence_mismatch';output='ERROR: '+json.dumps({'message':'业务值必须直接出现在摘录中'},ensure_ascii=False)
    atomic_json(ws.path/'result.json',{'task_id':'export_error','status':'stalled','answer':'循环已停止'})
    atomic_json(ws.path/'task.json',{'task_id':'export_error','task':'Export correction fixture'})
    atomic_json(ws.path/'tool_journal.json',{'last':{'name':'export_business_excel','error_code':code,'output':output}})
    data=c.get('/tasks/export_error/workspace').json()
    assert data['status']=='stalled' and '业务表格' in data['failure']['label']
    assert data['failure']['root_code']=='business_validation'
    if historical:assert '旧版日志' in data['failure']['next_action']
    assert ('第2行' in data['failure']['detail']) if not historical else ('摘录' in data['failure']['detail'])


def test_saved_draft_can_be_reopened_and_only_bad_cell_repaired(tmp_path):
    from web_agent.business_excel import repair_business_excel
    ws=fixture_workspace(tmp_path)
    spec=sheet('综合岗位')
    spec[0]['columns'].append('专业')
    spec[0]['rows'][0]['cells']['专业']={'value':'专业不限','source_id':'1','quote':'专业不限'}
    with pytest.raises(BusinessExcelValidationError) as error:export_business_excel(ws,spec)
    draft_id=error.value.details['draft_id']
    assert len(draft_id)==24 and error.value.details['repair_tool']=='repair_business_excel'
    restored=Workspace(tmp_path,ws.task_id)
    repair_business_excel(restored,draft_id,[{'sheet':'机会对比','row':2,'column':'岗位','entry':{'value':'研究助理','source_id':'1','quote':'岗位：研究助理'}}])
    assert not verify_business_excel(restored)
    meta=read_json(restored.path/'business_artifact.json')
    assert meta['sheets'][0]['rows'][0]==['示例单位','研究助理','专业不限']
    assert not (restored.path/'business_draft.json').exists()


@pytest.mark.parametrize('patch',[
 {'sheet':'../other','row':2,'column':'岗位','entry':{'value':None,'reason':'unknown'}},
 {'sheet':'机会对比','row':0,'column':'岗位','entry':{'value':None,'reason':'unknown'}},
 {'sheet':'机会对比','row':2,'column':'岗位','entry':{'value':None,'path':'../outside'}},
])
def test_draft_patch_rejects_invalid_location_or_extra_input(tmp_path,patch):
    from web_agent.business_excel import repair_business_excel
    ws=fixture_workspace(tmp_path)
    with pytest.raises(BusinessExcelValidationError) as error:export_business_excel(ws,sheet('unsupported'))
    before=(ws.path/'business_draft.json').read_bytes()
    with pytest.raises(ValueError):repair_business_excel(ws,error.value.details['draft_id'],[patch])
    assert (ws.path/'business_draft.json').read_bytes()==before
    assert not (ws.path/'task_result.xlsx').exists()


def test_patch_stays_strict_and_rejects_stale_draft_version(tmp_path):
    from web_agent.business_excel import repair_business_excel
    ws=fixture_workspace(tmp_path)
    with pytest.raises(BusinessExcelValidationError) as error:export_business_excel(ws,sheet('unsupported'))
    old=error.value.details['draft_id']
    with pytest.raises(BusinessExcelValidationError) as changed:
        repair_business_excel(ws,old,[{'sheet':'机会对比','row':2,'column':'岗位','entry':{'value':'still invented','source_id':'1','quote':'岗位：研究助理'}}])
    assert changed.value.details['draft_id']!=old and not (ws.path/'task_result.xlsx').exists()
    with pytest.raises(ValueError,match='版本已变化'):repair_business_excel(ws,old,[{'sheet':'机会对比','row':2,'column':'岗位','entry':{'value':None,'reason':'unknown'}}])


@pytest.mark.asyncio
async def test_graph_can_patch_one_cell_without_rewriting_workbook(settings):
    class PatchingModel(ScriptModel):
        async def ainvoke(self,messages):
            if self.calls==1:
                error=json.loads(next(m for m in reversed(messages) if m.type=='tool').content[7:])
                self.calls+=1
                return call('repair_business_excel',{'draft_id':error['diagnostics']['draft_id'],'patches':[{'sheet':'机会对比','row':2,'column':'岗位','entry':{'value':'研究助理','source_id':'1','quote':'岗位：研究助理'}}]},'patch')
            return await super().ainvoke(messages)
    model=PatchingModel([call('export_business_excel',{'sheets':invalid_source_sheet('概括岗位')},'draft'),call('export_excel',id='sources'),call('export_report',{'markdown_report':'# Result\n研究助理。'},'report')])
    result=await run_task('Patch only the failing cell','small_patch',settings,model=model,browser_factory=fixture_browser)
    assert result.status=='needs_review' and result.metrics['requests']==4
    journal=read_json(settings.output_dir/'small_patch/tool_journal.json')
    assert journal['patch']['name']=='repair_business_excel' and not journal['patch']['output'].startswith('ERROR:')
    assert result.metrics['errors']==1
