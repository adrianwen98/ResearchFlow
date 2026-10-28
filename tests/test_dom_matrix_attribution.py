"""Offline DOM cell binding contracts. Does not invoke browser or model API."""
import hashlib
import json
import pytest
from web_agent.dom_matrix import validate_tables,compact_tables,save_table_binding,bound_rows
from web_agent.owner_guard import dom_matrix_subject_bound
from web_agent.workspace import Workspace
from web_agent.persistence import read_json,atomic_json

URL='https://example.com/devices/compare'
TABLE=[{'headers':['iPhone 17','iPhone 17 Pro','iPhone Duo'], 'rows':[
 {'owner':'iPhone 17','field':'','value':'A19 芯片'},
 {'owner':'iPhone 17 Pro','field':'','value':'A19 Pro 芯片'},
 {'owner':'iPhone Duo','field':'','value':'A20 Pro 芯片'},
 {'owner':'iPhone 17','field':'','value':'1800 万像素 Center Stage 前置摄像头'},
 {'owner':'iPhone 17 Pro','field':'','value':'1800 万像素 Center Stage 前置摄像头'},
 {'owner':'iPhone Duo','field':'','value':'1200 万像素 Center Stage 前置摄像头'},
]}]


def setup(ws, tables=TABLE):
    text='### Rendered page text\nA19 芯片\nA19 Pro 芯片\nA20 Pro 芯片\n1800 万像素 Center Stage 前置摄像头\n1200 万像素 Center Stage 前置摄像头\n'+compact_tables(tables)
    oid=ws.observe(URL,text)
    save_table_binding(ws,oid,URL,text,validate_tables(tables))
    source={'id':'1','url':URL,'observation_id':oid}
    return source


def bind(ws, source, name, field, quote):
    return dom_matrix_subject_bound(ws,source,name,field,quote,{'type':'text'},{'normalized_value':quote})


def test_realistic_device_column_binding(tmp_path):
    ws=Workspace(tmp_path,'device')
    source=setup(ws)
    assert bind(ws,source,'iPhone 17','芯片','A19 芯片')
    assert bind(ws,source,'iPhone 17 Pro','芯片','A19 Pro 芯片')
    assert bind(ws,source,'iPhone 17','前置摄像头','1800 万像素 Center Stage 前置摄像头')
    assert bind(ws,source,'iPhone 17 Pro','前置摄像头','1800 万像素 Center Stage 前置摄像头')
    assert not bind(ws,source,'iPhone 17','芯片','A19 Pro 芯片')
    assert not bind(ws,source,'iPhone Duo','芯片','A19 Pro 芯片')
    assert not bind(ws,source,'iPhone 17 Pro','前置摄像头','1200 万像素 Center Stage 前置摄像头')
    assert not bind(ws,source,'iPhone 17','芯片','A19 芯片\nA19 Pro 芯片')
    assert not bind(ws,source,'iPhone 17','芯片','A19')
    assert not bind(ws,source,'iPhone 17','芯片','A20 Pro 芯片')


def test_table_order_reversed_must_rebind(tmp_path):
    ws=Workspace(tmp_path,'order')
    matrix=[{'headers':['iPhone 17 Pro','iPhone 17'],'rows':[
        {'owner':'iPhone 17 Pro','field':'芯片','value':'A19 Pro 芯片'},
        {'owner':'iPhone 17','field':'芯片','value':'A19 芯片'}]}]
    source=setup(ws,matrix)
    assert bind(ws,source,'iPhone 17 Pro','芯片','A19 Pro 芯片')
    assert bind(ws,source,'iPhone 17','芯片','A19 芯片')
    assert not bind(ws,source,'iPhone 17 Pro','芯片','A19 芯片')


def test_sidecar_scoped_to_observation_url_and_saved_text(tmp_path):
    ws=Workspace(tmp_path,'secure');source=setup(ws)
    assert len(bound_rows(ws,source))==6
    assert not bound_rows(ws,{**source,'url':'https://other.example.com/'})
    assert not bound_rows(ws,{**source,'observation_id':'a'*20})
    path=ws.path/'observations'/f"{source['observation_id']}.json"
    doc=read_json(path);doc['text']+=' MODIFIED';atomic_json(path,doc)
    assert not bound_rows(ws,source)
    assert not bind(ws,source,'iPhone 17','芯片','A19 芯片')


def test_one_row_field_must_agree(tmp_path):
    ws=Workspace(tmp_path,'field')
    matrix=[{'headers':['Device A','Device B'],'rows':[
        {'owner':'Device A','field':'芯片','value':'A19 芯片'},
        {'owner':'Device B','field':'芯片','value':'A19 Pro 芯片'}]}]
    source=setup(ws,matrix)
    assert bind(ws,source,'Device A','芯片','A19 芯片')
    assert not bind(ws,source,'Device A','摄像头','A19 芯片')


@pytest.mark.parametrize('malformed',[
    {}, 'iPhone 17', None, [{'headers':['A','A'],'rows':[{'owner':'A','field':'芯片','value':'A19 芯片'}]}],
    [{'headers':['A'],'rows':[]}], [{'headers':['A','B'],'rows':[]}],
    [{'headers':['A','B'],'rows':[{'owner':'C','field':'芯片','value':'A19'}]}],
    [{'headers':['A','B'],'rows':[{'owner':'A','field':'芯片','value':'A19','extra':'evil'}]}],
    [{'headers':['A','B'],'rows':[{'owner':'A','field':'芯片','value':'A19'}]*301}],
    [{'headers':['iPhone 17','iPhone\u00a017'],'rows':[{'owner':'iPhone 17','field':'芯片','value':'A19'}]}],
])
def test_malformed_payload_fails_closed(malformed):
    assert not validate_tables(malformed)


def test_table_html_preview_not_sufficient_alone(tmp_path):
    ws=Workspace(tmp_path,'no_binding')
    text='### DOM table matrix\niPhone 17 芯片: A19 芯片'
    oid=ws.observe(URL,text)
    source={'url':URL,'observation_id':oid}
    assert not bind(ws,source,'iPhone 17','芯片','A19 芯片')


def test_actual_host_js_maps_only_visible_aligned_columns():
    """Execute the same fixed DOM JS with a minimal DOM object model (no network)."""
    import subprocess, shutil
    from web_agent.dom_matrix import DOM_MATRIX_JS
    if not shutil.which('node'):
        pytest.skip('Node.js unavailable for static host JS evaluation')
    script=r'''
    const mk=(tag, text='', attrs={}, children=[]) => ({
        tagName:tag, textContent:text, innerText:text, children,
        colSpan: +(attrs.colspan || 1), rowSpan: +(attrs.rowspan || 1),
        getAttribute(key){return attrs[key] || null},
        getClientRects(){return attrs.hidden ? [] : [1]},
        closest(sel){
            if (sel.startsWith('[hidden')) return attrs.hidden ? {} : null;
            if (sel === 'thead') return attrs.thead ? {} : null;
            if (sel.startsWith('table,')) return table;
            return null;
        }
    });
    const head=mk('TR','',{},[
        mk('TH','', {scope:'col'}),mk('TH','iPhone 17',{scope:'col'}),
        mk('TH','iPhone 17 Pro',{scope:'col'}),mk('TH','iPhone Duo',{scope:'col'})]);
    const chip=mk('TR','',{},[
        mk('TD','A19 芯片'), mk('TD','A19 Pro 芯片'),mk('TD','A20 Pro 芯片')]);
    const camera=mk('TR','',{},[
        mk('TD','1800 万像素 Center Stage 前置摄像头'),
        mk('TD','1800 万像素 Center Stage 前置摄像头'),
        mk('TD','1200 万像素 Center Stage 前置摄像头')]);
    const merged=mk('TR','',{},[
        mk('TH','摄像头',{scope:'row', colspan:2}), mk('TD','incorrect alignment'), mk('TD','bad')]);
    const hidden=mk('TR','',{hidden:'true'},[
        mk('TD','Hidden A'),mk('TD','Hidden B'),mk('TD','Hidden C')]);
    const table=mk('TABLE','',{},[]);
    table.querySelectorAll = () => [head,chip,camera,merged,hidden];
    globalThis.document = {querySelectorAll: () => [table]};
    console.log(JSON.stringify((EVAL)()));
    '''.replace('EVAL',DOM_MATRIX_JS)
    run = subprocess.run(
        ['node', '-e', script],
        capture_output=True,
        text=True,
        encoding='utf-8',  # Node.js 标准输出使用 UTF-8，不依赖 Windows 默认 GBK 编码
        timeout=15,
    )
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert len(result)==1
    assert result[0]['headers']==['iPhone 17','iPhone 17 Pro','iPhone Duo']
    rows=result[0]['rows']
    assert len(rows)==6
    assert rows[0]=={'owner':'iPhone 17','field':'','value':'A19 芯片'}
    assert rows[1]=={'owner':'iPhone 17 Pro','field':'','value':'A19 Pro 芯片'}
    assert rows[3]['owner']=='iPhone 17' and rows[4]['owner']=='iPhone 17 Pro'


def test_full_business_excel_pipeline_preserves_identity(tmp_path):
    from web_agent.business_excel import export_business_excel, BusinessExcelValidationError
    ws=Workspace(tmp_path,'excel')
    source=setup(ws)
    quotes=['A19 芯片','A19 Pro 芯片','1800 万像素 Center Stage 前置摄像头']
    ids={quote:ws.record('Observed table',URL,'Observed value',quote,require_observation=True)['id'] for quote in quotes}
    def make(label,chip):
        return {'label':label,'cells':{'芯片':{'value':chip,'source_id':ids[chip],'quote':chip},
               '前置摄像头':{'value':quotes[2],'source_id':ids[quotes[2]],'quote':quotes[2]}}}
    spec={'name':'Product matrix','mode':'comparison','columns':['芯片','前置摄像头'],
          'fields':{'芯片':{'type':'text','required':True},'前置摄像头':{'type':'text','required':True}},
          'rows':[make('iPhone 17',quotes[0]),make('iPhone 17 Pro',quotes[1])]}
    receipt=export_business_excel(ws,[spec],allow_structured_review=True,validate_only=True)
    assert receipt['rows']==2
    assert len(receipt['provenance'])==4
    assert all(x.get('status')=='verified' for x in receipt['provenance'])
    spec['rows'][0]['cells']['芯片']={'value':'A19 Pro 芯片','source_id':ids[quotes[1]],'quote':quotes[1]}
    with pytest.raises(BusinessExcelValidationError) as error:
        export_business_excel(ws,[spec],allow_structured_review=True,validate_only=True)
    assert error.value.details['issues_total']==1
    assert error.value.details['issues'][0]['column']=='芯片'
