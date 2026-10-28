"""Free regressions for actual resume/export/requirement failures."""
import copy,hashlib,json
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage,HumanMessage
from test_graph import settings,ScriptModel,empty_browser
from test_camera_delivery import camera_workspace
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json
from web_agent.business_excel import export_business_excel,repair_business_excel,inspect_business_draft,BusinessExcelValidationError
from web_agent.delivery_inspection import inspect_delivery,report_lines,DeliveryInputError,resolved_draft
from web_agent.completion import record_result,evaluate_requirements,completion_decision
from web_agent.context import bounded_context,CONTEXT_CHAR_LIMIT,prompt_size
from web_agent.harness import classify_error,error_output
from web_agent.runner import run_task

CASE=Path(__file__).parent/'fixtures/camera_resume_case.json'

def prepared(root,key='resume'):
    ws,draft=camera_workspace(root,key)
    case=json.loads(CASE.read_text('utf-8'))
    successful=[c for c in case['calls'] if c['detail']=='export_business_excel'][-1]
    export_business_excel(ws,successful['arguments']['sheets'])
    ws.export_excel()
    (ws.path/'research_report.md').write_text(case['report'],encoding='utf-8')
    digest=hashlib.sha256((ws.path/'research_report.md').read_bytes()).hexdigest()
    atomic_json(ws.path/'report_meta.json',{'format':'markdown','sha256':digest,
        'source_ids':[r['id'] for r in ws.sources()],
        'business_sha256':read_json(ws.path/'business_artifact.json')['sha256']})
    atomic_json(ws.path/'requirements.json',case['requirements'])
    return ws,draft,case


def test_consumed_draft_is_acknowledged_without_replaying_or_overwriting(tmp_path):
    ws,draft,case=prepared(tmp_path)
    before=(ws.path/'task_result.xlsx').read_bytes()
    assert resolved_draft(ws,draft['draft_id'])['status']=='exported'
    assert inspect_business_draft(ws,draft['draft_id'])['draft_consumed']
    # A delayed historical patch is NOT applied to the completed workbook.
    repair_business_excel(ws,draft['draft_id'],[{'sheet':'wrong','row':999,'column':'wrong','entry':{'value':'wrong'}}])
    assert (ws.path/'task_result.xlsx').read_bytes()==before
    assert not (ws.path/'business_draft.json').exists()
    with pytest.raises(DeliveryInputError) as err:repair_business_excel(ws,'0'*24,[])
    assert err.value.details['status']=='exported'
    assert err.value.details['next_tool']=='inspect_delivery'
    other=Workspace(tmp_path,'other')
    assert resolved_draft(other,draft['draft_id']) is None
    (ws.path/'task_result.xlsx').write_bytes(before+b'tamper')
    assert resolved_draft(ws,draft['draft_id']) is None


def test_new_draft_never_hidden_by_consumed_draft_acknowledgement(tmp_path):
    ws,draft,case=prepared(tmp_path)
    bad=copy.deepcopy(case['calls'][-1])
    sheets=next(c['arguments']['sheets'] for c in case['calls'] if c['detail']=='export_business_excel' and c['call_id']=='call_00_c6ovoYAA3J05n96SYynK1078')
    with pytest.raises(BusinessExcelValidationError) as err:export_business_excel(ws,sheets)
    assert resolved_draft(ws,draft['draft_id']) is None
    with pytest.raises(DeliveryInputError) as stale:repair_business_excel(ws,draft['draft_id'],[])
    assert stale.value.details['draft_id']==err.value.details['draft_id']
    current=inspect_business_draft(ws,err.value.details['draft_id'])
    assert '2.25-inch' in current['issues'][0]['source_context']


def test_actual_missing_column_error_gives_positions_instead_of_generic_failure(tmp_path):
    ws,draft,case=prepared(tmp_path)
    first=next(c for c in case['calls'] if c['detail']=='export_business_excel')
    before=(ws.path/'task_result.xlsx').read_bytes()
    with pytest.raises(DeliveryInputError) as err:export_business_excel(ws,first['arguments']['sheets'])
    error=json.loads(error_output(classify_error(err.value))[7:])
    assert error['code']=='invalid_input'
    assert error['diagnostics']['missing_columns']==['参数']
    assert error['diagnostics']['row']==2
    assert (ws.path/'task_result.xlsx').read_bytes()==before


def test_report_lines_replace_copying_and_are_version_bound(tmp_path):
    ws,_,case=prepared(tmp_path)
    view=inspect_delivery(ws,'传感器')['report']
    ids=[view['lines'][0]['line_id']]
    result=record_result(ws,'r1',['1','2','5'],report_line_ids=ids,report_sha256=view['sha256'])
    assert result['result']['report_excerpts']==[view['lines'][0]['text']]
    assert evaluate_requirements(ws)['requirements'][0]['passed']
    before=(ws.path/'requirements.json').read_bytes()
    with pytest.raises(DeliveryInputError):record_result(ws,'r1',['1'],report_line_ids=ids,report_sha256='0'*64)
    assert (ws.path/'requirements.json').read_bytes()==before
    path=ws.path/'research_report.md';path.write_text(path.read_text('utf-8')+'\nReport version changed.\n',encoding='utf-8')
    assert not evaluate_requirements(ws)['requirements'][0]['passed']


def test_actual_mismatching_requirement_calls_offer_real_report_candidates(tmp_path):
    ws,_,case=prepared(tmp_path)
    failed=[c for c in case['calls'] if c['detail']=='record_requirement_result'][:5]
    for index in (0,1,3):
        before=(ws.path/'requirements.json').read_bytes()
        with pytest.raises(DeliveryInputError) as err:record_result(ws,**failed[index]['arguments'])
        packet=json.loads(error_output(classify_error(err.value))[7:])
        assert packet['code']=='evidence_mismatch'
        assert packet['diagnostics']['next_tool']=='inspect_delivery'
        candidate=packet['diagnostics']['candidate_lines'][0]
        excerpts,_=report_lines(ws,[candidate['line_id']],packet['diagnostics']['report_sha256'])
        assert excerpts[0].startswith(candidate['text'])
        assert (ws.path/'requirements.json').read_bytes()==before
    assert all(r['passed'] for r in evaluate_requirements(ws)['requirements'])
    assert completion_decision(ws)['status']=='partial'  # missing waterproof still blocks


@pytest.mark.parametrize('ids',[[True],[0],[-1],[999999],[1,1],['1'],[],list(range(1,22))])
def test_invalid_report_line_ids_do_not_mutate_results(tmp_path,ids):
    ws,_,_=prepared(tmp_path);view=inspect_delivery(ws)['report']
    before=(ws.path/'requirements.json').read_bytes()
    with pytest.raises(ValueError):record_result(ws,'r1',['1'],report_line_ids=ids,report_sha256=view['sha256'])
    assert (ws.path/'requirements.json').read_bytes()==before


def test_snapshot_after_history_always_reports_disk_state_without_mutating_messages(tmp_path):
    ws,_,_=prepared(tmp_path)
    messages=[HumanMessage('继续任务'),AIMessage(content='历史草稿需要repair_business_excel(old_draft)')]
    before=copy.deepcopy(messages)
    context=bounded_context(messages,ws,'rules')
    assert messages==before and prompt_size(context)<=CONTEXT_CHAR_LIMIT
    assert '当前磁盘交付状态' in context[-1].content
    assert '"status": "exported"' in context[-1].content
    assert 'inspect_delivery' in context[-1].content


@pytest.mark.asyncio
async def test_runner_does_not_spend_extra_turns_on_already_disclosed_missing_parameter(settings):
    ws,_,_=prepared(settings.output_dir,'quality_warning')
    model=ScriptModel([AIMessage(content='现有交付已保存，防水参数未核实。')])
    result=await run_task('比较 Pocket 3 和 Action 4',ws.task_id,settings,model=model,browser_factory=empty_browser)
    assert result.status=='partial' and model.calls==1
    assert result.metrics['requests']==1
    assert '必要参数仍未核实' in result.answer and '防水' in result.answer
    assert result.completion['missing_requirements']==[]
    assert result.completion['quality_blockers']
    assert result.metrics['errors']==0


def test_new_tools_are_callable_via_actual_tool_schema(settings):
    from web_agent.tools import build_local_tools
    ws,draft,_=prepared(settings.output_dir)
    tools={t.name:t for t in build_local_tools(ws,settings)}
    state=json.loads(tools['inspect_delivery'].invoke({'query':'传感器'}))
    report=state['report']
    row=json.loads(tools['record_requirement_result'].invoke({'requirement_id':'r1','source_ids':['1'],
             'report_line_ids':[report['lines'][0]['line_id']],'report_sha256':report['sha256']}))
    assert row['result']['report_line_ids']
    ack=json.loads(tools['repair_business_excel'].invoke({'draft_id':draft['draft_id'],'patches':[]}))
    assert ack['draft_consumed'] and not ack['stale_patches_applied']


def test_report_inspection_is_read_only_bounded_and_paginated(tmp_path):
    ws,_,_=prepared(tmp_path)
    path=ws.path/'research_report.md'
    path.write_text('\n'.join('ROW '+str(i)+' '+'正文'*600 for i in range(40)),encoding='utf-8')
    before=path.read_bytes()
    first=inspect_delivery(ws)
    assert len(json.dumps(first,ensure_ascii=False))<=10000
    assert first['report']['lines'] and first['report']['next_offset']
    second=inspect_delivery(ws,line_offset=first['report']['next_offset'])
    assert first['report']['lines'][-1]['line_id']<second['report']['lines'][0]['line_id']
    assert path.read_bytes()==before
    with pytest.raises(ValueError):inspect_delivery(ws,query='x'*121)
    with pytest.raises(ValueError):inspect_delivery(ws,line_offset=-1)


def test_removed_report_invalidates_line_bound_requirement_without_crashing(tmp_path):
    ws,_,_=prepared(tmp_path)
    view=inspect_delivery(ws,'传感器')['report']
    record_result(ws,'r1',['1'],report_line_ids=[view['lines'][0]['line_id']],report_sha256=view['sha256'])
    (ws.path/'research_report.md').unlink()
    assert not evaluate_requirements(ws)['requirements'][0]['passed']


def test_free_reverification_names_unverified_parameter_and_preserves_budget(tmp_path):
    from web_agent.completion import verify_existing_delivery
    ws,_,_=prepared(tmp_path)
    atomic_json(ws.path/'result.json',{'task_id':ws.task_id,'status':'partial'})
    atomic_json(ws.path/'usage.json',{'requests':29})
    result=verify_existing_delivery(ws)
    assert result['status']=='partial'
    assert '防水' in result['answer'] and '必要参数仍未核实' in result['answer']
    assert read_json(ws.path/'usage.json')['requests']==29
