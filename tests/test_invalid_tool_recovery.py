import json
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from web_agent.config import Settings
from web_agent.runner import run_task
from web_agent.persistence import read_json


class FakeModel:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        import copy
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        message = copy.deepcopy(self.responses[index])
        message.id = f"fake-response-{self.calls}"
        return message


def invalid_call(index):
    return AIMessage(
        content="",
        invalid_tool_calls=[{
            "name": "save_business_rows",
            "args": "{invalid-json",
            "id": f"broken-{index}",
            "error": "JSON parse error",
            "type": "invalid_tool_call",
        }],
    )


@asynccontextmanager
async def no_browser(settings, workspace):
    yield []


def make_settings(tmp_path):
    return replace(
        Settings.load(),
        api_key="",
        output_dir=tmp_path / "outputs",
        checkpoint_path=tmp_path / "state.sqlite",
        max_model_calls=8,
        task_timeout=30,
    )


def recovery_events(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if '"event": "invalid_tool_recovery"' in line
    ]


@pytest.mark.asyncio
async def test_invalid_calls_stop_after_two_retries(tmp_path):
    settings = make_settings(tmp_path)
    model = FakeModel([invalid_call(i) for i in range(4)])

    result = await run_task(
        "Test malformed model tool calls",
        "invalid_exhausted",
        settings,
        model=model,
        browser_factory=no_browser,
    )

    folder = settings.output_dir / "invalid_exhausted"
    events = recovery_events(folder / "trace.jsonl")

    assert model.calls == 3
    assert result.metrics["requests"] == 3
    assert [e["action"] for e in events] == [
        "retry_invalid_tools",
        "retry_invalid_tools",
        "finalize_delivery",
    ]
    assert result.status not in ("completed", "needs_review")
    assert not read_json(folder / "tool_journal.json", {})


@pytest.mark.asyncio
async def test_valid_tool_runs_after_invalid_call(tmp_path):
    settings = make_settings(tmp_path)
    model = FakeModel([
        invalid_call(1),
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": ["Review available evidence"]},
            "id": "valid-plan",
            "type": "tool_call",
        }]),
        AIMessage(content="No verified evidence available."),
    ])

    result = await run_task(
        "Test recovery to a valid tool call",
        "invalid_then_valid",
        settings,
        model=model,
        browser_factory=no_browser,
    )

    folder = settings.output_dir / "invalid_then_valid"
    journal = read_json(folder / "tool_journal.json", {})
    events = recovery_events(folder / "trace.jsonl")

    assert model.calls >= 2
    assert model.calls <= settings.max_model_calls
    assert any(v.get("name") == "update_plan" for v in journal.values())
    assert len(events) == 1
    assert events[0]["action"] == "retry_invalid_tools"
    assert result.status != "error"
