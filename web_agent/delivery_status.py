"""Delivery usability is separate from execution termination and fact acceptance."""
import json
import re
from .persistence import read_json,read_trace
from .evidence_urls import url_key

_BUSINESS_RECOVERY_TOOLS=frozenset({'export_business_excel','repair_business_excel'})
_INSPECTION_RECOVERY_TOOLS=frozenset({'inspect_business_draft','inspect_delivery'})
_SOURCE_TOOLS=frozenset({'record_source','record_source_from_observation'})
_EXTERNAL_CODES=frozenset({'search_empty','http_404','http_403','http_410','image_document'})


def _trace_failed(event):
    return (event.get('event') in {'tool_error','task_error','task_limit','loop_guard','task_interrupted'}
            or str(event.get('output_preview','')).startswith('ERROR:'))


def _source_url(workspace,start,end=None):
    """Resolve the URL a source-recording call was actually about."""
    args=start.get('arguments') if isinstance(start.get('arguments'),dict) else {}
    if isinstance(args.get('url'),str) and args['url'].strip():
        return args['url'].strip()
    observation_id=args.get('observation_id')
    if (workspace is not None and isinstance(observation_id,str)
            and re.fullmatch(r'[a-f0-9]{20}',observation_id)):
        observed=read_json(workspace.path/'observations'/f'{observation_id}.json',{})
        if isinstance(observed.get('url'),str):
            return observed['url']
    text=str(end.get('output_preview','')) if isinstance(end,dict) else ''
    match=re.search(r'实际来源URL：\s*(\S+)',text)
    if match:
        return match.group(1)
    if text.lstrip().startswith('{'):
        try:
            data=json.loads(text)
        except (ValueError,TypeError):
            data={}
        if isinstance(data,dict):
            if isinstance(data.get('url'),str):
                return data['url']
            source_id=data.get('source_id')
            if workspace is not None and isinstance(source_id,str):
                source=next((row for row in workspace.sources()
                             if row.get('id')==source_id),None)
                if source:
                    return source.get('url','')
    return ''


def _execution_error_recovered(index,event,events,starts,workspace=None):
    """A later success of the same tool means the model already corrected it."""
    call_id=event.get('call_id')
    if not call_id:return False
    start=starts.get(call_id,{})
    name=start.get('detail')
    args=start.get('arguments') if isinstance(start.get('arguments'),dict) else {}
    requirement=args.get('requirement_id') if name=='record_requirement_result' else None
    if name=='browser_search' and event.get('code')=='search_empty':
        # Empty search is an external condition, not an unresolved data error.
        # Missing data still surfaces through requirements/data warnings.
        return True
    for later in events[index+1:]:
        if later.get('event')!='tool_end' or _trace_failed(later):
            continue
        if later.get('call_id')==call_id:return True
        later_start=starts.get(later.get('call_id'),{})
        later_name=later_start.get('detail')
        if requirement:
            later_args=later_start.get('arguments') if isinstance(later_start.get('arguments'),dict) else {}
            if later_name==name and later_args.get('requirement_id')==requirement:return True
            continue
        if name in _SOURCE_TOOLS:
            if later_name in _SOURCE_TOOLS:
                failed_url=_source_url(workspace,start)
                later_url=_source_url(workspace,later_start,later)
                if failed_url and later_url and url_key(failed_url)==url_key(later_url):
                    return True
            continue
        if name and later_name==name:return True
        if name in _BUSINESS_RECOVERY_TOOLS and later_name in _BUSINESS_RECOVERY_TOOLS:return True
        if name in _INSPECTION_RECOVERY_TOOLS and later_name in _INSPECTION_RECOVERY_TOOLS:return True
    return False


def aggregate_delivery(workspace,status,check,requirements):
    if status in {'running','queued','awaiting_review','idle'}:return None
    rows=read_json(workspace.path/'requirements.json',[])
    business_required=(workspace.path/'business_rows.json').exists() or (workspace.path/'business_draft.json').exists() or (workspace.path/'business_artifact.json').exists() or any(r.get('artifact')=='task_result.xlsx' for r in rows)
    missing=list(dict.fromkeys(check.get('issues',[])))
    if (business_required and not (workspace.path/'task_result.xlsx').is_file()
            and 'business_excel_missing' not in missing):
        missing.append('business_excel_missing')
    usable=bool(check.get('artifacts_valid')) and not missing
    events=list(read_trace(workspace.path/'trace.jsonl'))
    starts={event.get('call_id'):event for event in events if event.get('event')=='tool_start'}
    warnings=[];recovered_warnings=[];seen=set()
    for index,event in enumerate(events):
        if event.get('event')!='tool_error':continue
        key=event.get('call_id') or event.get('detail','')
        if key in seen:continue
        seen.add(key)
        detail=event.get('detail','')
        try:
            detail=json.loads(detail.removeprefix('ERROR:'))['message']
        except (ValueError,KeyError,TypeError):
            # Legacy JSONL details are bounded and can end mid-diagnostics.
            # Recover the complete message, never spill truncated raw JSON into UI.
            match=re.search(r'"message"\s*:\s*("(?:\\.|[^"\\])*")',detail)
            if match:
                try:detail=json.loads(match.group(1))
                except ValueError:detail='整理时曾遇到问题，原始记录可在执行轨迹查看。'
            elif detail.startswith('ERROR: {'):detail='整理时曾遇到问题，原始记录可在执行轨迹查看。'
        blocking=not usable and (event.get('code') in {'business_validation','storage_error','call_id_conflict'} or (status=='permission_denied' and event.get('code')=='permission_denied'))
        entry={'kind':'execution','message':str(detail)[:500], 'call_id':event.get('call_id'),
               'blocking':blocking}
        if event.get('code') in _EXTERNAL_CODES:
            # Unavailable/unsupported external sources are not data-integrity
            # warnings. Keep the event visible in the trace; missing data still
            # surfaces through requirements/budget/report warnings.
            recovered_warnings.append({**entry,'recovered':True,'external':True})
        elif _execution_error_recovered(index,event,events,starts,workspace):
            recovered_warnings.append({**entry,'recovered':True})
        else:
            warnings.append(entry)
    if status=='budget_exceeded':warnings.append({'kind':'budget','message':'预算已用尽，已保存阶段性结果；仍需核对未完成需求' if usable else '预算已用尽，核心交付尚未齐全','blocking':not usable})
    elif status in {'error','timeout','interrupted','stalled','permission_denied'}:
        warnings.append({'kind':'execution_stop','message':'执行已停止，已有文件保留；详见执行轨迹。','blocking':not usable})
    for note in read_json(workspace.path/'business_artifact.json',{}).get('quality_notes',[]):
        warnings.append({'kind':'data','message':note,'blocking':False})
    pending=requirements.get('missing_requirements',[])
    if pending:warnings.append({'kind':'requirements','message':f'{len(pending)} 项需求尚待核对；文件交付不代表事实已全部核实。','blocking':False})
    for issue in missing:warnings.append({'kind':'blocking','message':issue,'blocking':True})
    accepted=usable and status=='completed' and requirements.get('requirements_supported',False)
    unresolved=bool(pending or requirements.get('quality_blockers') or status in {'partial','incomplete','budget_exceeded','error','timeout','stalled','interrupted','permission_denied'})
    outcome=('completed_with_warnings' if warnings else 'completed') if accepted else ('partial' if unresolved else 'needs_review') if usable else 'failed'
    return {'outcome':outcome,'usable':usable,'label':{'completed':'已完成','completed_with_warnings':'已完成（有告警）','partial':'部分完成','needs_review':'结果待确认','failed':'失败'}[outcome],
            'headline':'任务已完成' if accepted else '已保存阶段性交付，仍有需求未完成' if usable and unresolved else '交付已生成，等待你核对' if usable else '核心交付尚未完成',
            'warning_count':len(warnings),'execution_warning_count':sum(w['kind']=='execution' for w in warnings),
            'recovered_execution_warning_count':len(recovered_warnings),
            'recovered_execution_warnings':recovered_warnings,
            'warnings':warnings,'missing_core':missing,'semantic_accepted':accepted}
