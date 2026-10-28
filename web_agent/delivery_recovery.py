"""Small, valid JSON recovery packets. Never truncate structured errors mid-field."""
import json
import hashlib


def recovery_messages(workspace,journal):
    """Do not keep directing recovery at an already repaired business export."""
    from .persistence import read_json
    business_names={'export_business_excel','repair_business_excel'}
    rows=list(journal.values())
    exports=[r for r in rows if r.get('name') in business_names]
    resolved=False
    workbook=workspace.path/'task_result.xlsx'
    if exports and not (workspace.path/'business_draft.json').exists() and workbook.is_file() and not workbook.is_symlink() and workbook.stat().st_size<=20*1024*1024:
        meta=read_json(workspace.path/'business_artifact.json',{})
        if meta.get('sha256')==hashlib.sha256(workbook.read_bytes()).hexdigest():
            latest=exports[-1]
            resolved=not latest.get('output','').startswith('ERROR:')
            if not resolved:
                receipt=read_json(workspace.path/'business_repair_receipt.json',{})
                resolved=(receipt.get('task_id')==workspace.task_id
                    and receipt.get('business_sha256')==meta['sha256']
                    and receipt.get('resolved_failure_sha256')==hashlib.sha256(latest.get('output','').encode()).hexdigest())
    return [compact_error(row.get('output',''),2200) for row in rows[-4:]
            if row.get('error_code') and not (resolved and row.get('name') in business_names)]


def compact_result(content, limit=12000):
    """Keep structured success results parseable, especially saved source text."""
    content=str(content)
    if content.startswith('ERROR:'):return compact_error(content,min(limit,6000))
    if len(content)<=limit:return content
    try:data=json.loads(content)
    except (ValueError,TypeError):return content[:limit]
    if isinstance(data,list):
        data={'sources':data,'total':len(data),'offset':0,'next_offset':None}
    encode=lambda:json.dumps(data,ensure_ascii=False)
    if isinstance(data,dict) and isinstance(data.get('text'),str):
        original=data['text'];data['truncated']=True
        low,high=0,len(original)
        while low<high:
            mid=(low+high+1)//2
            data['text']=original[:mid]
            if len(encode())<=limit:low=mid
            else:high=mid-1
        data['text']=original[:low]
        if len(encode())<=limit:return encode()
    elif isinstance(data,dict) and isinstance(data.get('sources'),list):
        data['truncated']=True
        while data['sources'] and len(encode())>limit:data['sources'].pop()
        data['next_offset']=data.get('offset',0)+len(data['sources'])
        if len(encode())<=limit:return encode()
    elif isinstance(data,dict) and isinstance(data.get('issues'),list):
        data['truncated']=True
        while data['issues'] and len(encode())>limit:data['issues'].pop()
        data['next_offset']=data.get('issue_offset',0)+len(data['issues'])
        if len(encode())<=limit:return encode()
    # Do not emit half a JSON token. The original remains in the tool journal.
    return json.dumps({'truncated':True,'message':'结构化结果超过上下文上限，原始结果保存在工具记录；请使用该工具的筛选或分页参数缩小范围。'},ensure_ascii=False)


def compact_error(content, limit=6000):
    """Preserve current draft/version and cell locations under a hard size bound."""
    if not isinstance(content, str) or not content.startswith('ERROR:'):
        return str(content)[:limit]
    try:
        data=json.loads(content[7:])
    except (ValueError, TypeError):
        return content[:limit]
    if not isinstance(data,dict):return content[:limit]
    diagnostics=data.get('diagnostics')
    if not isinstance(diagnostics,dict) or diagnostics.get('kind')!='business_excel':
        return content[:limit]
    packet={k:str(data[k])[:300] for k in ('code','label','message','next_action') if k in data}
    packet['retryable']=bool(data.get('retryable',False))
    details={k:diagnostics[k] for k in ('kind','draft_id','repair_tool','issues_total') if k in diagnostics}
    total=diagnostics.get('issues_total',len(diagnostics.get('issues',[])))
    details.update(issues=[],issues_omitted=total)
    packet['diagnostics']=details
    encode=lambda:'ERROR: '+json.dumps(packet,ensure_ascii=False)
    for original in diagnostics.get('issues',[]):
        issue={k:original[k] for k in ('sheet','row','column','source_id','expected_type','field') if k in original}
        issue['message']=str(original.get('message',''))[:220]
        for key,bound in (('quote_preview',160),('source_context',1000)):
            if key in original:issue[key]=str(original[key])[:bound]
        if 'suggested_entry' in original:issue['suggested_entry']=original['suggested_entry']
        details['issues'].append(issue)
        details['issues_omitted']=max(0,total-len(details['issues']))
        if len(encode())>limit:
            # Prefer the location and actionable message over optional previews.
            for key in ('source_context','suggested_entry','quote_preview'):
                issue.pop(key,None)
                if len(encode())<=limit:break
        if len(encode())>limit:
            details['issues'].pop()
            details['issues_omitted']=max(0,total-len(details['issues']))
            break
    if len(encode())>limit:
        for key in ('message','next_action','label'):packet.pop(key,None)
    return encode()
