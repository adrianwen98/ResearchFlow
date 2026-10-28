"""Actual files and local API only; no model calls."""
import json
from test_web_workbench import client
from test_memory_completion import prepared_workspace
from web_agent.persistence import atomic_json


def test_pdf_preview_download_and_legacy_files(client):
    c,cfg=client
    ws=prepared_workspace(cfg.output_dir)
    manifest=json.loads((ws.path/'task.json').read_text(encoding='utf-8'))
    manifest['report_format']='both'
    atomic_json(ws.path/'task.json',manifest)
    ws.export_report('# 中文交付报告\n\n## 执行摘要\n测试实际文件预览。')
    meta=c.get('/tasks/study/report-pdf')
    assert meta.status_code==200 and meta.json()['pages']>=1
    image=c.get('/tasks/study/report-pdf/pages/1')
    assert image.status_code==200 and image.content.startswith(b'\x89PNG')
    for page in (0,81):assert c.get(f'/tasks/study/report-pdf/pages/{page}').status_code==404
    assert c.get('/tasks/other-task/report-pdf').status_code==404
    pdf=c.get(meta.json()['url'])
    assert pdf.content==(ws.path/'research_report.pdf').read_bytes()
    assert pdf.headers['content-type']=='application/pdf'
    assert pdf.headers['content-disposition'].startswith('attachment')
    assert c.get(meta.json()['url']+'?inline=true').headers['content-disposition'].startswith('inline')
    assert c.get('/tasks/study/files/research_report.md').status_code==200
    assert c.get('/tasks/study/files/research_sources.xlsx').content.startswith(b'PK')
    assert c.get('/tasks/study/workspace').json()['report_format']=='both'


def test_invalid_format_rejected_before_execution(client):
    c,_=client
    assert c.post('/tasks',json={'task':'No execution','report_format':'unknown'}).status_code==422


def test_report_source_query_link_survives_browser_sanitizing(client):
    from web_agent.workspace import Workspace
    from web_agent.report_export import safe_html
    from web_agent.web_views import clean
    c,cfg=client
    ws=Workspace(cfg.output_dir,'querylink')
    text='Observed value 6.3 inches'
    url='https://example.com/shop?product_id=20618'
    ws.observe(url,text)
    ws.record('Official',url,'Size',text,require_observation=True)
    atomic_json(ws.path/'task.json',{'task':'Link test','report_format':'both'})
    ws.export_report('# Query report\nContent')
    html=safe_html(clean((ws.path/'research_report.md').read_text(encoding='utf-8')))
    assert 'href="'+url+'"' in html
    assert '20618%29' not in html
