"""Checks for new UI data handling; no CSS snapshots or paid model calls."""
import json
from types import SimpleNamespace
import pytest
from web_agent import dashboard


def test_dashboard_rejects_path_traversal():
    with pytest.raises(ValueError): dashboard.task_folder('../../.env')


def test_source_cards_escape_untrusted_text():
    rendered=dashboard.sources_html({'sources':[{'id':'1','title':'<script>alert(1)</script>',
        'url':'javascript:alert(1)','fact':'<img src=x onerror=alert(1)>','quote':'<b>untrusted</b>'}]})
    assert '<script>' not in rendered and '<img' not in rendered
    assert 'href="javascript:' not in rendered
    assert '&lt;script&gt;' in rendered


def test_historical_task_uses_research_time_not_resume_time(tmp_path,monkeypatch):
    monkeypatch.setattr(dashboard.Settings,'load',lambda:SimpleNamespace(output_dir=tmp_path))
    folder=tmp_path/'live_B'; folder.mkdir()
    (folder/'task.json').write_text(json.dumps({'task':'Research iris'}),encoding='utf-8')
    (folder/'result.json').write_text(json.dumps({'status':'needs_review','elapsed_seconds':0.1}),encoding='utf-8')
    (tmp_path/'live_evaluation.json').write_text(json.dumps([{'task_id':'live_B','elapsed_seconds':44.22}]),encoding='utf-8')
    data=dashboard.snapshot('live_B')
    assert data['elapsed']==44.22 and data['elapsed_label']=='首次研究的实际执行时间'