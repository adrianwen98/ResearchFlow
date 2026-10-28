"""Offline execution contracts: no paid API, browser, or fabricated acceptance."""
import copy
import json
from dataclasses import replace
import pytest
from test_graph import settings,ScriptModel,call,empty_browser
from test_structured_delivery import source_ws,captured
from test_web_workbench import client
from web_agent.persistence import atomic_json,read_json
from web_agent.delivery_pipeline import stage_business_rows,finalize_delivery
from web_agent.business_excel import BusinessExcelValidationError,verify_business_excel
from web_agent.completion import define_requirements,evaluate_requirements
from web_agent.delivery_status import aggregate_delivery
from web_agent.runner import run_task


def batch(label='Item',source='1'):
    return [{'name':'Facts','mode':'comparison','columns':['Screen'],
             'fields':{'Screen':{'type':'number','unit':'inch','required':True}},
             'rows':[{'label':label,'cells':{'Screen':{'value':6,'source_id':source,'quote':'Screen 6 inches.'}}}]}]


def test_batches_validate_without_export_and_reject_atomically(tmp_path):
    ws=source_ws(tmp_path)
    stage_business_rows(ws,batch())
    before=(ws.path/'business_rows.json').read_bytes()
    with pytest.raises(BusinessExcelValidationError):stage_business_rows(ws,batch('Bad','missing'))
    assert read_json(ws.path/'business_batch_validation.json')['issues_total']==1
    assert (ws.path/'business_rows.json').read_bytes()==before
    assert not (ws.path/'business_draft.json').exists()
    assert not (ws.path/'task_result.xlsx').exists()
    stage_business_rows(ws,batch('Second'))
    stage_business_rows(ws,batch('Item'))
    assert len(read_json(ws.path/'business_rows.json')['sheets'][0]['rows'])==2
    receipt=finalize_delivery(ws,'threshold')
    assert not receipt['errors'] and verify_business_excel(ws)==[]
    assert len(read_json(ws.path/'business_artifact.json')['sheets'][0]['rows'])==2


def test_flat_rows_update_unique_saved_sheet(tmp_path):
    ws=source_ws(tmp_path)
    stage_business_rows(ws,batch())
    flat=[
        {'label':'Third','cells':{'Screen':{'value':6,'source_id':'1','quote':'Screen 6 inches.'}}},
        {'label':'Fourth','cells':{'Screen':{'value':6,'source_id':'1','quote':'Screen 6 inches.'}}},
    ]
    result=stage_business_rows(ws,flat,recover_invalid=True)
    assert result['saved_rows']==3
    assert any(n.get('rule')=='flat_rows_to_comparison_sheet' for n in result['normalizations'])
    persisted=read_json(ws.path/'business_rows.json')
    assert persisted['sheets'][0]['name']=='Facts'
    assert [r['label'] for r in persisted['sheets'][0]['rows']]==['Item','Third','Fourth']


def test_flat_rows_without_prior_sheet_use_generic_table(tmp_path):
    ws=source_ws(tmp_path)
    flat=[{'label':'Item','cells':{'说明':{'value':'Screen 6 inches.','source_id':'1','quote':'Screen 6 inches.'}}}]
    result=stage_business_rows(ws,flat,recover_invalid=True)
    assert result['saved_rows']==1
    assert any(n.get('rule')=='flat_rows_to_comparison_sheet' and n.get('sheet')=='对比表'
               for n in result['normalizations'])
    persisted=read_json(ws.path/'business_rows.json')
    assert persisted['sheets'][0]['name']=='对比表'
    assert persisted['sheets'][0]['columns']==['说明']


def test_flat_rows_ambiguous_prior_sheets_rejected_atomically(tmp_path):
    ws=source_ws(tmp_path)
    stage_business_rows(ws,batch())
    second=batch('Other');second[0]['name']='Facts2'
    stage_business_rows(ws,second)
    before=(ws.path/'business_rows.json').read_bytes()
    flat=[{'label':'Third','cells':{'Screen':{'value':6,'source_id':'1','quote':'Screen 6 inches.'}}}]
    with pytest.raises(ValueError,match='多个已保存表'):
        stage_business_rows(ws,flat,recover_invalid=True)
    assert (ws.path/'business_rows.json').read_bytes()==before


def test_complete_sheet_without_mode_infers_from_shape(tmp_path):
    from web_agent.business_contract import comparison_contract
    ws=source_ws(tmp_path)
    table,table_changes=comparison_contract(ws,[{'name':'下载表','filename':'public_x.csv'}])
    assert table[0]['mode']=='table' and table_changes[0]['rule']=='table_shape_to_mode'
    calc,calc_changes=comparison_contract(ws,[{'name':'统计','calculation_id':'calc1'}])
    assert calc[0]['mode']=='calculation' and calc_changes[0]['rule']=='calculation_shape_to_mode'
    rows,row_changes=comparison_contract(ws,[{
        'name':'对比','columns':['Screen'],
        'rows':[{'label':'Item','cells':{'Screen':{'value':6,'source_id':'1','quote':'Screen 6 inches.'}}}],
    }])
    assert rows[0]['mode']=='comparison' and row_changes[0]['rule']=='comparison_shape_to_mode'
    with pytest.raises(ValueError,match='mode必须为'):
        comparison_contract(ws,[{'name':'x','filename':'a.csv','calculation_id':'c'}])


def test_flat_rows_mismatched_cell_keys_still_rejected(tmp_path):
    ws=source_ws(tmp_path)
    flat=[
        {'label':'Item','cells':{'Screen':{'value':6,'source_id':'1','quote':'Screen 6 inches.'}}},
        {'label':'Second','cells':{}},
    ]
    with pytest.raises(ValueError):
        stage_business_rows(ws,flat,recover_invalid=True)
    assert not (ws.path/'business_rows.json').exists()


@pytest.mark.asyncio
async def test_graph_forces_delivery_before_next_paid_turn(settings):
    ws,data=captured(settings.output_dir,'forced-pc')
    stage_business_rows(ws,data['sheets'])
    define_requirements(ws,[{'description':'核实未来降价趋势','kind':'research'},
                            {'description':'生成配置价格表','kind':'artifact','artifact':'task_result.xlsx'}])
    atomic_json(ws.path/'usage.json',{'requests':27,'input_tokens':0,'output_tokens':0})
    model=ScriptModel([call('update_plan',{'steps':['keep researching forever']})])
    result=await run_task(data['task'],'forced-pc',replace(settings,max_model_calls=30),model=model,browser_factory=empty_browser)
    assert model.calls==1 and result.metrics['requests']==28
    assert result.status=='partial' and verify_business_excel(ws)==[]
    assert result.completion['delivery']['outcome']=='partial'
    assert not result.completion['delivery']['semantic_accepted']
    requirement_rows = read_json(ws.path/'requirements.json')
    assert [(r['kind'], r['result']['status']) for r in requirement_rows] == [
        ('research', 'missing'), ('artifact', 'satisfied')
    ]
    assert requirement_rows[1]['artifact'] == 'task_result.xlsx'
    assert requirement_rows[1]['result'].get('verified_by') == 'host_artifact_integrity'
    assert read_json(ws.path/'delivery_finalization.json')['additional_model_requests']==0


@pytest.mark.asyncio
async def test_tool_budget_also_forces_free_delivery(settings):
    ws=source_ws(settings.output_dir)
    stage_business_rows(ws,batch())
    atomic_json(ws.path/'tool_usage.json',{'attempts':12,'debited_ids':[]})
    model=ScriptModel([])
    result=await run_task('Compare saved screens','source',replace(settings,max_tool_calls=12,max_model_calls=30),model=model,browser_factory=empty_browser)
    assert model.calls==0 and result.metrics['tool_attempts']==12
    assert result.status == 'budget_exceeded'
    assert result.completion['delivery']['usable'], result.completion['delivery']

    assert (ws.path / 'task_result.xlsx').is_file()
    assert (ws.path / 'research_sources.xlsx').is_file()
    assert (ws.path / 'research_report.md').is_file()

    assert read_json(ws.path / 'delivery_finalization.json')['additional_model_requests'] == 0


def test_one_export_failure_does_not_suppress_other_outputs(tmp_path,monkeypatch):
    ws=source_ws(tmp_path);stage_business_rows(ws,batch())
    def fail():raise OSError('injected workbook lock')
    monkeypatch.setattr(ws,'export_excel',fail)
    receipt=finalize_delivery(ws,'stop')
    assert receipt['errors'][0]['artifact']=='research_sources.xlsx'
    assert (ws.path/'task_result.xlsx').is_file() and (ws.path/'research_report.md').is_file()
    assert verify_business_excel(ws)==[]


def test_invalid_draft_preserved_and_missing_business_blocks_acceptance(tmp_path):
    ws=source_ws(tmp_path)
    invalid=batch(source='missing')
    atomic_json(ws.path/'business_draft.json',{'draft_id':'bad','sheets':invalid})
    receipt=finalize_delivery(ws,'stop')
    assert receipt['errors'] and receipt['business_missing']
    assert read_json(ws.path/'business_draft.json')['sheets']==invalid
    assert (ws.path/'research_sources.xlsx').is_file() and (ws.path/'research_report.md').is_file()
    check=ws.verify_delivery()
    assert not check['artifacts_valid'] and 'business_excel_missing' in check['issues']


def test_usable_files_with_unmet_requirements_never_label_completed(tmp_path):
    ws=source_ws(tmp_path)
    define_requirements(ws,[{'description':'尚未核实的结论','kind':'research'}])
    finalize_delivery(ws,'stop')
    requirements=evaluate_requirements(ws)
    for status in ('partial','needs_review','budget_exceeded','completed'):
        state=aggregate_delivery(ws,status,ws.verify_delivery(),requirements)
        assert state['usable'] and state['outcome']=='partial'
        assert not state['semantic_accepted'] and '已完成' not in state['label']


@pytest.mark.asyncio
async def test_crashed_model_still_exports_staged_rows_without_retry(settings):
    ws=source_ws(settings.output_dir);stage_business_rows(ws,batch())
    model=ScriptModel([])
    result=await run_task('Compare saved screens','source',settings,model=model,browser_factory=empty_browser)
    assert model.calls==1 and result.status=='error'
    assert result.completion['delivery']['outcome']=='partial'
    assert (ws.path/'task_result.xlsx').is_file() and (ws.path/'research_report.md').is_file()


@pytest.mark.asyncio
async def test_explicit_resume_can_correct_using_remaining_reserve(settings):
    ws=source_ws(settings.output_dir);stage_business_rows(ws,batch())
    atomic_json(ws.path/'usage.json',{'requests':28,'input_tokens':0,'output_tokens':0})
    cfg=replace(settings,max_model_calls=30)
    await run_task('Compare saved screens','source',cfg,model=ScriptModel([]),browser_factory=empty_browser)
    model=ScriptModel([call('export_report',{'markdown_report':'# 修订结果\nScreen 6 inches.'},'revision')])
    result=await run_task('修订报告','source',cfg,resume=True,model=model,browser_factory=empty_browser)
    assert model.calls==1 and result.metrics['requests']==29
    assert '修订结果' in (ws.path/'research_report.md').read_text(encoding='utf-8')


@pytest.mark.asyncio
async def test_large_tool_batch_cannot_spend_delivery_reserve(settings):
    from langchain_core.messages import AIMessage
    ws=source_ws(settings.output_dir)
    atomic_json(ws.path/'tool_usage.json',{'attempts':7,'debited_ids':[]})
    calls=[{'id':str(i),'name':'update_plan','args':{'steps':[str(i)]},'type':'tool_call'} for i in range(8)]
    model=ScriptModel([AIMessage(content='',tool_calls=calls)])
    result=await run_task('Keep tool reserve available','source',replace(settings,max_model_calls=30,max_tool_calls=12),model=model,browser_factory=empty_browser)
    assert model.calls==1 and result.metrics['tool_attempts']==12
    # At 7/12, exactly five attempts can run; the remaining three must not dispatch.
    journal=read_json(ws.path/'tool_journal.json',{})
    assert set(journal)=={str(i) for i in range(5)}
    assert all(journal[str(i)]['name']=='update_plan' for i in range(5))
    assert read_json(ws.path/'plan.json')==['4']
    assert read_json(ws.path/'delivery_finalization.json')['additional_model_requests']==0


def test_history_corrects_legacy_completed_label_without_rewriting_result(client):
    c,cfg=client;ws=source_ws(cfg.output_dir)
    atomic_json(ws.path/'task.json',{'task':'Legacy incomplete research'})
    finalize_delivery(ws,'stop')
    saved={'status':'partial','completion':{'delivery':{'outcome':'completed_with_warnings','label':'已完成（有告警）','usable':True}}}
    atomic_json(ws.path/'result.json',saved)
    history=c.get('/tasks').json()['tasks'][0]
    assert history['delivery_outcome']=='partial' and history['status_label']=='部分完成'
    assert read_json(ws.path/'result.json')==saved
    diagnostics=c.get('/tasks/source/files/delivery_finalization.json')
    assert diagnostics.status_code==200 and diagnostics.json()['additional_model_requests']==0
