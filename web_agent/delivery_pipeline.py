"""Validated incremental material and bounded, model-free delivery."""
import copy
import hashlib
import json
from .persistence import atomic_json, read_json, PersistenceError


def persist_rows(workspace,sheets,provenance,*,validate_only=False):
    canonical=copy.deepcopy(sheets)
    audits={(p.get('sheet'),p.get('label'),p.get('column')):p for p in provenance}
    for sheet in canonical:
        for row in sheet.get('rows',[]):
            for column,entry in row.get('cells',{}).items():
                audit=audits.get((sheet['name'],row['label'],column),{})
                if audit.get('status')=='verified':
                    entry.update(value=audit['normalized_value'],quote=audit['quote'],status='verified')
                    if 'raw_value' in audit:entry['raw_value']=audit['raw_value']
                    if 'input_unit' in audit:entry['input_unit']=audit['input_unit']
                    entry.setdefault('_original_input',audit.get('original_input',{}))
                elif audit.get('status') in {'review_required','editorial'}:
                    # Never persist a model-supplied "verified" claim when the
                    # program has downgraded this cell to manual review.
                    entry.pop('status', None)
    payload=json.dumps(canonical,ensure_ascii=False,sort_keys=True,allow_nan=False)
    if len(payload.encode('utf-8'))>512000:raise ValueError('增量数据超过512KB，请减少范围')
    version=hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]
    verified=sum(p.get('status')=='verified' for p in provenance)
    pending=sum(p.get('status') in {'review_required','unverified','not_disclosed'} for p in provenance)
    editorial=sum(p.get('status')=='editorial' for p in provenance)
    validation = {
        'verified_cells': verified,
        'pending_cells': pending,
        'status': 'verified' if verified > 0 and pending == 0 else 'needs_review'
    }
    if editorial:
        validation['editorial_cells'] = editorial
    if not validate_only:
        atomic_json(workspace.path/'business_rows.json',{'schema_version':3,'version':version,
                    'sheets':canonical,'provenance':provenance,'validation':validation})
    return version,validation


_FLAT_ROW_KEYS = frozenset({'label', 'cells', 'entity'})


def _flat_row_columns(sheets):
    """Return shared cell keys when input is exactly a list of row objects."""
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= 5:
        return None
    columns = None
    for item in sheets:
        if not isinstance(item, dict):
            return None
        if set(item) - _FLAT_ROW_KEYS:
            return None
        if not isinstance(item.get('label'), str) or not isinstance(item.get('cells'), dict):
            return None
        current = set(item['cells'])
        if columns is None:
            columns = current
        elif current != columns:
            return None
    if not columns or len(columns) > 40:
        return None
    return list(sheets[0]['cells'].keys())


def _coerce_flat_row_batch(sheets, prior_sheets):
    """Wrap a row-object batch into one comparison sheet without guessing facts.

    The model sometimes places rows directly in ``sheets``.  Only when every
    element is a row with the same cell keys do we reconstruct the envelope.
    A unique saved sheet with the same column set wins; otherwise a generic
    table name is used.  All fact/evidence validation still runs downstream.
    """
    columns = _flat_row_columns(sheets)
    if columns is None:
        return sheets, []
    if not isinstance(prior_sheets, list):
        prior_sheets = []
    matches = [
        item for item in prior_sheets
        if isinstance(item, dict) and item.get('mode') == 'comparison'
        and isinstance(item.get('name'), str)
        and isinstance(item.get('columns'), list)
        and set(item['columns']) == set(columns)
    ]
    if len(matches) > 1:
        names = '；'.join(str(item.get('name')) for item in matches[:10])
        raise ValueError(
            f"扁平行批次可匹配多个已保存表，无法自动确定：{names}。"
            "请使用 name+columns+rows 的完整结构。"
        )
    if matches:
        prior = matches[0]
        spec = {
            'name': prior['name'],
            'mode': 'comparison',
            'columns': copy.deepcopy(prior['columns']),
            'rows': copy.deepcopy(sheets),
        }
        note = {
            'sheet': prior['name'], 'rule': 'flat_rows_to_comparison_sheet',
            'rows': len(sheets), 'columns': len(columns),
            'note': '输入为 label/cells 行列表；按既有同列表包装为 comparison 表。',
        }
    else:
        spec = {
            'name': '对比表', 'mode': 'comparison',
            'columns': list(columns), 'fields': {},
            'rows': copy.deepcopy(sheets),
        }
        note = {
            'sheet': '对比表', 'rule': 'flat_rows_to_comparison_sheet',
            'rows': len(sheets), 'columns': len(columns),
            'note': '输入为 label/cells 行列表；按首次出现顺序包装为 comparison 表。',
        }
    return [spec], [note]


def stage_business_rows(workspace, sheets, *, recover_invalid=False):
    """Upsert batches by stable identity; reject bad schemas, disclose pending facts."""
    from .delivery_compiler import compile_comparisons
    from .business_excel import export_business_excel, BusinessExcelValidationError
    from .business_contract import comparison_contract,fail
    # A later batch may omit an existing sheet's field declarations. Reuse the
    # saved, validated contract only when the sheet identity AND columns match.
    # Explicit nonempty declarations are never overridden or weakened.
    saved=read_json(workspace.path/'business_rows.json',{})
    prior_sheets=saved.get('sheets',[])
    reused=[]
    schema_changes=[]
    flat_changes=[]
    if recover_invalid:
        sheets,flat_changes=_coerce_flat_row_batch(sheets,prior_sheets)
    if isinstance(sheets,list) and isinstance(prior_sheets,list):
        sheets=copy.deepcopy(sheets)
        for spec in sheets:
            if not isinstance(spec,dict) or spec.get('mode')!='comparison':
                continue
            prior=next((s for s in prior_sheets if isinstance(s,dict) and
                        s.get('name')==spec.get('name') and s.get('mode')=='comparison'),None)
            if prior and spec.get('columns')==prior.get('columns'):
                saved_fields=copy.deepcopy(prior.get('fields',{}))
                submitted=spec.get('fields')
                if not submitted:
                    spec['fields']=saved_fields
                    reused.append(spec['name'])
                elif submitted!=prior.get('fields'):
                    # An existing sheet's validated contract is authoritative.
                    # Replace a simplified/retyped declaration instead of
                    # rejecting the batch; every row is still validated below.
                    spec['fields']=saved_fields
                    reused.append(spec['name'])
                    schema_changes.append({
                        'sheet':spec['name'],'rule':'saved_fields_reused',
                        'note':'后续批次提交了不同的字段定义；已使用已保存且已验证的字段契约，未放宽任何校验。',
                    })
    sheets,contract_changes=comparison_contract(workspace,sheets)
    if any(s['mode']!='comparison' for s in sheets):
        fail('staging_mode','sheets','增量数据使用comparison模式；下载表与计算使用export_business_excel')
    if any(len(s['rows'])>20 for s in sheets):
        fail('batch_size','sheets.rows','每批每表保存1–20行')
    sheets,batch_changes=compile_comparisons(workspace,sheets,allow_structured_review=True)
    merged=copy.deepcopy(saved.get('sheets',[]))
    for incoming in sheets:
        if not isinstance(incoming,dict) or incoming.get('mode')!='comparison':
            fail('staging_mode','sheets','增量数据使用comparison模式；下载表与计算使用export_business_excel')
        rows=incoming.get('rows')
        if not isinstance(rows,list) or not 1<=len(rows)<=20:
            raise ValueError('每批每表保存1–20行，采集后立即校验')
        existing=next((s for s in merged if s['name']==incoming.get('name')),None)
        if existing is None:
            merged.append(copy.deepcopy(incoming));continue
        if any(existing.get(k)!=incoming.get(k) for k in ('columns','fields','layout')):
            # Precise, bounded diagnostics let the model reuse the saved schema
            # rather than guessing or deleting units/required constraints.
            changed=[{'column':col,
                      'saved':existing.get('fields',{}).get(col),
                      'submitted':incoming.get('fields',{}).get(col)}
                     for col in existing.get('columns',[])
                     if existing.get('fields',{}).get(col)!=incoming.get('fields',{}).get(col)]
            fail('sheet_contract_conflict','sheets.'+incoming['name'],
                 '同表后续批次必须保持columns/fields/layout一致；下次保留相同columns并省略fields复用已保存定义，或使用原有完整定义',
                 saved_version=saved.get('version'),
                 changed_columns=changed[:25],
                 columns_changed=existing.get('columns')!=incoming.get('columns'),
                 layout_changed=existing.get('layout')!=incoming.get('layout'))
        by_label={r['label'].casefold():r for r in existing['rows']}
        by_label.update({r['label'].casefold():copy.deepcopy(r) for r in rows})
        existing['rows']=list(by_label.values())
    # Validate through exactly the export path. No workbook/draft is changed here.
    compiled,changes=compile_comparisons(workspace,merged,allow_structured_review=True)
    # The core staging API remains STRICT and atomic by default. A rejected
    # batch must never replace previously persisted business_rows.json.
    # Agent-facing tools may explicitly request bounded cell-level disclosure.
    recovered_issues = []
    try:
        checked = export_business_excel(
            workspace, compiled, allow_structured_review=True, validate_only=True)
    except BusinessExcelValidationError as exc:
        atomic_json(workspace.path/'business_batch_validation.json', {
            'issues_total': len(exc.issues), 'issues': exc.issues,
            'saved_version': saved.get('version'), 'batch_saved': False,
        })
        if not recover_invalid:
            raise
        from .business_batch_recovery import disclose_invalid_cells
        for recovery_pass in range(3):
            recovered_issues.extend(exc.issues)
            disclosed, changed = disclose_invalid_cells(compiled, exc.issues)
            if not changed:
                raise
            compiled = disclosed
            try:
                checked = export_business_excel(
                    workspace, compiled, allow_structured_review=True, validate_only=True)
            except BusinessExcelValidationError as next_exc:
                exc = next_exc
                atomic_json(workspace.path/'business_batch_validation.json', {
                    'issues_total': len(recovered_issues) + len(exc.issues),
                    'issues': (recovered_issues + exc.issues)[:50],
                    'saved_version': saved.get('version'), 'batch_saved': False,
                })
            else:
                break
        else:
            raise exc
    version,validation=persist_rows(workspace,compiled,checked['provenance'])
    if recovered_issues:
        atomic_json(workspace.path/'business_batch_validation.json', {
            'issues_total': len(recovered_issues),
            'issues': recovered_issues[:50],
            'saved_version': saved.get('version'),
            'batch_saved': True,
            'saved_version_new': version,
            'recovery': 'invalid_cells_disclosed_as_unverified',
        })
        workspace.log('business_batch_recovered',
                      '错误单元格已明确标为未核实；其他字段通过原有校验后保存',
                      issues_total=len(recovered_issues), version=version)
    notes=[{'sheet':p['sheet'],'row':p['row'],'column':p['column'],'status':p.get('status'),'note':p.get('note',''),'diagnostic':p.get('diagnostic',{}),'source_id':p.get('source_id'),'input':p.get('original_input',{})}
           for p in checked['provenance'] if p.get('status') in {'review_required','unverified','not_disclosed'}]
    workspace.log('business_rows_saved','已保存；字段核验结果见validation',version=version,rows=checked['rows'],validation=validation)
    return {'version':version,'saved_rows':checked['rows'],'validation':validation,'review_count':len(notes),'review_items':notes,
            'normalizations':flat_changes+schema_changes+contract_changes+batch_changes+changes,'reused_existing_fields':reused,'note':'已保存；收尾时程序自动导出。复核项仍须披露，不能视为事实验收。'}


_HOST_MISSING_MARKER = '尚无完整逐项证据记录'
_ARTIFACT_NAMES = frozenset({
    'task_result.xlsx', 'research_sources.xlsx',
    'research_report.md', 'research_report.pdf',
})


def _host_artifact_candidate(row):
    """Only a pending artifact or an older *host*-missing artifact may be reconciled.

    Deliberate model/user ``missing`` assessments are never overwritten.
    """
    if row.get('kind') != 'artifact' or row.get('artifact') not in _ARTIFACT_NAMES:
        return False
    result = row.get('result') or {}
    if result.get('status', 'pending') == 'pending':
        return True
    return (result.get('status') == 'missing'
            and _HOST_MISSING_MARKER in str(result.get('reason', ''))
            and not result.get('source_ids'))


def _artifact_expected_to_exist(workspace, name, *, report_is_fallback):
    """Only suppress a stale 'missing' line before the final export.

    This is NOT an acceptance check. Final acceptance requires verify_delivery().
    """
    path = workspace.path / name
    if name == 'task_result.xlsx':
        if not path.is_file() or path.is_symlink() or not (workspace.path/'business_artifact.json').is_file():
            return False
        from .business_excel import verify_business_excel
        return not verify_business_excel(workspace)
    if name == 'research_sources.xlsx':
        return path.is_file() and not path.is_symlink()
    if name in {'research_report.md', 'research_report.pdf'}:
        return not report_is_fallback  # Will be checked after export, not certified yet.
    return False


def reconcile_generated_artifacts(workspace, *, report_is_fallback=False):
    """Reconcile only file *delivery*, after all output integrity checks pass.

    This never certifies the research findings, business cell values, calculations,
    machine_check contracts, or manual human acceptance. No network/model calls.
    """
    check = workspace.verify_delivery()
    if not check.get('artifacts_valid'):
        return []
    rows = read_json(workspace.path/'requirements.json', [])
    changed = []
    for row in rows:
        if not _host_artifact_candidate(row):
            continue
        name = row['artifact']
        path = workspace.path/name
        if not path.is_file() or path.is_symlink():
            continue
        if report_is_fallback and name in {'research_report.md', 'research_report.pdf'}:
            continue
        # A model cannot introduce a filename outside the defined artifact list.
        row['result'] = {
            'status': 'satisfied',
            'source_ids': [], 'report_excerpt': '', 'calculation_id': '',
            'reason': '由程序验证文件完整性后登记；不代表正文事实已独立核验。',
            'verified_by': 'host_artifact_integrity',
            'artifact_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        changed.append(row['id'])
    if changed:
        atomic_json(workspace.path/'requirements.json', rows)
        workspace.log('artifact_requirements_reconciled',
                      '仅按现有文件完整性登记 artifact，不代替事实与人工验收',
                      requirement_ids=changed, additional_model_requests=0)
    return changed


def finalize_delivery(workspace, reason):
    """Export saved material then reconcile deliverables without model requests."""
    from .delivery_compiler import export_compiled
    from .business_excel import verify_business_excel
    errors=[];generated=[]
    def attempt(name, action):
        try:
            action();generated.append(name)
        except PersistenceError:raise
        except Exception as exc:
            from .persistence import redact
            message=redact(str(exc))[:600]
            errors.append({'artifact':name,'message':message})
            workspace.log('delivery_warning',message,artifact=name)
    staged=read_json(workspace.path/'business_rows.json',{})
    draft=read_json(workspace.path/'business_draft.json',{})
    if staged.get('sheets'):
        attempt('task_result.xlsx',lambda:export_compiled(workspace,staged['sheets'],allow_structured_review=True))
    elif draft.get('sheets'):
        attempt('task_result.xlsx',lambda:export_compiled(workspace,draft['sheets'],allow_structured_review=True))
    if 'task_result.xlsx' in generated:
        meta=read_json(workspace.path/'business_artifact.json',{})
        statuses=[p.get('status') for p in meta.get('provenance',[])
                  if isinstance(p,dict) and p.get('status')]
        # Only apply the no-useless-Excel rule to OCR-table tasks. The
        # deterministic OCR path can and should produce verified values; an
        # all-unverified OCR workbook is not a useful deliverable. Legacy
        # review-only fixtures without OCR keep their existing contract.
        if ((workspace.path/'ocr_tables.json').is_file()
                and statuses and 'verified' not in statuses):
            # A workbook containing only unverified/placeholder cells is not a
            # useful deliverable. Keep the report and disclose the missing data;
            # do not present an all-"未核实" Excel as a result.
            (workspace.path/'task_result.xlsx').unlink(missing_ok=True)
            (workspace.path/'business_artifact.json').unlink(missing_ok=True)
            generated.remove('task_result.xlsx')
            workspace.log('business_excel_suppressed',
                          '没有任何已核实业务数据，未生成业务Excel；缺失项由报告披露')
    if workspace.sources():attempt('research_sources.xlsx',workspace.export_excel)
    rows=read_json(workspace.path/'requirements.json',[])
    # Keep research/calculation claims strict. Only file artifacts are reconciled
    # after export; do NOT prematurely turn those pending rows into missing.
    changed=False
    for row in rows:
        if row.get('result',{}).get('status','pending')=='pending' and not _host_artifact_candidate(row):
            row['result']={'status':'missing','source_ids':[], 'reason':reason+'；尚无完整逐项证据记录。'}
            changed=True
    if changed:atomic_json(workspace.path/'requirements.json',rows)
    report=workspace.path/'research_report.md'
    report_is_fallback=not report.is_file() or report.is_symlink()
    if not report_is_fallback:
        text = report.read_text(encoding='utf-8')
        appendix_marker = '\n\n---\n\n## 已记录的来源与原文摘录'
        if appendix_marker in text:
            text = text.rsplit(appendix_marker, 1)[0]

        for heading in ('## 数据口径与未核实事项','## 规范化数据与核验说明',
                        '## 尚未完成的需求', '## 文件生成问题'):
            text=text.split(heading,1)[0].rstrip()
        import re
        text=re.sub(r'(?m)^生成日期：[^\n]*\n\n数据范围：[^\n]*\n*','',text)
        # A previous host-generated placeholder is not substantive research.
        if re.search(r'(?m)^# 阶段性研究结果\s*$', text[:700]) and '未完成独立事实验收' in text[:700]:
            report_is_fallback=True
    else:
        text='# 阶段性研究结果\n\n'+reason+'。以下内容来自本任务已保存资料；未完成独立事实验收。'
        sources=workspace.sources()
        for source in sources[:50]:
            text+='\n\n- 来源 '+source['id']+'：'+source['fact'][:1000]
        if len(sources)>50:text+='\n\n正文仅列前50条摘要；完整已登记证据见来源附录与证据表。'
    missing=[]
    for row in rows:
        if row.get('result',{}).get('status')=='satisfied':
            continue
        if _host_artifact_candidate(row):
            try:
                if _artifact_expected_to_exist(workspace,row['artifact'],report_is_fallback=report_is_fallback):
                    continue  # Prevent a false 'missing' line in the final report.
            except (OSError,ValueError,KeyError,TypeError):
                pass
        missing.append(row)
    if missing:
        marker='## 尚未完成的需求'
        text=text.rstrip()+'\n\n'+marker+'\n\n'+'\n'.join(
            '- '+r['id']+'：'+r['description']+'；'+r.get('result',{}).get('reason','尚未验证') for r in missing)
    if errors:text+='\n\n## 文件生成问题\n\n'+'\n'.join('- '+e['artifact']+'：'+e['message'] for e in errors)
    attempt('research_report',lambda:workspace.export_report(text))
    reconciled=reconcile_generated_artifacts(workspace,report_is_fallback=report_is_fallback)
    # Any outstanding artifact is still genuinely incomplete. Do not suppress it
    # merely because the export was attempted or an outdated file exists.
    final_rows=read_json(workspace.path/'requirements.json',[])
    newly_missing=[]
    for row in final_rows:
        if _host_artifact_candidate(row) and row.get('result',{}).get('status','pending')=='pending':
            row['result']={'status':'missing','source_ids':[], 'reason':reason+'；尚无完整逐项证据记录。'}
            newly_missing.append(row['id'])
    if newly_missing:atomic_json(workspace.path/'requirements.json',final_rows)
    required=any(r.get('artifact')=='task_result.xlsx' for r in rows) or bool(staged or draft)
    business_missing=required and (not (workspace.path/'task_result.xlsx').is_file() or bool(verify_business_excel(workspace)))
    result={'reason':reason,'generated':generated,'errors':errors,'business_missing':business_missing,
            'artifact_requirement_ids_reconciled':reconciled,
            'additional_model_requests':0}
    atomic_json(workspace.path/'delivery_finalization.json',result)
    workspace.log('delivery_finalized',reason,**{k:v for k,v in result.items() if k!='reason'})
    return result
