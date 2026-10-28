import json
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from web_agent import api,dashboard
from web_agent.completion import accept_delivery,review_binding,evaluate_requirements,current_result
from web_agent.memory import memory_for
from web_agent.persistence import atomic_json,read_json
from test_memory_completion import prepared_workspace

@pytest.mark.parametrize('failure',['pending','missing','bad_excerpt','missing_calculation'])
def test_unsatisfied_requirements_never_complete_or_write_memory(tmp_path,failure):
    ws=prepared_workspace(tmp_path)
    rows=read_json(ws.path/'requirements.json')
    if failure in {'pending','missing'}:rows[0]['result']['status']=failure
    elif failure=='bad_excerpt':rows[0]['result']['report_excerpt']='Fabricated report words'
    else:rows[0]['kind']='calculation';rows[0]['result']['calculation_id']='does_not_exist'
    atomic_json(ws.path/'requirements.json',rows)
    assert not evaluate_requirements(ws)['requirements_supported']
    before=(ws.path/'result.json').read_bytes()
    with pytest.raises(ValueError,match='未满足'):accept_delivery(ws,['r1'],review_binding(ws))
    assert (ws.path/'result.json').read_bytes()==before
    assert not (ws.path/'acceptance.json').exists() and not (ws.path/'.running').exists()
    assert not memory_for(ws).list()


def test_legacy_invalid_completed_result_is_downgraded_even_with_matching_binding(tmp_path):
    ws=prepared_workspace(tmp_path)
    rows=read_json(ws.path/'requirements.json');rows[0]['result']['status']='missing'
    atomic_json(ws.path/'requirements.json',rows)
    atomic_json(ws.path/'acceptance.json',{'binding':review_binding(ws)})
    atomic_json(ws.path/'result.json',{'status':'completed','completion':{'all_requirements_verified':True}})
    memory_for(ws).save('accepted historical task',kind='accepted_task',task_id=ws.task_id)
    fixed=current_result(ws)
    assert fixed['status']=='partial' and not fixed['completion']['all_requirements_verified']
    assert not memory_for(ws).list()


def test_api_and_ui_refuse_acceptance_of_missing_requirement(tmp_path,monkeypatch):
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    ws=prepared_workspace(tmp_path)
    rows=read_json(ws.path/'requirements.json');rows[0]['result']['status']='pending'
    atomic_json(ws.path/'requirements.json',rows)
    with TestClient(api.app) as client:
        preview=client.get('/tasks/study/acceptance').json()
        assert not preview['requirements_supported']
        response=client.post('/tasks/study/accept',json={'requirement_ids':['r1'],'binding':preview['binding']})
        assert response.status_code==409 and client.get('/tasks/study').json()['status']=='needs_review'
    data=dashboard.snapshot('study')
    assert not data['can_accept'] and '待补充' in dashboard.requirements_html(data)


def test_progress_needs_review_is_not_completed():
    data={'task_id':'task','trace':[],'status':'needs_review'}
    waiting=dashboard.stage_html(data)
    assert waiting.count('stage done')==3 and '等待人工验收' in waiting
    data['status']='completed'
    assert dashboard.stage_html(data).count('stage done')==4


def test_current_request_budget_and_errors_visible(tmp_path,monkeypatch):
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    ws=prepared_workspace(tmp_path)
    atomic_json(ws.path/'usage.json',{'requests':8})
    atomic_json(ws.path/'run_limits.json',{'model_requests':12})
    ws.log('tool_error','permission denied',code='permission_denied')
    data=dashboard.snapshot('study');html=dashboard.summary_html(data)
    assert '8 / 12' in html and data['metrics']['errors']==1
    assert '结果待确认' in html


def test_preview_escapes_cells_and_works_without_download(tmp_path,monkeypatch):
    from web_agent.business_excel import export_business_excel
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    ws=prepared_workspace(tmp_path)
    (ws.path/'public_preview.csv').write_text('name,value\n<script>bad</script>,-1.5\n',encoding='utf-8')
    atomic_json(ws.path/'downloads.json',{'public_preview.csv':{'url':'https://example.com/data.csv'}})
    export_business_excel(ws,[{'name':'Data','mode':'table','filename':'public_preview.csv'}])
    preview=dashboard.workbook_preview_html(dashboard.snapshot('study'))
    assert '&lt;script&gt;' in preview and '<script>' not in preview and '-1.5' in preview


def test_ui_outputs_match_all_components_and_no_fixed_live_b():
    import app
    assert len(app.present(dashboard.snapshot()))==len(app.ui_outputs)==17
    assert 'live_B' not in __import__('inspect').getsource(app.show_demo)


def test_readonly_public_demo_cannot_be_accepted_or_resumed(tmp_path,monkeypatch):
    import asyncio,app
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    ws=prepared_workspace(tmp_path)
    atomic_json(ws.path/'demo_meta.json',{'read_only':True})
    data=dashboard.snapshot('study')
    assert not data['can_accept']
    with pytest.raises(ValueError,match='只读'):accept_delivery(ws,['r1'],review_binding(ws))
    async def collect():return [x async for x in app.resume_stream('study')]
    views=asyncio.run(collect())
    assert len(views)==1 and not (ws.path/'usage.json').exists()


def test_public_trace_does_not_require_raw_logs(tmp_path,monkeypatch):
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    ws=prepared_workspace(tmp_path)
    atomic_json(ws.path/'public_trace.json',[{'event':'tool_start','detail':'browser_click','call_id':'1'},{'event':'tool_end','detail':'browser_click','call_id':'1','output_preview':'Sanitized tool receipt','elapsed_seconds':0.2}])
    view=dashboard.trace_html(dashboard.snapshot('study'))
    assert 'Sanitized tool receipt' in view and '参数与实际结果' in view


def test_shared_states_expose_separate_permission_budget_and_approval():
    from web_agent.status import STATUS,completion_outcome
    assert len({STATUS[s][0] for s in ('permission_denied','budget_exceeded','awaiting_review','needs_review','completed')})==5
    assert completion_outcome('needs_review',True)=='needs_review'
    assert completion_outcome('completed',True)=='completed'
    assert completion_outcome('awaiting_review',True)=='pending'
    assert completion_outcome('permission_denied',False)=='failed'
