"""Staging hard guard cannot permanently lock a research task (offline)."""
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from langchain_core.tools import tool
from test_graph import settings, ScriptModel, call
from web_agent.persistence import atomic_json, read_json
from web_agent.runner import run_task
from web_agent.workspace import Workspace


def seed(ws):
    for i in (1, 2):
        url = f'https://example.com/source-{i}'
        text = f'Observed metric {i} on source page.'
        ws.observe(url, text)
        ws.record(str(i), url, str(i), text, require_observation=True)
    atomic_json(ws.path / 'requirements.json', [
        {'id': 'r1', 'description': 'Excel table', 'kind': 'artifact',
         'artifact': 'task_result.xlsx', 'result': {'status': 'pending'}}
    ])
    atomic_json(ws.path / 'usage.json', {'requests': 7, 'input_tokens': 0, 'output_tokens': 0})


@pytest.mark.asyncio
async def test_three_bounded_staging_turns_then_research_resumes(settings):
    ws = Workspace(settings.output_dir, 'bounded-staging-release')
    seed(ws)
    visits = []

    @asynccontextmanager
    async def browser(cfg, workspace):
        @tool('browser_navigate')
        async def navigate(url: str) -> str:
            """Fake browser: records tool effects without network access."""
            visits.append(url)
            return 'Another public document'
        yield [navigate]

    responses = [
        call('browser_navigate', {'url': f'https://example.com/blocked-{i}'}, f'blocked-{i}')
        for i in range(3)
    ] + [call('browser_navigate', {'url': 'https://example.com/allowed'}, 'allowed')]
    model = ScriptModel(responses)
    await run_task('Gather business facts for an Excel table', 'bounded-staging-release',
                   replace(settings, max_model_calls=30), model=model, browser_factory=browser)
    # A model that ignores bind_tools is blocked for the bounded checkpoint,
    # but necessary research becomes available afterwards.
    assert visits == ['https://example.com/allowed']
    journal = read_json(ws.path / 'tool_journal.json')
    assert all(not journal[f'blocked-{i}']['error_code'] for i in range(3))
    assert all('save_business_rows' in journal[f'blocked-{i}']['output'] for i in range(3))
    assert not journal['allowed']['error_code']
    trace=[json.loads(line) for line in (ws.path/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert sum(e.get('event')=='progress_gate' and e.get('code')=='business_staging_required' for e in trace)==3
    assert not any(e.get('event')=='tool_error' and e.get('code')=='business_staging_required' for e in trace)
