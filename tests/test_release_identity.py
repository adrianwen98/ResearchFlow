"""Release-page identity: one primary H1 plus a secondary section H1."""
import pytest

from web_agent.workspace import Workspace
from web_agent.business_excel import export_business_excel, BusinessExcelValidationError

URL = 'https://www.python.org/downloads/release/python-3110/'
RELEASE_QUOTE = 'Release date: Oct. 24, 2022'
TEXT = '''### Rendered page text
Python 3.11.0
Release date: Oct. 24, 2022
This is the stable release of Python 3.11.0
### Snapshot
- heading "Python 3.11.0" [level=1]
- heading "Major new features of the 3.11 series, compared to 3.10" [level=1]
- Page Title: Python Release Python 3.11.0 | Python.org
'''


def workspace(tmp_path, text=TEXT, record_quote=RELEASE_QUOTE):
    ws = Workspace(tmp_path, 'release-identity')
    ws.observe(URL, text)
    ws.record('Python 3.11.0 release', URL, 'release notes', record_quote,
              require_observation=True)
    return ws


def sheet(version='Python 3.11.0', version_quote='Python 3.11.0',
          date='Oct. 24, 2022', date_quote=RELEASE_QUOTE):
    return [{'name': '版本特性', 'mode': 'comparison',
             'columns': ['版本', '发布日期'],
             'fields': {'版本': {'type': 'text', 'role': 'document_identity'},
                        '发布日期': {'type': 'text'}},
             'rows': [{'label': 'Python 3.11.0 - 发布日期', 'cells': {
                 '版本': {'value': version, 'source_id': '1', 'quote': version_quote},
                 '发布日期': {'value': date, 'source_id': '1', 'quote': date_quote}}}]}]


def test_primary_h1_verifies_version_and_release_date(tmp_path):
    ws = workspace(tmp_path)
    result = export_business_excel(ws, sheet(), allow_structured_review=True, validate_only=True)
    by_column = {row['column']: row for row in result['provenance']}
    assert by_column['版本']['status'] == 'verified'
    assert by_column['发布日期']['status'] == 'verified'
    assert not by_column['版本'].get('diagnostic')
    assert not by_column['发布日期'].get('diagnostic')


def test_wrong_version_is_still_rejected(tmp_path):
    ws = workspace(tmp_path)
    with pytest.raises(BusinessExcelValidationError):
        export_business_excel(
            ws, sheet(version='Python 3.12.0', version_quote='Python 3.11.0'),
            allow_structured_review=False, validate_only=True)


def test_release_date_quote_with_other_version_is_rejected(tmp_path):
    text = TEXT.replace(
        'This is the stable release of Python 3.11.0',
        'This is the stable release of Python 3.11.0\n'
        'Released on Oct. 24, 2022 with Python 3.12.0')
    ws = workspace(tmp_path, text)
    result = export_business_excel(
        ws, sheet(date_quote='Released on Oct. 24, 2022 with Python 3.12.0'),
        allow_structured_review=True, validate_only=True)
    by_column = {row['column']: row for row in result['provenance']}
    assert by_column['发布日期']['status'] == 'review_required'
    assert by_column['发布日期']['diagnostic']['code'] in {
        'subject_evidence_unbound', 'fact_validation'}


def test_quote_before_primary_h1_is_rejected(tmp_path):
    text = '''### Rendered page text
Release date: Oct. 24, 2022
Python 3.11.0
This is the stable release of Python 3.11.0
### Snapshot
- heading "Python 3.11.0" [level=1]
- heading "Major new features of the 3.11 series, compared to 3.10" [level=1]
- Page Title: Python Release Python 3.11.0 | Python.org
'''
    ws = workspace(tmp_path, text)
    result = export_business_excel(ws, sheet(), allow_structured_review=True, validate_only=True)
    by_column = {row['column']: row for row in result['provenance']}
    assert by_column['发布日期']['status'] == 'review_required'


def test_competing_version_h1_does_not_verify(tmp_path):
    text = TEXT.replace(
        '- heading "Major new features of the 3.11 series, compared to 3.10" [level=1]',
        '- heading "Python 3.12.0" [level=1]')
    ws = workspace(tmp_path, text)
    result = export_business_excel(ws, sheet(), allow_structured_review=True, validate_only=True)
    by_column = {row['column']: row for row in result['provenance']}
    assert by_column['版本']['status'] == 'review_required'
    assert by_column['发布日期']['status'] == 'review_required'


def test_body_date_is_not_accepted_as_release_date(tmp_path):
    text = '''### Rendered page text
Python 3.11.0
Release date: Oct. 24, 2022
This is the stable release of Python 3.11.0
Major new features of the 3.11 series, compared to 3.10
This version was released on Oct. 2, 2023 according to a later note.
### Snapshot
- heading "Python 3.11.0" [level=1]
- heading "Major new features of the 3.11 series, compared to 3.10" [level=1]
- Page Title: Python Release Python 3.11.0 | Python.org
'''
    ws = workspace(tmp_path, text)
    result = export_business_excel(
        ws, sheet(date='Oct. 2, 2023',
                  date_quote='This version was released on Oct. 2, 2023 according to a later note.'),
        allow_structured_review=True, validate_only=True)
    by_column = {row['column']: row for row in result['provenance']}
    assert by_column['发布日期']['status'] == 'review_required'
