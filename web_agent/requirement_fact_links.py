"""Strict, host-derived audit of explicitly named business cells.

Never infer requirement coverage from a shared URL or a fuzzy column name.
This optional contract does not certify the semantic completeness of a task.
"""
from __future__ import annotations
import hashlib

MAX_LINKS=60


def normalize_fact_refs(refs):
    if not isinstance(refs,list) or not 1 <= len(refs) <= MAX_LINKS:
        raise ValueError('fact_refs必须是1–60个业务字段定位对象')
    result=[];seen=set()
    for i,ref in enumerate(refs):
        if not isinstance(ref,dict) or set(ref)!={'sheet','label','column'}:
            raise ValueError(f'fact_refs[{i}]必须只有sheet、label、column')
        parts=[]
        for key,limit in [('sheet',31),('label',200),('column',80)]:
            value=ref[key]
            if not isinstance(value,str) or not value.strip() or len(value)>limit or value!=value.strip():
                raise ValueError(f'fact_refs[{i}].{key}必须是有效且未加空格的名称')
            parts.append(value)
        identity=tuple(parts)
        if identity in seen:raise ValueError('fact_refs不能重复引用同一业务字段')
        seen.add(identity);result.append(dict(zip(('sheet','label','column'),parts)))
    return result


def audit_fact_refs(workspace, refs, source_ids, business):
    """Recheck current file+provenance; no model-provided status is trusted."""
    if refs is None:return None
    try:refs=normalize_fact_refs(refs)
    except ValueError as exc:return {'all_verified':False,'checks':[], 'issues':[str(exc)]}
    path=workspace.path/'task_result.xlsx'
    if (path.is_symlink() or not path.is_file() or not isinstance(business,dict)
            or business.get('sha256')!=hashlib.sha256(path.read_bytes()).hexdigest()):
        return {'all_verified':False,'checks':[], 'issues':['业务表或Provenance文件哈希不匹配']}
    known=set(source_ids) if isinstance(source_ids,list) and all(isinstance(s,str) for s in source_ids) else set()
    index={}
    for p in business.get('provenance',[]):
        if not isinstance(p,dict) or not isinstance(p.get('row'),int):continue
        identity=(p.get('sheet'),p.get('label'),p.get('column'))
        if all(isinstance(x,str) for x in identity):index.setdefault(identity,[]).append(p)
    checks=[];issues=[]
    for ref in refs:
        identity=(ref['sheet'],ref['label'],ref['column'])
        matches=index.get(identity,[])
        if len(matches)!=1:
            state='missing' if not matches else 'ambiguous'
        else:
            p=matches[0];sid=p.get('source_id')
            if not isinstance(sid,str) or sid not in known:state='source_mismatch'
            elif p.get('status')!='verified' or p.get('price_status')=='unverified':state='not_verified'
            else:state='verified'
        checks.append({**ref,'status':state})
        if state!='verified':issues.append(' / '.join(identity)+'：'+state)
    return {'all_verified':not issues,'checks':checks,'issues':issues[:20]}
