"""Grouped CSV statistics can populate comparison rows from a calculation."""
from web_agent.business_excel import export_business_excel
from web_agent.persistence import atomic_json
from web_agent.workspace import Workspace

URL = 'https://example.org/penguins.csv'


def workspace(tmp_path, value=3700.662251655629, method='mean',
              numeric_column='body_mass_g', unit='g'):
    ws = Workspace(tmp_path, 'calc-cells')
    quote = '{"group": "Adelie", "value": %s}' % value
    ws.observe(URL, quote)
    ws.record('calc source', URL, 'group calculation', quote, 'calculation',
              require_observation=True)
    atomic_json(ws.path / 'calculations.json', {
        'calc_test': {'filename': 'public_test.csv', 'group_by': 'species',
                      'numeric_column': numeric_column, 'method': method,
                      'result': {'group_by': 'species', 'method': method,
                                 'numeric_column': numeric_column,
                                 'top_20': [{'group': 'Adelie', 'value': value}]},
                      'observation_id': ws.sources()[0]['observation_id'],
                      'source_id': '1'}})
    return ws, quote, unit


def sheet(value, quote, unit):
    return [{'name': '统计', 'mode': 'comparison', 'columns': ['均值'],
             'fields': {'均值': {'type': 'number', 'unit': unit}},
             'rows': [{'label': 'Adelie', 'cells': {'均值': {
                 'value': value, 'source_id': '1', 'quote': quote}}}]}]


def test_rounded_calculation_value_is_verified_against_the_exact_result(tmp_path):
    ws, quote, unit = workspace(tmp_path)
    result = export_business_excel(
        ws, sheet(3700.6623, quote, unit), allow_structured_review=True,
        validate_only=True)
    provenance = result['provenance'][0]
    assert provenance['status'] == 'verified'
    assert provenance['rule'] == 'calculation_group_value'
    assert provenance['calculation_id'] == 'calc_test'


def test_wrong_calculation_value_is_not_verified(tmp_path):
    ws, quote, unit = workspace(tmp_path)
    result = export_business_excel(
        ws, sheet(3800, quote, unit), allow_structured_review=True,
        validate_only=True)
    assert result['provenance'][0]['status'] != 'verified'
