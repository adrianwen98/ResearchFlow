"""Explicit per-task model-budget grants. Never debit/reset usage or auto-run jobs."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import stat

from .persistence import atomic_json, read_json

MAX_ADDITION = 50
MAX_TOTAL = 200


class BudgetConflict(ValueError):
    pass


def _path(folder, name='model_budget.json'):
    path = folder / name
    for candidate in (path, path.with_suffix(path.suffix + '.tmp')):
        if candidate.is_symlink() or (candidate.exists() and getattr(candidate.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise BudgetConflict('预算文件不能是链接或重解析点')
        if not candidate.resolve().is_relative_to(folder.resolve()):
            raise BudgetConflict('预算文件路径越界')
    return path


def ledger(folder):
    try:value = read_json(_path(folder), {})
    except (ValueError,OSError) as exc:raise BudgetConflict('无法读取任务预算记录') from exc
    if not isinstance(value,dict):raise BudgetConflict('任务预算记录格式错误')
    if value and (value.get('task_id') != folder.name or type(value.get('limit')) is not int or not 1 <= value['limit'] <= MAX_TOTAL
                  or type(value.get('revision')) is not int or not isinstance(value.get('grants'), list)
                  or not isinstance(value.get('other_limits'), dict)):
        raise BudgetConflict('任务预算记录损坏或不属于当前任务，拒绝执行')
    if value:
        bounds={'tool_calls':150,'timeout_seconds':1800,'graph_steps':100,'tool_timeout':120}
        if any(type(value['other_limits'].get(k)) is not int or not 1<=value['other_limits'][k]<=v for k,v in bounds.items()):
            raise BudgetConflict('任务的原执行预算记录不合法')
    return value


def result_digest(folder):
    value = read_json(folder/'result.json', {})
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot(folder, cfg):
    saved = read_json(folder/'result.json', {})
    metrics = saved.get('metrics', {})
    limits = {**metrics.get('limits', {}), **read_json(folder/'run_limits.json', {})}
    record = ledger(folder)
    limit = record.get('limit', limits.get('model_requests', cfg.max_model_calls))
    used = int(read_json(folder/'usage.json', {}).get('requests', metrics.get('requests', 0)))
    other = record.get('other_limits') or {
        'tool_calls': limits.get('tool_calls', cfg.max_tool_calls),
        'timeout_seconds': limits.get('timeout_seconds', cfg.task_timeout),
        'graph_steps': limits.get('graph_steps', cfg.max_graph_steps),
        'tool_timeout': cfg.tool_timeout,
    }
    tool_used = int(read_json(folder/'tool_usage.json', {}).get('attempts', metrics.get('tool_attempts', metrics.get('tool_calls', 0))))
    time_used = float(read_json(folder/'time_budget.json', {}).get('spent', metrics.get('execution_seconds_used', 0)))
    revision = record.get('revision', 0)
    binding_data = {'task_id': folder.name, 'limit': limit, 'used': used, 'revision': revision,
                    'other_limits': other, 'tool_used': tool_used, 'time_used': time_used,
                    'result': result_digest(folder)}
    binding = hashlib.sha256(json.dumps(binding_data, sort_keys=True).encode()).hexdigest()
    return {'used': used, 'limit': limit, 'remaining': max(0, limit-used), 'exhausted': used >= limit,
            'revision': revision, 'binding': binding, 'max_addition': min(MAX_ADDITION, max(0, MAX_TOTAL-limit)),
            'max_total': MAX_TOTAL, 'other_limits': other, 'tool_used': tool_used, 'time_used': time_used,
            'grants': record.get('grants', [])}


def view(folder, cfg, *, status, checkpoint, read_only, locked, archived):
    data = snapshot(folder, cfg)
    reason = ''
    if read_only or archived: reason = '只读或归档任务不能追加预算；请先恢复归档任务'
    elif locked or status in {'running','queued','awaiting_review'}: reason = '任务正在执行、等待审批或仍有进程锁'
    elif status not in {'budget_exceeded','partial'} or not data['exhausted']: reason = '仅模型请求次数耗尽的任务可追加；已有剩余额度时请直接恢复'
    elif not checkpoint: reason = '原 Checkpoint 不存在，不能重新开始冒充恢复'
    elif data['max_addition'] < 1: reason = f'累计请求上限最多 {MAX_TOTAL} 次'
    elif data['tool_used'] >= data['other_limits']['tool_calls']: reason = '工具调用预算也已耗尽；本操作只增加模型请求次数'
    elif data['time_used'] >= data['other_limits']['timeout_seconds']: reason = '累计执行时间已耗尽；本操作不增加时间预算'
    data.update(can_add=not bool(reason), blocked_reason=reason)
    return data


def effective_settings(cfg, folder):
    record = ledger(folder)
    if not record: return cfg
    other = record['other_limits']
    return replace(cfg, max_model_calls=record['limit'], max_tool_calls=other['tool_calls'],
                   task_timeout=other['timeout_seconds'], max_graph_steps=other['graph_steps'],
                   tool_timeout=other['tool_timeout'])


@contextmanager
def mutation_lock(folder):
    path = _path(folder, '.budget-mutating')
    try: handle = path.open('x')
    except FileExistsError as exc: raise BudgetConflict('预算正在更新，请刷新后重试') from exc
    try:
        with handle: yield
    finally: path.unlink(missing_ok=True)


def request_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def previous_grant(folder, operation_id, payload):
    item = next((g for g in ledger(folder).get('grants', []) if g['operation_id']==operation_id), None)
    if item and item['request_hash'] != request_hash(payload):
        raise BudgetConflict('同一操作标识不能用于不同的额度或任务确认')
    return item


def grant(folder, cfg, payload, current):
    if payload['task_id'] != folder.name or payload['binding'] != current['binding']:
        raise BudgetConflict('任务或预算状态已变化，请刷新后重新确认')
    addition = payload['additional_requests']
    if not current['can_add']: raise BudgetConflict(current['blocked_reason'])
    if not 1 <= addition <= current['max_addition']: raise BudgetConflict('追加次数超过当前允许范围')
    record = ledger(folder)
    receipt = {'operation_id': payload['operation_id'], 'request_hash': request_hash(payload),
               'task_id': folder.name, 'added': addition, 'old_limit': current['limit'],
               'new_limit': current['limit']+addition, 'used_at_confirmation': current['used'],
               'confirmed_at': datetime.now(timezone.utc).isoformat(), 'paid_execution_confirmed': True}
    value = {'task_id': folder.name, 'limit': receipt['new_limit'], 'revision': current['revision']+1,
             'other_limits': current['other_limits'], 'grants': [*record.get('grants', []), receipt],
             'last_resume': {'operation_id': payload['operation_id'], 'result_digest': result_digest(folder)}}
    atomic_json(_path(folder), value)
    return receipt
