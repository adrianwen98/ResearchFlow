"""Commercial search API adapter contract and ranking."""
from types import SimpleNamespace

import pytest
from web_agent.harness import ToolFailure
from web_agent.search_api import _rank_and_format, api_search


def test_data_table_results_are_ranked_first_and_invalid_urls_skipped():
    text = _rank_and_format([
        {'title': '普通新闻', 'url': 'https://example.org/news', 'description': '说明'},
        {'title': '2015-2024 化肥数据表', 'url': 'https://example.org/data',
         'description': '万吨 氮磷钾'},
        {'title': '坏链接', 'url': 'javascript:alert(1)', 'description': ''},
    ], 'brave')
    assert text.index('[数据表] 2015-2024') < text.index('普通新闻')


@pytest.mark.asyncio
async def test_missing_search_api_key_is_actionable():
    settings = SimpleNamespace(search_api_provider='', search_api_key='')
    with pytest.raises(ToolFailure, match='SEARCH_API'):
        await api_search(settings, 'query')


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected():
    settings = SimpleNamespace(search_api_provider='unknown', search_api_key='key')
    with pytest.raises(ToolFailure, match='brave/serper/tavily'):
        await api_search(settings, 'query')


def test_search_api_results_respect_allowed_domains():
    from web_agent.harness import ToolFailure
    from web_agent.search_api import _rank_and_format

    items = [
        {"title": "allowed", "url": "https://www.stats.gov.cn/a", "description": ""},
        {"title": "blocked", "url": "https://evil.example.com/b", "description": ""},
    ]
    result = _rank_and_format(items, "serper", allowed_domains=("stats.gov.cn",))
    assert "https://www.stats.gov.cn/a" in result
    assert "evil.example.com" not in result
    try:
        _rank_and_format([{"title": "x", "url": "https://evil.example.com/b", "description": ""}],
                         "serper", allowed_domains=("stats.gov.cn",))
    except ToolFailure as exc:
        assert exc.code == "search_empty"
    else:
        raise AssertionError("off-domain-only results should be empty")
