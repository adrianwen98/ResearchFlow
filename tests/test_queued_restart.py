import json

from web_agent import web_views
from web_agent.workspace import Workspace


def test_queued_task_survives_restart_as_restartable(tmp_path):
    ws = Workspace(tmp_path, "queued-after-restart")
    ws.path.mkdir(parents=True, exist_ok=True)
    (ws.path / "queued.json").write_text(
        json.dumps(
            {
                "task": "调查 Python 官网的发布日期并生成报告",
                "task_id": "queued-after-restart",
                "created_at": "2026-10-26T00:00:00+08:00",
                "report_format": "both",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    view = web_views.workspace_view(ws.path, {})
    assert view["status"] == "interrupted"
    assert view["capabilities"]["can_resume"] is False
    assert view["capabilities"]["can_restart"] is True
    assert "尚未开始" in view["failure"]["label"]


import asyncio

import pytest

from web_agent import api


@pytest.mark.asyncio
async def test_restart_queued_task_reuses_original_goal(tmp_path, monkeypatch):
    ws = Workspace(tmp_path, "queued-restart-call")
    ws.path.mkdir(parents=True, exist_ok=True)
    (ws.path / "queued.json").write_text(
        json.dumps(
            {
                "task": "调查 Python 官网的发布日期并生成报告",
                "task_id": "queued-restart-call",
                "created_at": "2026-10-26T00:00:00+08:00",
                "report_format": "both",
                "requirements": [],
                "review_mode": "auto",
            }
        ),
        encoding="utf-8",
    )
    calls = {}

    async def fake_execute(task_id, task="", **kwargs):
        calls.update(task_id=task_id, task=task, **kwargs)

    monkeypatch.setattr(api, "task_path", lambda _task_id: ws.path)
    monkeypatch.setattr(api, "execute", fake_execute)
    try:
        result = await api.continue_task("queued-restart-call")
        await asyncio.sleep(0)
        assert result["restarted"] is True
        assert calls["task"].startswith("调查 Python 官网")
        assert calls["report_format"] == "both"
    finally:
        job = api.jobs.pop("queued-restart-call", None)
        if job:
            job.cancel()


import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_unlock_refuses_live_pid_and_removes_dead_lock(tmp_path, monkeypatch):
    from web_agent import api

    ws = Workspace(tmp_path, "unlock-test")
    ws.path.mkdir(parents=True, exist_ok=True)
    lock = ws.path / ".running"
    lock.write_text(json.dumps({"pid": __import__("os").getpid()}), encoding="utf-8")
    monkeypatch.setattr(api, "task_path", lambda _task_id: ws.path)
    with pytest.raises(HTTPException) as live:
        await api.unlock_task("unlock-test", api.UnlockInput(confirm=True))
    assert live.value.status_code == 409
    assert lock.exists()

    lock.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
    result = await api.unlock_task("unlock-test", api.UnlockInput(confirm=True))
    assert result["unlocked"] is True
    assert not lock.exists()
