"""Unedited historical calls and domain-independent evidence adversaries."""
import copy,json,socket
from pathlib import Path
from dataclasses import replace
from contextlib import asynccontextmanager
import pytest
from openpyxl import load_workbook
from langchain_core.tools import tool
from test_graph import settings,ScriptModel,call,empty_browser
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json
from web_agent.delivery_pipeline import stage_business_rows,finalize_delivery
from web_agent.delivery_compiler import export_compiled
from web_agent.delivery_schema import field_for,normalize_entry
from web_agent.scalar_contract import ScalarContractError
from web_agent.tools import build_local_tools
from web_agent.execution_budget import handoff
from web_agent.runner import run_task
from web_agent.report_export import table_markdown

FIXTURES=Path(__file__).parent/'fixtures'

def captured(tmp_path,name,index=0):
    data=json.loads((FIXTURES/f'{name}.json').read_text(encoding='utf8'))
    ws=Workspace(tmp_path,f'{name}-{index}')
    atomic_json(ws.data_file,data['sources']);(ws.path/'observations').mkdir()
    for obs in data['observations']:atomic_json(ws.path/'observations'/f"{obs['id']}.json",obs)
    return ws,data

@pytest.mark.parametrize('index',[0,1])
@pytest.mark.asyncio
async def test_unchanged_actual_tool_calls_and_all_output_values(tmp_path,settings,index,monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('Network forbidden'))
    ws,data=captured(tmp_path,'latest_contract_case',index)
    c=[c for c in data['calls'] if c['detail']=='save_business_rows'][index]
    args=copy.deepcopy(c['arguments']);before=copy.deepcopy(args)
    tools={t.name:t for t in build_local_tools(ws,settings)}
    result=json.loads(await tools['save_business_rows'].ainvoke(args))
    assert args==before
    assert result['validation']=={'verified_cells':2,'pending_cells':10,'status':'needs_review'}
    staged=read_json(ws.path/'business_rows.json')
    verified=[p for p in staged['provenance'] if p['status']=='verified']
    assert {(p['source_id'],p['normalized_value']) for p in verified}=={('1',6.3),('7',4000)}
    for p in verified:
        ws.evidence_for(p['url'],p['quote'],p['observation_id'])
        assert p['original_input']==before['sheets'][0]['rows'][p['row']-2]['cells'][p['column']]
    codes={p.get('diagnostic',{}).get('code') for p in staged['provenance']}
    assert {'ambiguous_quantity','text_not_extractive','scalar_required'}<=codes
    # Run real deterministic Excel/Markdown/PDF chain. File creation alone is
    # insufficient: every canonical row and every verified fact must agree.
    atomic_json(ws.path/'task.json',{'report_format':'both'})
    receipt=finalize_delivery(ws,'offline captured replay')
    assert not receipt['errors']
    artifact=read_json(ws.path/'business_artifact.json')
    assert sum(p['status']=='verified' for p in artifact['provenance'])==2
    md=(ws.path/'research_report.md').read_text(encoding='utf8')
    book=load_workbook(ws.path/'task_result.xlsx',read_only=True)
    for sheet in artifact['sheets']:
        assert list(book[sheet['name']].values)==[tuple(sheet['columns'])]+[tuple(r) for r in sheet['rows']]
        assert table_markdown(sheet['columns'],sheet['rows']) in md
    book.close()
    import pymupdf
    with pymupdf.open(ws.path/'research_report.pdf') as pdf:
        text=''.join(p.get_text() for p in pdf)
        assert '6.3' in text and '4000' in text

def test_historical_hardware_still_requires_real_evidence(tmp_path):
    ws,data=captured(tmp_path,'pc_delivery_case')
    frozen=copy.deepcopy(data['sheets'])
    export_compiled(ws,data['sheets'],allow_structured_review=True)
    assert data['sheets']==frozen
    audit=read_json(ws.path/'business_artifact.json')['provenance']
    assert sum(p['status']=='verified' for p in audit)==11
    for p in audit:
        if p['status']=='verified':ws.evidence_for(p['url'],p['quote'],p['observation_id'])
    assert any(p['status']!='verified' for p in audit)

@pytest.mark.parametrize('unit,raw,value,quote',[
    ('厘米','10 毫米',1,'长度10 毫米'),('英寸','6.3 英寸（注）',6.3,'6.3 英寸 (对角线)'),
    ('毫安时','4000mAh（典型值）',4000,'容量 (mAh, 典型值)\n4000'),
    ('kg','2000 g',2,'Weight 2000 g'),('分钟','120 秒',2,'Elapsed 120 秒')])
def test_dimension_conversion_has_no_domain_or_field_dependency(unit,raw,value,quote):
    for label in ['A','measurement','任意字段']:
        field=field_for(label,{label:{'type':'number','unit':unit}})
        actual,audit=normalize_entry({'value':raw,'quote':quote},field,None)
        assert actual==value and audit['status']=='verified'

@pytest.mark.parametrize('value,quote,code',[
    ('6 cm / 7 cm','6 cm / 7 cm','ambiguous_quantity'),
    ('9 cm','6 cm','quantity_mismatch'),('6 cm','6 kg','quantity_not_evidenced')])
def test_ambiguous_or_fabricated_quantities_remain_rejected(value,quote,code):
    with pytest.raises(ScalarContractError) as err:normalize_entry({'value':value,'quote':quote},{'type':'number','unit':'cm','required':True},None)
    assert err.value.details['code']==code

def test_text_raw_mapping_is_not_a_paraphrase_license():
    field={'type':'text','required':True}
    assert normalize_entry({'value':'ABC (X)','raw_value':'ＡＢＣ （X）','quote':'ＡＢＣ （X）'},field,None)[0]=='ABC (X)'
    for value,raw in [('ABC','DEF'),('six cores','6 核')]:
        with pytest.raises(ValueError):normalize_entry({'value':value,'raw_value':raw,'quote':raw},field,None)

def test_explicit_input_unit_cannot_be_silently_replaced():
    with pytest.raises(ScalarContractError) as err:
        normalize_entry({'value':'6 cm','input_unit':'kg','quote':'6 cm'},
                        {'type':'number','unit':'cm','required':True},None)
    assert err.value.details['code']=='input_unit_conflict'

def test_price_currency_is_context_and_missing_market_never_certified():
    field=field_for('Any',{'Any':{'type':'price','unit':'CNY'}})
    assert field['currency']=='CNY' and 'unit' not in field
    source={'url':'https://example.com/cn/shop','accessed_at':'2026-10-18T00:00:00Z'}
    entry={'value':'RMB 1,299','raw_value':'RMB 1,299','quote':'购买 RMB 1,299 起','price':{'market':'CN','type':'starting','queried_at':'2026-10-18'}}
    assert normalize_entry(entry,field,source)[0]==1299
    del entry['price']['market']
    with pytest.raises(ValueError):normalize_entry(entry,field,source)

def test_tools_expose_same_fact_schema(tmp_path,settings):
    tools={t.name:t for t in build_local_tools(Workspace(tmp_path,'schema'),settings)}
    schemas=[tools[n].args_schema.model_json_schema()['properties']['sheets'] for n in ['save_business_rows','export_business_excel']]
    assert schemas[0]==schemas[1]
    text=json.dumps(schemas[0]);assert all(k in text for k in ['raw_value','input_unit','source_id','price','quote'])

@pytest.mark.asyncio
async def test_registered_source_does_not_prematurely_disable_collection(settings):
    ws=Workspace(settings.output_dir,'late-core-fact')
    ws.observe('https://example.com/one','one verified page')
    ws.record('one','https://example.com/one','one','one verified page',require_observation=True)
    atomic_json(ws.path/'usage.json',{'requests':20,'input_tokens':0,'output_tokens':0})
    assert not handoff(ws,30,20)['local_only']
    visits=[]
    @asynccontextmanager
    async def browser(cfg,workspace):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Offline fake observation; no network."""
            visits.append(url);workspace.observe(url,'second fact 12 cm');return 'second fact 12 cm'
        yield [navigate]
    model=ScriptModel([call('browser_navigate',{'url':'https://example.com/two'},'late')])
    await run_task('Complete core facts','late-core-fact',replace(settings,max_model_calls=30),model=model,browser_factory=browser)
    assert visits==['https://example.com/two']
    assert handoff(ws,30,28)['phase']=='delivery'
    assert handoff(ws,30,30)['remaining']==0

@pytest.mark.asyncio
async def test_staging_gate_is_enforced_even_if_model_ignores_bound_tools(settings):
    ws=Workspace(settings.output_dir,'stage-guard')
    for i in [1,2]:
        url=f'https://example.com/{i}'
        ws.observe(url,f'fact {i}')
        ws.record(str(i),url,str(i),f'fact {i}',require_observation=True)
    atomic_json(ws.path/'requirements.json',[{'id':'r1','description':'table','kind':'artifact','artifact':'task_result.xlsx','result':{'status':'pending'}}])
    atomic_json(ws.path/'usage.json',{'requests':7,'input_tokens':0,'output_tokens':0})
    visits=[]
    @asynccontextmanager
    async def browser(cfg,workspace):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Must not execute during staging."""
            visits.append(url);return 'unexpected'
        yield [navigate]
    model=ScriptModel([call('browser_navigate',{'url':'https://example.com/3'},'bypass')])
    await run_task('Save facts','stage-guard',replace(settings,max_model_calls=30),model=model,browser_factory=browser)
    assert not visits
    bypass=read_json(ws.path/'tool_journal.json')['bypass']
    assert not bypass['error_code']
    assert 'save_business_rows' in bypass['output']
