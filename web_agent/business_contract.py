"""Shared comparison input contract. No network, model, or inferred facts."""
import copy
import re
from .delivery_inspection import DeliveryInputError
from .delivery_schema import field_for, inferred_field
from .evidence_urls import url_key

META = {'来源ID':'source_id', '来源编号':'source_id', 'source_id':'source_id',
        '来源URL':'url', '来源网址':'url', '官方来源':'url', 'source_url':'url',
        '核验状态':'submitted_status', 'verification_status':'submitted_status',
        '原文摘录':'quote'}
IDENTITY = ('机型', '对象', '产品名称', '名称')
_EDITORIAL_LABEL_COLUMN = re.compile(r'主题|分类|类别|标签|topic|category|tag', re.I)


def _normalize_field_role(column, supplied, sheet):
    """Downgrade a role that cannot serve as an editorial/document label.

    The model sometimes marks an identifier column (e.g. PEP/编号) as
    editorial_label.  Treat it as a normal text field so its values are still
    fact-validated instead of rejecting the whole batch.
    """
    if not isinstance(supplied, dict):
        return supplied, None
    role = supplied.get('role')
    if role not in {'editorial_label', 'document_identity'}:
        return supplied, None
    inferred = inferred_field(column)
    if role == 'editorial_label':
        invalid = 'role' in inferred or not _EDITORIAL_LABEL_COLUMN.search(column)
    else:
        invalid = 'role' in inferred
    if not invalid:
        return supplied, None
    normalized = {key: value for key, value in supplied.items() if key != 'role'}
    return normalized, {
        'sheet': sheet, 'column': column, 'rule': 'invalid_role_downgraded_to_fact',
        'removed_role': role,
        'note': f'{column} 的 {role} 角色不适用于该列；已按普通文本字段继续逐格核验。',
    }


def fail(code, path, message, **context):
    raise DeliveryInputError(message, {'kind':'comparison_schema', 'issues_total':1, **context,
        'issues':[{'code':code, 'path':path, 'message':message, **context}],
        'expected':{'mode':'comparison', 'columns':['参数'],
                    'rows':[{'label':'对象名称', 'cells':{'参数':{
                        'value':'原文短值', 'source_id':'已有来源ID', 'quote':'连续原文'}}}]}})


def scalar(value):
    return value.get('value') if isinstance(value, dict) else value


def source_text(workspace, source):
    """Read only the exact observation bound to this source, validating identity."""
    from .persistence import read_json
    oid=source.get('observation_id', '')
    if not isinstance(oid,str) or not re.fullmatch(r'[a-f0-9]{20}',oid):return ''
    path=workspace.path/'observations'/f'{oid}.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:return ''
    observed=read_json(path,{})
    if observed.get('id')!=oid or url_key(observed.get('url',''))!=url_key(source['url']):return ''
    return observed.get('text','') if isinstance(observed.get('text'),str) else ''


def primary_subject_quote(workspace, source, label, quote, *, max_distance=600):
    """A short literal quote after the actual page's primary object heading.

    A rendered owner and first observed H1 must agree exactly. Other section
    headings are allowed, but a competing same-family H1 invalidates ownership.
    This is a conservative extra verification path, not semantic inference.
    """
    observed=source_text(workspace,source)
    if not observed or not isinstance(label,str) or not isinstance(quote,str):
        return False
    headings=re.findall(r'(?im)^\s*-\s*heading\s+"([^"\n]+)"\s+\[level=1\]',observed)
    canonical=lambda v:re.sub(r'\s+',' ',v.strip()).casefold()
    target=canonical(label)
    if not headings or canonical(headings[0])!=target:
        return False
    # Product/version headings must not introduce a competing row owner.
    # For example, a Python release page may also have "Major new features"
    # as H1; that section title isn't a competing Python release.
    family=target.split()[0]
    if any(canonical(h)!=target and canonical(h).split()[0]==family
           for h in headings[1:] if canonical(h)):
        return False
    rendered=observed.split('### Page',1)[0]
    if rendered==observed and '### Rendered page text' not in observed:
        return False
    owners=list(re.finditer(r'(?m)^\s*'+re.escape(label.strip())+r'\s*$',rendered,re.I))
    if len(owners)!=1:
        return False
    span=exact_span(rendered[owners[0].end():],quote)
    return span is not None and 0<=span[0]<=max_distance


def exact_span(text, value):
    """Whitespace equivalence only, returning actual original characters."""
    if not isinstance(value,str) or not value.strip() or len(value)>200:return None
    pattern=r'\s+'.join(re.escape(part) for part in value.split())
    match=re.search(pattern,text)
    return match.span() if match else None



def release_feature_subject_bound(workspace, source, label, column, quote, audit):
    """Bind an extractive major-feature item to one saved release document.

    This does NOT certify translated summaries, unrelated fields, page snippets,
    or a feature from a different release-series section. The observation's
    primary heading, rendered owner, and major-feature section must agree.
    """
    from .scalar_contract import text_form
    if not all(isinstance(s, str) for s in (label, column, quote)):
        return False
    if not re.fullmatch(r'(?:主要改进|主要特性)[1-9]\d?|major(?: new)? features?\s*[1-9]?', column.strip(), re.I):
        return False
    if not isinstance(audit, dict) or not isinstance(audit.get('normalized_value'), str):
        return False
    # An English original must be the actual stored scalar, not a Chinese
    # translation, a concatenation, or a model's semantic interpretation.
    if text_form(audit['normalized_value']) != text_form(quote.strip()):
        return False
    match = re.fullmatch(r'[A-Za-z][A-Za-z0-9 .+#_-]{1,60}\s+(\d+(?:\.\d+){1,4})',label.strip())
    if not match or not 12 <= len(quote.strip()) <= 200:
        return False
    version=match.group(1)
    series='.'.join(version.split('.')[:2])
    observed=source_text(workspace,source)
    if not observed or not primary_subject_quote(workspace,source,label,quote,max_distance=15000):
        return False
    rendered=observed.split('### Page',1)[0]
    owner=re.search(r'(?m)^\s*'+re.escape(label.strip())+r'\s*$',rendered,re.I)
    if owner is None:
        return False
    # Require an explicit version-specific section heading, not a generic
    # mention elsewhere in a multi-release document or navigation/sidebar.
    pattern=re.compile(r'(?im)^\s*Major new features of the\s+(\d+\.\d+)\s+series\b[^\n]*$')
    sections=list(pattern.finditer(rendered))
    own=[m for m in sections if m.group(1)==series and m.start()>owner.end()]
    if len(own)!=1:
        return False
    section=own[0]
    next_sections=[m.start() for m in sections if m.start()>section.end()]
    end=min(next_sections) if next_sections else len(rendered)
    # Quote must be a complete rendered feature line in the matching section,
    # never a sentence borrowed from elsewhere on the same page.
    feature_region=rendered[section.end():min(end, section.end()+15000)]
    full_quote=re.compile(r'(?im)^\s*'+r'\s+'.join(re.escape(x) for x in quote.split())+r'\s*$')
    if not full_quote.search(feature_region):
        return False
    # Cross-owner claims in a feature line do not become this release's fact.
    if re.search(r'\b'+re.escape(version)+r'\s+(?:pro|plus|ultra|mini|max)\b',quote,re.I):
        return False
    return True

def display_entry(workspace, entry, field, source):
    """Resolve a compound display value against a bounded original context."""
    if field.get('role')!='display_type' or not isinstance(entry,dict) or not source:return entry
    value=entry.get('value');quote=entry.get('quote','')
    if not isinstance(value,str) or not isinstance(quote,str):return entry
    parts=[p.strip() for p in re.split('[，、]',value)]
    if not 2<=len(parts)<=4 or any(not p for p in parts):return entry
    workspace.evidence_for(source['url'],quote,source.get('observation_id'))
    text=source_text(workspace,source);anchor=exact_span(text,quote)
    if not anchor:return entry
    start=max(0,anchor[0]-300);context=text[start:anchor[1]+300]
    spans=[exact_span(context,p) for p in parts]
    if any(s is None for s in spans):return entry
    left=min(s[0] for s in spans);right=max(s[1] for s in spans)
    if right-left>600:return entry
    original=context[left:right]
    workspace.evidence_for(source['url'],original,source.get('observation_id'))
    return {**entry,'quote':original,'_display_input_quote':quote}


def comparison_contract(workspace, sheets):
    """Normalize canonical or unambiguous flat rows; reject ambiguous identities."""
    if not isinstance(sheets,list) or not 1<=len(sheets)<=5:
        fail('sheets_shape','sheets','需要1–5个业务工作表')
    output=copy.deepcopy(sheets); changes=[]
    sources={s['id']:s for s in workspace.sources()}
    names=set()
    for si,spec in enumerate(output):
        path=f'sheets[{si}]'
        if not isinstance(spec,dict):fail('sheet_shape',path,'工作表必须为对象')
        mode=spec.get('mode')
        if mode is None and spec.get('layout')=='comparison':
            spec['mode']=mode='comparison'
            changes.append({'sheet':spec.get('name'),'rule':'layout_comparison_to_mode'})
        if mode is None:
            # Each mode has a distinct, unambiguous shape signal.  Infer only
            # when exactly one signal is present; explicit invalid modes still
            # fail below and no validation branch is skipped.
            signals=[]
            if isinstance(spec.get('rows'),list) and isinstance(spec.get('columns'),list):
                signals.append('comparison')
            if isinstance(spec.get('filename'),str):
                signals.append('table')
            if isinstance(spec.get('calculation_id'),str):
                signals.append('calculation')
            if len(signals)==1:
                spec['mode']=mode=signals[0]
                changes.append({'sheet':spec.get('name'),'rule':signals[0]+'_shape_to_mode'})
        if not isinstance(mode,str) or mode not in {'comparison','table','calculation'}:
            fail('invalid_mode',path+'.mode','mode必须为comparison/table/calculation；comparison的行使用label/cells',received=mode)
        name=spec.get('name')
        if (not isinstance(name,str) or not name or len(name)>31 or re.search(r'[\\/*?:\[\]]',name)
                or name.casefold() in names|{'provenance','metadata'}):
            fail('sheet_name',path+'.name','工作表名无效或重复')
        names.add(name.casefold())
        if mode!='comparison':continue
        if spec.get('layout')=='comparison':spec.pop('layout')
        columns=spec.get('columns');rows=spec.get('rows');fields=spec.get('fields',{})
        if (not isinstance(columns,list) or not 1<=len(columns)<=40 or
                any(not isinstance(c,str) or not c.strip() or len(c)>80 for c in columns) or len(set(columns))!=len(columns)):
            fail('columns_shape',path+'.columns','需要1–40个唯一非空业务列名')
        if not isinstance(rows,list) or not 1<=len(rows)<=200:
            fail('rows_shape',path+'.rows','比较表需要1–200行')
        if not isinstance(fields,dict) or set(fields)-set(columns):
            fail('fields_shape',path+'.fields','fields必须对应columns')
        identity=[c for c in columns if fields.get(c)=='label' or (c in IDENTITY and all(
            isinstance(r,dict) and ('cells' not in r or not str(r.get('label','')).strip()
                or (isinstance(r.get('cells'),dict) and scalar(r['cells'].get(c))==r.get('label')))
            for r in rows))]
        metadata=[c for c in columns if c in META]
        business=[c for c in columns if c not in identity+metadata]
        if not business:fail('no_facts',path+'.columns','至少需要一个业务事实字段，元数据不能替代事实')
        canonical_fields={}
        for col in business:
            supplied=fields.get(col,{})
            # Explicit flat-row column mappings are adapters, not field types.
            if supplied=='value' and all(isinstance(r,dict) and 'cells' not in r for r in rows):supplied={}
            supplied, role_change = _normalize_field_role(col, supplied, name)
            if role_change:changes.append(role_change)
            try:
                definition=field_for(col,{col:supplied})
                if col in fields or 'role' in definition:canonical_fields[col]=definition
            except (ValueError,TypeError) as exc:fail('field_definition',path+'.fields.'+col,str(exc))
        labels={}
        for ri,row in enumerate(rows):
            rp=path+f'.rows[{ri}]'
            if not isinstance(row,dict):fail('row_shape',rp,'行必须为对象')
            flat='cells' not in row
            cells=row if flat else row['cells']
            if not isinstance(cells,dict):fail('cells_shape',rp+'.cells','cells必须为对象')
            label=row.get('label')
            if not isinstance(label,str) or not label.strip():
                entity=row.get('entity')
                if isinstance(entity,dict) and isinstance(entity.get('id'),str) and entity['id'].strip() and isinstance(entity.get('name'),str) and entity['name'].strip():
                    label=entity['name'].strip()+' ['+entity['id'].strip()+']'
                else:
                    candidates=[scalar(cells[c]) for c in identity if c in cells]
                    candidates=list(dict.fromkeys(v.strip() for v in candidates if isinstance(v,str) and v.strip()))
                    label=candidates[0] if len(candidates)==1 else None
            if not isinstance(label,str) or not label.strip() or len(label)>200:
                fail('empty_label',rp+'.label','对象label为空；提供label、唯一机型名称或entity:{id,name}')
            label=re.sub(r'\s+',' ',label).strip()
            key=label.casefold()
            if key in labels:fail('duplicate_label',rp+'.label','批次内对象label重复；使用稳定的对象ID区分，不能使用批次序号',label=label,first_row=labels[key])
            labels[key]=ri
            if row.get('label')!=label:changes.append({'sheet':name,'row':ri+2,'rule':'object_identity_to_label','label':label})
            extras=set(cells)-set(columns)-({'label','entity'} if flat else set())
            missing=set(business)-set(cells)
            if extras or missing:
                fail('cells_columns',rp+'.cells','cells必须对应业务列；缺失事实用null及reason',sheet=name,row=ri+2,label=label,missing_columns=sorted(missing),extra_columns=sorted(extras))
            hints={META[c]:scalar(cells[c]) for c in metadata if c in cells}
            # A row-level URL is only redundant metadata when it identifies one
            # of this row's actual cell sources. A URL matching nothing in the
            # row is forged/unrelated and must still fail.
            row_source_urls=set()
            for c in business:
                candidate=cells.get(c)
                candidate_sid=(candidate.get('source_id') or candidate.get('provenance_ref')
                               if isinstance(candidate,dict) else None) or hints.get('source_id')
                if isinstance(candidate_sid,str) and candidate_sid in sources:
                    row_source_urls.add(url_key(sources[candidate_sid]['url']))
            # Program status is always recomputed. Model status is not evidence.
            row_cells={}
            for col in business:
                raw=cells[col]
                entry=copy.deepcopy(raw) if isinstance(raw,dict) else {'value':raw}
                sid=entry.get('source_id') or entry.get('provenance_ref') or hints.get('source_id')
                if sid is not None:
                    if not isinstance(sid,str) or sid not in sources:
                        if hints.get('source_id') is not None:
                            fail('unknown_source',rp+'.cells.'+col,'来源ID必须属于当前任务',source_id=sid)
                        row_cells[col]=entry
                        continue
                    entry['source_id']=sid
                    source=sources[sid]
                    if hints.get('source_id') not in (None,sid):fail('metadata_source_conflict',rp+'.cells.'+col,'行来源ID与单元格来源冲突')
                    if hints.get('url') is not None and (not isinstance(hints['url'],str) or url_key(hints['url'])!=url_key(source['url'])):
                        hint_text = hints['url'] if isinstance(hints['url'],str) else ''
                        hint_urls = re.findall(r'https?://[^\s;；,，|)）]+', hint_text)
                        # A row may legitimately list several source URLs
                        # ("homepage; downloads"). It is redundant metadata
                        # when at least one URL belongs to this row; a URL
                        # matching nothing in the row is still forged.
                        if any(url_key(item) in row_source_urls for item in hint_urls):
                            changes.append({'sheet':name,'row':ri+2,'column':col,
                                'rule':'row_metadata_url_ignored',
                                'input_url':hint_text[:300],
                                'source_url':source['url'],
                                'note':'行级来源URL与单元格来源不一致；保留单元格source_id及Per-cell URL，不把行级URL当事实。'})
                        else:
                            fail('metadata_url_conflict',rp+'.cells.'+col,
                                 '来源URL与真实来源记录不匹配',expected_url=source['url'])
                    if flat:
                        quote=hints.get('quote') or source.get('quote','')
                        entry.setdefault('quote',quote)
                        # Flat rows have one quote for many facts. Locate exact
                        # scalar strings on that same saved page, never fact summaries.
                        if isinstance(entry.get('value'),str):
                            text=source_text(workspace,source);span=exact_span(text,entry['value'])
                            if span:
                                entry['quote']=text[span[0]:span[1]]
                    elif 'quote' not in entry and hints.get('quote'):entry['quote']=hints['quote']
                row_cells[col]=entry
            row.clear();row.update(label=label,cells=row_cells)
        spec['columns']=business;spec['rows']=rows;spec['fields']=canonical_fields
        if metadata:changes.append({'sheet':name,'rule':'system_metadata_to_provenance','columns':metadata,
            'note':'来源URL/ID取自真实来源；核验状态由字段校验计算，不采信输入状态。'})
    return output,changes
