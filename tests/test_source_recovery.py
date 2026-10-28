"""A failed source quote is corrected only by a successful source on the same URL."""
from web_agent.delivery_status import _execution_error_recovered, aggregate_delivery
from web_agent.workspace import Workspace


def test_empty_search_is_recovered_by_a_later_direct_page_path():
    events = [
        {'event': 'tool_start', 'call_id': 's', 'detail': 'browser_search',
         'arguments': {'query': 'Nobel Prize in Literature 2030'}},
        {'event': 'tool_error', 'call_id': 's', 'code': 'search_empty',
         'detail': 'ERROR: {"code":"search_empty","message":"no results"}'},
        {'event': 'tool_start', 'call_id': 'n', 'detail': 'browser_navigate',
         'arguments': {'url': 'https://www.nobelprize.org/prizes/literature/'}},
        {'event': 'tool_end', 'call_id': 'n', 'output_preview': '### Rendered page text'},
    ]
    starts = {event['call_id']: event for event in events if event['event'] == 'tool_start'}
    assert _execution_error_recovered(1, events[1], events, starts)


def test_empty_search_is_external_even_without_a_later_path():
    events = [
        {'event': 'tool_start', 'call_id': 's', 'detail': 'browser_search',
         'arguments': {'query': 'Nobel Prize in Literature 2030'}},
        {'event': 'tool_error', 'call_id': 's', 'code': 'search_empty',
         'detail': 'ERROR: {"code":"search_empty","message":"no results"}'},
    ]
    starts = {event['call_id']: event for event in events if event['event'] == 'tool_start'}
    assert _execution_error_recovered(1, events[1], events, starts)


def test_aggregate_delivery_never_counts_search_empty_as_unresolved(tmp_path):
    ws = Workspace(tmp_path, 'search-delivery')
    ws.log('tool_start', 'browser_search', call_id='s', arguments={'query': 'x'})
    ws.log('tool_error', 'ERROR: {"code":"search_empty","message":"no results"}',
           call_id='s', code='search_empty')
    ws.log('tool_end', 'browser_search', call_id='s',
           output_preview='ERROR: {"code":"search_empty","message":"no results"}')
    result = aggregate_delivery(
        ws, 'needs_review', {'artifacts_valid': True, 'issues': []},
        {'missing_requirements': []})
    assert result['execution_warning_count'] == 0
    assert result['recovered_execution_warning_count'] == 1
    assert not any(w['kind'] == 'execution' for w in result['warnings'])


def test_aggregate_delivery_does_not_count_external_404(tmp_path):
    ws = Workspace(tmp_path, '404-delivery')
    ws.log('tool_start', 'browser_navigate', call_id='n', arguments={'url': 'https://example.org/missing'})
    ws.log('tool_error', 'ERROR: {"code":"http_404","message":"not found"}',
           call_id='n', code='http_404')
    ws.log('tool_end', 'browser_navigate', call_id='n',
           output_preview='ERROR: {"code":"http_404","message":"not found"}')
    result = aggregate_delivery(
        ws, 'needs_review', {'artifacts_valid': True, 'issues': []},
        {'missing_requirements': []})
    assert result['execution_warning_count'] == 0
    assert result['recovered_execution_warning_count'] == 1


def test_source_tool_pair_recovers_same_url_only(tmp_path):
    url = 'https://example.org/data.csv'
    other = 'https://example.org/other.csv'
    ws = Workspace(tmp_path, 'source-pair')
    observation = ws.observe(url, 'species,value\na,1\n')
    other_observation = ws.observe(other, 'species,value\nb,2\n')
    events = [
        {'event': 'tool_start', 'call_id': 'a', 'detail': 'record_source',
         'arguments': {'url': url}},
        {'event': 'tool_error', 'call_id': 'a',
         'detail': 'ERROR: {"code":"evidence_mismatch","message":"quote not found"}'},
        {'event': 'tool_start', 'call_id': 'b', 'detail': 'record_source_from_observation',
         'arguments': {'observation_id': observation}},
        {'event': 'tool_end', 'call_id': 'b',
         'output_preview': '{"source_id":"1","url":"' + url + '"}'},
    ]
    starts = {event['call_id']: event for event in events if event['event'] == 'tool_start'}
    assert _execution_error_recovered(1, events[1], events, starts, ws)
    events[2]['arguments']['observation_id'] = other_observation
    events[3]['output_preview'] = '{"source_id":"1","url":"' + other + '"}'
    assert not _execution_error_recovered(1, events[1], events, starts, ws)
    events.append({'event': 'tool_start', 'call_id': 'c', 'detail': 'record_source',
                   'arguments': {'url': other}})
    starts['c'] = events[-1]
    events.append({'event': 'tool_end', 'call_id': 'c',
                   'output_preview': '已记录来源 #2\n实际来源URL：' + other})
    assert not _execution_error_recovered(1, events[1], events, starts, ws)
    events[2]['arguments']['observation_id'] = observation
    events[3]['output_preview'] = '{"source_id":"1","url":"' + url + '"}'
    assert _execution_error_recovered(1, events[1], events, starts, ws)
