import asyncio
from dataclasses import replace
from fastapi.testclient import TestClient
from test_memory_completion import prepared_workspace
from web_agent import api
from web_agent.completion import review_binding
from web_agent.persistence import atomic_json


def test_memory_api_create_list_delete_and_secret_rejection(tmp_path,monkeypatch):
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    with TestClient(api.app) as client:
        saved=client.post('/memories',json={'content':'Use official sources and Chinese reports'})
        assert saved.status_code==200
        key=saved.json()['id']
        assert client.get('/memories').json()[0]['id']==key
        assert client.post('/memories',json={'content':'password: secret-api-test'}).status_code==422
        assert client.delete('/memories/'+key).json()['deleted']
        assert client.get('/memories').json()==[]


def test_acceptance_api_requires_current_binding_and_explicit_all_ids(tmp_path,monkeypatch):
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    ws=prepared_workspace(tmp_path)
    with TestClient(api.app) as client:
        preview=client.get('/tasks/study/acceptance').json()
        assert preview['artifacts_valid'] and preview['requirements_supported']
        bad=client.post('/tasks/study/accept',json={'requirement_ids':['r1'],'binding':'0'*64})
        assert bad.status_code==409
        done=client.post('/tasks/study/accept',json={'requirement_ids':['r1'],'binding':preview['binding']})
        assert done.status_code==200 and done.json()['status']=='completed'
        assert client.get('/tasks/study').json()['completion']['all_requirements_verified']
        assert client.get('/memories').json()[0]['kind']=='accepted_task'


def test_resume_api_delivers_followup_without_resetting_task_id(tmp_path,monkeypatch):
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    prepared_workspace(tmp_path)
    captured=[]
    async def spy(task_id,task='',**kwargs):captured.append((task_id,task,kwargs))
    monkeypatch.setattr(api,'execute',spy)
    monkeypatch.setattr(api,'jobs',{})
    with TestClient(api.app) as client:
        response=client.post('/tasks/study/resume',json={'task':'Also compare official documentation','requirements':[{'description':'Compare documentation','kind':'research'}]})
        assert response.status_code==202
        client.get('/health')
    assert captured[0][0:2]==('study','Also compare official documentation')
    assert captured[0][2]['resume'] and captured[0][2]['requirements'][0]['description']=='Compare documentation'


def test_approval_api_refuses_wrong_scope_before_queue(tmp_path,monkeypatch):
    settings=replace(api.Settings.load(),output_dir=tmp_path)
    monkeypatch.setattr(api.Settings,'load',lambda:settings)
    ws=prepared_workspace(tmp_path)
    atomic_json(ws.path/'result.json',{'pending_review':[{'tool':'submit_public_form','approval_id':'a'*64}]})
    monkeypatch.setattr(api,'jobs',{})
    with TestClient(api.app) as client:
        assert client.post('/tasks/study/review',json={'decision':'approve'}).status_code==422
        assert client.post('/tasks/study/review',json={'decision':'approve','approval_id':'b'*64}).status_code==409
        assert api.jobs=={}
