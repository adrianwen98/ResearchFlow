from fastapi.testclient import TestClient
from web_agent.api import app


def test_health_and_input_boundaries():
    with TestClient(app) as client:
        assert client.get('/health').json()['external_writes']=='approval_required'
        assert client.post('/tasks',json={'task':'a'}).status_code==422
        assert client.get('/tasks/no_such_task').status_code==404
        assert client.get('/tasks/no_such_task/files/.env').status_code==404
        assert client.post('/tasks/no_such_task/review',json={'decision':'approve'}).status_code==422


def test_gradio_constructs_and_has_async_handlers():
    import inspect
    from app import demo, resume_ui, handled_ui, reject_ui
    assert demo.config['components']
    assert all(inspect.iscoroutinefunction(f) for f in (resume_ui,handled_ui,reject_ui))
