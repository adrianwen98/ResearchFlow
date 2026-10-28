"""Regression for a recurring batch conflict observed in a real agent run."""
import copy
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from web_agent.delivery_pipeline import stage_business_rows
from web_agent.persistence import read_json, atomic_json


def _row(label='Product A', value=None):
    return {'label': label, 'cells': {'CPU count': {'value': value, 'reason': 'unavailable'}}}


def _sheet(fields=None, cols=None):
    spec = {'name': 'Compare', 'mode': 'comparison', 'columns': cols or ['CPU count'],
            'rows': [_row()]}
    if fields is not None:
        spec['fields'] = copy.deepcopy(fields)
    return spec


@pytest.fixture
def ws(tmp_path, monkeypatch):
    workspace = SimpleNamespace(path=tmp_path, sources=lambda: [], log=lambda *a, **kw: None)
    import web_agent.delivery_compiler as compiler
    import web_agent.business_excel as excel_module

    # Replace expensive workbook compilation/verification, not comparison_contract.
    # This keeps the actual schema normalizer and stage merge in the test.
    monkeypatch.setattr(compiler, 'compile_comparisons', lambda ws, sheets, **kw: (sheets, []))
    monkeypatch.setattr(excel_module, 'export_business_excel', lambda ws, sheets, **kw: {'provenance': [], 'rows': sum(len(s['rows']) for s in sheets)})
    return workspace


def test_omitted_fields_reuses_saved_contract(ws):
    first = stage_business_rows(ws, [_sheet({'CPU count': {'type': 'number', 'unit': 'count'}})])
    before = read_json(ws.path/'business_rows.json')['sheets'][0]['fields']
    second = stage_business_rows(ws, [_sheet()])
    after = read_json(ws.path/'business_rows.json')['sheets'][0]['fields']
    assert before == after
    assert second['reused_existing_fields'] == ['Compare']
    assert second['version'] == first['version']


def test_empty_fields_reuses_contract_without_mutating_input(ws):
    stage_business_rows(ws, [_sheet({'CPU count': {'type': 'number', 'unit': 'count'}})])
    incoming = _sheet({})
    snapshot = copy.deepcopy(incoming)
    result = stage_business_rows(ws, [incoming])
    assert result['reused_existing_fields'] == ['Compare']
    assert incoming == snapshot


def test_explicit_conflicting_fields_reuse_saved_contract(ws):
    stage_business_rows(ws, [_sheet({'CPU count': {'type': 'number', 'unit': 'count'}})])
    saved = read_json(ws.path/'business_rows.json')['sheets'][0]['fields']
    result = stage_business_rows(ws, [_sheet({'CPU count': {'type': 'number', 'required': True}})])
    after = read_json(ws.path/'business_rows.json')['sheets'][0]['fields']
    assert after == saved
    assert 'Compare' in result['reused_existing_fields']
    assert any(n.get('rule') == 'saved_fields_reused' for n in result['normalizations'])


def test_mismatched_columns_never_inherit_existing_contract(ws):
    stage_business_rows(ws, [_sheet({'CPU count': {'type': 'number', 'unit': 'count'}})])
    original = (ws.path/'business_rows.json').read_bytes()
    with pytest.raises(Exception):
        stage_business_rows(ws, [_sheet(cols=['different'])])
    assert original == (ws.path/'business_rows.json').read_bytes()


def test_new_sheet_omitted_fields_uses_existing_validation(ws):
    result=stage_business_rows(ws, [_sheet()])
    assert result['reused_existing_fields'] == []
    assert read_json(ws.path/'business_rows.json')['sheets'][0]['fields'] == {}


def test_runner_has_phase_telemetry_without_changing_task_timeout():
    source=(Path(__file__).resolve().parent.parent / "web_agent" / "runner.py").read_text(encoding="utf-8")
    for marker in ('graph_invoke_returned','browser_context_closed','run_async_context_closed'):
        assert marker in source
    assert 'asyncio.timeout(remaining_time)' in source
