"""Bounded, host-derived repair agenda for saved business-cell evidence.

Advisory only. It cannot certify a fact or change any saved artifact.
"""
from __future__ import annotations
from .persistence import read_json

REVIEW_STATES = frozenset({'review_required', 'unverified', 'not_disclosed'})


def repair_worklist(workspace, *, limit: int = 10) -> dict:
    """Summarize CURRENT staged provenance; never infer truth from model prose."""
    staged = read_json(workspace.path / 'business_rows.json', {})
    if not isinstance(staged, dict) or not staged.get('sheets'):
        return {'version': None, 'verified': 0, 'pending': 0, 'items': [], 'omitted': 0}
    provenance = staged.get('provenance') or []
    if not isinstance(provenance, list):
        provenance = []
    items, verified = [], 0
    for cell in provenance:
        if not isinstance(cell, dict):
            continue
        state = cell.get('status')
        if state == 'verified':
            verified += 1
        if state not in REVIEW_STATES and cell.get('price_status') != 'unverified':
            continue
        diagnostic = cell.get('diagnostic') or {}
        if not isinstance(diagnostic, dict): diagnostic = {}
        code = str(diagnostic.get('code') or state or 'unknown')[:80]
        source_id = cell.get('source_id') or ''
        observation_id = cell.get('observation_id') or ''
        actions = ('检查保存的观察和网页正文，补充能够对应行对象与字段的连续原文；'
                   '如证据确实缺失，用null与具体reason标记，不能编造或改变required约束。')
        if state == 'not_disclosed':
            actions = '核实来源是否明确披露该字段；若没有，保留null及具体原因。'
        elif code in {'value_not_in_quote', 'fact_validation', 'subject_evidence_unbound'}:
            actions = '从当前来源真实观察中重新定位对象、字段及相邻原文；不要把中文概括冒充英文原句。'
        items.append({'sheet': str(cell.get('sheet',''))[:48],
                      'label': str(cell.get('label',''))[:120],
                      'column': str(cell.get('column',''))[:80],
                      'status': state, 'code': code,
                      'source_id': str(source_id)[:32],
                      'observation_id': str(observation_id)[:48],
                      'reason': str(diagnostic.get('message') or cell.get('note') or '')[:220],
                      'next_action': actions})
    limit=max(1,min(20,int(limit)))
    return {'version':staged.get('version'), 'verified':verified, 'pending':len(items),
            'items':items[:limit], 'omitted':max(0,len(items)-limit)}
