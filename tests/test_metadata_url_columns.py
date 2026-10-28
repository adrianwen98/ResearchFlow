"""A redundant row-level source URL cannot override per-cell provenance."""
from web_agent.business_contract import comparison_contract
from web_agent.workspace import Workspace


def test_row_source_url_is_ignored_when_cells_have_different_sources(tmp_path):
    ws = Workspace(tmp_path, 'metadata-url')
    ws.observe('https://www.python.org/', 'Python is a programming language.')
    ws.record('Python home', 'https://www.python.org/', 'homepage quote',
              'Python is a programming language.', require_observation=True)
    ws.observe('https://docs.python.org/3/', 'Welcome! This is the official documentation.')
    ws.record('Python docs', 'https://docs.python.org/3/', 'docs quote',
              'Welcome! This is the official documentation.', require_observation=True)
    sheets = [{'name': '对比', 'mode': 'comparison',
               'columns': ['官方定位/主要用途', '官方文档入口', '来源网址'],
               'fields': {'官方定位/主要用途': {'type': 'text'},
                          '官方文档入口': {'type': 'text'},
                          '来源网址': {'type': 'text'}},
               'rows': [{'label': 'Python（python.org）', 'cells': {
                   '官方定位/主要用途': {'value': 'Python is a programming language.',
                                         'source_id': '1',
                                         'quote': 'Python is a programming language.'},
                   '官方文档入口': {'value': 'https://docs.python.org/3/',
                                   'source_id': '2',
                                   'quote': 'Welcome! This is the official documentation.'},
                   '来源网址': 'https://www.python.org/ ; https://docs.python.org/3/'}}]}]
    output, changes = comparison_contract(ws, sheets)
    spec = output[0]
    assert '来源网址' not in spec['columns']
    assert spec['rows'][0]['cells']['官方文档入口']['source_id'] == '2'
    assert any(change['rule'] == 'row_metadata_url_ignored' for change in changes)
