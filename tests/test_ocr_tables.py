"""OCR yearbook tables convert directly into a verified business sheet."""
from web_agent.business_excel import export_business_excel
from web_agent.ocr_tables import ocr_business_sheet, save_ocr_table
from web_agent.workspace import Workspace

TEXT = ('OCR 机器识别，表格行按坐标分组；数字和列名仍需逐项核对。\n'
        '年份 | 耕地灌溉 | 农用化肥施用量\n'
        '地区 | 面积 | （万吨） | 氮肥 | 磷肥 | 钾肥 | 复合肥\n'
        '2015 | 65872.6 | 6022.6 | 2361.6 | 843.1 | 642.3 | 2175.7\n'
        '2024 | 72431.7 | 4988.2 | 1563.9 | 517.2 | 468.4 | 2438.7')


def test_headerless_ocr_page_is_not_turned_into_numeric_columns(tmp_path):
    ws = Workspace(tmp_path, 'headerless-ocr')
    text = ('OCR 机器识别，表格行按坐标分组；数字和列名仍需逐项核对。\n'
            '2017 | 66160.7 | 61520.5 | 21267.6\n'
            '2018 | 65789.2 | 61003.6 | 21212.9')
    observation = ws.observe('https://example.org/C12-10.jpg', text, 'image_ocr')
    assert save_ocr_table(ws, 'https://example.org/C12-10.jpg', observation, text) == ''
    assert not (ws.path / 'ocr_tables.json').exists()


def test_ocr_table_to_business_sheet_is_verified(tmp_path):
    ws = Workspace(tmp_path, 'ocr-table')
    observation = ws.observe('https://example.org/C12-05.jpg', TEXT, 'image_ocr')
    filename = save_ocr_table(ws, 'https://example.org/C12-05.jpg', observation, TEXT)
    assert filename.endswith('.csv')
    sheets = ocr_business_sheet(ws, filename)
    assert '氮肥（万吨）' in ws.sources()[0]['fact']
    result = export_business_excel(ws, sheets, allow_structured_review=True, validate_only=True)
    assert len(result['provenance']) == 14
    assert all(row['status'] == 'verified' for row in result['provenance'])
    assert {row['rule'] for row in result['provenance']} == {'ocr_table_row'}
