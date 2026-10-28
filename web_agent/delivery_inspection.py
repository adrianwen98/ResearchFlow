"""Read current delivery state and exact report lines without network/model IO."""
import hashlib
import json
from .persistence import read_json


class DeliveryInputError(ValueError):
    def __init__(self,message,details):
        super().__init__(message)
        self.details=details


def saved_report(workspace):
    path=workspace.path/'research_report.md'
    if not path.is_file() or path.is_symlink() or path.stat().st_size>2*1024*1024:
        raise ValueError('当前报告不存在或不可读取；请先export_report')
    data=path.read_bytes()
    return data.decode('utf-8'),hashlib.sha256(data).hexdigest()


def workbook_state(workspace):
    draft=read_json(workspace.path/'business_draft.json',{})
    if draft:
        return {'status':'draft','draft_id':draft.get('draft_id'),
                'next_tool':'inspect_business_draft','note':'只使用当前draft_id定点修正，不复制历史版本。'}
    path=workspace.path/'task_result.xlsx';meta=read_json(workspace.path/'business_artifact.json',{})
    if (path.is_file() and not path.is_symlink() and path.stat().st_size<=20*1024*1024
            and meta.get('sha256')==hashlib.sha256(path.read_bytes()).hexdigest()):
        return {'status':'exported','filename':path.name,'sha256':meta['sha256'],
                'sheets':[{'name':s['name'],'columns':s['columns'][:12],'columns_omitted':max(0,len(s['columns'])-12),'rows':len(s['rows'])} for s in meta.get('sheets',[])],
                'note':'业务表已导出；旧draft_id已失效。优先复用现有文件，继续报告和需求核对，不要重建整表。'}
    return {'status':'not_exported','next_tool':'export_business_excel'}


def resolved_draft(workspace,draft_id):
    """Acknowledge only a recorded, hash-bound consumed version; never apply it."""
    if (workspace.path/'business_draft.json').exists():return None
    state=workbook_state(workspace)
    if state['status']!='exported':return None
    receipt=read_json(workspace.path/'business_repair_receipt.json',{})
    completed=read_json(workspace.path/'business_export_state.json',{})
    if ((receipt.get('task_id')==workspace.task_id and receipt.get('original_draft_id')==draft_id
         and receipt.get('business_sha256')==state['sha256']) or
        (completed.get('task_id')==workspace.task_id and draft_id in completed.get('consumed_draft_ids',[])
         and completed.get('business_sha256')==state['sha256'])):
        from .business_excel import verify_business_excel
        if not verify_business_excel(workspace):return state
    return None


def inspect_delivery(workspace,query='',line_offset=0):
    if not isinstance(query,str) or len(query)>120 or type(line_offset) is not int or not 0<=line_offset<=100000:
        raise ValueError('query最多120字；line_offset需为0–100000的整数')
    result={'business':workbook_state(workspace)}
    try:text,digest=saved_report(workspace)
    except ValueError:return {**result,'report':{'status':'missing','next_tool':'export_report'}}
    lines=text.splitlines()
    candidates=[(i+1,s) for i,s in enumerate(lines) if s.strip() and (not query or query.casefold() in s.casefold())]
    selected=candidates[line_offset:line_offset+20]
    result['report']={'status':'exported','sha256':digest,'total_lines':len(lines),'matching_lines':len(candidates),
        'lines':[{'line_id':i,'text':s[:1200],'truncated':len(s)>1200} for i,s in selected],
        'next_offset':line_offset+20 if len(candidates)>line_offset+20 else None,
        'note':'report_line_ids引用这里的行号并提供report_sha256，不必重新抄写或拼接摘录。行号从1开始；报告变化后须重新读取。'}
    # Leave structured output intact under the model context budget.
    while len(json.dumps(result,ensure_ascii=False))>10000 and result['report']['lines']:
        result['report']['lines'].pop()
        result['report']['next_offset']=line_offset+len(result['report']['lines'])
    return result


def report_lines(workspace,line_ids,report_sha256):
    text,digest=saved_report(workspace)
    if report_sha256!=digest:
        raise DeliveryInputError('报告版本已变化；先inspect_delivery读取当前行号和sha256',{'kind':'report_excerpt','report_sha256':digest,'next_tool':'inspect_delivery'})
    lines=text.splitlines()
    if not isinstance(line_ids,list) or not 1<=len(line_ids)<=20 or len(set(i for i in line_ids if type(i) is int))!=len(line_ids):
        raise ValueError('report_line_ids需为1–20个不重复的整数行号')
    if any(type(i) is not int or not 1<=i<=len(lines) or not lines[i-1].strip() for i in line_ids):
        raise ValueError('报告行号不存在或为空，请使用inspect_delivery返回的line_id')
    excerpts=[lines[i-1] for i in sorted(line_ids)]
    if sum(map(len,excerpts))>2000:raise ValueError('摘录总长不能超过2000字，请只选择相关行')
    return excerpts,digest
