"""Captured 2026-10-19 failure: original JSON + exact saved Apple observations."""
import copy
import json
import socket
from pathlib import Path
import pytest
from openpyxl import load_workbook
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json
from web_agent.business_contract import comparison_contract
from web_agent.delivery_pipeline import stage_business_rows,finalize_delivery
from web_agent.delivery_compiler import export_compiled,compile_comparisons
from web_agent.business_excel import export_business_excel,verify_business_excel,BusinessExcelValidationError
from web_agent.delivery_inspection import DeliveryInputError
from web_agent.delivery_schema import normalize_entry
from web_agent.completion import evaluate_requirements
from web_agent.harness import classify_error,error_output
from web_agent.tools import build_local_tools
from test_graph import settings

CASE=Path(__file__).parent/'fixtures/iphone_contract_case.json'


@pytest.fixture
def captured(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Offline regression attempted network IO')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    data=json.loads(CASE.read_text(encoding='utf8'))
    ws=Workspace(tmp_path,'captured-iphone-contract')
    atomic_json(ws.data_file,data['sources'])
    (ws.path/'observations').mkdir()
    for obs in data['observations']:atomic_json(ws.path/'observations'/f"{obs['id']}.json",obs)
    return ws,data


def original(data,index):return copy.deepcopy(data['calls'][index]['arguments']['sheets'])


def corrected(data):
    # Fix only the unsupported CPU paraphrase using a literal from its actual
    # submitted quote. No expected fact is substituted from model memory.
    sheets=original(data,3)
    for row in sheets[0]['rows']:
        e=row['cells']['CPU核心'];e['value']=e['quote'].split('，')[0]
    return sheets


@pytest.mark.parametrize('index',[0,1,2])
def test_actual_failed_flat_parameters_are_canonical_and_diagnosed_at_save(captured,index):
    ws,data=captured;sheets=original(data,index);unchanged=copy.deepcopy(sheets)
    result=stage_business_rows(ws,sheets)
    assert sheets==unchanged
    assert result['saved_rows']==2
    assert result['validation']=={'status':'needs_review','verified_cells':10,'pending_cells':2}
    assert {(p['column'],p['status']) for p in result['review_items']}=={('CPU核心','review_required')}
    persisted=read_json(ws.path/'business_rows.json')
    assert [r['label'] for r in persisted['sheets'][0]['rows']]==['iPhone 17','iPhone 17 Pro']
    assert set(persisted['sheets'][0]['columns'])=={'屏幕尺寸(英寸)','屏幕类型','芯片型号','CPU核心','GPU核心','神经网络引擎核心'}
    assert not (ws.path/'task_result.xlsx').exists()
    finalize_delivery(ws,'offline replay')
    assert not verify_business_excel(ws)
    audits=read_json(ws.path/'business_artifact.json')['provenance']
    assert sum(p['status']=='verified' for p in audits)==10
    assert all(p['status']=='verified' for p in audits if p['column'] in {'屏幕尺寸(英寸)','屏幕类型','芯片型号'})


def test_actual_export_no_longer_validates_system_metadata_as_facts(captured):
    ws,data=captured
    export_compiled(ws,original(data,3),allow_structured_review=True)
    meta=read_json(ws.path/'business_artifact.json')
    assert len(meta['provenance'])==12
    assert {p['column'] for p in meta['provenance'] if p['status']!='verified'}=={'CPU核心'}
    assert any(c['rule']=='system_metadata_to_provenance' for c in meta['normalizations'])
    for p in meta['provenance']:
        source=next(s for s in ws.sources() if s['id']==p['source_id'])
        assert p['url']==source['url'] and p['observation_id']==source['observation_id']
        ws.evidence_for(p['url'],p['quote'],p['observation_id'])


def assert_verified(ws):
    meta=read_json(ws.path/'business_artifact.json');audits=meta['provenance']
    assert len(audits)==12 and all(p['status']=='verified' for p in audits)
    facts={(p['label'],p['column']):p['normalized_value'] for p in audits}
    for label,chip in [('iPhone 17','A19 芯片'),('iPhone 17 Pro','A19 Pro 芯片')]:
        assert facts[label,'屏幕尺寸(英寸)']==6.3
        assert facts[label,'芯片型号']==chip
        assert facts[label,'屏幕类型']=='超视网膜 XDR 显示屏，OLED 全面屏'
    for p in audits:ws.evidence_for(p['url'],p['quote'],p['observation_id'])
    displays=[p for p in audits if p['column']=='屏幕类型']
    assert all(p['rule']=='display_extractive_components' for p in displays)
    assert all(p['evidence_parts']==['超视网膜 XDR 显示屏','OLED 全面屏'] for p in displays)
    assert all(p['raw_value']==p['quote'] and '\n' in p['raw_value'] for p in displays)
    assert not meta['quality_notes'] and not verify_business_excel(ws)
    book=load_workbook(ws.path/'task_result.xlsx',read_only=True)
    try:
        assert book.sheetnames==['iPhone17对比','Provenance','Metadata']
        rows=list(book['iPhone17对比'].values)
        assert rows[1][:4]==('iPhone 17',6.3,'超视网膜 XDR 显示屏，OLED 全面屏','A19 芯片')
        assert rows[2][:4]==('iPhone 17 Pro',6.3,'超视网膜 XDR 显示屏，OLED 全面屏','A19 Pro 芯片')
    finally:book.close()


def test_corrected_real_parameters_verify_all_facts_and_preserve_unmet_requirements(captured):
    ws,data=captured
    # The six actual requirements include architectural review; a spreadsheet
    # cannot establish them. Preserve the true missing results byte for byte.
    atomic_json(ws.path/'requirements.json',data['requirements'])
    original_requirements=(ws.path/'requirements.json').read_bytes()
    result=stage_business_rows(ws,corrected(data))
    assert result['validation']=={'status':'verified','verified_cells':12,'pending_cells':0}
    stored=read_json(ws.path/'business_rows.json')
    assert len(stored['provenance'])==12 and all(p['status']=='verified' for p in stored['provenance'])
    assert '\n' in stored['sheets'][0]['rows'][0]['cells']['屏幕类型']['quote']
    receipt=finalize_delivery(ws,'offline replay')
    assert not receipt['errors'] and receipt['additional_model_requests']==0
    assert_verified(ws)
    md=(ws.path/'research_report.md').read_text(encoding='utf8')
    assert 'A19 芯片' in md and 'A19 Pro 芯片' in md and md.count('6.3')>=2
    assert '## 尚未完成的需求' in md
    check=evaluate_requirements(ws)
    # Research/explicitly unmet requirements remain unmet; the generated report
    # artifact alone may be reconciled after full delivery integrity checks.
    assert check['missing_requirements']==['r1','r2','r3','r4','r5']
    report_check = next(r for r in check['requirements'] if r['id'] == 'r6')
    assert report_check['status'] == 'satisfied' and report_check['passed']
    report_requirement = next(r for r in read_json(ws.path/'requirements.json') if r['id'] == 'r6')
    assert report_requirement['kind'] == 'artifact'
    assert report_requirement['artifact'] == 'task_result.xlsx'
    assert report_requirement['result'].get('verified_by') == 'host_artifact_integrity'
    assert not check['requirements_supported']
    # 研究需求必须保持原样，不能因为 Excel 生成而自动通过
    updated = read_json(ws.path/'requirements.json')
    assert len(updated) == len(data['requirements']) == 6
    assert updated[:5] == data['requirements'][:5]

# 只有已通过完整性校验的 r6 文件需求可以自动更新
    assert updated[5]['id'] == 'r6'
    assert updated[5]['result']['status'] == 'satisfied'
    assert updated[5]['result']['verified_by'] == 'host_artifact_integrity'


@pytest.mark.parametrize('exporter',[export_compiled,export_business_excel])
def test_same_canonical_contract_on_both_export_paths(captured,exporter):
    ws,data=captured
    exporter(ws,corrected(data))
    assert_verified(ws)


@pytest.mark.parametrize('problem,code',[('mode','invalid_mode'),('empty','empty_label'),('duplicate','duplicate_label'),('field','field_definition'),('cells','cells_columns')])
def test_structured_schema_diagnostics_on_both_paths_and_atomic_rejection(captured,problem,code):
    ws,data=captured;sheets=corrected(data)
    stage_business_rows(ws,sheets);saved=(ws.path/'business_rows.json').read_bytes()
    if problem=='mode':sheets[0]['mode']='compare'
    if problem=='empty':sheets[0]['rows'][0]['label']='  '
    if problem=='duplicate':sheets[0]['rows'][1]['label']='iPhone 17'
    if problem=='field':sheets[0]['fields']['芯片型号']='value'
    if problem=='cells':sheets[0]['rows'][0]['cells'].pop('芯片型号')
    for action in (stage_business_rows,export_compiled,export_business_excel):
        with pytest.raises(DeliveryInputError) as caught:action(ws,sheets)
        diagnostic=json.loads(error_output(classify_error(caught.value)).removeprefix('ERROR: '))['diagnostics']
        assert diagnostic['kind']=='comparison_schema'
        assert diagnostic['issues'][0]['code']==code and diagnostic['issues'][0]['path']
        assert diagnostic['expected']['rows'][0]['label']
    assert (ws.path/'business_rows.json').read_bytes()==saved


def test_stable_entity_labels_and_idempotent_upsert_independent_of_batch_order(captured):
    ws,data=captured;sheets=corrected(data)
    for row,identifier in zip(sheets[0]['rows'],['17','17pro']):
        row.pop('label');row['entity']={'id':identifier,'name':'iPhone'}
    stage_business_rows(ws,sheets)
    for row in reversed(sheets[0]['rows']):
        single=copy.deepcopy(sheets);single[0]['rows']=[row]
        stage_business_rows(ws,single)
    rows=read_json(ws.path/'business_rows.json')['sheets'][0]['rows']
    assert {r['label'] for r in rows}=={'iPhone [17]','iPhone [17pro]'} and len(rows)==2


@pytest.mark.parametrize('change',['fake_url','fake_id','invented_display','wrong_observation','wrong_chip'])
def test_forged_or_unsupported_values_never_become_verified(captured,change):
    ws,data=captured;sheets=corrected(data);row=sheets[0]['rows'][0]
    if change=='fake_url':row['cells']['来源URL']['value']='https://example.com/fake'
    if change=='fake_id':row['cells']['来源ID']['value']='unknown'
    if change=='invented_display':row['cells']['屏幕类型']['value']='超视网膜 XDR 显示屏，LTPO OLED 全面屏'
    if change=='wrong_chip':row['cells']['芯片型号'].update(value='A19 Pro 芯片',quote='A19 Pro 芯片')
    if change=='wrong_observation':
        sources=ws.sources();sources[0]['observation_id']=sources[1]['observation_id'];atomic_json(ws.data_file,sources)
    with pytest.raises((BusinessExcelValidationError,DeliveryInputError,ValueError)):
        export_compiled(ws,sheets)
    assert not (ws.path/'task_result.xlsx').exists()
    if change in {'invented_display','wrong_chip'}:
        result=stage_business_rows(ws,sheets)
        assert result['validation']['status']=='needs_review' and result['validation']['pending_cells']>=1


def test_direct_export_corrects_staged_proposals_without_finalizer_restoring_old_data(captured):
    ws,data=captured;stage_business_rows(ws,original(data,0))
    export_compiled(ws,corrected(data))
    assert read_json(ws.path/'business_rows.json')['validation']['pending_cells']==0
    for _ in range(2):
        finalize_delivery(ws,'offline resume');assert_verified(ws)


@pytest.mark.parametrize('separator',['-','\u2010','\u2011'])
def test_inch_adjective_binding_is_exact_and_does_not_weaken_number_or_unit(captured,separator):
    ws,_=captured
    field={'type':'number','unit':'inch','role':'screen_size','required':True}
    value,audit=normalize_entry({'value':6.3,'quote':f'6.3{separator}inch OLED'},field,None)
    assert value==6.3 and audit['status']=='verified'
    for quote in ('6.3 mm; 7 inch','6.3 pixels; 7 inches'):
        with pytest.raises(ValueError):normalize_entry({'value':6.3,'quote':quote},field,None)


def test_invalid_schema_is_rejected_even_when_review_export_is_enabled(captured):
    ws,data=captured;sheets=corrected(data);sheets[0]['rows'][1]['label']='iPhone 17'
    with pytest.raises(DeliveryInputError):export_compiled(ws,sheets,allow_structured_review=True)


def test_registered_save_and_export_tools_use_the_real_shared_path(captured,settings):
    ws,data=captured;tools={t.name:t for t in build_local_tools(ws,settings)}
    result=json.loads(tools['save_business_rows'].invoke({'sheets':corrected(data)}))
    assert result['validation']['verified_cells']==12
    tools['export_business_excel'].invoke({'sheets':corrected(data)})
    assert_verified(ws)


def test_real_business_name_is_not_silently_removed_as_identity(captured):
    ws,data=captured;sheets=corrected(data)
    for row in sheets[0]['rows']:row['cells']['名称']=copy.deepcopy(row['cells']['芯片型号'])
    sheets[0]['columns'].append('名称')
    normalized,_=comparison_contract(ws,sheets)
    assert '名称' in normalized[0]['columns']


def test_oversized_direct_export_cannot_replace_previous_staged_delivery(captured):
    ws,data=captured;sheets=corrected(data);stage_business_rows(ws,sheets);export_compiled(ws,sheets)
    before={name:(ws.path/name).read_bytes() for name in ('business_rows.json','business_artifact.json','task_result.xlsx')}
    sheets[0]['rows'][0]['cells']['芯片型号']['extra_blob']='x'*512001
    with pytest.raises(ValueError,match='512KB'):export_compiled(ws,sheets)
    assert all((ws.path/name).read_bytes()==content for name,content in before.items())


def test_video_format_reconciliation_requires_exact_resolution_and_rate_list(captured):
    ws,_=captured
    # This checks the replacement of the old similarity rule without certifying
    # unseen camera data from the phone observations.
    for value in ('4K (16:9) 3840×2160@60fps','4K (16:9) 3840×2160@120fps','4K (16:9) 4096×2160@60fps'):
        sheets=[{'name':'Video','mode':'comparison','columns':['视频规格'],'rows':[{'label':'Example','cells':{
            '视频规格':{'value':value,'quote':'4K (16:9): 3840×2160@30/60fps'}}}]}]
        compiled,_=compile_comparisons(ws,sheets)
        result=compiled[0]['rows'][0]['cells']['视频规格']['value']
        assert result==('4K (16:9): 3840×2160@30/60fps' if value.endswith('2160@60fps') and '3840' in value else value)


def test_invalid_editorial_label_is_downgraded_and_fact_validated(tmp_path):
    ws=Workspace(tmp_path,'role-downgrade')
    url='https://www.python.org/downloads/release/python-3110/'
    text='PEP 657 -- Include Fine-Grained Error Locations in Tracebacks'
    ws.observe(url,text)
    ws.record('Python 3.11.0',url,'release notes',text,require_observation=True)
    sheets=[{'name':'版本特性对比','mode':'comparison',
             'columns':['特性类别','官方英文原文','PEP/编号','来源URL'],
             'fields':{'特性类别':{'type':'text','role':'editorial_label'},
                       '官方英文原文':{'type':'text','role':'verbatim_excerpt'},
                       'PEP/编号':{'type':'text','role':'editorial_label'},
                       '来源URL':{'type':'text','role':'document_identity'}},
             'rows':[{'label':'Python 3.11.0 - PEP 657','cells':{
                 '特性类别':{'value':'新特性','source_id':'1','quote':text},
                 '官方英文原文':{'value':text,'source_id':'1','quote':text},
                 'PEP/编号':{'value':'PEP 657','source_id':'1','quote':text},
                 '来源URL':{'value':url,'source_id':'1','quote':text}}}]}]
    out,changes=comparison_contract(ws,sheets)
    fields=out[0]['fields']
    assert 'role' not in fields['PEP/编号']
    assert '来源URL' not in out[0]['columns']
    assert fields['特性类别']['role']=='editorial_label'
    assert any(c.get('rule')=='invalid_role_downgraded_to_fact' and c.get('column')=='PEP/编号'
               for c in changes)
    assert sheets[0]['fields']['PEP/编号']['role']=='editorial_label'
