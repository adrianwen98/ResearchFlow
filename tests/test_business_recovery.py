"""Free, exact-evidence regressions for progressively corrected workbooks."""
import copy
import json
from dataclasses import replace

import pytest

from test_graph import settings, ScriptModel, call
from test_export_correction import fixture_browser, fixture_workspace, sheet
from test_web_workbench import client
from web_agent.business_excel import (
    BusinessExcelValidationError, business_repair_context,
    export_business_excel, repair_business_excel, verify_business_excel,
)
from web_agent.context import bounded_context
from web_agent.harness import classify_error, error_output, stop_reason
from web_agent.persistence import atomic_json, read_json
from web_agent.runner import run_task
from web_agent.tools import build_local_tools
from web_agent.workspace import Workspace
from langchain_core.messages import HumanMessage


def failure(cells, omitted=0):
    diagnostics = {'issues_total': len(cells)+omitted, 'issues_omitted': omitted,
                   'issues': [{'sheet': 'Compare', 'row': n, 'column': 'Value'} for n in cells]}
    return {'name': 'repair_business_excel', 'error_code': 'business_validation',
            'output': 'ERROR: '+json.dumps({'diagnostics': diagnostics})}


class _SourceWorkspace:
    def __init__(self, sources): self._sources = sources
    def sources(self): return self._sources


def test_hard_failures_without_any_source_stop_early():
    journal = {}
    for i in range(10):
        journal[f'e{i}'] = {'name': 'browser_navigate', 'error_code': 'http_404',
                            'output': 'ERROR: 404'}
        journal[f'ok{i}'] = {'name': 'inspect_saved_pages', 'output': 'ok'}
    assert stop_reason(journal, _SourceWorkspace([]))[0] == 'stalled'
    assert stop_reason(journal, _SourceWorkspace([{'id': '1'}])) is None


def test_progress_is_not_a_loop_but_unchanged_or_regressing_errors_are():
    assert stop_reason({'a': failure(range(12)), 'b': failure(range(3)), 'c': failure(range(2))}) is None
    for cells in ([0, 1], [0, 1, 2], [0, 13]):
        assert stop_reason({'a': failure(range(12)), 'b': failure(range(2)), 'c': failure(cells)})[0] == 'stalled'
    assert stop_reason({str(i): failure(range(20-i)) for i in range(8)})[0] == 'stalled'
    assert stop_reason({str(i): failure(range(3-i), omitted=1) for i in range(3)})[0] == 'stalled'


def test_text_zero_is_normalized_only_when_exactly_evidenced(tmp_path):
    ws=fixture_workspace(tmp_path)
    ws.observe('https://example.com/free', '免费版\n¥\n0\n人/月')
    ws.record('Free plan', 'https://example.com/free', '免费版', '免费版\n¥\n0\n人/月', require_observation=True)
    spec=sheet(0, '免费版\n¥\n0\n人/月')
    spec[0]['fields']={'岗位': {'type': 'text'}}
    spec[0]['rows'][0]['cells']['岗位']['source_id']='2'
    export_business_excel(ws,spec)
    assert verify_business_excel(ws)==[]
    meta=read_json(ws.path/'business_artifact.json')
    assert meta['sheets'][0]['rows'][0][1]=='0'
    assert meta['provenance'][0]['input_value']==0
    assert meta['provenance'][0]['format_rule']=='number_to_text_exact_quote'
    messages=bounded_context([HumanMessage('Test restored task')],ws,'Rules')
    assert any('business_artifact' in str(m.content) and '优先复用' in str(m.content) for m in messages)
    spec[0]['rows'][0]['cells']['岗位']['quote']='免费版'
    with pytest.raises(BusinessExcelValidationError) as err:
        export_business_excel(ws,spec)
    assert 'suggested_entry' not in err.value.issues[0]


def test_latest_draft_diagnostics_survive_bounded_context(tmp_path):
    ws=fixture_workspace(tmp_path)
    with pytest.raises(BusinessExcelValidationError) as err:
        export_business_excel(ws,sheet('错误概括'))
    atomic_json(ws.path/'tool_journal.json', {'bad': {
        'name':'export_business_excel', 'output':error_output(classify_error(err.value))}})
    summary=business_repair_context(ws)
    assert summary['draft_id']==err.value.details['draft_id']
    messages=bounded_context([HumanMessage('Test repair')],ws,'Rules')
    assert any(summary['draft_id'] in str(m.content) and 'business_repair' in str(m.content) for m in messages)
    draft=read_json(ws.path/'business_draft.json'); draft['draft_id']='f'*24
    atomic_json(ws.path/'business_draft.json',draft)
    assert business_repair_context(ws) is None


def test_saved_observation_excerpt_works_without_network_and_is_task_bound(settings):
    ws=fixture_workspace(settings.output_dir)
    original='介绍段不含上限\n认证前\n20 MB\n认证后\n10 GB'
    ws.observe('https://example.com/limit',original)
    ws.record('Upload','https://example.com/limit','不应当作为原文的概括','介绍段不含上限',require_observation=True)
    tool=next(t for t in build_local_tools(ws,settings) if t.name=='list_recorded_sources')
    result=json.loads(tool.invoke({'source_id':'2','keyword':'20 MB'}))
    assert result['keyword_found'] and result['text'] in original
    assert '20 MB' in result['text'] and '不应当作为原文的概括' not in result['text']
    assert len(result['text'])<=6000
    assert json.loads(tool.invoke({}))==ws.sources()
    assert not json.loads(tool.invoke({'source_id':'2','keyword':'不存在'}))['text']
    with pytest.raises(ValueError):ws.source_excerpt('../other','20 MB')
    with pytest.raises(ValueError):ws.source_excerpt('2','x'*721)
    other=Workspace(settings.output_dir,'other')
    with pytest.raises(ValueError):other.source_excerpt('2','20 MB')


@pytest.mark.asyncio
async def test_runner_allows_real_progress_through_third_failure_then_delivers(settings):
    specs=[]
    for bad_count in (4,3,2,0):
        spec=sheet()
        spec[0]['rows']=[{'label':f'unit {i}', 'cells':{'岗位': {
            'value':'错误概括' if i<bad_count else '研究助理',
            'source_id':'missing' if i<bad_count else '1', 'quote':'岗位：研究助理'}}} for i in range(4)]
        specs.append(spec)
    model=ScriptModel([call('export_business_excel',{'sheets':s},f'c{i}') for i,s in enumerate(specs)]+
                      [call('export_excel',id='sources'),call('export_report',{'markdown_report':'# Result\n研究助理'},'report')])
    cfg=replace(settings,max_model_calls=8)
    result=await run_task('Progressively correct actual cells','progress',cfg,model=model,browser_factory=fixture_browser)
    assert result.status=='needs_review'
    assert result.metrics['requests']==6 and result.metrics['errors']==3
    assert verify_business_excel(Workspace(cfg.output_dir,'progress'))==[]


def test_offline_repair_receipt_reports_progress_without_faking_task_success(client):
    import hashlib
    c,cfg=client
    ws=fixture_workspace(cfg.output_dir,'repaired')
    atomic_json(ws.path/'task.json',{'task':'Offline correction'})
    atomic_json(ws.path/'result.json',{'task_id':ws.task_id,'status':'stalled','answer':'Old failure'})
    with pytest.raises(BusinessExcelValidationError) as err:
        export_business_excel(ws,sheet('错误概括'))
    output=error_output(classify_error(err.value))
    atomic_json(ws.path/'tool_journal.json',{'failed':{'name':'export_business_excel','error_code':'business_validation','output':output}})
    atomic_json(ws.path/'usage.json',{'requests':29})
    repair_business_excel(ws,err.value.details['draft_id'],[{'sheet':'机会对比','row':2,'column':'岗位',
        'entry':{'value':'研究助理','source_id':'1','quote':'岗位：研究助理'}}])
    receipt={'task_id':ws.task_id,'business_sha256':read_json(ws.path/'business_artifact.json')['sha256'],
             'requests':29,'patched_cells':1,'resolved_failure_sha256':hashlib.sha256(output.encode()).hexdigest()}
    atomic_json(ws.path/'business_repair_receipt.json',receipt)
    data=c.get('/tasks/repaired/workspace').json()
    assert data['status']=='stalled' and '已修正' in data['failure']['label']
    assert any(f['name']=='task_result.xlsx' for f in data['files'])
    assert c.get('/tasks/repaired/files/task_result.xlsx').content[:2]==b'PK'
    atomic_json(ws.path/'usage.json',{'requests':30})
    assert '已修正' not in c.get('/tasks/repaired/workspace').json()['failure']['label']
