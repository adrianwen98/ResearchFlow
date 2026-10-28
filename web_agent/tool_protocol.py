"""Provider-neutral, non-fabricating tool-call history normalization.

Model API protocol: an assistant tool_calls message must be immediately followed
by one tool result for every call ID. Do not invent a result for a missing call.
Instead replace only a corrupt/incomplete historical group with an explicitly
untrusted summary. Checkpoint records remain unchanged.
"""
from __future__ import annotations

from typing import Callable


def _type(message):
    return getattr(message, 'type', '')


def _tool_calls(message):
    result = getattr(message, 'tool_calls', None)
    return result if isinstance(result, list) else []


def _preview(message, size=240):
    return str(getattr(message, 'content', ''))[:size]


def repair_tool_protocol(messages, system_message: Callable[[str], object]):
    """Return (safe copies, diagnostic counters); never mutate original state.

    Preserves completed assistant/tool groups, including tool-call ids and real
    results. Invalid groups become *system summaries*, not fabricated messages.
    Includes a final assertion so an unknown shape fails locally, not at API.
    """
    result = []
    diagnostics = {'incomplete_groups': 0, 'orphan_tools': 0,
                   'hidden_raw_calls': 0, 'hidden_invalid_calls': 0,
                   'hidden_legacy_calls': 0, 'complete_groups': 0}
    i = 0
    while i < len(messages):
        head = messages[i]
        kind = _type(head)
        if kind == 'tool':
            diagnostics['orphan_tools'] += 1
            result.append(system_message(
                '孤立历史工具输出（未作为可验证证据使用；请从持久来源重新读取）：' +
                _preview(head, 260)))
            i += 1
            continue
        if kind != 'ai':
            result.append(head)
            i += 1
            continue

        calls = _tool_calls(head)
        kwargs = getattr(head, 'additional_kwargs', {}) or {}
        raw_calls = kwargs.get('tool_calls') if isinstance(kwargs, dict) else None
        legacy_call = kwargs.get('function_call') if isinstance(kwargs, dict) else None
        # IMPORTANT: ChatOpenAI serializes BOTH .tool_calls and
        # .invalid_tool_calls. A model can return 0 parsed tool_calls but 1+
        # invalid_tool_calls: the graph then emits no ToolMessage, while the
        # provider receives a tool_calls message and rejects the next request.
        invalid_calls = getattr(head, 'invalid_tool_calls', None) or []
        if invalid_calls:
            diagnostics['hidden_invalid_calls'] += len(invalid_calls)
        if not calls:
            if invalid_calls or raw_calls or legacy_call:
                if raw_calls: diagnostics['hidden_raw_calls'] += 1
                if legacy_call: diagnostics['hidden_legacy_calls'] += 1
                names = [str(c.get('name', ''))[:60] for c in invalid_calls if isinstance(c, dict)]
                result.append(system_message(
                    '历史模型输出中存在未成功解析或配对的工具调用；已隔离且未执行，'
                    '不代表工具执行成功。需要执行时重新按工具schema调用。'
                    f'调用名称：{names!r}；消息摘要：' + _preview(head)))
            else:
                result.append(head)
            i += 1
            continue

        tail = []
        j = i + 1
        while j < len(messages) and _type(messages[j]) == 'tool':
            tail.append(messages[j])
            j += 1
        call_ids = [c.get('id') for c in calls if isinstance(c, dict)]
        result_ids = [getattr(m, 'tool_call_id', None) for m in tail]
        complete = (len(call_ids) == len(calls) and
                    all(isinstance(x, str) and bool(x) for x in call_ids) and
                    len(set(call_ids)) == len(call_ids) and
                    len(result_ids) == len(call_ids) and
                    set(result_ids) == set(call_ids) and
                    len(set(result_ids)) == len(result_ids))
        if complete:
            # Preserve only the parsed and actually executed calls. The OpenAI
            # adapter otherwise serializes invalid_tool_calls as additional
            # tool_calls, for which there are deliberately no tool results.
            # Also strip the legacy/raw fallback metadata.
            if (invalid_calls or raw_calls or legacy_call) and hasattr(head, 'model_copy'):
                safe_kwargs = {k: v for k, v in kwargs.items()
                               if k not in ('tool_calls', 'function_call')}
                head = head.model_copy(update={
                    'additional_kwargs': safe_kwargs,
                    'invalid_tool_calls': [],
                })
                if raw_calls: diagnostics['hidden_raw_calls'] += 1
                if legacy_call: diagnostics['hidden_legacy_calls'] += 1
            result.append(head)
            result.extend(tail)
            if invalid_calls:
                result.append(system_message(
                    f'同一历史消息另有 {len(invalid_calls)} 个无法解析的工具调用，'
                    '这些调用没有执行，也没有被用作证据；如有必要请重新发起。'))
            diagnostics['complete_groups'] += 1
        else:
            diagnostics['incomplete_groups'] += 1
            names = [str(c.get('name',''))[:70] for c in calls if isinstance(c, dict)]
            receipts = [_preview(m, 160) for m in tail[:4]]
            result.append(system_message(
                '历史工具调用与返回消息不完整，已隔离避免模型API协议错误；'
                '不代表工具执行成功，不能据此证明事实。'
                f'工具：{names!r}；已有返回片段：{receipts!r}。'
                '如需原始证据请重新读取本任务持久观察或来源。'))
        i = j

    # Defensive invariant: internal helpers must not produce an invalid batch.
    for pos, message in enumerate(result):
        if _type(message) == 'tool':
            if pos == 0: raise ValueError('orphan tool message')
            preceding = pos-1
            while preceding >= 0 and _type(result[preceding]) == 'tool':
                preceding -= 1
            if preceding < 0 or _type(result[preceding]) != 'ai':
                raise ValueError('orphan tool message')
        if _type(message) == 'ai':
            if (getattr(message, 'invalid_tool_calls', None) or
                    (getattr(message, 'additional_kwargs', None) or {}).get('tool_calls') or
                    (getattr(message, 'additional_kwargs', None) or {}).get('function_call')):
                raise ValueError('outgoing assistant message still contains noncanonical tool calls')
        if _type(message) == 'ai' and _tool_calls(message):
            n = len(_tool_calls(message))
            responses = result[pos+1:pos+1+n]
            if len(responses) != n or not all(_type(r) == 'tool' for r in responses):
                raise ValueError('incomplete tool-call result group')
            if {r.tool_call_id for r in responses} != {c['id'] for c in _tool_calls(message)}:
                raise ValueError('mismatched tool-call IDs')
    return result, diagnostics
