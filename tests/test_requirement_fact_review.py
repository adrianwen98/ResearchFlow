"""Distinguish source/report binding from factual verification without relaxing gates."""
import json
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json, read_json
from web_agent.completion import evaluate_requirements,define_requirements,record_result
from web_agent.dashboard import requirements_html


def prepared(tmp_path):
    ws=Workspace(tmp_path,'fact_review')
    ws.observe('https://example.org/spec','Battery 10 mAh, Screen 6 inches.')
    ws.record('Source','https://example.org/spec','Device','Battery 10 mAh, Screen 6 inches.', require_observation=True)
    (ws.path/'research_report.md').write_text('# Device\nBattery 10 mAh, Screen 6 inches.',encoding='utf-8')
    define_requirements(ws,[{'description':'Research battery','kind':'research'},
                            {'description':'Research screen','kind':'research'},
                            {'description':'Save report','kind':'artifact','artifact':'research_report.md'}])
    record_result(ws,'r1',['1'],'Battery 10 mAh')
    record_result(ws,'r2',['1'],'Screen 6 inches')
    record_result(ws,'r3',[])
    return ws


def flagged(ws,status='review_required',source='1'):
    atomic_json(ws.path/'business_artifact.json',{
        'provenance':[{'required':True,'status':status,'source_id':source,'label':'Device','column':'Battery'}],
        'structured_review':True})


def test_claimed_satisfied_is_not_machine_certified(tmp_path):
    ws=prepared(tmp_path)
    rows=evaluate_requirements(ws)['requirements']
    assert rows[0]['status']=='satisfied' and rows[0]['passed']
    assert rows[0]['fact_status']=='not_independently_verified'
    assert rows[0]['source_quality_issues']==[]
    assert rows[2]['fact_status']=='not_applicable'


def test_uncertain_cells_are_reported_by_cited_source(tmp_path):
    ws=prepared(tmp_path);flagged(ws)
    checks=evaluate_requirements(ws)
    assert checks['requirements'][0]['fact_status']=='review_required'
    assert checks['requirements'][0]['source_quality_issues']==['Device / Battery']
    assert checks['requirements'][1]['source_quality_issues']==['Device / Battery']
    assert checks['quality_blockers']  # Existing blocker policy stays unchanged.


def test_unrelated_source_does_not_invent_requirement_binding(tmp_path):
    ws=prepared(tmp_path);flagged(ws,source='unrelated')
    checks=evaluate_requirements(ws)['requirements']
    assert not checks[0]['source_quality_issues']
    assert checks[0]['fact_status']=='not_independently_verified'


def test_only_actual_research_needs_fact_review(tmp_path):
    ws=prepared(tmp_path);flagged(ws)
    rows=evaluate_requirements(ws)['requirements']
    assert rows[2]['passed'] and rows[2]['fact_status']=='not_applicable'


def test_reviewer_ui_never_calls_claim_full_verification(tmp_path):
    ws=prepared(tmp_path);flagged(ws)
    html=requirements_html({'requirement_checks':evaluate_requirements(ws)})
    assert '引用校验通过 · 事实待核对' in html
    assert '引用来源还包含待核验字段' in html
    assert 'Device / Battery' in html


def test_escaping_untrusted_source_names(tmp_path):
    ws=prepared(tmp_path)
    atomic_json(ws.path/'business_artifact.json',{
        'provenance':[{'required':True,'status':'review_required','source_id':'1',
                       'label':'<img src=x onerror=alert(1)>','column':'Spec'}],
        'structured_review':True})
    html=requirements_html({'requirement_checks':evaluate_requirements(ws)})
    assert '<img ' not in html and '&lt;img' in html


def test_pending_or_invalid_claim_never_becomes_verified(tmp_path):
    ws=prepared(tmp_path)
    rows=read_json(ws.path/'requirements.json')
    rows[0]['result']['status']='missing';rows[0]['result']['reason']='Unobserved'
    atomic_json(ws.path/'requirements.json',rows)
    check=evaluate_requirements(ws)['requirements'][0]
    assert not check['passed'] and check['fact_status']=='unmet'


def test_no_state_or_budget_mutation(tmp_path):
    ws=prepared(tmp_path);flagged(ws)
    before=(ws.path/'requirements.json').read_bytes()
    first=evaluate_requirements(ws)
    second=evaluate_requirements(ws)
    assert first==second
    assert (ws.path/'requirements.json').read_bytes()==before
    assert not (ws.path/'usage.json').exists()
