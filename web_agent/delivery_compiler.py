"""Compile source-backed comparison inputs before strict workbook validation.

Only deterministic layout, scalar-unit and verbatim-fragment transformations.
Never translate, infer an unavailable fact, or silently convert invalid evidence
into a successful null value.
"""
import copy
import re
from .delivery_schema import inferred_field,field_for,UNITS,ALIASES,NUMBER

PARAMETER=re.compile(r'传感器|镜头|光圈|屏幕|视频|帧率|码率|电池|续航|重量|尺寸|防水|温度|价格|售价|商城价|sensor|screen|battery|weight|price|bitrate',re.I)


def definition(label,entries,supplied=None):
    result=field_for(label,{label:supplied or {}})
    # Explicit price metadata always means a price, regardless of table direction.
    known=[e for e in entries if isinstance(e,dict) and e.get('value') is not None]
    if known and all(isinstance(e.get('price'),dict) for e in known):result.update(type='price',role='price')
    if result.get('type','text')=='text' or (result.get('type')=='number' and result.get('unit')=='count'):
        for pattern,unit in [(r'码率|bit.?rate','Mbps'),(r'帧率|frame.?rate','fps'),(r'续航|operating time|duration','min'),(r'^重量$|^weight$','g')]:
            if re.search(pattern,label,re.I):result.update(type='number',unit=unit);break
    return result


def key_value(line):
    depth=0
    for i,char in enumerate(line):
        if char in '(（[':depth+=1
        elif char in ')）]':depth=max(0,depth-1)
        elif char==':' and depth==0:
            key,value=line[:i].strip(),line[i+1:].strip()
            return (key,value) if key and value else None
    return None


def verbatim_parts(quote):
    lines=[line.strip() for line in quote.splitlines() if line.strip()]
    if not 2<=len(lines)<=8:return None
    keyed=[key_value(line) for line in lines]
    if all(keyed):
        parts=keyed
    elif (len(lines)==2 and len(lines[0])<=60 and
          re.fullmatch(r'[A-Za-z][A-Za-z ()/:-]+',lines[0]) and re.match(r'^[±+-]?\d',lines[1])):
        parts=[(lines[0].rstrip(':'),lines[1])]
    elif lines[0].endswith(':') and 2<=len(lines)<=6 and all(re.fullmatch(r'[A-Za-z][A-Za-z0-9 .+/()-]{1,60}',v) for v in lines[1:]):
        parts=[(lines[0].rstrip(':')+' '+str(i),v) for i,v in enumerate(lines[1:],1)]
    else:return None
    if len({key for key,_ in parts})!=len(parts) or any(not value or len(value)>80 or re.search(r'[;；|]',value) for _,value in parts):return None
    return parts


def compile_comparisons(workspace,sheets,*,allow_structured_review=False):
    if not isinstance(sheets,list) or not 1<=len(sheets)<=5:raise ValueError('需要1–5个业务工作表')
    from .business_contract import comparison_contract
    output,changes=comparison_contract(workspace,sheets)
    sources={s['id']:s for s in workspace.sources()}
    for spec in output:
        if spec.get('mode')!='comparison':continue
        columns=spec.get('columns',[]);rows=spec.get('rows',[]);fields=spec.get('fields',{})
        if not isinstance(columns,list) or not isinstance(rows,list) or not isinstance(fields,dict):continue
        labels=[r.get('label','') for r in rows if isinstance(r,dict)]
        explicit=spec.get('layout')=='parameters_in_rows'
        price_row=any(isinstance(r,dict) and isinstance(r.get('cells'),dict) and r['cells']
                      and all(isinstance(e,dict) and isinstance(e.get('price'),dict) for e in r['cells'].values()) for r in rows)
        matrix=(len(labels)>=3 and (sum(bool(PARAMETER.search(str(label))) for label in labels)>=3 or price_row)
                and not any(PARAMETER.search(str(col)) for col in columns)
                and all(f.get('type','text')=='text' for f in fields.values() if isinstance(f,dict)))
        if explicit or (matrix and spec.get('layout')!='parameters_in_columns'):
            if len(labels)!=len(rows) or len(set(labels))!=len(labels) or not labels:raise ValueError('参数行需要唯一label')
            if any(not isinstance(r.get('cells'),dict) or set(r['cells'])!=set(columns) for r in rows):continue
            original=copy.deepcopy(rows)
            spec['columns']=labels
            spec['fields']={r['label']:definition(r['label'],list(r['cells'].values()),r.get('field')) for r in original}
            spec['rows']=[{'label':col,'cells':{r['label']:copy.deepcopy(r['cells'][col]) for r in original}} for col in columns]
            columns=spec['columns'];rows=spec['rows'];fields=spec['fields']
            changes.append({'sheet':spec['name'],'rule':'transpose_parameter_rows','note':'产品改为行、参数改为列，保留全部输入单元格及来源。'})
        if not rows or any(not isinstance(r,dict) or not isinstance(r.get('cells'),dict) or set(r['cells'])!=set(columns) for r in rows):continue
        duplicates=[col for col in columns if all(isinstance(r['cells'][col],dict) and r['cells'][col].get('value')==r['label'] for r in rows)]
        for col in duplicates:
            if len(columns)==1:break
            columns.remove(col);fields.pop(col,None)
            for row in rows:row['cells'].pop(col)
            changes.append({'sheet':spec['name'],'field':col,'rule':'deduplicate_row_label',
                            'note':'分类仅作为行标签保留，不冒充来源参数。'})
        for col in list(columns):
            entries=[r['cells'][col] for r in rows]
            if any(not isinstance(e,dict) for e in entries):continue
            field=definition(col,entries,fields.get(col));fields[col]=field
            # Validate observation identity BEFORE any source-driven extraction.
            for entry in entries:
                source=sources.get(entry.get('source_id'))
                if entry.get('value') is not None and source and isinstance(entry.get('quote'),str):
                    try:workspace.evidence_for(source['url'],entry['quote'],source.get('observation_id'))
                    except ValueError:
                        if not allow_structured_review:raise
            if field.get('type','text')=='text':
                parts=[verbatim_parts(e.get('quote','')) if e.get('value') is not None and isinstance(e.get('quote'),str) else None for e in entries]
                needs_split=any(p and (not isinstance(e.get('value'),str) or e['value'] not in e['quote'] or re.search(r'[;；|\n]',e['value'])) for e,p in zip(entries,parts))
                if needs_split and all(p or e.get('value') is None for e,p in zip(entries,parts)):
                    keys=list(dict.fromkeys(key for p in parts if p for key,_ in p))
                    newcols=[(col+' · '+key)[:80] for key in keys]
                    if len(set(newcols))!=len(newcols) or set(newcols)&(set(columns)-{col}):raise ValueError('拆分后的字段名重复，请缩短参数名称')
                    at=columns.index(col);columns[at:at+1]=newcols;fields.pop(col,None)
                    for newcol in newcols:fields[newcol]={'type':'text','required':False}
                    for row,entry,p in zip(rows,entries,parts):
                        row['cells'].pop(col)
                        for key,newcol in zip(keys,newcols):
                            value=dict(p or []).get(key)
                            if value is None:
                                row['cells'][newcol]={'value':None,'status':'unverified','reason':entry.get('reason') if entry.get('value') is None else '保存的该对象摘录未提供此分项；不推断、不用于数值比较。',
                                                       '_required_for_review':entry.get('value') is None and field.get('required',True)}
                            else:
                                row['cells'][newcol]={**entry,'value':value,'_compiled_input':entry.get('value'),'_compile_rule':'verbatim_subfield'}
                    changes.append({'sheet':spec['name'],'field':col,'rule':'split_verbatim_subfields','columns':newcols,
                                    'note':'仅保留保存摘录中的实际指标；输入概括不作为已核实结论。'})
                    continue
            for entry in entries:
                original_entry=copy.deepcopy(entry)
                value=entry.get('value');quote=entry.get('quote','')
                if value is None or not isinstance(quote,str):continue
                unit=field.get('unit')
                if field.get('type')=='number' and isinstance(value,str) and unit in UNITS:
                    for source_unit in UNITS:
                        if UNITS[source_unit][0]!=UNITS[unit][0]:continue
                        separator=r'\s*[-\u2010\u2011]?\s*' if source_unit=='inch' else r'\s*'
                        match=re.fullmatch('('+NUMBER+')'+separator+ALIASES[source_unit],value.strip(),0 if UNITS[unit][0]=='bit_rate' else re.I)
                        if match:
                            raw=float(match.group(1).replace(',',''));number=raw*UNITS[source_unit][1]/UNITS[unit][1]
                            entry.setdefault('_original_input',original_entry)
                            entry.update(value=int(number) if number.is_integer() else number,raw_value=raw,input_unit=source_unit,
                                         _compiled_input=value,_compile_rule='source_unit_scalar')
                            break
                # Similar wording and matching digits do not establish a
                # normalization rule. Unsupported paraphrases remain reviewable.
                elif field.get('type','text')=='text' and isinstance(value,str):
                    # Preserve the existing video-list formatting correction
                    # through an exact grammar instead of fuzzy text similarity.
                    pattern=r'(\d+K \(\d+:\d+\)):?\s+(\d+[×x]\d+)@(\d+(?:/\d+)*)fps'
                    proposed=re.fullmatch(pattern,value);original=re.fullmatch(pattern,quote)
                    if (proposed and original and proposed.groups()[:2]==original.groups()[:2]
                            and set(proposed[3].split('/'))<=set(original[3].split('/')) and value!=quote):
                        entry.setdefault('_original_input',original_entry)
                        entry.update(value=quote,_compiled_input=value,_compile_rule='verbatim_video_rate_list')
                        changes.append({'sheet':spec['name'],'field':col,'rule':'verbatim_video_rate_list','input':value,'output':quote})
        spec['fields']=fields
        if len(spec['columns'])>40:raise ValueError('拆分后超过40个参数，请分成多个工作表')
    return output,changes


def export_compiled(workspace,sheets,*,allow_structured_review=False):
    from .business_excel import export_business_excel
    compiled,changes=compile_comparisons(workspace,sheets,allow_structured_review=allow_structured_review)
    return export_business_excel(workspace,compiled,review_disclosed_missing=True,normalizations=changes,
                                 allow_structured_review=allow_structured_review)
