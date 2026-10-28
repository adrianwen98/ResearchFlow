"""Offline regression for the CURRENT closing policy (30 calls: soft at 20, hard at 28).

Copy into your project's tests/ directory and run:
    python -m pytest -q tests/test_webtask_true_delivery_gate.py --tb=short

Does not call DeepSeek or real Playwright. Does not change production modules.
"""
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from test_graph import settings  # noqa: F401  (pytest fixture)
from web_agent.execution_budget import handoff
from web_agent.persistence import atomic_json, read_json
from web_agent.runner import run_task
from web_agent.workspace import Workspace


@pytest.mark.asyncio
async def test_browser_call_is_rejected_at_actual_delivery_boundary(settings):
    workspace = Workspace(settings.output_dir, 'real-delivery-gate')
    assert handoff(workspace, 30, 20)['phase'] == 'summary'
    assert handoff(workspace, 30, 20)['local_only'] is False
    assert handoff(workspace, 30, 28)['phase'] == 'delivery'
    assert handoff(workspace, 30, 28)['local_only'] is True

    # The next LLM request is #28 of 30; the hard guard should apply then.
    atomic_json(workspace.path / 'usage.json', {
        'requests': 27, 'input_tokens': 0, 'output_tokens': 0
    })
    actual_browser_calls = []

    @asynccontextmanager
    async def fake_browser(cfg, ws):
        @tool('browser_navigate')
        async def navigate(url: str) -> str:
            """Offline browser mock: records any unauthorized dispatch."""
            actual_browser_calls.append(url)
            return 'SHOULD NOT REACH BROWSER'
        yield [navigate]

    class IgnoresBoundTools:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            # Deliberately ignore the allowed tool list, simulating a bad model.
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content='', tool_calls=[{
                    'id': 'forbidden-browser', 'name': 'browser_navigate',
                    'args': {'url': 'https://example.com/new'}, 'type': 'tool_call'
                }])
            return AIMessage(content='Offline test: no further research.')

    result = await run_task(
        'Offline true delivery boundary test', workspace.task_id,
        replace(settings, max_model_calls=30, task_timeout=40),
        model=IgnoresBoundTools(), browser_factory=fake_browser,
    )
    journal = read_json(workspace.path / 'tool_journal.json', {})
    assert actual_browser_calls == [], (
        'Actual browser dispatch occurred after the hard delivery boundary'
    )
    assert journal['forbidden-browser']['error_code'] == 'delivery_phase', journal
    assert result.metrics['requests'] <= 30
