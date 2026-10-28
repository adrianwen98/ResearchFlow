"""Bing RSS search returns real result links instead of a JS shell."""
import pytest
from web_agent.harness import ToolFailure
from web_agent.search_rss import parse_360_html, parse_bing_rss

RSS = '''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item><title>国家统计局 农用化肥施用量</title>
<link>https://www.stats.gov.cn/sj/ndsj/2024/indexch.htm</link>
<description>统计年鉴 &amp; 数据</description></item>
<item><title>化肥用量报告 PDF</title>
<link>https://example.org/report.pdf</link>
<description><b>2015-2024</b> 数据</description></item>
</channel></rss>'''


def test_parse_bing_rss_returns_title_url_and_plain_description():
    items = parse_bing_rss(RSS)
    assert [item['title'] for item in items] == [
        '国家统计局 农用化肥施用量', '化肥用量报告 PDF']
    assert items[0]['url'].startswith('https://www.stats.gov.cn/')
    assert items[1]['description'] == '2015-2024 数据'


def test_invalid_rss_is_reported_as_empty_search():
    with pytest.raises(ToolFailure, match='搜索服务'):
        parse_bing_rss('<html>challenge</html>')


def test_parse_360_html_prefers_the_real_target_url():
    page = ('<h3 class="res-title"><a href="/link?m=redirect" '
            'data-mdurl="https://data.chinabaogao.com/huagong/2021/1.html">'
            '化肥数据表</a></h3>'
            '<span class="res-list-summary">2010-2019 氮磷钾 数据表</span>')
    items = parse_360_html(page)
    assert items == [{'title': '化肥数据表',
                      'url': 'https://data.chinabaogao.com/huagong/2021/1.html',
                      'description': '2010-2019 氮磷钾 数据表'}]
