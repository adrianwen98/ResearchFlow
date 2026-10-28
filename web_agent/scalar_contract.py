"""Domain-independent scalar formatting and evidence-bound quantity extraction.

This module never fetches evidence or certifies its origin. Callers must validate
the quote against the stored observation before accepting a normalized result.
"""
import copy
import re
import unicodedata


class ScalarContractError(ValueError):
    def __init__(self,code,message,**details):
        super().__init__(message)
        self.details={'code':code,'message':message,**details}


def canonical_unit(value):
    from .delivery_schema import UNITS,ALIASES
    if not value:return ''
    if not isinstance(value,str):raise ScalarContractError('unit_type','unit必须是字符串')
    value=unicodedata.normalize('NFKC',value).strip()
    if value in UNITS:return value
    matches=[unit for unit,pattern in ALIASES.items()
             if re.fullmatch(pattern,value,0 if UNITS[unit][0]=='bit_rate' else re.I)]
    if len(matches)==1:return matches[0]
    raise ScalarContractError('unsupported_unit','单位不受支持；使用明确单位及量纲',input_unit=value,allowed=list(UNITS))


def text_form(value):
    # Presentation equivalence only: no translation, word deletion or fuzzy match.
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',value)).strip()


def quantity_candidates(text,unit):
    from .delivery_schema import UNITS,ALIASES,NUMBER
    candidates=[]
    for source_unit in UNITS:
        if UNITS[source_unit][0]!=UNITS[unit][0]:continue
        flags=0 if UNITS[source_unit][0]=='bit_rate' else re.I
        pattern='('+NUMBER+r')[ \t]*[-\u2010\u2011]?[ \t]*'+ALIASES[source_unit]+r'(?![A-Za-z])'
        for match in re.finditer(pattern,text,flags):
            raw=float(match[1].replace(',',''))
            candidates.append({'raw_value':raw,'input_unit':source_unit,
                               'value':raw*UNITS[source_unit][1]/UNITS[unit][1],
                               'span':list(match.span()),'raw_text':match[0],'rule':'bound_quantity'})
    # A unit-bearing header followed immediately by one scalar is common in
    # tables. Do not borrow a unit from another line/block or from free prose.
    for source_unit in UNITS:
        if UNITS[source_unit][0]!=UNITS[unit][0]:continue
        flags=0 if UNITS[source_unit][0]=='bit_rate' else re.I
        pattern = (
                r'(?m)^[^\n\d]{0,80}[（(][ \t]*'
                + ALIASES[source_unit]
                + r'(?:[ \t]*[,，][^()（）\n\d]{1,30})?[ \t]*[)）][ \t]*\r?\n[ \t]*('
                + NUMBER + r')[ \t]*\r?$'
        )
        for match in re.finditer(pattern,text,flags):
            raw=float(match[1].replace(',',''))
            candidates.append({'raw_value':raw,'input_unit':source_unit,'value':raw*UNITS[source_unit][1]/UNITS[unit][1],
                               'span':list(match.span()),'raw_text':match[0],'rule':'unit_header_scalar'})
    return candidates


def prepare(entry,field):
    """Normalize only unambiguous quantities; preserve every submitted field."""
    from .delivery_schema import NUMBER
    result=copy.deepcopy(entry)
    audit={'original_input':copy.deepcopy(entry.get('_original_input',entry))}
    if result.get('value') is None:return result,audit
    if result.get('input_unit'):result['input_unit']=canonical_unit(result['input_unit'])
    if field['type']=='price':
        currency=field.get('currency') or result.get('price',{}).get('currency') if isinstance(result.get('price',{}),dict) else field.get('currency')
        marker={'USD':r'USD|US\$','CNY':r'CNY|RMB|人民币','EUR':r'EUR|€','GBP':r'GBP|£','SGD':r'SGD|S\$','JPY':r'JPY|日元'}.get(currency)
        if marker:
            for key in ('value','raw_value'):
                raw=result.get(key)
                if isinstance(raw,str):
                    match=re.fullmatch(r'(?:'+marker+r')\s*('+NUMBER+r')|('+NUMBER+r')\s*(?:'+marker+r')',raw.strip())
                    if match:
                        result[key]=float((match[1] or match[2]).replace(',',''))
                        audit['format_rule']='currency_scalar'
    unit=field.get('unit')
    if field['type']=='number' and unit:
        candidates=quantity_candidates(result.get('quote',''),unit)
        value=result['value']
        # An annotated string is a proposal for one scalar. Ambiguous quantities
        # require separate cells/rows; never choose the first or largest number.
        if isinstance(value,str) and not re.fullmatch(NUMBER,value.strip()):
            proposed=quantity_candidates(value,unit)
            unique={(p['raw_value'],p['input_unit']) for p in candidates}
            if len(unique)!=1:
                raise ScalarContractError('ambiguous_quantity' if unique else 'quantity_not_evidenced',
                    '摘录中没有唯一的带单位数值；请明确raw_value/input_unit或拆分多个规格',candidates=candidates)
            evidence=candidates[0]
            if result.get('input_unit') and result['input_unit']!=evidence['input_unit']:
                raise ScalarContractError('input_unit_conflict','显式input_unit与原文绑定单位冲突',candidates=candidates)
            if not any(p['raw_value']==evidence['raw_value'] and p['input_unit']==evidence['input_unit'] for p in proposed):
                raise ScalarContractError('quantity_mismatch','输入数值与摘录中绑定单位的数值不一致',candidates=candidates)
            if 'raw_value' in result:
                raw=str(result['raw_value']).strip()
                if raw not in {str(evidence['raw_value']),str(int(evidence['raw_value']))} and raw!=evidence['raw_text']:
                    raise ScalarContractError('raw_value_conflict','显式raw_value与可提取原值冲突',candidates=candidates)
            result.update(value=evidence['value'],raw_value=evidence['raw_value'],input_unit=evidence['input_unit'])
            audit.update(format_rule='unique_evidenced_quantity',evidence_span=evidence['span'],evidence_raw=evidence['raw_text'])
    return result,audit
