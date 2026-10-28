"""Host-enforced error policy. Prompts are guidance, never loop enforcement."""
from __future__ import annotations

import asyncio
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

import httpx
from .security import UnsafeURL, validate_public_url,binary_document_url
from .persistence import redact, sanitize, PersistenceError


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable=False, details=None):
        self.code, self.retryable, self.details = code, retryable, details
        super().__init__(message)


ERROR_POLICY = {
    'business_staging_required': ('需要先保存业务数据', '使用已有来源保存一批数据；不能用继续浏览跳过保存检查点', False),
    'delivery_phase': ('已转入交付收尾', '复用已有来源与下载文件，生成业务Excel和报告；不再新增浏览', False),
    'interrupted': ('执行已中断', '进度已保留；继续执行需要你确认，不会自动发起模型请求', False),
    'storage_error': ('本地任务记录保存失败，已停止', '检查文件占用、目录权限和磁盘空间后再继续；不会自动重试模型请求', False),
    'model_timeout': ('模型响应超时，已暂停', '已保留进度；可稍后继续或检查模型服务，已发出的请求仍计入用量', False),
    'timeout': ('网络或工具等待超时', '有限重试；仍失败则换可信来源', True),
    'transport': ('临时连接失败', '有限重试；检查网络后再恢复', True),
    'http_404': ('页面或文件不存在', '使用已观察链接或搜索其它可信公开来源', False),
    'http_401': ('网站要求登录', '停止此路径，选择公开来源或请求人工处理', False),
    'http_403': ('网站拒绝访问或限制自动化', '停止此路径，不绕过登录、验证码或反自动化', False),
    'site_challenge': ('网站需要登录或人工验证', '停止此路径，请求人工处理或选择公开来源', False),
    'invalid_input': ('工具参数不正确', '根据工具说明和真实返回修正参数', False),
    'unobserved_path': ('当前检索路径需要调整', '停止猜接口；搜索官方文档或使用已观察到的真实链接，必要时换公开来源并保留缺失说明', False),
    'search_empty': ('没有读取到有效搜索结果', '调整关键词或转用官网站内检索；搜索页打开不等于搜索成功，遇到登录/验证码停止该路径', False),
    'stale_ref': ('页面元素已失效', '先调用 browser_snapshot，再使用新快照中的 ref', False),
    'permission_denied': ('安全策略禁止此操作', '停止此目标；不得改写域名、换 IP 或路径绕过限制', False),
    'approval_required': ('操作需要人工审批', '使用 request_human_review 或受控表单审批，不直接重试', False),
    'business_validation': ('业务表格单元格证据校验未通过', '按issues中的工作表、行、列逐项修正；使用连续原文值，无法核实则填null及reason。有draft_id时调用repair_business_excel仅修正错误单元格，不重写整表', False),
    'evidence_mismatch': ('来源或报告摘录未通过校验', '重新读取原始来源，复制连续原文并修正来源或报告摘录', False),
    'report_render': ('PDF报告排版失败', 'Markdown如已保存可先查看；PDF仍未交付。停止重复导出并检查报告排版日志', False),
    'loop_detected': ('相同操作重复且没有进展', '使用现有观察重新规划；无法完成则交付部分结果', False),
    'budget_exceeded': ('执行预算已耗尽', '保存中间结果；只有用户明确提高预算后才可继续', False),
}


def error_policy(code):
    if code.startswith('http_') and code[5:].isdigit():
        status=int(code[5:])
        if status in {408,429} or status>=500:
            return '网站暂时不可用或限制请求频率','预算内有限重试；仍失败则停止该路径或选择可信公开来源',True
    return ERROR_POLICY.get(code,('工具执行失败','检查具体原因并调整计划；不要盲目重复',False))


def browser_failure(text):
    low = text.lower()
    if any(s in low for s in ('not found in the current page snapshot', 'no element found', 'element is not attached', 'ref不在', 'snapshot first')):
        return ToolFailure('stale_ref', text[:600])
    if any(s in low for s in ('err_blocked_by_client', 'err_address_invalid')):
        return ToolFailure('permission_denied', text[:600])
    if any(s in low for s in ('captcha', 'verify you are human', 'authentication required')):
        return ToolFailure('site_challenge', text[:600])
    if 'timeout' in low or 'err_timed_out' in low:
        return ToolFailure('timeout', text[:600], retryable=True)
    if any(s in low for s in ('err_connection_', 'err_network_changed','err_name_not_resolved','err_tunnel_connection_failed')):
        return ToolFailure('transport', text[:600], retryable=True)
    return ToolFailure('browser_error', text[:600])


def classify_error(exc: Exception) -> ToolFailure:
    if isinstance(exc, ToolFailure):
        return exc
    if isinstance(exc, PersistenceError):
        return ToolFailure('storage_error', str(exc))
    if isinstance(exc, PermissionError) and (exc.filename or exc.filename2):
        return ToolFailure('storage_error', '本地文件访问失败；请检查文件占用及目录权限。')
    if isinstance(exc, (UnsafeURL, PermissionError)):
        return ToolFailure('permission_denied', str(exc))
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return ToolFailure('timeout', str(exc) or 'tool timeout', retryable=True)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return ToolFailure('http_' + str(status), str(exc), retryable=status in {408, 429} or status >= 500)
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return ToolFailure('transport', str(exc), retryable=True)
    from .report_export import ReportRenderError
    if isinstance(exc,ReportRenderError):
        return ToolFailure('report_render',str(exc))
    if isinstance(exc, FileNotFoundError):
        return ToolFailure('not_found', str(exc))
    from .business_excel import BusinessExcelValidationError
    if isinstance(exc,BusinessExcelValidationError):
        return ToolFailure('business_validation',str(exc),details=exc.details)
    from .delivery_inspection import DeliveryInputError
    if isinstance(exc,DeliveryInputError):
        return ToolFailure('evidence_mismatch' if exc.details.get('kind')=='report_excerpt' else 'invalid_input',str(exc),details=exc.details)
    if isinstance(exc, (ValueError, TypeError)):
        if any(word in str(exc) for word in ('原文','摘录','未观察','没有观察','来源ID','观察记录')):
            return ToolFailure('evidence_mismatch', str(exc))
        return ToolFailure('invalid_input', str(exc))
    if type(exc).__name__ in {'ToolException','McpError'}:
        return browser_failure(str(exc))
    # Unknown errors must not automatically trigger the same operation again.
    return ToolFailure('internal', str(exc))


def error_output(failure: ToolFailure) -> str:
    label, action, _ = error_policy(failure.code)
    kind=failure.details.get('kind') if isinstance(failure.details,dict) else None
    if kind=='business_excel' and not failure.details.get('draft_id'):
        action='该增量批次未保存；按issues修正本批数据后重新save_business_rows，原有已通过数据仍保留；不要调用草稿修复工具。'
    elif kind=='report_excerpt':action='调用inspect_delivery读取当前报告原文，再用report_line_ids和report_sha256引用；不要重抄、改写或重新浏览来源。'
    elif kind=='business_state':action='调用inspect_delivery；业务表已导出则复用，没有草稿时不要继续使用历史draft_id。'
    elif kind=='business_structure':action='按diagnostics中的工作表、行及missing_columns/extra_columns修正结构；label已包含行名，不要重复创建参数列。'
    elif kind=='comparison_schema':action='按diagnostics.issues中的code/path修正结构；两个业务工具共用diagnostics.expected。对象名称或entity ID必须稳定唯一；来源元数据由程序管理。'
    elif kind=='source_quote_recovery':action='先检查diagnostics中该URL已保存的观察ID与原文上下文；确认相关后，调用record_source_from_observation(observation_id,keyword)由程序截取连续摘录。未找到合适线索时调用inspect_saved_pages按ID查看；不要重拼引文或把建议线索当作事实核实。'
    return 'ERROR: ' + json.dumps({'code': failure.code, 'retryable': failure.retryable,
                                  'message': redact(str(failure))[:700], 'label':label,'next_action':action,
                                  **({'diagnostics':sanitize(failure.details)} if failure.details else {})}, ensure_ascii=False)


def signature(name, args, scope=''):
    canonical = dict(args)
    if isinstance(canonical.get('url'), str):
        parsed = urlsplit(canonical['url'].strip())
        canonical['url'] = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                                      parsed.path.rstrip('/') or '/', parsed.query, ''))
    return hashlib.sha256(json.dumps([name, canonical, scope], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preflight(name, args, journal, allowed_domains, scope=''):
    """Reject every disallowed URL before dispatch, including different paths/tools."""
    if name in {'browser_navigate', 'read_public_pdf', 'download_public_file','inspect_public_forms','submit_public_form'}:
        validate_public_url(args.get('url', ''), allowed_domains)
        host = urlsplit(args.get('url', '')).hostname
        if host and any(row.get('error_code')=='site_challenge' and row.get('target_domain')==host for row in journal.values()):
            raise ToolFailure('site_challenge','该网站已要求人工验证；停止访问该站点，选择其他公开来源。')
    if name=='browser_navigate' and binary_document_url(args.get('url','')):
        raise ToolFailure('file_tool_required','这是二进制文件，请用download_public_file+inspect_downloaded_table或read_public_pdf；文件分析返回的JSON已可作来源观察，不要改为浏览文件目录。')
    key = signature(name, args, scope)
    previous = [entry for entry in journal.values() if entry.get('signature') == key]
    failures = sum(entry.get('output', '').startswith('ERROR:') for entry in previous)
    if failures >= 2 or len(previous) >= 3:
        raise ToolFailure('loop_detected', '相同调用已失败两次或执行三次；请利用已有观察、调整路径或结束。')
    return key


def trailing_failures(journal):
    count = 0
    for entry in reversed(list(journal.values())):
        if not entry.get('output', '').startswith('ERROR:'):
            break
        count += 1
    return count


def stop_reason(journal, workspace=None):
    """Different recoverable failures are not automatically a dead loop."""
    tail=[]
    for row in reversed(list(journal.values())):
        if not row.get('output','').startswith('ERROR:'):break
        tail.append(row)
    if any(row.get('error_code')=='storage_error' for row in tail):
        return 'error','本地任务记录保存失败，已停止；请检查文件占用及目录权限后再继续。'
    if sum(row.get('error_code')=='site_challenge' for row in tail)>=2:
        return 'interrupted','网站连续要求人工验证，已暂停；已保存的资料和累计用量保留，可补充其他公开来源后继续。'
    # Inspect durable diagnostics across intervening browse/inspection calls.
    # A different tool-call id or modified arguments are not evidence of progress.
    # A successful business write starts a new correction attempt sequence.
    business = []
    for row in journal.values():
        if row.get('name') not in {'save_business_rows', 'export_business_excel', 'repair_business_excel'}:
            continue
        if not row.get('output', '').startswith('ERROR:'):
            business.clear()
            continue
        if row.get('error_code') != 'business_validation':
            continue
        try:
            diagnostics = json.loads(row['output'][7:])['diagnostics']
            issues = diagnostics['issues']
            total = diagnostics['issues_total']
            if (not isinstance(total, int) or isinstance(total, bool)
                    or total < 1 or diagnostics.get('issues_omitted', 0)
                    or not isinstance(issues, list) or len(issues) != total):
                positions = None  # Partial diagnostics: cannot prove correction progress.
            else:
                positions = frozenset((issue['sheet'], issue['row'], issue['column'],
                                       (issue.get('diagnostic') or {}).get('code'))
                                      for issue in issues)
                if len(positions) != total:
                    positions = None
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            positions = None
        business.append(positions)

    if (len(business) >= 3
            and journal[next(reversed(journal))].get('name') in
            {'save_business_rows', 'export_business_excel', 'repair_business_excel'}
            and journal[next(reversed(journal))].get('error_code') == 'business_validation'):
        _, previous, current = business[-3:]
        # Last correction made real progress: allow more work, even if earlier
        # attempts stagnated. An unrelated worksheet is a new problem.
        if (previous is None or current is None
                or not (current < previous or current.isdisjoint(previous))):
            return 'stalled', ('业务表格连续多次未能确认校验进展（部分诊断可能省略错误项），已停止无效纠错；'
                               '已有来源、报告、Checkpoint和剩余预算保留。'
                               '需要取得对应商品/字段的新证据、修正表格结构，或标记未核实。')
    source_mismatches=[row for row in tail if row.get('name')=='record_source' and row.get('error_code')=='evidence_mismatch']
    if len(source_mismatches)>=3:
        return 'stalled','来源登记连续三次未通过原文核验，已停止无效尝试。请核对实际最终网址与连续原文；已有资料、Checkpoint和累计预算保留。'
    denied=[row for row in tail if row.get('error_code')=='permission_denied']
    for row in denied:
        if row.get('target_domain') and sum(r.get('target_domain')==row['target_domain'] for r in denied)>=2:
            return 'permission_denied','相同目标已被安全策略拒绝，已停止；不要通过换路径或 IP 绕过限制。'
    if len(denied)>=3:
        return 'permission_denied','多个目标被安全策略拒绝，已停止此任务；请检查网络模式或任务范围。'
    if sum(row.get('error_code')=='loop_detected' for row in tail)>=3:
        return 'stalled','相同调用反复出现且没有进展，已停止；已有资料和 Checkpoint 保留。'
    if workspace is not None:
        hard_codes = {'http_404', 'http_403', 'http_410', 'image_document',
                      'site_challenge', 'search_empty', 'unsupported_document'}
        hard_failures = [row for row in journal.values()
                         if row.get('error_code') in hard_codes]
        if len(hard_failures) >= 10 and not workspace.sources():
            return 'stalled', ('连续多次没有取得可核验来源（404/403/图片/搜索无结果），'
                               '已停止无效尝试；请换公开数据源或结束并如实说明缺失。')
    if len(tail)>=8:
        return 'stalled','已给予有限纠错机会，连续八次工具调用仍未取得进展，已安全停止。'
    return None
