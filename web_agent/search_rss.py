"""Host-side Bing RSS search: real result links, not a JavaScript shell page."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urljoin

from .egress import public_http_client
from .harness import ToolFailure
from .security import validate_public_url


def parse_bing_rss(text: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ToolFailure(
            'search_empty',
            '搜索服务没有返回可解析的结果；请换关键词或改用已观察的官方页面。',
        ) from exc
    if root.tag != 'rss':
        raise ToolFailure(
            'search_empty',
            '搜索服务没有返回可解析的结果；请换关键词或改用已观察的官方页面。')
    items = []
    for item in root.findall('.//item'):
        title = ' '.join((item.findtext('title') or '').split())
        link = (item.findtext('link') or '').strip()
        description_node = item.find('description')
        description = ' '.join(''.join(description_node.itertext()).split()) if description_node is not None else ''
        description = html.unescape(description)
        if title and link:
            items.append({'title': title, 'url': link,
                          'description': description[:500]})
    if not items:
        raise ToolFailure(
            'search_empty',
            '搜索服务没有返回可解析的结果；请换关键词或改用已观察的官方页面。')
    return items


async def bing_rss(settings, query: str, workspace=None, limit: int = 10) -> str:
    url = 'https://www.bing.com/search?' + urlencode(
        {'q': query, 'format': 'rss'})
    async with public_http_client(settings) as client:
        response = await client.get(url)
        response.raise_for_status()
        items = parse_bing_rss(response.text)
    valid = []
    for item in items:
        try:
            validate_public_url(item['url'], settings.allowed_domains)
        except ValueError:
            continue
        if item['url'] not in {row['url'] for row in valid}:
            valid.append(item)
    if workspace:
        workspace.log('search_rss', '', query=query[:300], results=len(valid))
    if not valid:
        raise ToolFailure(
            'search_empty',
            '搜索服务没有返回可验证的结果项；请换关键词或改用已观察的官方页面。')
    lines = ['搜索结果仅供发现来源；请打开真实结果原文再引用。', '搜索方式：Bing RSS']
    for index, item in enumerate(valid[:limit], 1):
        lines.append(f'{index}. {item["title"]}\n   {item["url"]}')
        if item['description']:
            lines.append(f'   {item["description"]}')
    return '\n'.join(lines)


def parse_360_html(page: str, base: str = 'https://www.so.com') -> list[dict[str, str]]:
    """Extract result cards from 360 search HTML; prefer the real target URL."""
    items = []
    pattern = re.compile(
        r'<h3[^>]*class="[^"]*res-title[^"]*"[^>]*>\s*<a\b([^>]*)>(.*?)</a>',
        re.I | re.S)
    for match in pattern.finditer(page):
        attrs, title_html = match.group(1), match.group(2)
        title = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', title_html)).split())
        href_match = re.search(r'\bhref="([^"]+)"', attrs, re.I)
        mdurl_match = re.search(r'\bdata-mdurl="([^"]+)"', attrs, re.I)
        href = html.unescape(mdurl_match.group(1) if mdurl_match else (href_match.group(1) if href_match else ''))
        if not title or not href:
            continue
        url = urljoin(base, href)
        window = page[match.end():match.end() + 5000]
        description_match = re.search(
            r'class="[^"]*res-list-summary[^"]*"[^>]*>(.*?)</(?:span|div)>',
            window, re.I | re.S)
        description = ''
        if description_match:
            description = ' '.join(html.unescape(
                re.sub(r'<[^>]+>', ' ', description_match.group(1))).split())[:500]
        if url not in {row['url'] for row in items}:
            items.append({'title': title, 'url': url, 'description': description})
    return items


def parse_sogou_html(page: str, base: str = 'https://www.sogou.com') -> list[dict[str, str]]:
    items = []
    for block in re.split(r'(?=<div class="vrwrap")', page)[1:]:
        header = re.search(
            r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block, re.I | re.S)
        if not header:
            continue
        title = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', header.group(2))).split())
        href = html.unescape(header.group(1))
        direct = re.search(r'\bdata-url="([^"]+)"', block, re.I)
        url = urljoin(base, html.unescape(direct.group(1)) if direct else href)
        description_match = re.search(
            r'class="[^"]*(?:fz-mid|cacheresult_summary)[^"]*"[^>]*>(.*?)</div>',
            block, re.I | re.S)
        description = ''
        if description_match:
            description = ' '.join(html.unescape(
                re.sub(r'<[^>]+>', ' ', description_match.group(1))).split())[:500]
        if title and url and url not in {row['url'] for row in items}:
            items.append({'title': title, 'url': url, 'description': description})
    return items


async def search_sogou(settings, query: str, workspace=None, limit: int = 10) -> str:
    url = 'https://www.sogou.com/web?' + urlencode({'query': query})
    async with public_http_client(settings) as client:
        response = await client.get(
            url, headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True)
        response.raise_for_status()
        items = parse_sogou_html(response.text)
    valid = []
    for item in items:
        try:
            validate_public_url(item['url'], settings.allowed_domains)
        except ValueError:
            continue
        if item['url'] not in {row['url'] for row in valid}:
            valid.append(item)
    if workspace:
        workspace.log('search_sogou', '', query=query[:300], results=len(valid))
    if not valid:
        raise ToolFailure('search_empty', '搜狗搜索没有返回可验证结果；请换关键词或改用官方页面。')
    data_terms = re.compile(r'数据表|统计表|统计数据|万吨|\.pdf(?:\b|$)|年鉴', re.I)
    for item in valid:
        item['_data'] = bool(data_terms.search(item['title'] + ' ' + item['description']))
    valid.sort(key=lambda item: not item['_data'])
    lines = [
        '搜索结果仅供发现来源；请打开真实结果原文再引用。',
        '搜索方式：搜狗搜索。若官方页 403/只有图片，优先打开标注[数据表]的第三方页面；'
        '登记时注明“第三方整理，原始数据来源为国家统计局/年鉴”，不要冒充官方原文。',
    ]
    for index, item in enumerate(valid[:limit], 1):
        prefix = '[数据表] ' if item['_data'] else ''
        lines.append(f'{index}. {prefix}{item["title"]}\n   {item["url"]}')
        if item['description']:
            lines.append(f'   {item["description"]}')
    return '\n'.join(lines)


async def search_360(settings, query: str, workspace=None, limit: int = 10) -> str:
    url = 'https://www.so.com/s?' + urlencode({'q': query})
    async with public_http_client(settings) as client:
        response = await client.get(
            url, headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True)
        response.raise_for_status()
        items = parse_360_html(response.text)
    valid = []
    for item in items:
        try:
            validate_public_url(item['url'], settings.allowed_domains)
        except ValueError:
            continue
        if item['url'] not in {row['url'] for row in valid}:
            valid.append(item)
    if workspace:
        workspace.log('search_360', '', query=query[:300], results=len(valid))
    if not valid:
        raise ToolFailure('search_empty', '360 搜索没有返回可验证结果；请换关键词或改用官方页面。')
    data_terms = re.compile(r'数据表|统计表|统计数据|万吨|\.pdf(?:\b|$)|年鉴', re.I)
    for item in valid:
        item['_data'] = bool(data_terms.search(item['title'] + ' ' + item['description']))
    valid.sort(key=lambda item: not item['_data'])
    lines = [
        '搜索结果仅供发现来源；请打开真实结果原文再引用。',
        '搜索方式：360 搜索。若官方页 403/只有图片，优先打开标注[数据表]的第三方页面；'
        '登记时注明“第三方整理，原始数据来源为国家统计局/年鉴”，不要冒充官方原文。',
    ]
    for index, item in enumerate(valid[:limit], 1):
        prefix = '[数据表] ' if item['_data'] else ''
        lines.append(f'{index}. {prefix}{item["title"]}\n   {item["url"]}')
        if item['description']:
            lines.append(f'   {item["description"]}')
    return '\n'.join(lines)
