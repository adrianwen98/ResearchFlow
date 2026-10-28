"""Read real Excel cells through the API; no model or outbound request."""
from dataclasses import replace
from fastapi.testclient import TestClient
from openpyxl import Workbook
from web_agent import api

def test_preview_selected_workbook_and_reject_unexposed_names(tmp_path, monkeypatch):
    cfg=replace(api.Settings.load(),api_key='',output_dir=tmp_path/'out',checkpoint_path=tmp_path/'checkpoint.sqlite')
    folder=cfg.output_dir/'history';folder.mkdir(parents=True)
    monkeypatch.setattr(api.Settings,'load',lambda:cfg)
    for filename,value in [('task_result.xlsx','business value'),('research_sources.xlsx','actual evidence value')]:
        book=Workbook();book.active.append(['heading']);book.active.append([value]);book.save(folder/filename);book.close()
    with TestClient(api.app) as client:
        default=client.get('/tasks/history/workbook')
        evidence=client.get('/tasks/history/workbook',params={'filename':'research_sources.xlsx'})
        assert default.status_code==evidence.status_code==200
        assert default.json()['sheets'][0]['rows']==[['business value']]
        assert evidence.json()['sheets'][0]['rows']==[['actual evidence value']]
        for name in ['../task_result.xlsx','trace.jsonl','unknown.xlsx','C:/private.xlsx']:
            assert client.get('/tasks/history/workbook',params={'filename':name}).status_code==400
        (folder/'research_sources.xlsx').unlink()
        assert client.get('/tasks/history/workbook',params={'filename':'research_sources.xlsx'}).status_code==404
