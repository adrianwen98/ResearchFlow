"""Evidence appendix groups multiple sources per PDF Story/page."""
import pymupdf
from web_agent.report_export import _appendix_groups, render_pdf


def test_wide_table_keeps_all_headers_on_one_page(tmp_path):
    headers = ['年份', '化肥总量', '氮肥', '磷肥', '钾肥', '复合肥', '总量较2015变化', '总量较2015降幅']
    lines = ['# 报告', '', '## 逐年对比表', '', '| ' + ' | '.join(headers) + ' |',
             '|' + '---|' * len(headers)]
    for year in range(2015, 2025):
        lines.append('| ' + ' | '.join([str(year)] + ['1.0'] * 7) + ' |')
    pdf = tmp_path / 'wide.pdf'
    render_pdf('\n'.join(lines), pdf)
    doc = pymupdf.open(pdf)
    try:
        pages = [page.get_text() for page in doc]
        assert any(all(header in text for header in headers) for text in pages)
    finally:
        doc.close()

HEADING = '## 已记录的来源与原文摘录\n'
SOURCE = '### 来源 {n}\n\n记录的事实：第 {n} 条\n\n原文摘录：{text}\n\n'


def test_appendix_groups_multiple_sources_and_preserves_text():
    parts = [HEADING] + [SOURCE.format(n=n, text='短来源 ' * 50) for n in range(1, 9)]
    groups = _appendix_groups(parts, budget=1200, min_sources=2)
    assert 1 < len(groups) < 8
    rendered = '\n'.join(groups)
    for n in range(1, 9):
        assert f'### 来源 {n}' in rendered
    assert all(group.count('### 来源') >= 1 for group in groups)
