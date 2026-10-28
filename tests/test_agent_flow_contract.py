"""Regression cases from failed real calls; deterministic, offline, no model."""
import inspect
from web_agent.delivery_schema import field_for
from web_agent.owner_guard import owner_matches
from web_agent.source_recovery_gate import needs_source_recovery


def test_text_resolution_does_not_become_a_number():
    actual=field_for('分辨率', {'分辨率': {'type':'text','unit':'px'}})
    assert actual['type']=='text' and 'unit' not in actual


def test_text_refresh_rate_hint_is_not_scalar():
    actual=field_for('刷新率', {'刷新率': {'type':'text','unit':'Hz'}})
    assert actual['type']=='text' and 'unit' not in actual


def test_number_invalid_unit_still_rejected():
    import pytest
    with pytest.raises(ValueError,match='单位不受支持'):
        field_for('像素',{'像素':{'type':'number','unit':'px'}})


def test_semantic_inferred_screen_not_downgraded():
    actual=field_for('屏幕尺寸', {'屏幕尺寸':{'type':'text','unit':'inch'}})
    assert actual['type']=='number' and actual['unit']=='inch'


def test_price_role_not_downgraded():
    actual=field_for('官方起售价', {'官方起售价': {'type':'text'}})
    assert actual['type']=='price'


def test_region_label_matches_only_grounded_url_and_exact_model():
    cn={'url':'https://www.apple.com.cn/iphone/compare/'}
    assert owner_matches('iPhone 17（中国大陆）','iPhone 17',cn)
    assert owner_matches('iPhone 17 Pro（中国大陆）','iPhone 17 Pro',cn)
    assert not owner_matches('iPhone 17 Pro（中国大陆）','iPhone 17',cn)
    assert not owner_matches('iPhone 17（中国大陆）','iPhone 17 Pro',cn)
    assert not owner_matches('iPhone 17（日本）','iPhone 17',cn)
    assert not owner_matches('iPhone 17（中国大陆）','iPhone 17',{'url':'https://example.com/'})
    assert not owner_matches('iPhone 17（中国大陆）Pro','iPhone 17',cn)


def test_source_recovery_only_after_saved_quote_failure():
    import json
    detail={'kind':'source_quote_recovery','related_observations':['a'*20],'candidates':[]}
    bad={'name':'record_source','error_code':'evidence_mismatch',
         'output':'ERROR: '+json.dumps({'code':'evidence_mismatch','diagnostics':detail})}
    good={'name':'inspect_saved_pages','error_code':'','output':'saved pages'}
    assert needs_source_recovery({'call1':bad})
    assert not needs_source_recovery({'call1':bad,'call2':good})
    not_saved={'name':'record_source','error_code':'evidence_mismatch',
               'output':'ERROR: '+json.dumps({'code':'evidence_mismatch',
                   'diagnostics':{'kind':'source_quote_recovery','related_observations':[]}})}
    assert not needs_source_recovery({'call1':not_saved})
    assert not needs_source_recovery({'call1':{'name':'record_source',
                 'error_code':'invalid_input','output':bad['output']}})
    assert not needs_source_recovery({'call1':{'name':'save_business_rows',
                 'error_code':'evidence_mismatch','output':bad['output']}})
    assert not needs_source_recovery({'call1':{'name':'record_source',
                 'error_code':'evidence_mismatch','output':'ERROR: quote rejected'}})
    assert not needs_source_recovery({})


def test_graph_connects_recovery_router_without_changing_fail_guard():
    from pathlib import Path
    code=(Path(__file__).resolve().parents[1]/'web_agent/graph.py').read_text(encoding='utf-8')
    assert "needs_source_recovery(read_json(ledger_path, {}))" in code
    assert "'record_source_from_observation'" in code
    assert 'stop_reason(journal, workspace)' in code
    assert 'settings.max_model_calls' in code


def test_comparison_contract_preserves_composite_text(tmp_path):
    from web_agent.business_contract import comparison_contract
    class DummyWorkspace:
        def sources(self): return []
    proposal=[{'name':'研究比较','mode':'comparison','columns':['分辨率'],
               'fields':{'分辨率':{'type':'text','unit':'px'}},
               'rows':[{'label':'设备A','cells':{'分辨率':{
                   'value':'2622 x 1206 像素，460 ppi',
                   'quote':'2622 x 1206 像素分辨率，460 ppi'}}}]}]
    output,changes=comparison_contract(DummyWorkspace(),proposal)
    assert output[0]['fields']['分辨率']['type']=='text'
    assert 'unit' not in output[0]['fields']['分辨率']
    assert output[0]['rows'][0]['cells']['分辨率']['value']=='2622 x 1206 像素，460 ppi'


def test_dom_attribution_keeps_product_cell_boundary(monkeypatch):
    import sys
    from types import ModuleType
    from web_agent.owner_guard import dom_matrix_subject_bound
    dom=ModuleType('web_agent.dom_matrix')
    dom.bound_rows=lambda *_: [
        {'owner':'iPhone 17', 'field':'芯片', 'value':'A19 芯片'},
        {'owner':'iPhone 17 Pro', 'field':'芯片', 'value':'A19 Pro 芯片'},
    ]
    evidence=ModuleType('web_agent.evidence_text')
    evidence.contains_observed_excerpt=lambda text,quote: quote in text
    monkeypatch.setitem(sys.modules,'web_agent.dom_matrix',dom)
    monkeypatch.setitem(sys.modules,'web_agent.evidence_text',evidence)
    source={'url':'https://www.apple.com.cn/iphone/compare/'}
    assert dom_matrix_subject_bound(None,source,'iPhone 17（中国大陆）','芯片','A19 芯片',{}, {})
    assert dom_matrix_subject_bound(None,source,'iPhone 17 Pro（中国大陆）','芯片','A19 Pro 芯片',{}, {})
    assert not dom_matrix_subject_bound(None,source,'iPhone 17（中国大陆）','芯片','A19 Pro 芯片',{}, {})
    assert not dom_matrix_subject_bound(None,source,'iPhone 17 Pro（日本）','芯片','A19 Pro 芯片',{}, {})
    assert not dom_matrix_subject_bound(None,source,'iPhone 17 Pro（中国大陆）','芯片','A19 Pro 芯片\n6 核 CPU',{}, {})
