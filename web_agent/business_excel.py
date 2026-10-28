"""Bounded business workbooks with cell provenance and deterministic table exports."""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path
import pandas as pd
from openpyxl import load_workbook
from .persistence import atomic_json, read_json
from .security import excel_safe
from .tabular import read_table, json_cell

FILENAME = 'task_result.xlsx'


def cell(value):
    if value is None:return None
    if isinstance(value,bool):return value
    if isinstance(value,(int,float)):
        if not math.isfinite(value):raise ValueError('单元格数值必须有限')
        return value
    if not isinstance(value,str) or len(value)>2000:raise ValueError('只支持短文本、数字、布尔值和空值')
    if value=='':return None
    return excel_safe(value)



def calculation_group_cell(workspace, source, entry, label, definition):
    """Validate one comparison cell from a recorded group calculation.

    A grouped CSV statistic naturally becomes a row per group and a column per
    method.  The calculation source is authoritative; the JSON quote need not
    repeat a unit token.  Values must still match the exact calculation result
    (rounded display is allowed within a tight tolerance) and the group label.
    """
    if not isinstance(entry, dict) or not isinstance(source, dict):
        return None
    if definition.get('type') not in {'number', 'text'}:
        return None
    value = entry.get('value')
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    calculations = read_json(workspace.path / 'calculations.json', {})
    if not isinstance(calculations, dict) or not calculations:
        return None
    calc_id = entry.get('calculation_id')
    calculation = calculations.get(calc_id) if isinstance(calc_id, str) else None
    if not isinstance(calculation, dict):
        observation_id = source.get('observation_id')
        calc_id, calculation = next(
            ((key, item) for key, item in calculations.items()
             if isinstance(item, dict)
             and (item.get('source_id') == source.get('id')
                  or (observation_id and item.get('observation_id') == observation_id))),
            (None, None))
    if not isinstance(calculation, dict):
        return None
    group = next((item for item in calculation.get('result', {}).get('top_20', [])
                  if str(item.get('group')) == str(label)), None)
    expected = group.get('value') if isinstance(group, dict) else None
    if type(expected) not in (int, float) or not math.isfinite(expected):
        return None
    if not math.isclose(float(value), float(expected), rel_tol=1e-6, abs_tol=1e-9):
        return None
    unit = definition.get('unit', '')
    if unit == 'count' and calculation.get('method') != 'count':
        return None
    if unit and unit != 'count' and not calculation.get('numeric_column'):
        return None
    return {'calculation_id': calc_id, 'expected': expected}


def ocr_table_cell(workspace, source, entry, label, column, definition):
    """Validate a cell taken from an OCR'd official statistical table row.

    OCR is machine transcription: only accept a row whose first cell is the
    row's year, whose value is literally in the row, and whose table header
    contains the declared unit (e.g. 万吨).  The provenance keeps the OCR note.
    """
    if not isinstance(entry, dict) or not isinstance(source, dict):
        return None
    if definition.get('type') not in {'number', 'text'}:
        return None
    observation_id = source.get('observation_id')
    observation = read_json(
        workspace.path / 'observations' / f'{observation_id}.json', {}
    ) if isinstance(observation_id, str) else {}
    is_ocr = (isinstance(observation, dict)
              and observation.get('kind') == 'image_ocr')
    if not is_ocr and 'OCR' not in str(source.get('category', '')).upper():
        return None
    from .business_contract import source_text
    observed = source_text(workspace, source)
    if not isinstance(observed, str) or 'OCR' not in observed[:100]:
        return None
    quote = entry.get('quote', '')
    if not isinstance(quote, str):
        return None
    row = [part.strip() for part in quote.split('|')]
    if len(row) < 2 or not re.fullmatch(r'(?:19|20)\d{2}', row[0]):
        return None
    if row[0] not in str(label):
        return None
    value = entry.get('value')
    if type(value) in (int, float):
        if not any(part == str(value) or part == str(int(value)) for part in row[1:]):
            return None
    elif isinstance(value, str):
        if value.strip() not in row:
            return None
    else:
        return None
    unit = definition.get('unit', '')
    if unit and unit not in observed:
        return None
    key = next((term for term in ('氮肥', '磷肥', '钾肥', '复合肥', '化肥') if term in column), '')
    if key and key not in observed:
        return None
    return {'unit': unit, 'year': row[0]}


class BusinessExcelValidationError(ValueError):
    """Actionable cell diagnostics; no invalid workbook is committed."""
    def __init__(self, issues, draft_id=None):
        self.issues=issues
        self.details={'kind':'business_excel','issues_total':len(issues),'issues':issues[:20],
                      'issues_omitted':max(0,len(issues)-20)}
        if draft_id:self.details.update(draft_id=draft_id,repair_tool='repair_business_excel')
        first=issues[0]
        super().__init__(f"业务表格有{len(issues)}处证据/参数问题；首处：工作表{first['sheet']}第{first['row']}行「{first['column']}」：{first['message']}")






class SubjectEvidenceMismatch(ValueError):
    """Observed facts explicitly conflict with the proposed row/field."""

class SubjectEvidenceUnbound(ValueError):
    """Scalar evidence is valid, but association with the row is not proven."""

def require_subject_evidence(workspace, source, label, column, quote, definition, audit):
    """Bind an already validated scalar to an *explicit* object/field assertion.

    This is deliberately conservative. Evidence without an unambiguous object
    and field association must be repaired or marked unverified, never verified.
    It does not guess from the page URL or from a model-written source summary.
    """
    from .business_contract import source_text,primary_subject_quote,release_feature_subject_bound
    from .delivery_schema import NUMBER,source_dates
    from .scalar_contract import text_form

    observed = source_text(workspace, source)
    if not isinstance(observed, str) or not observed or not isinstance(quote, str) or not quote.strip():
        raise SubjectEvidenceMismatch('没有可核验的网页原文或连续摘录')
    if not isinstance(label, str) or not label.strip() or not isinstance(column, str) or not column.strip():
        raise SubjectEvidenceMismatch('缺少对象或字段标识')

    # Explicit document-owner + literal-quote + source-section validation.
    # Only scoped release fields are eligible. Unrecognized semantic columns,
    # product specs, wrong versions and navigation quotes still use the
    # existing conservative guards below.
    from .article_field_binding import observed_release_field
    if observed_release_field(workspace, source, label, column, quote, definition, audit):
        audit['owner_binding'] = 'observed_single_h1'
        audit['field_binding'] = 'observed_release_section_or_literal_cue'
        return

    from .observed_article import site_scoped_owner
    if (definition.get('type') == 'text'
            and definition.get('role') not in {'source_url', 'price'}
            and re.search(r'官方(?:定位|用途|主页|网站|文档|下载)|主要用途|文档入口|下载入口|'
                          r'official\s+(?:positioning|purpose|homepage|documentation|download)',
                          column, re.I)
            and site_scoped_owner(observed, label, source.get('url', ''))):
        audit['owner_binding'] = 'site_domain_and_page_title'
        return

    if definition.get('type') == 'text' and not definition.get('role'):
        # A short original sentence, release version and document section are
        # article-scoped claims. Bind those to the host's saved H1 and body;
        # translated labels and other scalar attributes still require the
        # legacy exact field/owner checks.
        from .observed_article import observed_article_scalar_owner
        original = audit.get('original_input') or {}
        supplied = original.get('evidence_subject') if isinstance(original, dict) else None
        bound = observed_article_scalar_owner(
            observed, label, column, audit.get('normalized_value'), quote,
            evidence_subject=supplied,
            context_quote=original.get('context_quote') if isinstance(original, dict) else None)
        if bound is True:
            return
        if bound is False:
            raise SubjectEvidenceUnbound('文章字段与已保存正文主标题、章节或连续原文不一致')

    if definition.get('type')=='text' and definition.get('role')=='verbatim_excerpt':
        # Source ownership comes from the SAVED page's real H1 and actual
        # rendered body, never a model-provided page title or URL guess.
        # Feature rows may suffix that H1, but cross-version suffixes are
        # rejected. The scalar checker already requires value ⊆ exact quote.
        from .observed_article import observed_verbatim_owner
        raw_input = audit.get('original_input') or {}
        claimed_title = raw_input.get('evidence_subject') if isinstance(raw_input, dict) else None
        ok, reason = observed_verbatim_owner(
            observed, label, quote, evidence_subject=claimed_title)
        if not ok:
            raise SubjectEvidenceUnbound(reason)
        return

    # Only demand an explicit adjacent subject/field association when the saved
    # observation actually uses the supplied row/column labels. Older genuine
    # sources can use manufacturer names, translated fields, or matrix layouts.
    label_exact = re.search(r'(?<![\w])' + re.escape(label.strip()) + r'(?![\w])', observed, re.I)
    field_exact = re.search(r'(?<![\w])' + re.escape(column.strip()) + r'(?![\w])', observed, re.I)
    if not label_exact:
        # Site-level comparison rows ("Python（python.org）") are a legitimate
        # object label when the source URL belongs to that same site and the
        # saved page title/H1 names it.  This does not relax product/version
        # ownership or allow a different site.
        from .observed_article import site_scoped_owner
        if (definition.get('type') == 'text'
                and definition.get('role') not in {'source_url', 'price'}
                and site_scoped_owner(observed, label, source.get('url', ''))):
            audit['owner_binding'] = 'site_domain_and_page_title'
            return
        # A prefix of another identifier is never proof of this row's identity.
        collision = re.search(re.escape(label.strip()) + r'(?=[A-Za-z0-9_])', observed, re.I)
        if collision:
            raise SubjectEvidenceMismatch('对象名称只匹配到另一对象标识的一部分，不能核实本行')
        # Never silently certify a value for an unnamed/aliased subject.
        # The scalar can be retained for review, not counted as verified.
        raise SubjectEvidenceUnbound('来源原文未明确对应本行对象，归属尚需复核')
    if definition.get('role')=='source_url':
        # Provenance identity is verified against a recorded observation, not
        # by searching for a URL inside an unrelated body quote.
        if audit.get('rule')=='observed_source_url' and primary_subject_quote(workspace,source,label,quote):
            return
        raise SubjectEvidenceUnbound('来源URL有效，但尚未证实对应当前行的主对象')
    if not field_exact:
        # A translated field name is not proof by itself. Only allow a small
        # deterministic set of aliases when this short quote is anchored near
        # the exact primary H1 of the *saved observed page*.
        primary=primary_subject_quote(workspace,source,label,quote)
        value=audit.get('normalized_value')
        if primary and definition['type']=='date' and column.strip() in {'发布日期','正式发布日期','Release date'}:
            # Require the first explicit release-date line directly under this
            # primary article's rendered heading. A date for another version
            # mentioned farther down cannot be silently attributed to this row.
            rendered=observed.split('### Page',1)[0]
            owner=re.search(r'(?m)^\s*'+re.escape(label.strip())+r'\s*$',rendered,re.I)
            after=rendered[owner.end():] if owner else ''
            first=re.search(r'(?im)^\s*(?:release\s+date|发布日期|正式发布日期)\s*[:：][^\n]*',after)
            if (first and first.start()<=400 and
                    first.group().strip().casefold()==quote.strip().casefold() and
                    isinstance(value,str) and value in source_dates(quote)):
                return
        if primary and definition['type']=='text' and column.strip() in {'版本号','版本','Version'}:
            version=re.search(r'(\d+(?:\.\d+){1,4})$',label.strip())
            if version and value==version.group(1) and quote.strip().casefold() in {
                    label.strip().casefold(),
                    ('This is the stable release of '+label.strip()).casefold()}:
                return
        if (primary_subject_quote(workspace,source,label,quote,max_distance=15000)
                and definition['type']=='text'
                and release_feature_subject_bound(workspace,source,label,column,quote,audit)):
            return
        if primary and definition['type']=='text' and column.strip() in {'发布状态','Release status'}:
            if (value=='stable release' and
                    quote.strip().casefold()==('This is the stable release of '+label.strip()).casefold()):
                return
        raise SubjectEvidenceUnbound('来源原文未使用当前字段名，且缺少可核验的主对象及字段关联')

    # A valid quote may have a different amount of whitespace than the saved
    # browser snapshot. Preserve paragraph boundaries (unlike generic \s+).
    quote_pattern = re.compile(r'[ \t\r\n]+'.join(re.escape(x) for x in quote.split()))
    label_pattern = re.compile(r'^' + r'[ \t]+'.join(re.escape(x) for x in label.split()) + r'(?!\w)', re.I)
    field_pattern = re.compile(r'^' + re.escape(column) + r'(?!\w)', re.I)

    def is_label(line):
        heading = line.strip().strip('：:|—- ')
        if label_pattern.fullmatch(heading):
            return True
        match = label_pattern.match(heading)
        if not match:
            return False
        # A documented owner heading can carry an ordinary page-title suffix.
        # Do not mistake another product/model identifier for that suffix.
        suffix = heading[match.end():].strip(' ：:|—- ')
        return bool(re.fullmatch(
            r'(?:.{0,16}(?:公告|参数|规格|说明|简介|详情)|'
            r'(?:product[ -]?details|specifications?|overview|technical[ -]?details))',
            suffix, re.I))

    def field_payload(line):
        """Return the portion belonging to one explicit field, or None."""
        line = line.strip()
        direct = label_pattern.match(line)
        if direct:
            line = line[direct.end():].lstrip(' ：:|—-\t,，')
        matched = field_pattern.match(line)
        if not matched:
            return None
        after = line[matched.end():]
        # Require a field/value separator, so a field prefix isn't mistaken
        # for a complete field. Allow (unit) in a table header.
        match = re.match(r'^[ \t]*(?:\([^\n)]{1,25}\)[ \t]*)?[:：|=\t ][ \t]*', after)
        if not match:
            return None
        return after[match.end():].split('|', 1)[0].split('；', 1)[0].split(';', 1)[0].strip()

    # Compare with the raw *observed* value, not a converted number. The
    # normalizer has already checked unit conversion and exact extractability.
    raw = audit.get('raw_value', audit.get('normalized_value'))
    numeric = definition['type'] in {'number', 'price'}
    if definition['type']=='date':
        expected=audit.get('normalized_value')
        def contains_value(text):
            return isinstance(expected,str) and expected in source_dates(text)
    elif numeric:
        if type(raw) not in (int, float):
            raise SubjectEvidenceMismatch('字段原始数值不是可定位的单个数字')
        def contains_value(text):
            found = [float(m.group().replace(',', '')) for m in re.finditer(NUMBER, text)]
            return len(found) == 1 and found[0] == float(raw)
    else:
        value = audit.get('normalized_value')
        if not isinstance(value, (str, bool)):
            raise SubjectEvidenceMismatch('无法定位原文中的字段值')
        # Text may be the union of extractive display components; every one
        # must be in this one explicitly attributed field payload.
        components = [text_form(x) for x in re.split('[，、]', str(value))]
        def contains_value(text):
            normalized = text_form(text)
            return all(
                re.search(r'(?<!\w)' + re.escape(part) + r'(?!\w)', normalized, re.I)
                for part in components if part
            )

    for match in quote_pattern.finditer(observed):
        # Never borrow object names from another paragraph or another line.
        start = observed.rfind('\n', 0, match.start()) + 1
        end = observed.find('\n', match.end())
        if end < 0:
            end = len(observed)
        snippet = observed[start:end]
        lines = snippet.splitlines()
        if not lines:
            continue
        # A quote may start inside a field and end inside it, or span the
        # immediate object heading and the field line. Check the field line
        # containing the *value*, never the last line of the quote alone.
        for index, line in enumerate(lines):
            candidate = field_payload(line)
            if candidate is not None:
                if not contains_value(candidate) and definition['type'] == 'text' and index + 1 < len(lines):
                    # A quoted value can occupy a field line plus one plain-text
                    # continuation line. Never splice a second field, a product
                    # heading, or an unquoted observation line into the value.
                    continuation = lines[index + 1].strip()
                    if (continuation and ':' not in continuation and '：' not in continuation
                            and '=' not in continuation and '|' not in continuation
                            and not re.match(r'(?i)^(?:product|item|device|model)\s+\S', continuation)
                            and not re.match(r'^(?:产品|机型|型号)[A-Za-z0-9]', continuation)):
                        candidate = candidate + ' ' + continuation
            if candidate is not None and contains_value(candidate):
                prefix = label_pattern.match(line.strip())
                if prefix is not None or (index > 0 and is_label(lines[index - 1])):
                    return
                # A short quote often omits the object heading. Inspect the
                # true immediately preceding saved line, not an invented fact.
                absolute_start = observed.find(line, start, end)
                if absolute_start >= 0:
                    previous_start = observed.rfind('\n', 0, max(0, absolute_start - 1)) + 1
                    previous = observed[previous_start:absolute_start].strip('\r\n')
                    if is_label(previous):
                        return
            # Explicit object + field/value on one line, already handled above.
    raise SubjectEvidenceMismatch(
        '来源摘录虽真实，但未找到与本行对象紧邻的字段及对应数值；'
        '请提供含对象名称、字段和值的原文，或填 null 并说明未核实原因'
    )

def infer_verified_quote_type(entry, definition):
    """Classify an unchanged, long, observed quote as a verbatim excerpt.

    This only changes how the scalar is validated. The caller must still
    validate the source observation and then independently bind its single
    observed H1 to the row. A quoted translation, mismatched article, short
    scalar, or document with no unique H1 receives no automatic verification.
    """
    if definition.get('type') != 'text' or definition.get('role'):
        return definition, False
    if not isinstance(entry, dict):
        return definition, False
    value, quote = entry.get('value'), entry.get('quote')
    if (not isinstance(value, str) or not isinstance(quote, str)
            or not (80 < len(value) <= 1500) or value.strip() != quote.strip()):
        return definition, False
    return {**definition, 'role': 'verbatim_excerpt'}, True



def descriptive_role_cell(workspace,entry,definition,source,label):
    """Verify document identity or record a nonfactual editorial label."""
    from .business_contract import source_text
    from .observed_article import non_competing_h1s, owner_matches, observed_verbatim_owner

    role = definition['role']
    if not isinstance(entry,dict) or not isinstance(entry.get('value'),str):
        raise ValueError('文档身份/编辑标签要求非空文本value')

    value = entry['value'].strip()
    if not value or len(value)>80 or '\n' in value:
        raise ValueError('身份/标签必须是80字符以内的单行文本')

    if entry.get('source_id') and source is None:
        raise ValueError('来源编号不存在')

    quote = entry.get('quote','')
    if quote:
        if source is None or not isinstance(quote,str):
            raise ValueError('摘录必须来自当前任务已保存来源')
        workspace.evidence_for(
            source['url'], quote, source.get('observation_id')
        )

    if role == 'editorial_label':
        return value, {
            'normalized_value': value,
            'status': 'editorial',
            'required': False,
            'role': role,
            'rule': 'editorial_index_only',
            'note': '编辑标签，非已核验事实',
            'original_input': entry
        }

    if source is None or not isinstance(quote,str) or not quote.strip():
        raise ValueError('文档身份必须提供真实来源和原文摘录')

    observed = source_text(workspace,source)
    subject = entry.get('evidence_subject')
    if subject is not None and not isinstance(subject,str):
        raise ValueError('evidence_subject必须是实际网页主标题')

    ok, reason = observed_verbatim_owner(
        observed,label,quote,evidence_subject=subject
    )
    h1s = re.findall(
        r'(?im)^- heading "([^"\n]{3,180})" \[level=1\]',
        observed
    )

    if h1s and not non_competing_h1s(h1s):
        raise ValueError('页面存在竞争版本标题，无法确定唯一版本身份')

    identity_ok = False
    if ok and h1s:
        # observed_verbatim_owner already proved the FIRST H1 is unique and
        # the quote occurs after it. Secondary section H1s must not invalidate
        # the document identity.
        h1 = h1s[0]
        identity_ok = owner_matches(value,h1,subject) or (
            owner_matches(label,h1,subject)
            and label.casefold().startswith(value.casefold() + ' - ')
            and h1.casefold().endswith(value.casefold())
        )
    if not identity_ok:
        raise ValueError(reason or '版本身份与真实网页标题不匹配')

    return value, {
        'normalized_value': value,
        'status': 'verified',
        'required': definition.get('required',True),
        'role': role,
        'rule': 'observed_document_heading_identity',
        'note': '依据已保存网页主标题核验',
        'original_input': entry
    }

def comparison_frame(workspace,spec,sources,provenance,issues,*,allow_structured_review=False):
    name=spec['name'];columns=spec.get('columns',[]);rows=spec.get('rows',[])
    if not isinstance(columns,list) or not 1<=len(columns)<=40 or any(not isinstance(c,str) or not c or len(c)>80 for c in columns) or len(set(columns))!=len(columns) or '对象' in columns:
        raise ValueError('需要1–40个唯一业务列名，不能重复对象列')
    if not isinstance(rows,list) or not 1<=len(rows)<=200:raise ValueError('比较表需要1–200行')
    from .delivery_schema import field_for,normalize_entry
    from .evidence_text import find_observed_span
    fields=spec.get('fields',{})
    if not isinstance(fields,dict) or set(fields)-set(columns):raise ValueError('fields必须对应业务列名')
    definitions={col:field_for(col,fields) for col in columns}
    data=[]
    price_columns=[c for c in columns if definitions[c]['type']=='price']
    expanded=['对象']
    for col in columns:
        expanded.append(col+(' ('+definitions[col]['unit']+')' if definitions[col].get('unit') else ''))
        if col in price_columns:expanded.extend((col+' · ' if len(price_columns)>1 else '')+x for x in ['币种','市场','价格类型','查询日期'])
    for i,row in enumerate(rows,2):
        if not isinstance(row,dict):raise ValueError('每行必须为对象')
        label=row.get('label','')
        if not isinstance(label,str) or not label or len(label)>200:raise ValueError('每行需要对象label')
        cells=row.get('cells',{})
        if not isinstance(cells,dict) or set(cells)!=set(columns):
            from .delivery_inspection import DeliveryInputError
            raise DeliveryInputError('cells必须对应全部业务列；label已经提供对象名称，不要在columns重复添加行名/参数列。缺失参数需明确null及reason',
                {'kind':'business_structure','sheet':name,'row':i,'label':label,
                 'missing_columns':[c for c in columns if c not in (cells if isinstance(cells,dict) else {})],
                 'extra_columns':[c for c in cells if c not in columns] if isinstance(cells,dict) else [],
                 'columns':columns,'next_tool':'inspect_delivery'})
        values=[cell(label)]
        for col in columns:
            entry=cells[col];sid=(entry.get('source_id') or entry.get('provenance_ref','')) if isinstance(entry,dict) else ''
            source=sources.get(sid) if isinstance(sid,str) else None
            audit={};value=None;quote='';definition=definitions[col]
            try:
                if definition.get('role') in {'document_identity','editorial_label'}:
                    value,audit=descriptive_role_cell(workspace,entry,definition,source,label)
                    values.append(cell(value))
                    provenance.append({
                        'sheet':name,'row':i,'column':col,'label':label,
                        'source_id':sid,
                        'observation_id':source.get('observation_id','') if source else '',
                        'url':source.get('url','') if source else '',
                        'quote':entry.get('quote','') if isinstance(entry,dict) else '',
                        'calculation_id':'',
                        **audit
                    })
                    continue
                if not isinstance(entry,dict) or 'value' not in entry:raise ValueError('单元格必须是value/source_id/quote对象')
                from .business_contract import display_entry
                entry=display_entry(workspace,entry,definition,source)
                quote=entry.get('quote','')
                if source or entry['value'] is not None:
                    if not source or not isinstance(quote,str) or not quote.strip():raise ValueError('需要真实source_id及连续原文quote')
                    workspace.evidence_for(source['url'],quote,source.get('observation_id'))
                    if entry.get('context_quote'):workspace.evidence_for(source['url'],entry['context_quote'],source.get('observation_id'))
                # Legacy simple numeric columns remain supported; never admit long text blobs.
                definition=definitions[col]
                ocr_cell=ocr_table_cell(
                    workspace,source,entry,label,col,definition)
                if ocr_cell:
                    value=entry['value']
                    values.append(cell(value))
                    provenance.append({
                        'sheet':name,'row':i,'column':col,'label':label,
                        'source_id':sid,
                        'observation_id':source.get('observation_id','') if source else '',
                        'url':source.get('url','') if source else '',
                        'quote':quote,'calculation_id':'',
                        'status':'verified','required':definition.get('required',True),
                        'rule':'ocr_table_row','owner_binding':'ocr_table_year_row',
                        'normalized_value':value,'raw_value':value,
                        'note':'OCR 机器识别；已按年份行、单位表头和数值核对，请复核数字。',
                    })
                    continue
                calculation_cell=calculation_group_cell(
                    workspace,source,entry,label,definition)
                if calculation_cell:
                    value=entry['value']
                    values.append(cell(value))
                    provenance.append({
                        'sheet':name,'row':i,'column':col,'label':label,
                        'source_id':sid,
                        'observation_id':source.get('observation_id','') if source else '',
                        'url':source.get('url','') if source else '',
                        'quote':quote,'calculation_id':calculation_cell['calculation_id'],
                        'status':'verified','required':definition.get('required',True),
                        'rule':'calculation_group_value',
                        'normalized_value':value,'raw_value':calculation_cell['expected'],
                        'input_value':value,
                    })
                    continue
                if not fields.get(col) and 'role' not in definition and type(entry['value']) in (int,float):definition={**definition,'type':'number'}
                # A model may omit fields.role despite supplying an unchanged
                # 80+ character quotation. Infer only the representation, never
                # factual truth; require_subject_evidence still checks the
                # host-observed page heading, row identity and body placement.
                definition, inferred_excerpt = infer_verified_quote_type(entry, definition)
                value,audit=normalize_entry(entry,definition,source)
                if inferred_excerpt:
                    audit['field_role_inferred']='verbatim_excerpt_from_identical_observed_quote'
                if (
                        entry['value'] is not None
                        and audit.get('status') == 'verified'
                        and not (col == '来源日期' and definition['type'] == 'date')
                ):
                    try:
                        require_subject_evidence(workspace,source,label,col,quote,definition,audit)
                    except SubjectEvidenceUnbound as exc:
                        audit.update(status='review_required',
                                     note='对象或字段归属待人工复核：'+str(exc),
                                     diagnostic={'code':'subject_evidence_unbound', 'message':str(exc)})
                if '_display_input_quote' in entry:audit['input_quote']=entry['_display_input_quote']
                if entry.get('_required_for_review') is True:audit['required']=True
                if '_compile_rule' in entry:audit.update(input_value=entry.get('_compiled_input'),format_rule=entry['_compile_rule'])
            except (ValueError,TypeError,KeyError) as exc:
                if allow_structured_review and not isinstance(exc, SubjectEvidenceMismatch):
                    from .structured_facts import review_fact
                    try:
                        value,audit,quote=review_fact(workspace,entry,definition,source,exc)
                    except (ValueError,TypeError,KeyError):pass
                    else:
                        values.append(cell(value))
                        if col in price_columns:
                            values.extend([audit.get('currency','未核实'),audit.get('market','未核实'),
                                           {'launch':'发布价','current':'当前售价','starting':'起售价','promotional':'促销价'}.get(audit.get('price_type'),'未核实'),audit.get('queried_at','未核实')])
                        provenance.append({'sheet':name,'row':i,'column':col,'label':label,'source_id':sid,
                                           'observation_id':source.get('observation_id','') if source else '',
                                           'url':source.get('url','') if source else '', 'quote':quote,'calculation_id':'',**audit})
                        continue
                candidate=None
                # Only suggest an exactly evidenced type correction, never a guess,
                # translation, cross-source match or automatic missing-value fallback.
                if isinstance(entry,dict) and definition['type']=='text' and type(entry.get('value')) in (int,float) and len(str(entry.get('quote','')))<=300:
                    proposed={**entry,'value':str(entry['value'])}
                    try:
                        workspace.evidence_for(source['url'],proposed['quote'],source.get('observation_id'))
                        normalize_entry(proposed,definition,source)
                        candidate=proposed
                    except (ValueError,TypeError,KeyError):pass
                source_context=''
                if source and isinstance(entry,dict) and entry.get('value') is not None:
                    # Prefer the supplied, actually observed quote over an unrelated
                    # first substring hit (e.g. 60 in a microSD model number).
                    keyword=str(entry['value']).strip()
                    if isinstance(quote,str) and 1<=len(quote)<=120 and keyword.lower() in quote.lower():
                        try:
                            workspace.evidence_for(source['url'],quote,source.get('observation_id'))
                            keyword=quote
                        except ValueError:pass
                    if 1<=len(keyword)<=120:
                        try:
                            found=workspace.source_excerpt(str(sid),keyword)
                            if not found['keyword_found']:
                                keyword=str(entry['value']).strip()
                                found=workspace.source_excerpt(str(sid),keyword)
                            if not found['keyword_found']:
                                # Numeric anchor locates the saved original even when a
                                # translated unit (2.25 inches vs 2.25-inch) was invented.
                                anchor=re.search(r'(?<![\w.])\d+(?:\.\d+)?',str(entry['value']))
                                if anchor:
                                    keyword=anchor.group();found=workspace.source_excerpt(str(sid),keyword)
                            if found['keyword_found']:
                                text=found['text'];span=find_observed_span(text,keyword)
                                at=span.start if span else -1
                                source_context=text[max(0,at-400):max(0,at-400)+1000] if span else ''
                        except (ValueError,TypeError,KeyError):pass
                issues.append({'sheet':name,'row':i,'column':col,'label':label[:60],'source_id':str(sid)[:40],
                               'value':str(entry.get('value') if isinstance(entry,dict) else entry)[:100],
                               'diagnostic':getattr(exc,'details',{'code':'fact_validation','message':str(exc)}),'message':str(exc)[:220],'expected_type':definition['type'],'field':definition,
                               'quote_preview':str(quote)[:160],
                               **({'source_context':source_context,'context_requires_review':True} if source_context else {}),
                               **({'suggested_entry':candidate} if candidate is not None else {}),'repair':'修正标量值、单位和证据；无法核实用value:null、status:unverified及reason。价格填写price元数据。来源URL自动附于Provenance。'})
            values.append(cell(value))
            if col in price_columns:
                values.extend([audit.get('currency','未核实'),audit.get('market','未核实'),{'launch':'发布价','current':'当前售价','starting':'起售价','promotional':'促销价'}.get(audit.get('price_type'),'未核实'),audit.get('queried_at','未核实')])
            provenance.append({'sheet':name,'row':i,'column':col,'label':label,'source_id':sid,
                               'observation_id':source.get('observation_id','') if source else '',
                               'url':source.get('url','') if source else '', 'quote':quote,'calculation_id':'',
                               'note':entry.get('reason','') if isinstance(entry,dict) else '',**audit})
        data.append(values)
    reviewed=[p for p in provenance if p['sheet']==name and p.get('status')=='review_required']
    if allow_structured_review and (reviewed or any(
            p['sheet'] == name
            and p.get('status') in {'unverified', 'not_disclosed'}
            for p in provenance
    )):
        expanded.append('核验说明')
        for i,values in enumerate(data,2):
            fields=[p['column'] for p in reviewed if p['row']==i]
            missing=[p['column'] for p in provenance if p['sheet']==name and p['row']==i and p.get('status') in {'unverified','not_disclosed'}]
            notes=(['待人工复核：'+'、'.join(fields)] if fields else [])+(['未核实：'+'、'.join(missing)] if missing else [])
            values.append('；'.join(notes) if notes else '已按来源核对')
    return pd.DataFrame(data,columns=expanded)



def save_business_draft(workspace,sheets):
    payload=json.dumps(sheets,ensure_ascii=False,sort_keys=True,allow_nan=False)
    if len(payload.encode('utf-8'))>512000:return None
    draft_id=hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]
    atomic_json(workspace.path/'business_draft.json',{'draft_id':draft_id,'sheets':sheets})
    return draft_id


def business_repair_context(workspace):
    draft=read_json(workspace.path/'business_draft.json',{})
    if not isinstance(draft,dict) or not isinstance(draft.get('sheets'),list):return None
    try:payload=json.dumps(draft['sheets'],ensure_ascii=False,sort_keys=True,allow_nan=False)
    except (ValueError,TypeError):return None
    if len(payload.encode('utf-8'))>512000 or hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]!=draft.get('draft_id'):return None
    packet={'draft_id':draft['draft_id'],'repair_tool':'repair_business_excel',
            'note':'仅修正列出的单元格。文本值使用字符串及连续短原文；quote须包含该值，介绍段落不能证明表格数值。无法核实才填null及具体reason；缺失项仍需披露。'}
    for entry in reversed(list(read_json(workspace.path/'tool_journal.json',{}).values())):
        if entry.get('name') not in {'export_business_excel','repair_business_excel'}:continue
        try:data=json.loads(entry.get('output','')[7:]).get('diagnostics',{})
        except (ValueError,TypeError,AttributeError):continue
        if data.get('draft_id')!=draft['draft_id']:continue
        packet.update(issues_total=data.get('issues_total'),issues=[
            {k:v for k,v in issue.items() if k in {'sheet','row','column','message','expected_type','field','quote_preview','suggested_entry'}}
            for issue in data.get('issues',[])[:3]])
        break
    return packet


def load_business_draft(workspace,draft_id):
    draft=read_json(workspace.path/'business_draft.json',{})
    if not draft or draft.get('draft_id')!=draft_id:
        from .delivery_inspection import DeliveryInputError,workbook_state
        raise DeliveryInputError('业务表草稿不存在或版本已变化；先inspect_delivery读取当前状态；已导出则复用文件，有草稿则使用当前draft_id',
                                 {'kind':'business_state',**workbook_state(workspace),'next_tool':'inspect_delivery'})
    payload=json.dumps(draft['sheets'],ensure_ascii=False,sort_keys=True,allow_nan=False)
    if hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]!=draft_id:raise ValueError('草稿内容与版本不一致，拒绝修正')
    return draft,payload


def inspect_business_draft(workspace,draft_id,issue_offset=0):
    # Re-run cell checks without export, network, model or draft mutation.
    from .delivery_inspection import resolved_draft
    completed=resolved_draft(workspace,draft_id)
    if completed:return {**completed,'issues_total':0,'issues':[],'draft_consumed':True}
    draft,_=load_business_draft(workspace,draft_id)
    if type(issue_offset) is not int or not 0<=issue_offset<=1000:raise ValueError('issue_offset需为0–1000的整数')
    sources={r['id']:r for r in workspace.sources()};issues=[];provenance=[]
    for spec in draft['sheets']:
        if spec.get('mode')=='comparison':comparison_frame(workspace,spec,sources,provenance,issues)
    selected=issues[issue_offset:issue_offset+10]
    return {'draft_id':draft_id,'repair_tool':'repair_business_excel','issues_total':len(issues),
            'issues':selected,'issue_offset':issue_offset,'next_offset':issue_offset+10 if len(issues)>issue_offset+10 else None,
            'valid_cells':len(provenance)-len(issues),
            'fields':[{'sheet':s['name'],'definitions':s.get('fields',{})} for s in draft['sheets'] if s.get('mode')=='comparison'],
            'note':'source_context仅是实际保存原文的定位线索，需核对对象、版本与条件后选择连续quote。不要把翻译/概括作为原文值；无法核实才用null及reason。'}


def repair_business_excel(workspace,draft_id,patches,field_patches=None,*,review_disclosed_missing=False,allow_structured_review=False):
    """Patch explicit cells of the current local draft, then revalidate everything."""
    from .delivery_inspection import resolved_draft
    if resolved_draft(workspace,draft_id):return workspace.path/FILENAME
    draft,payload=load_business_draft(workspace,draft_id)
    if not isinstance(patches,list) or len(patches)>40 or (not patches and not field_patches):raise ValueError('一次最多修正40个明确单元格，或提供field_patches修正字段')
    if field_patches is not None and (not isinstance(field_patches,list) or not 1<=len(field_patches)<=20):raise ValueError('field_patches需为1–20个字段定义')
    sheets=json.loads(payload);seen=set()
    field_seen=set()
    for patch in field_patches or []:
        if not isinstance(patch,dict) or set(patch)!={'sheet','column','field'}:raise ValueError('字段修正需sheet/column/field')
        spec=next((s for s in sheets if s['name']==patch['sheet'] and s.get('mode')=='comparison'),None)
        column=patch['column'];field=patch['field']
        if spec is None or column not in spec['columns']:raise ValueError('字段修正位置不存在；请使用inspect_business_draft返回的表名和列名')
        if not isinstance(field,dict) or set(field)-{'type','unit','required','role'}:raise ValueError('字段只支持type/unit/required/role')
        from .delivery_schema import field_for
        previous=field_for(column,spec.get('fields',{}))
        updated=field_for(column,{column:field})
        if previous.get('required') and not updated.get('required'):raise ValueError('不能通过字段修正取消必需核验')
        if (patch['sheet'],column) in field_seen:raise ValueError('字段修正不能重复')
        field_seen.add((patch['sheet'],column))
        spec.setdefault('fields',{})[column]=updated
    for index,patch in enumerate(patches,1):
        if not isinstance(patch,dict):raise ValueError('修正项必须是sheet/row/column/entry对象')
        name=patch.get('sheet');row=patch.get('row');column=patch.get('column');entry=patch.get('entry')
        spec=next((s for s in sheets if s['name']==name and s.get('mode')=='comparison'),None)
        if spec is None or type(row) is not int or not 2<=row<len(spec['rows'])+2 or column not in spec['columns']:
            raise ValueError(f'第{index}个修正位置不存在：sheet={name!r},row={row!r},column={column!r}；三个位置字段均必填，row从2开始。请复制inspect_business_draft中的位置，不要猜测')
        key=(name,row,column)
        if key in seen:raise ValueError('同一次修正不能重复指定单元格')
        seen.add(key)
        if not isinstance(entry,dict) or set(entry)-{'value','source_id','quote','reason','status','raw_value','input_unit','price','context_quote'} or 'value' not in entry:
            raise ValueError('entry需完整value/source_id/quote，或value:null及reason')
        spec['rows'][row-2]['cells'][column]=entry
    return export_business_excel(workspace,sheets,review_disclosed_missing=review_disclosed_missing,allow_structured_review=allow_structured_review)


def export_business_excel(workspace, sheets,*,review_disclosed_missing=False,normalizations=None,allow_structured_review=False,validate_only=False):
    """Model chooses structure, but cannot supply invented calculation results."""
    from .business_contract import comparison_contract
    sheets,contract_changes=comparison_contract(workspace,sheets)
    normalizations=list(normalizations or [])+contract_changes
    sources={r['id']:r for r in workspace.sources()}
    downloads=read_json(workspace.path/'downloads.json',{})
    calculations=read_json(workspace.path/'calculations.json',{})
    frames=[];provenance=[];names=set();issues=[]
    for spec in sheets:
        name=spec.get('name','')
        if not isinstance(name,str) or not name or len(name)>31 or re.search(r'[\\/*?:\[\]]',name) or name.casefold() in names|{'provenance','metadata'}:
            raise ValueError('工作表名无效或重复')
        names.add(name.casefold());mode=spec.get('mode')
        if mode=='comparison':
            frame=comparison_frame(workspace,spec,sources,provenance,issues,allow_structured_review=allow_structured_review)
        elif mode=='table':
            filename=spec.get('filename','');source=downloads.get(filename)
            if not source:raise ValueError('table模式需要真实下载清单中的filename')
            frame,notice=read_table(workspace.path,filename)
            columns=spec.get('columns') or list(frame.columns)
            if not columns or len(columns)>30 or len(set(columns))!=len(columns) or not set(columns)<=set(frame.columns):raise ValueError('选择的表格列不存在或超过30列')
            frame=frame[columns].map(lambda v:cell(json_cell(v)))
            provenance.append({'sheet':name,'row':'all','column':'all','url':source['url'],'filename':filename,
                               'sha256':hashlib.sha256((workspace.path/filename).read_bytes()).hexdigest(),'note':notice or '首个工作表/CSV，最多5000行'})
        elif mode=='calculation':
            key=spec.get('calculation_id','');calc=calculations.get(key)
            if not calc:raise ValueError('需要聚合工具实际生成的calculation_id')
            result=calc['result'];source=downloads.get(calc['filename'])
            if not source:raise ValueError('计算缺少下载来源')
            frame=pd.DataFrame([[cell(r['group']),cell(r['value'])] for r in result['top_20']],columns=[result['group_by'],result['method']+' '+result.get('numeric_column','')])
            provenance.append({'sheet':name,'row':'all','column':'all','url':source['url'],'calculation_id':key,
                               'note':f"{result['sampled_rows']} sampled rows; top20/{result['total_groups']} groups; "+result.get('note','')})
        else:raise ValueError('mode只能comparison/table/calculation')
        frame.columns=[excel_safe(str(c)) for c in frame.columns]
        frames.append((name,frame))
    if issues:
        if validate_only:raise BusinessExcelValidationError(issues)
        try:draft_id=save_business_draft(workspace,sheets)
        except (ValueError,TypeError):draft_id=None
        atomic_json(workspace.path/'business_validation.json',{'draft_id':draft_id,'issues_total':len(issues),'issues':issues})
        raise BusinessExcelValidationError(issues,draft_id)
    if validate_only:
        return {'provenance':provenance,'rows':sum(len(frame) for _,frame in frames)}
    sync_staged=(workspace.path/'business_rows.json').is_file() and all(s['mode']=='comparison' for s in sheets)
    staged_version = None
    if sync_staged:
        from .delivery_pipeline import persist_rows
        staged_version, _ = persist_rows(workspace,sheets,provenance,validate_only=True)
    from .delivery_schema import quality_notes
    manifest={'filename':FILENAME,'schema_version':2,'review_disclosed_missing':review_disclosed_missing,'structured_review':allow_structured_review,'normalizations':normalizations or [],'sheets':[], 'provenance':provenance,'quality_notes':quality_notes(provenance)}
    if staged_version is not None: manifest['staged_version'] = staged_version
    temporary=workspace.path/'task_result.pending.xlsx'
    try:
        with pd.ExcelWriter(temporary,engine='openpyxl') as writer:
            for name,frame in frames:
                frame.to_excel(writer,index=False,sheet_name=name)
                manifest['sheets'].append({'name':name,'columns':list(frame.columns),'rows':[[cell(json_cell(v)) for v in row] for row in frame.itertuples(index=False,name=None)]})
            labels={'sheet':'工作表','row':'行号','column':'字段','label':'对象','source_id':'来源编号','url':'来源网址','quote':'原始摘录','note':'缺失原因或说明','normalized_value':'规范化值','raw_value':'原始值','status':'核验状态','unit':'目标单位','input_unit':'原始单位','input_value':'输入值','raw_input':'原始输入','raw_format_rule':'原始数值类型规则','format_rule':'类型归一化规则','price_status':'价格口径核验状态','price_metadata_input':'原始价格元数据','rule':'规范化规则','currency':'币种','market':'市场','price_type':'价格类型','queried_at':'查询日期','context_quote':'口径证据','required':'必需字段','calculation_id':'计算编号'}
            labels.update(observation_id='观察记录ID',evidence_parts='原文分项',input_quote='输入摘录')
            pd.DataFrame(provenance).map(lambda v:cell(json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else json_cell(v))).rename(columns=labels).to_excel(writer,index=False,sheet_name='Provenance')
            pd.DataFrame([{'项目':'核验方式','说明':'参数按真实来源校验；单位换算由程序执行。原文对应关系见Provenance。'}, {'项目':'价格口径','说明':'币种和市场使用ISO代码；价格类型及查询日期分别列示。不同口径不可直接比较。'}]+[{'项目':'未核实事项','说明':n} for n in manifest['quality_notes']]).to_excel(writer,index=False,sheet_name='Metadata')
            from .workbook_style import style_workbook
            style_workbook(writer.book)
        destination=workspace.path/FILENAME;temporary.replace(destination)
    finally:temporary.unlink(missing_ok=True)
    manifest['sha256']=hashlib.sha256(destination.read_bytes()).hexdigest()
    atomic_json(workspace.path/'business_artifact.json',manifest)
    # A direct export supersedes staged material; closing must not restore an
    # older proposal over a corrected workbook. Keep the same canonical contract.
    if sync_staged:
        persist_rows(workspace,sheets,provenance)
    consumed=read_json(workspace.path/'business_draft.json',{}).get('draft_id')
    if consumed:
        atomic_json(workspace.path/'business_export_state.json',{'task_id':workspace.task_id,
                    'consumed_draft_ids':[consumed],'business_sha256':manifest['sha256']})
    (workspace.path/'business_draft.json').unlink(missing_ok=True)
    workspace.log('export_business_excel',FILENAME,sheets=[n for n,_ in frames])
    return destination


def verify_business_excel(workspace):
    path=workspace.path/FILENAME;meta=read_json(workspace.path/'business_artifact.json',{})
    if not meta and not path.exists():return []
    staged=read_json(workspace.path/'business_rows.json',{})
    if staged.get('sheets') and meta.get('staged_version') is not None and staged.get('version')!=meta['staged_version']:
        return ['business_excel_staged_version_mismatch']
    try:
        if path.is_symlink() or meta.get('sha256')!=hashlib.sha256(path.read_bytes()).hexdigest():return ['business_excel_hash_mismatch']
        with_path=load_workbook(path,read_only=True,data_only=False)
        try:
            for sheet in meta['sheets']:
                rows=list(with_path[sheet['name']].values)
                expected=[sheet['columns']]+sheet['rows']
                if len(rows)!=len(expected):return ['business_excel_cells_mismatch']
                for actual,wanted in zip(rows,expected):
                    if len(actual)!=len(wanted):return ['business_excel_cells_mismatch']
                    for a,b in zip(actual,wanted):
                        if isinstance(a,(int,float)) and isinstance(b,(int,float)):
                            if not math.isclose(a,b,rel_tol=1e-14,abs_tol=1e-14):return ['business_excel_cells_mismatch']
                        elif hasattr(a,'isoformat') and isinstance(b,str) and a.strftime('%Y-%m-%d')==b:pass
                        elif a!=b:return ['business_excel_cells_mismatch']
        finally:with_path.close()
        return []
    except Exception as exc:return ['business_excel_invalid:'+type(exc).__name__]

# Verified V6 owner-binding integration. Do not edit this anchor manually.
from .owner_guard import wrap_original, wrap_owner_evidence
_unwrapped_subject_guard = require_subject_evidence
_wrapped_subject_guard = wrap_owner_evidence(wrap_original(require_subject_evidence))

def require_subject_evidence(workspace, source, label, column, quote, definition, audit):
    # An evidence-excerpt field must NEVER bypass the strict observed-H1 check
    # via commercial product/document owner aliases in the legacy wrappers.
    if definition.get('type') == 'text' and definition.get('role') == 'verbatim_excerpt':
        return _unwrapped_subject_guard(workspace, source, label, column, quote, definition, audit)
    return _wrapped_subject_guard(workspace, source, label, column, quote, definition, audit)

