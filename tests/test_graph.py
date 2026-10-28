"""Deterministic fake-model tests of the REAL graph, not live acceptance claims."""
import json
from contextlib import asynccontextmanager
from dataclasses import replace
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from web_agent.runner import run_task
from web_agent.config import Settings
from web_agent.workspace import Workspace
from web_agent.persistence import read_json, redact


class ScriptModel:
    def __init__(self, responses): self.responses, self.calls = iter(responses), 0
    def bind_tools(self, tools, **kwargs): return self
    async def ainvoke(self, messages):
        self.calls += 1
        return next(self.responses)


def call(name, args=None, id='call-1'):
    return AIMessage(content='',tool_calls=[{'name':name,'args':args or {},'id':id,'type':'tool_call'}])


@asynccontextmanager
async def empty_browser(settings, workspace): yield []


@pytest.fixture
def settings(tmp_path):
    return replace(Settings.load(),api_key='',output_dir=tmp_path/'out',checkpoint_path=tmp_path/'state.sqlite',max_model_calls=8,task_timeout=10)


@pytest.mark.asyncio
async def test_retry_then_react_and_export(settings):
    attempts = []
    @asynccontextmanager
    async def browser(settings, ws):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Fake browser for harness unit test only."""
            attempts.append(url)
            if len(attempts)==1: raise TimeoutError('injected failure')
            ws.observe(url,'An example domain for documentation.')
            return 'An example domain for documentation.'
        yield [navigate]
    model=ScriptModel([
        call('browser_navigate',{'url':'https://example.com/'}),
        call('record_source',{'title':'example','url':'https://example.com/','observed_fact':'用途','verbatim_quote':'An example domain for documentation.'},'s'),
        call('export_excel',id='x'),call('export_report',{'markdown_report':'# Report\nObserved.'},'r'),AIMessage(content='Done')])
    result=await run_task('research example', 'retry',settings,model=model,browser_factory=browser)
    assert result.status=='needs_review' and len(result.sources)==1 and len(attempts)==2
    assert result.metrics['errors']==1
    assert result.metrics['requests']==4  # no paid model turn merely to repeat file paths
    assert 'tool_retry' in (settings.output_dir/'retry'/'trace.jsonl').read_text(encoding='utf-8')


@pytest.mark.asyncio
async def test_approval_resume_does_not_replay_prior_tool(settings):
    first=ScriptModel([AIMessage(content='',tool_calls=[
        {'name':'update_plan','args':{'steps':['research']},'id':'plan','type':'tool_call'},
        {'name':'request_human_review','args':{'action':'send form','reason':'external effect'},'id':'review','type':'tool_call'}])])
    r=await run_task('review external action','review',settings,model=first,browser_factory=empty_browser)
    assert r.status=='awaiting_review' and r.pending_review
    second=ScriptModel([AIMessage(content='User rejected, nothing submitted.'),AIMessage(content='No verified sources.')])
    r=await run_task('', 'review',settings,resume=True,decision={'decision':'reject'},model=second,browser_factory=empty_browser)
    assert r.status=='incomplete'
    trace=(settings.output_dir/'review'/'trace.jsonl').read_text(encoding='utf-8')
    assert trace.count('"event": "plan_updated"')==1
    assert 'human_decision' in trace
    assert read_json(settings.output_dir/'review'/'usage.json')['requests']==3


@pytest.mark.asyncio
async def test_model_budget_is_durable(settings):
    settings=replace(settings,max_model_calls=1)
    model=ScriptModel([call('update_plan',{'steps':['one']})])
    r=await run_task('budget task','budget',settings,model=model,browser_factory=empty_browser)
    assert r.status=='budget_exceeded' and model.calls==1
    other=ScriptModel([])
    r=await run_task('', 'budget',settings,resume=True,model=other,browser_factory=empty_browser)
    assert r.status=='budget_exceeded' and other.calls==0


@pytest.mark.asyncio
async def test_invalid_source_error_returns_to_model(settings):
    model=ScriptModel([call('record_source',{'title':'fabricated','url':'https://example.com','observed_fact':'x','verbatim_quote':'not observed'}),AIMessage(content='No facts verified'),AIMessage(content='Incomplete')])
    r=await run_task('invalid evidence','invalid',settings,model=model,browser_factory=empty_browser)
    assert r.status=='incomplete' and not r.sources and model.calls==3
    assert r.metrics['errors']==1


def test_observed_quote_must_match_url_and_text(tmp_path):
    ws=Workspace(tmp_path,'source')
    ws.observe('https://example.com/a','Real\n quoted text')
    assert ws.record('t','https://example.com/a','f','Real quoted text',require_observation=True)['observation_id']!='manual-unverified'
    with pytest.raises(ValueError): ws.record('t','https://example.com/b','f','Real quoted text',require_observation=True)


def test_key_redaction(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','private-test-secret')
    assert 'private-test-secret' not in redact('failed private-test-secret')


@pytest.mark.asyncio
async def test_unknown_tool_is_recoverable(settings):
    model=ScriptModel([call('imaginary_tool'), AIMessage(content='No result'),AIMessage(content='Incomplete')])
    r=await run_task('unknown tool test','unknown',settings,model=model,browser_factory=empty_browser)
    assert r.status=='incomplete' and r.metrics['errors']==1
