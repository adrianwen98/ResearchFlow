"""Cross-task deterministic delivery compilation and disclosed missing policy."""
import copy,json,hashlib
from contextlib import asynccontextmanager
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage
from test_graph import settings,ScriptModel,call
from test_export_correction import fixture_workspace,sheet
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json
from web_agent.delivery_compiler import compile_comparisons,export_compiled
from web_agent.business_excel import export_business_excel,verify_business_excel,BusinessExcelValidationError
from web_agent.completion import define_requirements,record_result,evaluate_requirements,completion_decision,accept_delivery,review_binding
from web_agent.runner import run_task

CASE=Path(__file__).parent/'fixtures/reliable_comparison_case.json'

def captured(root,key='new_case'):
    data=json.loads(CASE.read_text('utf-8'));ws=Workspace(root,key)
    atomic_json(ws.path/'sources.json',data['sources']);(ws.path/'observations').mkdir()
    for obs in data['observations']:atomic_json(ws.path/'observations'/f"{obs['id']}.json",obs)
    return ws,data


def test_real_first_attempt_exports_without_model_repair_and_price_is_typed(tmp_path):
    ws,data=captured(tmp_path);original=copy.deepcopy(data['sheets'])
    export_compiled(ws,data['sheets'])
    assert data['sheets']==original and verify_business_excel(ws)==[]
    meta=read_json(ws.path/'business_artifact.json')
    assert len(meta['sheets'][0]['rows'])==2
    assert len(meta['sheets'][0]['columns'])>20
    prices=[p for p in meta['provenance'] if p.get('currency')]
    assert len(prices)==2 and {p['normalized_value'] for p in prices}=={379,239}
    assert all(p['currency']=='USD' and p['market']=='SG' and p['price_type']=='current' for p in prices)
    assert not any(p.get('price_status')=='unverified' for p in meta['provenance'])
    assert any(p['column'].endswith('Aperture') and p['normalized_value']=='f/2.0' for p in meta['provenance'])
    assert not any(p.get('normalized_value')=='3-axis gimbal mechanical stabilization' for p in meta['provenance'])
    for p in meta['provenance']:
        if p.get('status')=='verified':assert str(p['normalized_value']) or p['normalized_value']==0
    assert meta['review_disclosed_missing'] and meta['normalizations']
    assert not (ws.path/'business_draft.json').exists()
    assert not (ws.path/'usage.json').exists()


def test_matrix_detection_is_generic_and_keeps_other_table_modes(tmp_path):
    ws=Workspace(tmp_path,'generic')
    text='Battery 4 Ah. Weight 900 g. Bitrate 50 Mbps. Screen 15 inches.'
    ws.observe('https://example.com/specs',text);ws.record('source','https://example.com/specs','specifications',text,require_observation=True)
    specs=[{'name':'Products','mode':'comparison','columns':['Product Alpha','Product Beta'],
      'rows':[{'label':label,'cells':{c:{'value':value,'source_id':'1','quote':quote} for c in ['Product Alpha','Product Beta']}}
      for label,value,quote in [('电池容量','4 Ah','Battery 4 Ah'),('重量','900 g','Weight 900 g'),('最大码率','50 Mbps','Bitrate 50 Mbps')]]}]
    export_compiled(ws,specs)
    rows=read_json(ws.path/'business_artifact.json')['sheets'][0]['rows']
    assert rows==[['Product Alpha',4000,900,50],['Product Beta',4000,900,50]]
    source_spec=[{'name':'Raw','mode':'table','filename':'public_123.csv'}]
    assert compile_comparisons(ws,source_spec)[0]==source_spec


def test_invalid_sources_values_prices_and_long_descriptions_remain_rejected(tmp_path):
    ws,data=captured(tmp_path)
    bad=copy.deepcopy(data['sheets']);bad[0]['rows'][0]['cells']['Osmo Pocket 3']['quote']='fabricated unseen text'
    with pytest.raises(ValueError):export_compiled(ws,bad)
    assert not (ws.path/'task_result.xlsx').exists()
    bad=copy.deepcopy(data['sheets']);bad[0]['rows'][-1]['cells']['Osmo Pocket 3']['price']['market']='CN'
    with pytest.raises(BusinessExcelValidationError):export_compiled(ws,bad)
    assert not (ws.path/'task_result.xlsx').exists()
    ws2=fixture_workspace(tmp_path,'plain')
    with pytest.raises(BusinessExcelValidationError):export_compiled(ws2,sheet('no evidenced value'))
    assert not (ws2.path/'task_result.xlsx').exists()


def model_requirement(ws):
    define_requirements(ws,[{'description':'Compare products and disclose limitations','kind':'research'}])
    record_result(ws,'r1',['1'],'# Comparison')


def test_disclosed_unknown_goes_to_human_review_but_never_automatic_success(tmp_path):
    ws,data=captured(tmp_path);export_compiled(ws,data['sheets'])
    ws.export_excel();ws.export_report('# Comparison\nReport limitations are disclosed below.')
    model_requirement(ws)
    check=evaluate_requirements(ws)
    assert check['requirements_supported'] and check['quality_warnings'] and not check['quality_blockers']
    assert completion_decision(ws)['status']=='needs_review'
    assert not (ws.path/'acceptance.json').exists()
    atomic_json(ws.path/'result.json',{'task_id':ws.task_id,'status':'needs_review'})
    with pytest.raises(ValueError,match='明确确认'):accept_delivery(ws,['r1'],review_binding(ws))
    assert not (ws.path/'acceptance.json').exists()
    accept_delivery(ws,['r1'],review_binding(ws),accept_limitations=True)
    receipt=read_json(ws.path/'acceptance.json')
    assert receipt['verification_mode']=='human_acceptance' and receipt['accepted_limitations']==check['quality_warnings']


def test_missing_disclosure_stale_report_and_explicit_user_requirements_stay_blocked(tmp_path):
    ws,data=captured(tmp_path);export_compiled(ws,data['sheets'])
    ws.export_excel();ws.export_report('# Comparison\nReport.')
    model_requirement(ws)
    path=ws.path/'research_report.md';original=path.read_bytes()
    path.write_text('# Comparison\nNothing disclosed.',encoding='utf-8')
    assert not evaluate_requirements(ws)['requirements_supported']
    path.write_bytes(original)
    define_requirements(ws,[{'description':'Explicit user requires verified waterproof information','kind':'research'}],origin='user')
    record_result(ws,'r2',['1'],'# Comparison')
    assert not evaluate_requirements(ws)['requirements_supported']
    assert completion_decision(ws)['status']=='partial'


def test_legacy_strict_export_semantics_do_not_silently_change(tmp_path):
    ws=fixture_workspace(tmp_path)
    spec=sheet();spec[0]['rows'][0]['cells']['岗位']={'value':None,'reason':'Not verified'}
    export_business_excel(ws,spec);ws.export_excel();ws.export_report('# Comparison')
    model_requirement(ws)
    assert not evaluate_requirements(ws)['requirements_supported']


@pytest.mark.asyncio
async def test_real_runner_delivers_captured_first_input_without_any_failed_tool(settings):
    ws,data=captured(settings.output_dir,'driver')
    @asynccontextmanager
    async def local_browser(cfg,workspace):yield []
    model=ScriptModel([AIMessage(content='',tool_calls=[
        {'name':'set_requirements','args':{'items':[{'description':'Compare products and disclose limitations','kind':'research'}]},'id':'requirements','type':'tool_call'},
        {'name':'export_business_excel','args':{'sheets':data['sheets']},'id':'business','type':'tool_call'},
        {'name':'export_excel','args':{},'id':'sources','type':'tool_call'},
        {'name':'export_report','args':{'markdown_report':'# Comparison\nUse-case comparison; missing details disclosed below.'},'id':'report','type':'tool_call'}]),
        call('record_requirement_result',{'requirement_id':'r1','source_ids':['1','2'],'report_excerpt':'Use-case comparison; missing details disclosed below.'},'record')])
    result=await run_task('Compare products using official data','driver',settings,model=model,browser_factory=local_browser)
    assert result.status=='needs_review' and result.metrics['errors']==0
    assert result.metrics['requests']==2 and model.calls==2
    assert result.completion['quality_warnings'] and result.completion['requirements_supported']
    assert not (ws.path/'acceptance.json').exists()
    assert all(not row.get('error_code') for row in read_json(ws.path/'tool_journal.json').values())


def test_reexport_rebinds_only_unchanged_verified_lines_not_missing_or_changed_claims(tmp_path):
    from web_agent.delivery_inspection import inspect_delivery
    ws=fixture_workspace(tmp_path);ws.export_excel();ws.export_report('# Report\nExact original conclusion.\nOther original line.')
    define_requirements(ws,[{'description':'Original conclusion','kind':'research'}, {'description':'Other fact','kind':'research'}, {'description':'Not yet verified','kind':'research'}])
    report=inspect_delivery(ws,'Exact original conclusion')['report']
    record_result(ws,'r1',['1'],report_line_ids=[report['lines'][0]['line_id']],report_sha256=report['sha256'])
    report=inspect_delivery(ws,'Other original line')['report']
    record_result(ws,'r2',['1'],report_line_ids=[report['lines'][0]['line_id']],report_sha256=report['sha256'])
    before=read_json(ws.path/'requirements.json')
    ws.export_report('# Report\nNew introduction.\nExact original conclusion.\nChanged other fact.')
    after=read_json(ws.path/'requirements.json')
    assert after[0]['result']['report_sha256']!=before[0]['result']['report_sha256']
    assert after[0]['result']['report_excerpts']==before[0]['result']['report_excerpts']
    assert after[1]['result']==before[1]['result']
    assert after[2]['result']['status']=='pending'
    checks=evaluate_requirements(ws)['requirements']
    assert checks[0]['passed'] and not checks[1]['passed'] and not checks[2]['passed']


def test_fragment_parser_does_not_split_aspect_ratio_or_url_colons():
    from web_agent.delivery_compiler import verbatim_parts
    assert verbatim_parts('4K (16:9): 3840×2160@120fps\n1080p: 1920×1080@240fps')==[
        ('4K (16:9)','3840×2160@120fps'),('1080p','1920×1080@240fps')]
    assert verbatim_parts('Aspect ratio: 16:9\nSource: https://example.com/specs')[0]==('Aspect ratio','16:9')


def test_service_plan_matrix_uses_price_metadata_without_device_field_names(tmp_path):
    ws=Workspace(tmp_path,'plans')
    text='Basic plan free 0 人民币。Member limit 10 users. Upload limit 20 MB.'
    ws.observe('https://example.com/cn/store/plans',text)
    source=ws.record('Official plan','https://example.com/cn/store/plans','Plan information',text,require_observation=True)
    price={'currency':'CNY','market':'CN','type':'current','queried_at':source['accessed_at'][:10]}
    specs=[{'name':'Plans','mode':'comparison','columns':['Service Alpha','Service Beta'],'fields':{'Service Alpha':{'type':'text'},'Service Beta':{'type':'text'}},
        'rows':[{'label':label,'cells':{name:dict(entry) for name in ['Service Alpha','Service Beta']}}
        for label,entry in [('基础方案',{'value':0,'quote':'Basic plan free 0 人民币','source_id':'1','price':price}),
                            ('协作人数',{'value':'10 users','quote':'Member limit 10 users','source_id':'1'}),
                            ('附件上限',{'value':'20 MB','quote':'Upload limit 20 MB','source_id':'1'})]]}]
    export_compiled(ws,specs)
    meta=read_json(ws.path/'business_artifact.json')
    assert len(meta['sheets'][0]['rows'])==2
    assert len([p for p in meta['provenance'] if p.get('currency')=='CNY' and p['normalized_value']==0])==2
    assert not any(p.get('price_status')=='unverified' for p in meta['provenance'])


from test_web_workbench import client


def test_real_workbook_preview_includes_price_type_and_query_date(client):
    c,cfg=client
    ws,data=captured(cfg.output_dir,'wide_comparison')
    atomic_json(ws.path/'task.json',{'task':'Comparison preview'})
    export_compiled(ws,data['sheets'])
    response=c.get('/tasks/wide_comparison/workbook')
    assert response.status_code==200
    preview=response.json();table=preview['sheets'][0]
    assert len(table['columns'])==table['column_count']==32
    assert '价格类型' in table['columns'] and '查询日期' in table['columns']
    assert preview['max_columns']==40 and not table['truncated']
