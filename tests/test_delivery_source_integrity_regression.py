"""Regression from fc5c6de2d53c7192fbca5ebb: evidence + inspection."""
from web_agent import graph, report_export


def test_full_source_quote_kept_even_when_lines_appear_separately_in_body(monkeypatch, tmp_path):
    url = 'https://example.org/specs'
    source = {'id': '3', 'title': 'Specs', 'url': url,
              'fact': 'Product A uses specification B',
              'quote': '规格 A\n规格 B', 'accessed_at': '2026-10-24T00:00:00+00:00'}

    class Workspace:
        path = tmp_path
        def sources(self):
            return [source]

    monkeypatch.setattr(report_export, 'read_json', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(report_export, 'quality_notes', lambda _records: [])
    body = f'# Test\n\nOfficial page: {url}\n\n规格 A\n\nDifferent section\n\n规格 B'
    rendered = report_export.canonical_report(Workspace(), body)
    assert '记录的事实：Product A uses specification B' in rendered
    assert '原文摘录：规格 A\n规格 B' in rendered


def test_distinct_delivery_inspections_are_not_redundant():
    mk = lambda signature: {'name': 'inspect_delivery', 'args_signature': signature}
    assert not graph._identical_delivery_inspections({
        'call1': mk('query=summary'), 'call2': mk('line_offset=20'),
        'call3': mk('line_offset=40')})
    assert graph._identical_delivery_inspections({
        'call1': mk('query=same'), 'call2': mk('query=same'),
        'call3': mk('query=same')})
    assert not graph._identical_delivery_inspections({
        'call1': mk('query=same'), 'call2': {'name': 'export_report', 'args_signature': 'x'},
        'call3': mk('query=same')})
