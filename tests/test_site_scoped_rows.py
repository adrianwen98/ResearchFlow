"""Site-level comparison rows must bind to the same site's observed page."""
from web_agent.business_excel import export_business_excel
from web_agent.workspace import Workspace

URL = 'https://www.python.org/'
QUOTE = ('Python is a programming language that lets you work quickly\n'
         'and integrate systems more effectively.')
TEXT = '''### Rendered page text
Python is a programming language that lets you work quickly
and integrate systems more effectively.
### Snapshot
- heading "Functions Defined" [level=1]
- Page Title: Welcome to Python.org
'''


def workspace(tmp_path):
    ws = Workspace(tmp_path, 'site-rows')
    ws.observe(URL, TEXT)
    ws.record('Python homepage', URL, 'official positioning', QUOTE,
              require_observation=True)
    return ws


def sheet(label='Python（python.org）'):
    return [{'name': '对比', 'mode': 'comparison',
             'columns': ['官方定位/主要用途'],
             'fields': {'官方定位/主要用途': {'type': 'text'}},
             'rows': [{'label': label, 'cells': {
                 '官方定位/主要用途': {
                     'value': 'Python is a programming language',
                     'source_id': '1', 'quote': QUOTE}}}]}]


def test_site_scoped_row_binds_to_same_domain_and_page_title(tmp_path):
    result = export_business_excel(
        workspace(tmp_path), sheet(), allow_structured_review=True, validate_only=True)
    provenance = result['provenance'][0]
    assert provenance['status'] == 'verified'
    assert provenance['owner_binding'] == 'site_domain_and_page_title'


def test_plain_site_label_binds_when_host_and_title_name_the_site(tmp_path):
    result = export_business_excel(
        workspace(tmp_path), sheet('Python'),
        allow_structured_review=True, validate_only=True)
    provenance = result['provenance'][0]
    assert provenance['status'] == 'verified'
    assert provenance['owner_binding'] == 'site_domain_and_page_title'


def test_site_scoped_row_rejects_a_different_domain(tmp_path):
    result = export_business_excel(
        workspace(tmp_path), sheet('Python（nodejs.org）'),
        allow_structured_review=True, validate_only=True)
    assert result['provenance'][0]['status'] == 'review_required'


def test_site_scoped_row_requires_the_page_to_name_the_site(tmp_path):
    result = export_business_excel(
        workspace(tmp_path), sheet('Ruby（python.org）'),
        allow_structured_review=True, validate_only=True)
    assert result['provenance'][0]['status'] == 'review_required'


def test_long_exact_site_value_is_verified(tmp_path):
    data = sheet()
    cell = data[0]['rows'][0]['cells']['官方定位/主要用途']
    cell['value'] = QUOTE
    result = export_business_excel(
        workspace(tmp_path), data, allow_structured_review=True, validate_only=True)
    assert result['provenance'][0]['status'] == 'verified'
    assert result['provenance'][0]['rule'] == 'observed_verbatim_excerpt'


def test_link_snapshot_value_resolves_against_the_observed_page(tmp_path):
    quote = '- link "Docs" [ref=e1] [cursor=pointer]:\n- /url: /doc/'
    ws = Workspace(tmp_path, 'site-link')
    ws.observe(URL, '### Rendered page text\n- link "Docs" [ref=e1] [cursor=pointer]:\n'
                     '- /url: /doc/\n### Snapshot\n- heading "Functions Defined" [level=1]\n'
                     '- Page Title: Welcome to Python.org\n')
    ws.record('Python homepage', URL, 'docs link', quote, require_observation=True)
    data = [{'name': '对比', 'mode': 'comparison',
             'columns': ['官方文档入口'],
             'fields': {'官方文档入口': {'type': 'text'}},
             'rows': [{'label': 'Python', 'cells': {
                 '官方文档入口': {'value': quote, 'source_id': '1', 'quote': quote}}}]}]
    result = export_business_excel(
        ws, data, allow_structured_review=True, validate_only=True)
    provenance = result['provenance'][0]
    assert provenance['status'] == 'verified'
    assert provenance['rule'] == 'observed_link_url'
    assert provenance['normalized_value'] == 'https://www.python.org/doc/'


def test_entry_url_equal_to_the_observed_source_is_verified(tmp_path):
    data = [{'name': '对比', 'mode': 'comparison',
             'columns': ['官方文档入口'],
             'fields': {'官方文档入口': {'type': 'text'}},
             'rows': [{'label': 'Python（python.org）', 'cells': {
                 '官方文档入口': {'value': URL, 'source_id': '1', 'quote': QUOTE}}}]}]
    result = export_business_excel(
        workspace(tmp_path), data, allow_structured_review=True, validate_only=True)
    provenance = result['provenance'][0]
    assert provenance['status'] == 'verified'
    assert provenance['rule'] == 'observed_entry_url'
    assert provenance['owner_binding'] == 'site_domain_and_page_title'
