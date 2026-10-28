"""Reviewable final facts, separated from strictly verified extractive values."""
import re
import json
from .delivery_schema import normalize_entry


def review_fact(workspace,entry,field,source,error):
    """Keep a source-linked scalar as a proposal, never certify it as a fact.

    Missing/invalid source identities still block export. Invalid scalar shapes
    become an explicitly missing cell; their original input stays in the audit.
    """
    if not isinstance(entry,dict) or 'value' not in entry:raise ValueError('单元格需要value')
    value=entry['value'];required=field.get('required',True)
    if value is None:
        note=entry.get('reason') or '页面未找到；待人工复核。'
        if entry.get('status')=='not_disclosed':note='来源未能证明“官方未公布”，保持未核实；'+note
        return '未核实',{'status':'unverified','required':required,'normalized_value':None,'note':note,'rule':'explicit_missing'},''
    if not source:raise ValueError('结构化字段仍需本任务真实source_id或provenance_ref')
    original_quote = entry.get('quote', '')
    quote = original_quote

    try:
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError('missing quote')
        workspace.evidence_for(source['url'], quote, source.get('observation_id'))
    except ValueError:
        quote = source['quote']
        workspace.evidence_for(source['url'], quote, source.get('observation_id'))
    audit_input=value if type(value) in (str,int,float,bool) else json.dumps(value,ensure_ascii=False,default=str)
    if isinstance(audit_input,str):audit_input=audit_input[:1500]
    audit={'status':'review_required','required':required,'normalized_value':value,
           'rule':'structured_fact_for_review','input_value':audit_input,'input_quote':str(original_quote)[:1500],
           'original_input':dict(entry),'diagnostic':getattr(error,'details',{'code':'fact_validation','message':str(error)}),
           'note':'依据已记录来源结构化整理，待人工复核；'+str(error)[:180]}
    kind=field.get('type','text')
    if kind in {'number','price'}:
        # A quoted number with incomplete market metadata may be delivered for
        # review; a fabricated number or an invalid unit conversion may not.
        scalar_field={**field,'type':'number'};scalar_field.pop('role',None)
        try:
            numeric,numeric_audit=normalize_entry({**entry,'quote':quote},scalar_field,source)
            audit.update(normalized_value=numeric,raw_value=numeric_audit['raw_value'])
            value=numeric
        except (ValueError,TypeError,KeyError):value=None
        if kind=='price' and value is not None:
            price={k:str(v)[:120] for k,v in entry.get('price',{}).items() if k in {'currency','market','type','queried_at'}} if isinstance(entry.get('price'),dict) else {}
            audit.update(price_status='unverified',price_metadata_input=price,
                         currency=price.get('currency','未核实'),market=price.get('market','未核实'),
                         price_type=price.get('type',''),queried_at=source.get('accessed_at','')[:10])
            # No comparable_group: unverified price contexts cannot feed totals.
    elif kind=='text':
        if not isinstance(value,str) or not value.strip() or len(value)>80 or re.search(r'[\r\n]',value):value=None
    elif kind=='date':
        try:
            from datetime import date
            date.fromisoformat(value)
        except (ValueError,TypeError):value=None
    elif kind=='boolean' and type(value) is not bool:value=None
    if value is None:
        audit.update(status='unverified',normalized_value=None,
                     note='提交值未通过规范化或证据校验；不代表网页没有该数据，待人工复核。'+str(error)[:180])
        return '未核实',audit,quote
    audit['normalized_value']=value
    return value,audit,quote
