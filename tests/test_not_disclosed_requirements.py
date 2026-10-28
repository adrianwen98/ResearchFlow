"""An allowed, evidence-backed 'not published' answer completes the requirement."""
import hashlib
import pytest
from web_agent.completion import (
    define_requirements, evaluate_requirements, rebind_unchanged_excerpts,
    record_result,
)
from web_agent.workspace import Workspace

URL = 'https://example.org/release'


def workspace(tmp_path):
    ws = Workspace(tmp_path, 'not-disclosed')
    ws.observe(URL, 'Release date: Oct. 2, 2023\nNo LTS end date is published.')
    ws.record('release page', URL, 'release metadata',
              'No LTS end date is published.', require_observation=True)
    define_requirements(ws, [{'description': '核实官方 LTS 截止日期，未公布则说明',
                              'kind': 'research'}])
    (ws.path / 'research_report.md').write_text(
        '# Report\n\nRelease date: Oct. 2, 2023\n'
        '官方未公布 LTS 截止日期，不做推断。\n', encoding='utf-8')
    return ws


def test_evidence_backed_not_disclosed_answer_passes(tmp_path):
    ws = workspace(tmp_path)
    row = record_result(
        ws, 'r1', ['1'], '官方未公布 LTS 截止日期，不做推断。',
        status='not_disclosed', reason='官方页面没有 LTS 截止日期')
    assert row['result']['status'] == 'not_disclosed'
    checks = evaluate_requirements(ws)
    assert checks['requirements_supported']
    assert checks['requirements'][0]['passed']
    assert checks['requirements'][0]['fact_status'] == 'not_disclosed'


def test_not_disclosed_still_requires_source_and_report_excerpt(tmp_path):
    ws = workspace(tmp_path)
    with pytest.raises(ValueError, match='来源'):
        record_result(ws, 'r1', [], '官方未公布 LTS 截止日期。',
                      status='not_disclosed', reason='未公布')


def test_not_disclosed_excerpt_survives_deterministic_report_rebind(tmp_path):
    ws = Workspace(tmp_path, 'not-disclosed-rebind')
    ws.observe(URL, 'No LTS end date is published.')
    ws.record('release page', URL, 'release metadata',
              'No LTS end date is published.', require_observation=True)
    define_requirements(ws, [{'description': '核实官方 LTS 截止日期，未公布则说明',
                              'kind': 'research'}])
    report = '# Report\n\n官方未公布 LTS 截止日期。\n'
    (ws.path / 'research_report.md').write_text(report, encoding='utf-8')
    old_sha = hashlib.sha256((ws.path / 'research_report.md').read_bytes()).hexdigest()
    record_result(ws, 'r1', ['1'], status='not_disclosed',
                  report_line_ids=[3], report_sha256=old_sha,
                  reason='官方未公布')
    (ws.path / 'research_report.md').write_text(report + '\n补充说明。\n', encoding='utf-8')
    new_sha = hashlib.sha256((ws.path / 'research_report.md').read_bytes()).hexdigest()
    assert rebind_unchanged_excerpts(ws, old_sha, new_sha) == ['r1']
    assert evaluate_requirements(ws)['requirements_supported']
