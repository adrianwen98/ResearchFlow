"""Offline browser search wrapper regressions; fake MCP transport is explicit."""
import re
from dataclasses import replace
from urllib.parse import parse_qs,urlsplit
from types import SimpleNamespace
import pytest
from test_graph import settings
from web_agent import browser
from web_agent.workspace import Workspace
from web_agent.persistence import read_json
from web_agent.security import UnsafeURL


@pytest.fixture(autouse=True)
def disable_host_search(monkeypatch):
    """These tests exercise the browser wrapper; host HTTP search is covered elsewhere."""
    from web_agent import search_api, search_rss
    from web_agent.harness import ToolFailure

    async def fail(*args, **kwargs):
        raise ToolFailure('search_empty', 'host search disabled in browser wrapper tests')

    monkeypatch.setattr(search_api, 'api_search', fail)
    monkeypatch.setattr(search_rss, 'search_360', fail)
    monkeypatch.setattr(search_rss, 'bing_rss', fail)
    monkeypatch.setattr(search_rss, 'search_sogou', fail)


def test_query_cannot_rewrite_provider_or_inject_url_parameters():
    query='site:python.org 文档 & next=http://127.0.0.1 #fragment'
    url=browser.search_url(query)
    assert urlsplit(url).hostname=='www.bing.com'
    assert parse_qs(urlsplit(url).query)=={'q':[query]}
    assert not urlsplit(url).fragment
    assert urlsplit(browser.search_url('Python documentation','duckduckgo')).hostname=='duckduckgo.com'


@pytest.mark.parametrize('query',['','x'*301,'abc\ndef','sk-1234567890123456','password=secret','Cookie: private'])
def test_invalid_or_credential_search_is_rejected(query):
    with pytest.raises(ValueError):browser.search_url(query)


def test_custom_configured_secret_never_goes_into_search(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','custom-secret-format-test')
    with pytest.raises(ValueError):browser.search_url('find custom-secret-format-test')


@pytest.mark.asyncio
async def test_real_wrapper_dispatches_search_and_follow_in_one_session(settings,monkeypatch):
    calls=[];current={'url':''}
    async def navigate(args):
        calls.append(args);current['url']=args['url']
        return '- Page URL: '+args['url']+'\n```yaml\n- link "Python official documentation" [ref=e7]:\n  - /url: https://docs.python.org/3/\n```'
    async def snapshot(args):return await navigate({'url':current['url']})
    async def load(session):
        assert session=='one-session'
        return [SimpleNamespace(name='browser_navigate',ainvoke=navigate),SimpleNamespace(name='browser_snapshot',ainvoke=snapshot)]
    monkeypatch.setattr(browser,'load_mcp_tools',load)
    cfg=replace(settings,allowed_domains=());ws=Workspace(cfg.output_dir,'search')
    tools={t.name:t for t in await browser.load_safe_browser_tools('one-session',cfg,ws)}
    result=await tools['browser_search'].ainvoke({'query':'site:python.org 文档'})
    assert '仅供发现来源' in result and '[ref=e7]' in result
    assert parse_qs(urlsplit(calls[0]['url']).query)['q']==['site:python.org 文档']
    followed=await tools['browser_follow_link'].ainvoke({'ref':'e7'})
    assert calls[-1]['url']=='https://docs.python.org/3/'
    state=read_json(ws.path/'browser_state.json')
    assert state['url']=='https://docs.python.org/3/'
    assert re.fullmatch(r'[a-f0-9]{20}',state['observation_id'])
    assert f"[observation_id: {state['observation_id']}]" in followed
    saved=read_json(ws.path/'observations'/f"{state['observation_id']}.json")
    assert '[observation_id:' not in saved['text']
    strict=replace(cfg,allowed_domains=('python.org',))
    ws2=Workspace(cfg.output_dir,'strict_search')
    restricted={t.name:t for t in await browser.load_safe_browser_tools('one-session',strict,ws2)}
    count=len(calls)
    with pytest.raises(UnsafeURL):await restricted['browser_search'].ainvoke({'query':'Python'})
    assert len(calls)==count


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[0,2])
async def test_empty_search_shell_is_not_success(settings,monkeypatch,count):
    from web_agent.harness import ToolFailure
    async def navigate(args):return '- Page URL: https://www.bing.com/search?q=test\n```yaml\n- searchbox "Search" [ref=e1]\n```'
    async def snapshot(args):return await navigate({})
    async def evaluate(args):
        if 'querySelectorAll' in args['function']:return '### Result\n'+str(count)
        assert 'main?.innerText?.trim()' in args['function']
        return '### Result\n"Rendered fallback body"'
    async def load(session):return [SimpleNamespace(name=n,ainvoke=f) for n,f in [('browser_navigate',navigate),('browser_snapshot',snapshot),('browser_evaluate',evaluate)]]
    monkeypatch.setattr(browser,'load_mcp_tools',load)
    ws=Workspace(settings.output_dir,'shell')
    tools={t.name:t for t in await browser.load_safe_browser_tools(None,settings,ws)}
    if count:
        assert 'Rendered fallback body' in await tools['browser_search'].ainvoke({'query':'test'})
    else:
        with pytest.raises(ToolFailure) as failure:await tools['browser_search'].ainvoke({'query':'test'})
        assert failure.value.code=='search_empty'


@pytest.mark.asyncio
async def test_chinese_captcha_page_is_not_observed_as_evidence(settings,monkeypatch):
    from web_agent.harness import ToolFailure
    async def navigate(args):return '- Page URL: https://wappass.baidu.com/static/captcha/test.html\n- Page Title: 百度安全验证\n```yaml\n- button "返回首页" [ref=e1]\n```'
    async def load(session):return [SimpleNamespace(name=n,ainvoke=navigate) for n in ['browser_navigate','browser_snapshot']]
    monkeypatch.setattr(browser,'load_mcp_tools',load)
    ws=Workspace(settings.output_dir,'captcha')
    tools={t.name:t for t in await browser.load_safe_browser_tools(None,settings,ws)}
    with pytest.raises(ToolFailure) as failure:await tools['browser_search'].ainvoke({'query':'Python','engine':'baidu'})
    assert failure.value.code=='site_challenge'
    assert not list((ws.path/'observations').glob('*.json'))
