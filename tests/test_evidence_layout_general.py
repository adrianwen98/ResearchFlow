"""Layout robustness without loosening literal evidence, URL or owner contracts."""
import json
from pathlib import Path
import pytest

from web_agent.evidence_text import (
    contains_observed_excerpt as matches,
    find_observed_span,
    normalize_layout_spaces,
)
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json


@pytest.mark.parametrize('observed, quote', [
    ('岗位：研究助理', '岗位：研究助理'),
    ('岗位：研究助理\n专业不限', '岗位：研究助理 专业不限'),
    ('岗位：研究助理\n\n专业不限', '岗位：研究助理\n专业不限'),
    ('岗位：研究助理\r\n\r\n专业不限', '岗位：研究助理 专业不限'),
    ('岗位：研究助理 专业不限', '岗位：研究助理\n\n专业不限'),
    ('屏幕6.3英寸', '屏幕\n6.3 英寸'),
    ('屏幕\n6.3 英寸', '屏幕6.3英寸'),
    ('免费版¥0人/月', '免费版\n¥\n0\n人/月'),
    ('免费版\n\n¥\n\n0\n\n人/月', '免费版¥0人/月'),
    ('Apple\tA19  CPU', 'Apple A19 CPU'),
    ('e\u0301cran 12', 'écran 12'),  # NFC equivalence, not paraphrase
    ('来源日期\t2026-10-20', '来源日期 2026-10-20'),
])
def test_rendering_does_not_change_observation(observed,quote):
    assert matches(observed,quote)


@pytest.mark.parametrize('observed, quote', [
    ('商品A 售价100元\n\n商品B 售价200元', '商品A 售价200元'),
    ('商品A 售价100元\n\n商品B 售价200元', '商品A 售价100元 售价200元'),
    ('商品A 售价100元', '商品A 售价101元'),
    ('iPhone 17 Pro', 'iPhone 17 Plus'),
    ('Galaxy S25, battery 4000mAh', 'Galaxy S25, battery 5000mAh'),
    ('Model A 123', 'ModelA123'),  # ASCII word spacing remains meaningful
    ('A  B  C', 'A C'),
    ('屏幕：6.3英寸', '屏幕：6.9英寸'),
    ('免费版¥0人/月', ''),
    ('免费版¥0人/月', '     '),
    ('免费版¥0人/月', '¥0元/人/月'),  # cannot invent 元
    ('价格 ¥1000', '100'),
    ('价格 ¥0.5', '¥0'),
    ('芯片 A19Pro', 'A19'),
    ('A19', '19'),
    ('￥1,200', '200'),
])
def test_changed_or_skipped_facts_are_not_extractive(observed,quote):
    assert not matches(observed,quote)


def test_evidence_for_still_requires_same_url_and_observation(tmp_path):
    ws = Workspace(tmp_path, 'layout-boundary')
    url = 'https://example.com/a'
    quote = '商品A\n屏幕6.3英寸'
    oid = ws.observe(url,'商品A\n\n屏幕 6.3 英寸')
    assert ws.evidence_for(url, quote) == oid
    assert ws.evidence_for(url, quote, oid) == oid
    with pytest.raises(ValueError,match='来源网址不匹配'):
        ws.evidence_for('https://example.com/b', quote)
    with pytest.raises(ValueError):
        ws.evidence_for(url, quote, '00000000000000000000')
    with pytest.raises(ValueError):
        ws.evidence_for(url, '商品A\n屏幕6.9英寸', oid)


def test_unchanged_historical_feishu_source_quote_replays(tmp_path):
    fixture = Path(__file__).parent / 'fixtures' / 'delivery_loop_case.json'
    data = json.loads(fixture.read_text(encoding='utf-8'))
    ws = Workspace(tmp_path, 'legacy-unmodified')
    (ws.path/'observations').mkdir(exist_ok=True)
    for row in data['observations']:
        atomic_json(ws.path/'observations'/f"{row['id']}.json",row)
    source = next(s for s in data['sources'] if s['id']=='3')
    assert ws.evidence_for(source['url'], source['quote'], source['observation_id']) == source['observation_id']


def test_cannot_attribute_other_product_by_skipping_lines(tmp_path):
    ws=Workspace(tmp_path,'product-guard')
    url='https://example.com/phones'
    ws.observe(url,'Galaxy S25\n屏幕 6.2 英寸\n\nGalaxy S25+\n屏幕 6.7 英寸')
    with pytest.raises(ValueError):
        ws.record('Galaxy S25',url,'S25 screen 6.7','Galaxy S25\n屏幕 6.7 英寸',require_observation=True)
    # Excerpt evidence is NOT item-level proof: owner_guard downstream must
    # independently verify the item-to-value association.
    assert ws.record('Galaxy S25',url,'S25 screen 6.2','Galaxy S25\n屏幕 6.2 英寸',require_observation=True)

@pytest.mark.parametrize("observed, quote, expected", [
    ("Members 20. Battery", "Members 20", True),
    ("Buy from $899. United States", "Buy from $899", True),
    ('"sampled_rows": 3, "columns": []', '"sampled_rows": 3', True),
    ("售价1000元", "100", False),
    ("价格100.5元", "价格100", False),
    ("价格100,000元", "价格100", False),
])
def test_numeric_punctuation_boundaries(observed, quote, expected):
    assert matches(observed, quote) is expected


NBSP = chr(0xA0)
NARROW_NBSP = chr(0x202F)
THIN_SPACE = chr(0x2009)
IDEOGRAPHIC_SPACE = chr(0x3000)


@pytest.mark.parametrize('observed, quote, expected_raw', [
    ('4800 万像素 Pro' + NBSP + '级融合式摄像头系统',
     '4800 万像素 Pro 级融合式摄像头系统',
     '4800 万像素 Pro' + NBSP + '级融合式摄像头系统'),
    ('USB' + NBSP + '3 (最高可达 10Gb/s)13',
     'USB 3 (最高可达 10Gb/s)',
     'USB' + NBSP + '3 (最高可达 10Gb/s)'),
    ('A19' + NBSP + 'Pro chip', 'A19 Pro chip', 'A19' + NBSP + 'Pro chip'),
    ('A19' + NARROW_NBSP + 'Pro', 'A19 Pro', 'A19' + NARROW_NBSP + 'Pro'),
    ('A19' + THIN_SPACE + 'Pro', 'A19 Pro', 'A19' + THIN_SPACE + 'Pro'),
    ('A19' + IDEOGRAPHIC_SPACE + 'Pro', 'A19 Pro', 'A19' + IDEOGRAPHIC_SPACE + 'Pro'),
    ('A19\tPro', 'A19 Pro', 'A19\tPro'),
    ('A19\r\nPro', 'A19 Pro', 'A19\r\nPro'),
    ('A19  Pro', 'A19 Pro', 'A19  Pro'),
    ('屏幕 6.3英寸', '屏幕6.3英寸', '屏幕 6.3英寸'),
])
def test_find_observed_span_returns_original_offsets(observed, quote, expected_raw):
    span = find_observed_span(observed, quote)
    assert span is not None
    assert observed[span.start:span.end] == expected_raw
    assert matches(observed, expected_raw)


@pytest.mark.parametrize('observed, quote', [
    ('USB' + NBSP + '3 (最高可达 10Gb/s)13', 'USB 4 (最高可达 10Gb/s)'),
    ('4800 万像素 Pro' + NBSP + '级融合式摄像头系统',
     '4800 万像素 Pro 级融合式摄像头系统 101'),
    ('Galaxy S25, battery 4000mAh', 'Galaxy S25, battery 5000mAh'),
    ('商品A 售价100元\n\n商品B 售价200元', '商品A 售价200元'),
    ('Model A 123', 'ModelA123'),
    ('A  B  C', 'A C'),
])
def test_find_observed_span_keeps_literal_strictness(observed, quote):
    assert find_observed_span(observed, quote) is None


def test_locator_does_not_replace_the_strict_quote_gate():
    observed = '芯片 A19Pro'
    span = find_observed_span(observed, 'A19')
    assert span is not None          # locating a prefix is allowed
    assert not matches(observed, observed[span.start:span.end])


def test_normalize_layout_spaces_preserves_length():
    value = 'A' + NBSP + 'B' + NARROW_NBSP + 'C' + THIN_SPACE + 'D'
    assert len(normalize_layout_spaces(value)) == len(value)
    assert normalize_layout_spaces(value) == 'A B C D'
