"""Real failed PC draft + offline end-to-end delivery/state regressions."""
import copy,json
from pathlib import Path
from dataclasses import replace
from contextlib import asynccontextmanager
import pytest
from langchain_core.tools import tool
from langchain_core.messages import AIMessage
from test_graph import settings,ScriptModel,call,empty_browser
from test_web_workbench import client
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json,read_trace
from web_agent.tools import build_local_tools
from web_agent.delivery_compiler import export_compiled
from web_agent.business_excel import verify_business_excel,BusinessExcelValidationError
from web_agent.delivery_status import aggregate_delivery
from web_agent.completion import evaluate_requirements,automatic_checks
from web_agent.delivery_schema import normalize_entry,numbers
from web_agent.runner import run_task
from web_agent.execution_budget import handoff

CASE=Path(__file__).parent/'fixtures/pc_delivery_case.json'


def test_deterministic_delivery_needs_two_reserved_turns(tmp_path):
    ws=Workspace(tmp_path,'no-sources')
    assert handoff(ws,30,19)['phase']=='research'
    assert handoff(ws,30,20)['phase']=='summary'
    last=handoff(ws,30,28)
    assert last['phase']=='delivery' and last['local_only']
    assert last['allocation']=={'research':20,'summary':8,'delivery':2}

def captured(root,key='pc'):
    data=json.loads(CASE.read_text(encoding='utf-8'));ws=Workspace(root,key)
    atomic_json(ws.path/'sources.json',data['sources'])
    (ws.path/'observations').mkdir()
    for obs in data['observations']:atomic_json(ws.path/'observations'/f"{obs['id']}.json",obs)
    return ws,data

def simple(ws,value,quote=None,**extra):
    return [{'name':'Facts','mode':'comparison','columns':['规格归纳'],'rows':[{'label':'Item','cells':{
        '规格归纳':{'value':value,'provenance_ref':'1',**({'quote':quote} if quote else {}),**extra}}}]}]

def source_ws(root):
    ws=Workspace(root,'source')
    text='Screen 6 inches. Date Oct 17, 2026. Buy USD 199.00. United States.'
    ws.observe('https://example.com/us/shop',text)
    ws.record('specification','https://example.com/us/shop','observed specification',text,require_observation=True)
    return ws


@pytest.mark.asyncio
async def test_real_39_issue_draft_exports_through_actual_tool(settings):
    ws,data=captured(settings.output_dir)
    tools={t.name:t for t in build_local_tools(ws,settings)}
    original=copy.deepcopy(data['sheets'])
    result=await tools['export_business_excel'].ainvoke({'sheets':data['sheets']})
    assert result.startswith('已生成') and data['sheets']==original
    assert verify_business_excel(ws)==[] and not (ws.path/'business_draft.json').exists()
    meta=read_json(ws.path/'business_artifact.json')
    assert '组件' not in meta['sheets'][0]['columns']
    assert len(meta['sheets'][0]['rows'])==9 and '核验说明' in meta['sheets'][0]['columns']
    assert meta['structured_review']
    assert '未核实' in meta['sheets'][0]['rows'][-1][-1] and '已按来源核对' not in meta['sheets'][0]['rows'][-1][-1]
    prices=[p for p in meta['provenance'] if p['column']=='当前价格(USD)' and p['label']!='显示器']
    assert [p['normalized_value'] for p in prices]==[138.72,88.96,449.99,789.99,119.99,111.67,49.99,139.99]
    assert all(p['status']=='review_required' and 'comparable_group' not in p for p in prices)
    assert all(p['queried_at']=='2026-10-18' for p in prices)
    dates=[p for p in meta['provenance'] if p['column']=='来源日期' and p['label']!='显示器']
    assert all(p['status']=='verified' and p['normalized_value']=='2026-10-17' for p in dates)
    assert any(p['status']=='unverified' and p['label']=='显示器' for p in meta['provenance'])
    assert all('官方未公布' not in p.get('note','') for p in meta['provenance'])


def test_final_facts_need_no_verbatim_but_always_require_review(tmp_path):
    ws=source_ws(tmp_path)
    export_compiled(ws,simple(ws,'6 英寸屏幕'),allow_structured_review=True)
    audit=read_json(ws.path/'business_artifact.json')['provenance'][0]
    assert audit['normalized_value']=='6 英寸屏幕' and audit['status']=='review_required'
    assert audit['source_id']=='1' and audit['quote']==ws.sources()[0]['quote']
    ws.export_excel();ws.export_report('# Facts\nStructured proposal, not independently certified.')
    assert '待人工复核' in (ws.path/'research_report.md').read_text(encoding='utf-8')
    assert not automatic_checks(ws)[0]


def test_unknown_source_still_blocks_and_strict_mode_remains_strict(tmp_path):
    ws=source_ws(tmp_path)
    bad=simple(ws,'translated value');bad[0]['rows'][0]['cells']['规格归纳']['provenance_ref']='999'
    with pytest.raises(BusinessExcelValidationError):export_compiled(ws,bad,allow_structured_review=True)
    assert not (ws.path/'task_result.xlsx').exists()
    with pytest.raises(BusinessExcelValidationError):export_compiled(ws,simple(ws,'translated value'))


def test_unsupported_number_becomes_explicit_unknown_never_invented(tmp_path):
    ws=source_ws(tmp_path)
    spec=[{'name':'Numbers','mode':'comparison','columns':['屏幕尺寸'],'rows':[{'label':'A','cells':{
        '屏幕尺寸':{'value':99,'source_id':'1','quote':'Screen 6 inches.'}}}]}]
    export_compiled(ws,spec,allow_structured_review=True)
    p=read_json(ws.path/'business_artifact.json')['provenance'][0]
    assert p['status']=='unverified' and p['normalized_value'] is None
    assert '待人工复核' in p['note'] and p['input_value']==99


def test_missing_reason_defaults_to_not_found_not_officially_undisclosed(tmp_path):
    ws=source_ws(tmp_path)
    export_compiled(ws,simple(ws,None),allow_structured_review=True)
    audit=read_json(ws.path/'business_artifact.json')['provenance'][0]
    assert audit['status']=='unverified' and '页面未找到' in audit['note']


def test_date_normalization_and_sentence_final_decimal_are_deterministic():
    assert numbers('US launch starting price was $429.00.')==[429.0]
    value,audit=normalize_entry({'value':'2026-10-17','quote':'Checked Oct 17, 2026'}, {'type':'date','required':True},None)
    assert value=='2026-10-17' and audit['raw_value']=='Oct 17, 2026'
    with pytest.raises(ValueError):normalize_entry({'value':'2026-10-18','quote':'Checked Oct 17, 2026'}, {'type':'date','required':True},None)


def test_delivery_aggregation_checks_files_and_does_not_accept_facts(tmp_path):
    ws=source_ws(tmp_path)
    export_compiled(ws,simple(ws,'中文归纳'),allow_structured_review=True)
    ws.export_excel();ws.export_report('# Delivered\nReview is required.')
    ws.log('tool_error','ERROR: {"message":"old recoverable error"}',call_id='same')
    ws.log('tool_error','ERROR: {"message":"duplicate error event"}',call_id='same')
    ws.log('tool_error','ERROR: '+json.dumps({'message':'bounded readable message','diagnostics':'x'*6000}),call_id='truncated')
    result=aggregate_delivery(ws,'budget_exceeded',ws.verify_delivery(),evaluate_requirements(ws))
    assert result['usable'] and result['outcome']=='partial'
    assert result['execution_warning_count']==2 and not result['semantic_accepted']
    assert result['warnings'][1]['message']=='bounded readable message'
    assert not any(w['blocking'] for w in result['warnings'])
    (ws.path/'task_result.xlsx').write_bytes(b'corrupted file')
    broken=aggregate_delivery(ws,'budget_exceeded',ws.verify_delivery(),evaluate_requirements(ws))
    assert not broken['usable'] and broken['outcome']=='failed'


def test_recovered_tool_errors_are_not_counted_as_red_warnings(tmp_path):
    ws=source_ws(tmp_path)
    ws.log('tool_start','browser_navigate',call_id='bad',arguments={'url':'https://example.org/a'})
    ws.log('tool_error','ERROR: {"message":"bad dropdown option"}',call_id='bad',code='invalid_input')
    ws.log('tool_start','browser_navigate',call_id='good',arguments={'url':'https://example.org/b'})
    ws.log('tool_end','browser_navigate',call_id='good',output_preview='ok')
    ws.log('tool_start','export_report',call_id='report-bad',arguments={})
    ws.log('tool_error','ERROR: {"message":"markdown_report missing"}',call_id='report-bad',code='invalid_input')
    result=aggregate_delivery(ws,'needs_review',ws.verify_delivery(),evaluate_requirements(ws))
    assert result['execution_warning_count']==1
    assert result['recovered_execution_warning_count']==1
    assert all(w.get('call_id')!='bad' for w in result['warnings'])
    assert any(w.get('call_id')=='bad' for w in result['recovered_execution_warnings'])


def test_requirement_recovery_requires_same_requirement_id(tmp_path):
    ws=source_ws(tmp_path)
    ws.log('tool_start','record_requirement_result',call_id='a',arguments={'requirement_id':'r1'})
    ws.log('tool_error','ERROR: {"message":"bad excerpt"}',call_id='a',code='evidence_mismatch')
    ws.log('tool_start','record_requirement_result',call_id='b',arguments={'requirement_id':'r2'})
    ws.log('tool_end','record_requirement_result',call_id='b',output_preview='ok')
    first=aggregate_delivery(ws,'needs_review',ws.verify_delivery(),evaluate_requirements(ws))
    assert first['execution_warning_count']==1
    assert first['recovered_execution_warning_count']==0
    ws.log('tool_start','record_requirement_result',call_id='c',arguments={'requirement_id':'r1'})
    ws.log('tool_end','record_requirement_result',call_id='c',output_preview='ok')
    second=aggregate_delivery(ws,'needs_review',ws.verify_delivery(),evaluate_requirements(ws))
    assert second['execution_warning_count']==0
    assert second['recovered_execution_warning_count']==1


def test_inspection_recovery_pair_counts_as_corrected(tmp_path):
    ws=source_ws(tmp_path)
    ws.log('tool_start','inspect_business_draft',call_id='a',arguments={'draft_id':'stale'})
    ws.log('tool_error','ERROR: {"message":"draft missing"}',call_id='a',code='invalid_input')
    ws.log('tool_start','inspect_delivery',call_id='b',arguments={})
    ws.log('tool_end','inspect_delivery',call_id='b',output_preview='current state')
    result=aggregate_delivery(ws,'needs_review',ws.verify_delivery(),evaluate_requirements(ws))
    assert result['execution_warning_count']==0
    assert result['recovered_execution_warning_count']==1


@pytest.mark.asyncio
async def test_runner_delivers_at_budget_ceiling_without_another_model(settings):
    ws,data=captured(settings.output_dir,'last-turn')
    from web_agent.delivery_pipeline import stage_business_rows
    stage_business_rows(ws,data['sheets'])
    atomic_json(ws.path/'usage.json',{'requests':29,'input_tokens':0,'output_tokens':0})
    model=ScriptModel([call('export_business_excel',{'sheets':data['sheets']},'last-export')])
    result=await run_task(data['task'],'last-turn',replace(settings,max_model_calls=30),model=model,browser_factory=empty_browser)
    assert model.calls==0 and result.metrics['requests']==29
    assert result.status=='needs_review' and result.completion['delivery']['usable']
    assert result.completion['delivery']['outcome']=='needs_review'
    assert verify_business_excel(ws)==[] and not result.completion['delivery']['semantic_accepted']


@pytest.mark.asyncio
async def test_closing_gate_enforces_tools_even_if_model_calls_hidden_browser(settings):
    ws,data=captured(settings.output_dir,'closing-gate')
    from web_agent.delivery_pipeline import stage_business_rows
    quote=ws.sources()[0]['quote']
    stage_business_rows(ws,simple(ws,quote.splitlines()[0][:40],quote))
    # Call 28 is within the actual restricted final-delivery window.
    atomic_json(ws.path/'usage.json',{'requests':27,'input_tokens':0,'output_tokens':0})
    invoked=[]
    @asynccontextmanager
    async def fake_browser(cfg,workspace):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Must not dispatch during closing."""
            invoked.append(url);raise AssertionError('research must have stopped')
        yield [navigate]
    model=ScriptModel([call('browser_navigate',{'url':'https://example.com/new'},'hidden-browser'),
                       call('export_business_excel',{'sheets':data['sheets']},'export'),
                       call('export_report',{'markdown_report':'# Current results\nReview these source-linked proposals.'},'report')])
    result=await run_task(data['task'],'closing-gate',replace(settings,max_model_calls=30),model=model,browser_factory=fake_browser)
    assert not invoked and model.calls==1
    assert read_json(ws.path/'tool_journal.json')['hidden-browser']['error_code']=='delivery_phase'
    assert result.completion['delivery']['usable'] and result.metrics['requests']==28


def test_api_exposes_delivery_state_separately_from_budget_state(client):
    c,cfg=client;ws=source_ws(cfg.output_dir)
    atomic_json(ws.path/'task.json',{'task':'Source-linked structured facts'})
    export_compiled(ws,simple(ws,'中文归纳'),allow_structured_review=True)
    ws.export_excel();ws.export_report('# Results\nReview needed.')
    atomic_json(ws.path/'result.json',{'status':'budget_exceeded','answer':'budget ended'})
    data=c.get('/tasks/source/workspace').json()
    assert data['status']=='budget_exceeded'
    assert data['delivery']['outcome']=='partial' and data['delivery']['usable']
    assert not data['delivery']['semantic_accepted'] and not data['capabilities']['can_accept']
