"""OCR'd official table rows bind by year, unit header and literal row values."""
from web_agent.business_excel import export_business_excel
from web_agent.workspace import Workspace

URL = 'https://example.org/yearbook/c12-05.jpg'
TEXT = ('OCR 机器识别，表格行按坐标分组；数字和列名仍需逐项核对。\n'
        '年份 | （万吨） | 氮肥 | 磷肥 | 钾肥\n'
        '2015 | 6022.6 | 2361.6 | 843.1 | 642.3')
ROW = '2015 | 6022.6 | 2361.6 | 843.1 | 642.3'


def workspace(tmp_path):
    ws = Workspace(tmp_path, 'ocr-table')
    ws.observe(URL, TEXT, 'image_ocr')
    ws.record('2025 yearbook table', URL, 'national fertilizer table', ROW,
              '官方统计年鉴', require_observation=True)
    return ws


def sheet():
    return [{'name': '化肥对比', 'mode': 'comparison',
             'columns': ['年份', '氮肥（万吨）'],
             'fields': {'年份': {'type': 'text'},
                        '氮肥（万吨）': {'type': 'number', 'unit': '万吨'}},
             'rows': [{'label': '2015年全国', 'cells': {
                 '年份': {'value': '2015', 'source_id': '1', 'quote': ROW},
                 '氮肥（万吨）': {'value': 2361.6, 'source_id': '1', 'quote': ROW}}}]}]


def test_ocr_year_row_is_verified_with_unit_header(tmp_path):
    result = export_business_excel(
        workspace(tmp_path), sheet(), allow_structured_review=True, validate_only=True)
    assert [row['status'] for row in result['provenance']] == ['verified', 'verified']
    assert {row['rule'] for row in result['provenance']} == {'ocr_table_row'}
