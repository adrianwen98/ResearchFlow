"""Offline driver tests the real Runner/tools with captured public observations.

The decision driver is scripted, not a live LLM. No autonomous model-quality
claim is inferred from this replay.
"""
import copy
import hashlib
import json
import re
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from test_graph import settings, ScriptModel, call
from test_export_correction import fixture_workspace, sheet
from test_web_workbench import client
from test_memory_completion import prepared_workspace
from test_task_budget import exhausted,proposal,ORIGIN
from web_agent.business_excel import (BusinessExcelValidationError, export_business_excel,
    inspect_business_draft, repair_business_excel, verify_business_excel)
from web_agent.context import compact_group, compact
from web_agent.delivery_recovery import compact_error,compact_result,recovery_messages
from web_agent.harness import error_output, classify_error
from web_agent.persistence import atomic_json, read_json
from web_agent.runner import run_task
from web_agent.workspace import Workspace
from web_agent.tools import build_local_tools
from web_agent.completion import define_requirements,record_result,evaluate_requirements

CASE=Path(__file__).parent/'fixtures'/'delivery_loop_case.json'


def saved_workspace(root,task_id='saved'):
    data=json.loads(CASE.read_text(encoding='utf-8'))
    ws=Workspace(root,task_id)
    atomic_json(ws.path/'sources.json',data['sources'])
    (ws.path/'observations').mkdir(exist_ok=True)
    for obs in data['observations']:
        atomic_json(ws.path/'observations'/f"{obs['id']}.json",obs)
    return ws,data


def test_large_diagnostics_remain_valid_json_with_version_and_paging(tmp_path):
    ws,data=saved_workspace(tmp_path)
    with pytest.raises(BusinessExcelValidationError) as err:
        export_business_excel(ws,data['calls'][0]['arguments']['sheets'])
    output=error_output(classify_error(err.value))
    for limit in (2200,6000):
        compacted=compact_error(output,limit)
        packet=json.loads(compacted[7:])
        assert len(compacted)<=limit
        assert packet['diagnostics']['draft_id']==err.value.details['draft_id']
        assert packet['diagnostics']['repair_tool']=='repair_business_excel'
        assert packet['diagnostics']['issues_total']==12
        assert packet['diagnostics']['issues_omitted']+len(packet['diagnostics']['issues'])==12
    original=call('export_business_excel',data['calls'][0]['arguments'],'large')
    before=copy.deepcopy(original.tool_calls)
    summarized=compact_group([original,ToolMessage(content=output,tool_call_id='large',name='export_business_excel')])
    assert original.tool_calls==before
    assert err.value.details['draft_id'] in str(summarized)
    assert json.loads(compact(ToolMessage(content=output,tool_call_id='large')).content[7:])['diagnostics']['draft_id']


def test_draft_inspection_is_read_only_task_bound_and_exposes_real_context(tmp_path):
    ws,data=saved_workspace(tmp_path)
    with pytest.raises(BusinessExcelValidationError) as err:
        export_business_excel(ws,data['calls'][0]['arguments']['sheets'])
    path=ws.path/'business_draft.json';before=path.read_bytes()
    result=inspect_business_draft(ws,err.value.details['draft_id'])
    assert result['issues_total']==12 and result['valid_cells']==8
    assert len(result['issues'])==10 and result['next_offset']==10
    upload=next(i for i in result['issues'] if i['column']=='Notion Free' and i['row']==6)
    assert '5 MB' in upload['source_context']
    assert upload['source_context'] in ws.source_excerpt('1','5 MB')['text']
    assert upload['context_requires_review']
    rest=inspect_business_draft(ws,err.value.details['draft_id'],10)
    assert len(rest['issues'])==2 and rest['next_offset'] is None
    assert path.read_bytes()==before and not (ws.path/'task_result.xlsx').exists()
    for offset in (-1,True,1001):
        with pytest.raises(ValueError):inspect_business_draft(ws,err.value.details['draft_id'],offset)
    with pytest.raises(ValueError):inspect_business_draft(Workspace(tmp_path,'other'),err.value.details['draft_id'])
    with pytest.raises(ValueError):inspect_business_draft(ws,'../other')


def test_scalar_type_normalization_preserves_facts_and_audit(tmp_path):
    ws=fixture_workspace(tmp_path)
    ws.observe('https://example.com/limits','Members 20. Battery 5 Ah. Count 10.')
    ws.record('limits','https://example.com/limits','Limits','Members 20. Battery 5 Ah. Count 10.',require_observation=True)
    spec=sheet('20','Members 20')
    spec[0]['fields']={'岗位':{'type':'number'}}
    spec[0]['rows'][0]['cells']['岗位']['source_id']='2'
    export_business_excel(ws,spec)
    meta=read_json(ws.path/'business_artifact.json')
    assert meta['sheets'][0]['rows'][0][1]==20
    assert meta['provenance'][0]['input_value']=='20'
    assert meta['provenance'][0]['format_rule']=='numeric_string_to_number'
    prior=(ws.path/'task_result.xlsx').read_bytes()
    for value in ('20 MB','999',True):
        bad=copy.deepcopy(spec);bad[0]['rows'][0]['cells']['岗位']['value']=value
        with pytest.raises(BusinessExcelValidationError):export_business_excel(ws,bad)
        assert (ws.path/'task_result.xlsx').read_bytes()==prior
    bad=sheet(0,'Count 10');bad[0]['fields']={'岗位':{'type':'text'}}
    bad[0]['rows'][0]['cells']['岗位']['source_id']='2'
    with pytest.raises(BusinessExcelValidationError):export_business_excel(ws,bad)


def test_genuine_free_price_is_allowed_but_all_other_price_evidence_stays_required(tmp_path):
    ws=fixture_workspace(tmp_path)
    text='Free USD 0 per member/month. United States.'
    ws.observe('https://example.com/us/shop',text)
    ws.record('free price','https://example.com/us/shop','Free plan',text,require_observation=True)
    entry={'value':'0','source_id':'2','quote':text,'price':{'currency':'USD','market':'US',
           'type':'current','queried_at':ws.sources()[1]['accessed_at'][:10]}}
    spec=[{'name':'Compare','mode':'comparison','columns':['价格'],'rows':[{'label':'Free plan','cells':{'价格':entry}}]}]
    export_business_excel(ws,spec)
    assert read_json(ws.path/'business_artifact.json')['sheets'][0]['rows'][0][1]==0
    for changes in ({'value':-1},{'quote':'USD 0 per member/month.'},{'price':{**entry['price'],'market':'SG'}}):
        bad=copy.deepcopy(spec);bad[0]['rows'][0]['cells']['价格'].update(changes)
        with pytest.raises(BusinessExcelValidationError):export_business_excel(ws,bad)


def test_compression_keeps_source_json_and_diagnostic_pagination_usable():
    text='原文 "value"\n'+('连续原文\\\n'*4000)
    raw=json.dumps({'source_id':'6','text':text,'offset':120,'total_chars':len(text)},ensure_ascii=False)
    result=json.loads(compact_result(raw,12000))
    assert result['text'] and text.startswith(result['text'])
    assert result['offset']==120 and len(json.dumps(result,ensure_ascii=False))<=12000
    issues={'draft_id':'a'*24,'issue_offset':10,'issues_total':40,
            'issues':[{'sheet':'Data','row':i,'column':'Value','source_context':'x'*1800} for i in range(10)],'next_offset':20}
    result=json.loads(compact_result(json.dumps(issues),6000))
    assert 0<len(result['issues'])<10 and result['next_offset']==10+len(result['issues'])


def test_source_list_pagination_and_text_price_cannot_bypass_quality_checks(settings):
    ws=fixture_workspace(settings.output_dir)
    records=ws.sources()
    atomic_json(ws.path/'sources.json',[{**records[0],'id':str(i)} for i in range(1,27)])
    tool=next(t for t in build_local_tools(ws,settings) if t.name=='list_recorded_sources')
    first=json.loads(tool.invoke({}));second=json.loads(tool.invoke({'offset':first['next_offset']}))
    assert first['total']==26 and len(first['sources'])==12
    assert first['sources'][-1]['id']=='12' and second['sources'][0]['id']=='13'
    with pytest.raises(ValueError):tool.invoke({'offset':-1})
    # Price metadata attached to a generic text field is retained but unverified.
    atomic_json(ws.path/'sources.json',records)
    spec=sheet('研究助理')
    spec[0]['rows'][0]['cells']['岗位']['price']={'currency':'USD','market':'US','type':'current','queried_at':'2026-10-18'}
    export_business_excel(ws,spec);ws.export_excel();ws.export_report('# Result\n研究助理')
    meta=read_json(ws.path/'business_artifact.json')
    assert meta['provenance'][0]['price_status']=='unverified'
    assert meta['quality_notes']
    define_requirements(ws,[{'description':'Workbook','kind':'artifact','artifact':'task_result.xlsx'}])
    record_result(ws,'r1',[])
    assert not evaluate_requirements(ws)['requirements_supported']


def test_numeric_string_precision_is_not_silently_changed(tmp_path):
    ws=fixture_workspace(tmp_path)
    original='12345678901234567890'
    ws.observe('https://example.com/number',original)
    ws.record('number','https://example.com/number','Exact integer',original,require_observation=True)
    spec=sheet(original,original);spec[0]['fields']={'岗位':{'type':'number'}}
    spec[0]['rows'][0]['cells']['岗位']['source_id']='2'
    with pytest.raises(BusinessExcelValidationError):export_business_excel(ws,spec)
    spec[0]['fields']={'岗位':{'type':'text'}}
    export_business_excel(ws,spec)
    assert read_json(ws.path/'business_artifact.json')['sheets'][0]['rows'][0][1]==original
    spec[0]['fields']={'岗位':{'type':'number'}}
    bad=copy.deepcopy(spec);bad[0]['rows'][0]['cells']['岗位']['value']='0.123456789012345678901'
    with pytest.raises(BusinessExcelValidationError):export_business_excel(ws,bad)


def test_repaired_business_error_is_not_replayed_as_current_recovery(tmp_path):
    ws=fixture_workspace(tmp_path)
    with pytest.raises(BusinessExcelValidationError) as err:export_business_excel(ws,sheet('错误概括'))
    output=error_output(classify_error(err.value))
    journal={'bad':{'name':'export_business_excel','output':output,'error_code':'business_validation'}}
    assert recovery_messages(ws,journal)
    repair_business_excel(ws,err.value.details['draft_id'],[{'sheet':'机会对比','row':2,'column':'岗位',
        'entry':{'value':'研究助理','source_id':'1','quote':'岗位：研究助理'}}])
    atomic_json(ws.path/'usage.json',{'requests':29})
    receipt={'task_id':ws.task_id,'business_sha256':read_json(ws.path/'business_artifact.json')['sha256'],
             'requests':29,'resolved_failure_sha256':hashlib.sha256(output.encode()).hexdigest()}
    atomic_json(ws.path/'business_repair_receipt.json',receipt)
    assert recovery_messages(ws,journal)==[]
    atomic_json(ws.path/'usage.json',{'requests':30})
    assert recovery_messages(ws,journal)==[] # an API request debit does not undo a verified repair
    receipt['resolved_failure_sha256']='0'*64
    atomic_json(ws.path/'business_repair_receipt.json',receipt)
    assert recovery_messages(ws,journal) # stale error binding cannot hide a new failure
    journal['fixed']={'name':'repair_business_excel','output':'已生成 task_result.xlsx'}
    assert recovery_messages(ws,journal)==[]
    with pytest.raises(BusinessExcelValidationError):export_business_excel(ws,sheet('新错误'))
    assert recovery_messages(ws,journal) # a new draft always exposes unresolved errors


def test_partial_delivery_does_not_display_a_repaired_old_export_error(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir,'partial')
    atomic_json(ws.path/'result.json',{'task_id':'partial','status':'partial','answer':'仍有需求未满足'})
    atomic_json(ws.path/'tool_journal.json',{'old':{'name':'export_business_excel',
        'error_code':'business_validation','output':'ERROR: old export failure'}})
    data=c.get('/tasks/partial/workspace').json()
    assert data['status']=='partial'
    assert data['failure']['code']=='requirements_incomplete'
    assert '仍有需求未完成' in data['failure']['label']


def test_exhausted_partial_task_can_only_extend_after_explicit_consent(exhausted,monkeypatch):
    from web_agent import api
    c,cfg,ws=exhausted
    result=read_json(ws.path/'result.json');result.update(status='partial',answer='已有文件，需求未满足')
    atomic_json(ws.path/'result.json',result)
    untouched={name:(ws.path/name).read_bytes() for name in ('usage.json','tool_usage.json','sources.json','tool_journal.json')}
    view=c.get('/tasks/budget-case/workspace').json()
    assert view['model_budget']['can_add'] and view['model_budget']['used']==30
    body=proposal(c)
    assert c.post('/tasks/budget-case/budget-resume',json={**body,'confirm_paid_execution':False},headers=ORIGIN).status_code in {409,422}
    assert not (ws.path/'model_budget.json').exists()
    async def offline_execute(*args,**kwargs):return {'status':'interrupted'}
    monkeypatch.setattr(api,'execute',offline_execute)
    response=c.post('/tasks/budget-case/budget-resume',json=body,headers=ORIGIN)
    assert response.status_code==202,response.text
    assert response.json()['budget']['used']==30 and response.json()['budget']['limit']==50
    assert all((ws.path/name).read_bytes()==value for name,value in untouched.items())


class CapturedDecisionReplay(ScriptModel):
    """Recorded failed decisions plus explicit offline test decisions, no LLM."""
    def __init__(self,root,task_id):
        self.calls=0;self.root=root;self.task_id=task_id;self.saw_inspection=False
        self.case=json.loads(CASE.read_text(encoding='utf-8'));self.draft_id='';self.entry=None
    async def ainvoke(self,messages):
        turn=self.calls;self.calls+=1
        ws=Workspace(self.root,self.task_id)
        if turn<3:
            recorded=self.case['calls'][turn]
            args=copy.deepcopy(recorded['arguments'])
            if turn:args['draft_id']=read_json(ws.path/'business_draft.json')['draft_id']
            return call(recorded['detail'],args,f'recorded-{turn}')
        if turn==3:
            self.draft_id=read_json(ws.path/'business_draft.json')['draft_id']
            return call('inspect_business_draft',{'draft_id':self.draft_id},'inspect')
        if turn==4:
            result=json.loads(next(m.content for m in reversed(messages) if m.type=='tool'))
            assert result['issues_total']==1 and result['valid_cells']==19
            self.saw_inspection=True
            self.issue=result['issues'][0]
            draft=read_json(ws.path/'business_draft.json')
            self.entry=copy.deepcopy(draft['sheets'][0]['rows'][self.issue['row']-2]['cells'][self.issue['column']])
            self.keyword=re.search(r'\d+\s*(?:MB|GB)',self.entry['value']).group()
            return call('list_recorded_sources',{'source_id':self.entry['source_id'],'keyword':self.keyword},'read-saved')
        if turn==5:
            result=json.loads(next(m.content for m in reversed(messages) if m.type=='tool'))
            assert result['keyword_found']
            at=result['text'].index(self.keyword)
            self.entry.update(value=self.keyword,quote=result['text'][max(0,at-800):at+250])
            return call('repair_business_excel',{'draft_id':self.draft_id,'patches':[{
                'sheet':self.issue['sheet'],'row':self.issue['row'],'column':self.issue['column'],
                'entry':self.entry}]},'repair-one')
        if turn==6:return call('export_excel',id='sources')
        if turn==7:return call('export_report',{'markdown_report':'# 离线流程回放\n\n使用保存的公开来源检查和修正业务表；仍保留未核实项，最终结论需人工核对。'},'report')
        raise AssertionError('Unexpected extra model turn / repeat loop')


@pytest.mark.asyncio
async def test_real_runner_exports_captured_material_without_repeating_legacy_failures(settings,monkeypatch):
    # Any accidental network/model/browser construction must fail this test.
    import web_agent.runner as runner
    def forbidden(*args,**kwargs):raise AssertionError('Paid model or live browser is forbidden')
    monkeypatch.setattr(runner,'ChatOpenAI',forbidden)
    @asynccontextmanager
    async def offline_browser(cfg,ws):
        saved_workspace(cfg.output_dir,ws.task_id)
        yield []
    case=json.loads(CASE.read_text(encoding='utf-8'))
    model=ScriptModel([call('export_business_excel',case['calls'][0]['arguments'],'captured-export'),
                       call('export_report',{'markdown_report':'# 离线流程回放\n\n现有资料已整理，未核实字段待复核。'},'report')])
    cfg=replace(settings,max_model_calls=12,task_timeout=30)
    result=await run_task('Use captured public observations to validate and repair a comparison workbook',
        'captured',cfg,model=model,browser_factory=offline_browser)
    assert model.calls==2
    assert result.status=='needs_review' and result.metrics['requests']==2
    assert result.metrics['errors']==0
    ws=Workspace(cfg.output_dir,'captured')
    assert verify_business_excel(ws)==[] and ws.verify_delivery()['artifacts_valid']
    meta=read_json(ws.path/'business_artifact.json')
    assert str(meta['sheets'][0]['rows'][0][2]).startswith('0') and meta['structured_review']
    assert meta['quality_notes'] # missing values were disclosed, not invented
    journal=read_json(ws.path/'tool_journal.json')
    assert len(journal)==2 and 'export_business_excel'==journal['captured-export']['name']
    assert not result.completion['delivery']['semantic_accepted']
