"""Host-only source recovery hints; exact evidence validation remains unchanged."""
import json
import pytest
from web_agent.workspace import Workspace
from web_agent.source_quote_recovery import quote_recovery_diagnostics
from web_agent.harness import ToolFailure, error_output

URL='https://example.com/catalog/?item=a'
TEXT='介绍\nA19 Pro 芯片\n产品参数：双屏\n不适用\n专利说明\n6 核中央处理器\n查看更多'


def test_same_url_unique_anchor_surfaces_observation_id_and_original_context(tmp_path):
    ws=Workspace(tmp_path,'test')
    oid=ws.observe(URL,TEXT)
    before=(ws.path/'observations'/f'{oid}.json').read_bytes()
    info=quote_recovery_diagnostics(ws,URL,'A19 Pro 芯片\n6 核中央处理器')
    assert info['kind']=='source_quote_recovery'
    assert info['candidates'][0]['observation_id']==oid
    assert info['candidates'][0]['keyword'] in TEXT
    assert info['related_observations']==[oid]
    assert not ws.sources()
    assert (ws.path/'observations'/f'{oid}.json').read_bytes()==before


def test_no_cross_url_or_task_evidence_hints(tmp_path):
    ws=Workspace(tmp_path,'local');ws.observe(URL,TEXT)
    assert not quote_recovery_diagnostics(ws,'https://example.com/other','A19 Pro 芯片')['candidates']
    assert not quote_recovery_diagnostics(Workspace(tmp_path,'other'),URL,'A19 Pro 芯片')['candidates']


def test_repeated_generic_anchor_never_claims_unique_recovery(tmp_path):
    ws=Workspace(tmp_path,'duplicates');ws.observe(URL,'常见参数说明\n常见参数说明\n')
    result=quote_recovery_diagnostics(ws,URL,'常见参数说明')
    assert result['related_observations']
    assert not result['candidates']


def test_fabricated_quote_stays_rejected(tmp_path):
    ws=Workspace(tmp_path,'fake');ws.observe(URL,TEXT)
    with pytest.raises(ValueError,match='摘录不在本任务'):
        ws.record('title',URL,'Invented','A19 Pro 芯片\n6 核中央处理器',require_observation=True)
    assert not ws.sources()


def test_exact_contiguous_quote_still_works(tmp_path):
    ws=Workspace(tmp_path,'correct');oid=ws.observe(URL,TEXT)
    row=ws.record('title',URL,'Chip','A19 Pro 芯片\n产品参数：双屏',require_observation=True)
    assert row['observation_id']==oid
    assert len(ws.sources())==1


def test_structured_error_provides_read_only_recovery_action():
    problem=ToolFailure('evidence_mismatch','摘录不在原文中',details={'kind':'source_quote_recovery',
        'candidates':[{'observation_id':'1234567890abcdef1234','keyword':'example phrase'}],
        'related_observations':['1234567890abcdef1234']})
    result=json.loads(error_output(problem)[7:])
    assert 'record_source_from_observation' in result['next_action']
    assert result['diagnostics']['candidates'][0]['keyword']=='example phrase'
    assert result['code']=='evidence_mismatch' and not result['retryable']
