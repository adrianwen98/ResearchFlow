"""Contract tests for exact, host-rechecked research -> business cell links."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from web_agent.requirement_fact_links import normalize_fact_refs, audit_fact_refs


def ref():
    return {'sheet':'比较','label':'产品A','column':'价格'}


def artifact(tmp_path, status='verified', source='s1', price_status=None):
    payload=b'xlsx-placeholder-not-a-real-workbook'  # hash binding only; workbook verified elsewhere
    (tmp_path/'task_result.xlsx').write_bytes(payload)
    p={'sheet':'比较','row':2,'column':'价格','label':'产品A','source_id':source,'status':status,'required':False}
    if price_status:p['price_status']=price_status
    return SimpleNamespace(path=tmp_path), {'sha256':hashlib.sha256(payload).hexdigest(),'provenance':[p]}


def test_normalize_preserves_exact_identity():
    assert normalize_fact_refs([ref()])==[ref()]


def test_reject_duplicate():
    with pytest.raises(ValueError,match='重复'):normalize_fact_refs([ref(),ref()])


def test_reject_empty_and_excessive():
    for value in [[],[ref()]*61,{},'product']:
        with pytest.raises(ValueError):normalize_fact_refs(value)


def test_reject_unknown_or_missing_keys():
    for row in [{'sheet':'比较','column':'价格'}, {**ref(),'status':'verified'}]:
        with pytest.raises(ValueError):normalize_fact_refs([row])


def test_reject_bad_names():
    for value in [1,' 产品A','', 'a'*201]:
        item=ref();item['label']=value
        with pytest.raises(ValueError):normalize_fact_refs([item])


def test_verified_requires_exact_cell_source_and_hash(tmp_path):
    ws,manifest=artifact(tmp_path)
    out=audit_fact_refs(ws,[ref()],['s1'],manifest)
    assert out['all_verified'] and out['checks'][0]['status']=='verified'


def test_source_not_in_requirement_is_rejected(tmp_path):
    ws,manifest=artifact(tmp_path)
    out=audit_fact_refs(ws,[ref()],['s2'],manifest)
    assert not out['all_verified'] and out['checks'][0]['status']=='source_mismatch'


def test_review_and_price_unverified_are_rejected(tmp_path):
    ws,manifest=artifact(tmp_path,status='review_required')
    assert audit_fact_refs(ws,[ref()],['s1'],manifest)['checks'][0]['status']=='not_verified'
    ws,manifest=artifact(tmp_path,price_status='unverified')
    assert audit_fact_refs(ws,[ref()],['s1'],manifest)['checks'][0]['status']=='not_verified'


def test_missing_and_ambiguous_cells_are_rejected(tmp_path):
    ws,manifest=artifact(tmp_path)
    other={**ref(),'column':'不存在'}
    assert audit_fact_refs(ws,[other],['s1'],manifest)['checks'][0]['status']=='missing'
    manifest['provenance'].append(manifest['provenance'][0].copy())
    assert audit_fact_refs(ws,[ref()],['s1'],manifest)['checks'][0]['status']=='ambiguous'


def test_tampered_workbook_hash_rejected(tmp_path):
    ws,manifest=artifact(tmp_path)
    (tmp_path/'task_result.xlsx').write_bytes(b'changed')
    assert not audit_fact_refs(ws,[ref()],['s1'],manifest)['all_verified']


def test_missing_workbook_and_invalid_structure_are_rejected(tmp_path):
    ws,manifest=artifact(tmp_path)
    (tmp_path/'task_result.xlsx').unlink()
    assert not audit_fact_refs(ws,[ref()],['s1'],manifest)['all_verified']
    ws,manifest=artifact(tmp_path)
    assert not audit_fact_refs(ws,[{'sheet':'比较'}],['s1'],manifest)['all_verified']


def test_integration_evaluate_research_requirement_fact_links(tmp_path):
    from web_agent.completion import evaluate_requirements
    ws,manifest=artifact(tmp_path,status='review_required')
    ws.sources=lambda:[{'id':'s1'}]
    (tmp_path/'business_artifact.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf8')
    (tmp_path/'research_report.md').write_text('这是报告中的原文事实。',encoding='utf8')
    req=[{'id':'r1','kind':'research','description':'研究并核验产品A价格','result':{'status':'satisfied',
         'source_ids':['s1'],'report_excerpt':'报告中的原文事实','fact_refs':[ref()]}}]
    (tmp_path/'requirements.json').write_text(json.dumps(req,ensure_ascii=False),encoding='utf8')
    check=evaluate_requirements(ws)
    assert not check['requirements_supported'] and check['missing_requirements']==['r1']
    assert check['requirements'][0]['fact_link_issues']
    # Same report+source after verified cell export must re-evaluate from file.
    manifest['provenance'][0]['status']='verified'
    (tmp_path/'business_artifact.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf8')
    check=evaluate_requirements(ws)
    assert check['requirements_supported'] and check['requirements'][0]['fact_status']=='linked_cells_verified'


def test_integration_record_result_preserves_links_on_report_update(tmp_path):
    from web_agent.completion import record_result
    ws,manifest=artifact(tmp_path)
    ws.sources=lambda:[{'id':'s1'}]
    (tmp_path/'research_report.md').write_text('报告中的事实。',encoding='utf8')
    req=[{'id':'r1','kind':'research','description':'研究事实','result':{'status':'pending'}}]
    (tmp_path/'requirements.json').write_text(json.dumps(req,ensure_ascii=False),encoding='utf8')
    record_result(ws,'r1',['s1'],'报告中的事实',fact_refs=[ref()])
    saved=record_result(ws,'r1',['s1'],'报告中的事实')
    assert saved['result']['fact_refs']==[ref()]
    with pytest.raises(ValueError):record_result(ws,'r1',['s1'],'报告中的事实',fact_refs=[{'sheet':'比较'}])


def test_integration_legacy_research_without_fact_refs_unchanged(tmp_path):
    from web_agent.completion import evaluate_requirements
    ws,manifest=artifact(tmp_path,status='review_required')
    ws.sources=lambda:[{'id':'s1'}]
    (tmp_path/'business_artifact.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf8')
    (tmp_path/'research_report.md').write_text('报告中的原文。',encoding='utf8')
    req=[{'id':'r1','kind':'research','description':'旧需求','result':{'status':'satisfied',
         'source_ids':['s1'],'report_excerpt':'报告中的原文'}}]
    (tmp_path/'requirements.json').write_text(json.dumps(req,ensure_ascii=False),encoding='utf8')
    check=evaluate_requirements(ws)
    assert check['requirements_supported']
    assert 'fact_links' not in check['requirements'][0]
