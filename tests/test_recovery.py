import asyncio
from dataclasses import replace
from contextlib import asynccontextmanager
import pytest
from langchain_core.tools import tool
from langchain_core.messages import AIMessage
from test_graph import ScriptModel,call,empty_browser,settings
from web_agent.runner import run_task
from web_agent.security import validate_resolved_url,UnsafeURL
from web_agent.workspace import Workspace


@pytest.mark.asyncio
async def test_tool_timeout_is_bounded_and_model_can_recover(settings):
    attempts=[]
    @asynccontextmanager
    async def browser(settings,ws):
        @tool('browser_navigate')
        async def slow(url:str)->str:
            """Simulated slow read for timeout test."""
            attempts.append(url)
            await asyncio.sleep(1)
            return 'unreachable'
        yield [slow]
    model=ScriptModel([call('browser_navigate',{'url':'https://example.com'}),AIMessage(content='Timed out safely.'),AIMessage(content='Incomplete')])
    result=await run_task('timeout recovery','slow',replace(settings,tool_timeout=0.01),model=model,browser_factory=browser)
    assert len(attempts)==2 and result.status=='incomplete'
    assert result.metrics['retries']==1 and result.metrics['errors']==2


@pytest.mark.asyncio
async def test_task_timeout_checkpoint_can_resume(settings):
    class SlowModel(ScriptModel):
        async def ainvoke(self,messages): await asyncio.sleep(1)
    result=await run_task('checkpoint timeout','timeout',replace(settings,task_timeout=0.3),model=SlowModel([]),browser_factory=empty_browser)
    assert result.status=='timeout'
    model=ScriptModel([AIMessage(content='No sources'),AIMessage(content='Incomplete')])
    result=await run_task('', 'timeout', settings,resume=True,model=model,browser_factory=empty_browser)
    assert result.status=='incomplete' and result.metrics['requests']==3


def test_dns_rebinding_preflight_blocks_private(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(UnsafeURL): validate_resolved_url('https://example.com')


def test_logs_never_include_key_in_nested_arguments(tmp_path,monkeypatch):
    secret='local-test-value'
    monkeypatch.setenv('DEEPSEEK_API_KEY',secret)
    ws=Workspace(tmp_path,'redaction')
    ws.log('test',secret,arguments={'api_key':secret,'nested':[secret,'normal']})
    text=(ws.path/'trace.jsonl').read_text(encoding='utf-8')
    assert secret not in text and 'normal' in text
