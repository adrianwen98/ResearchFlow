"""Deterministic, evidence-bound scalar fields for comparison workbooks."""
from __future__ import annotations
import math
import re
from datetime import date
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

STATUS = {'verified': '已核实', 'unverified': '未核实', 'not_disclosed': '官方未公布'}
UNITS = {'inch': ('length', 25.4), 'mm': ('length', 1), 'cm': ('length', 10),
         'mAh': ('charge', 1), 'Ah': ('charge', 1000), 'g': ('mass', 1), 'kg': ('mass', 1000),
         't': ('mass', 1000000), '万吨': ('mass', 10000000000), '亿吨': ('mass', 100000000000000),
         '%': ('percent', 1), 'count': ('count', 1), 'ppi': ('pixel_density', 1), 'x': ('magnification', 1),
         'fps': ('frame_rate', 1), 'bps': ('bit_rate', 1), 'kbps': ('bit_rate', 1000),
         'Mbps': ('bit_rate', 1000000), 's': ('time', 1), 'min': ('time', 60), 'h': ('time', 3600)}
ALIASES = {'inch': r'(?:in|inch(?:es)?|英寸|["″])', 'mm': r'(?:mm|millimet(?:er|re)s?|毫米)', 'cm': r'(?:cm|centimet(?:er|re)s?|厘米)',
           'mAh': r'(?:mAh|毫安时)', 'Ah': r'(?<!m)(?:Ah|安时)', 'g': r'(?<!k)(?:g|克)',
           'kg': r'(?:kg|kilograms?|千克|公斤)', 't': r'(?:t|ton(?:ne)?s?|吨)',
           '万吨': r'万吨', '亿吨': r'亿吨',
           '%': r'(?:%|percent|百分比)', 'count': r'(?:count|数量|个|核)', 'ppi': r'(?:ppi|像素/英寸|像素每英寸)', 'x': r'(?:倍)',
           'fps': r'(?:fps|帧/秒|帧每秒)', 'bps': r'(?:bps|bit/s)',
           'kbps': r'(?:kbps|kbit/s)', 'Mbps': r'(?:Mbps|Mbit/s)',
           's': r'(?:s|secs?|seconds?|秒)', 'min': r'(?:min|mins?|minutes?|分钟)',
           'h': r'(?:h|hrs?|hours?|小时)'}
NUMBER = r'(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?!\d|\.\d)'

def inferred_field(column):
    if re.fullmatch(r'(?:官方)?来源(?:URL|网址|链接)|官方(?:网页|页面)(?:URL|网址|链接)|(?:official\s+)?source\s*(?:url|link)|reference\s+url',column.strip(),re.I):
        return {'type':'text','role':'source_url'}
    if re.search(r'价格.*(?:趋势|变化|说明|类型)|(?:price|pricing).*(?:trend|change|type|note)',column,re.I):return {'type':'text'}
    if re.search(r'屏幕.*尺寸|screen.*(?:size|diagonal)', column, re.I):
        return {'type': 'number', 'unit': 'inch', 'role': 'screen_size'}
    if re.search(r'电池.*容量|battery.*capacity', column, re.I):
        return {'type': 'number', 'unit': 'mAh', 'role': 'battery_capacity'}
    if re.search(r'屏幕.*类型|显示屏.*类型|(?:screen|display).*type',column,re.I):
        return {'type':'text','role':'display_type'}
    if re.search(r'处理器|芯片|processor|chipset', column, re.I):return {'type': 'text', 'role': 'processor'}
    if re.search(r'价格|售价|price', column, re.I):return {'type': 'price', 'role': 'price'}
    return {'type': 'text'}

def field_for(column, fields):
    inferred = inferred_field(column)
    supplied = fields.get(column, {})
    if not isinstance(supplied, dict):raise ValueError('fields每列必须是type/unit/required字段定义')
    field = {**inferred, **supplied}
    if inferred.get('role')=='source_url':
        field.update(type='text',role='source_url',_column=column)
    elif field.get('role')=='source_url':
        raise ValueError('来源URL角色只允许明确的来源地址列')
    if supplied.get('role') in {'document_identity','editorial_label'}:
        if field.get('type') != 'text' or 'role' in inferred:
            raise ValueError('文档身份/编辑标签只能用于无既定语义的文本列')
        if supplied['role']=='editorial_label' and not re.search(r'主题|分类|类别|标签|topic|category|tag',column,re.I):
            raise ValueError('editorial_label不能代替普通业务事实列')
    # Recognized business semantics cannot be weakened by calling a price free text.
    if 'role' in inferred:field.update(inferred)
    if field['type'] not in {'text', 'number', 'price', 'boolean', 'date'}:raise ValueError('字段type不受支持')
    from .scalar_contract import canonical_unit
    # Currency is a price context, never a physical dimension. Preserve it for
    # validation against explicit price metadata; do not invent market/type.
    declared=supplied.get('unit',field.get('unit',''))
    if declared in {'USD','SGD','CNY','EUR','GBP','JPY'}:
        field.update(type='price',currency=declared)
        field.pop('unit',None)
    elif field['type']=='text' and 'role' not in inferred and supplied.get('type')=='text':
        # A composite text value (e.g. resolution 2622x1206, 460 ppi) is
        # not a scalar with a physical dimension. Discard its redundant unit
        # hint, but keep quoted text and all provenance/subject checks strict.
        field.pop('unit', None)
    elif declared:
        normalized = canonical_unit(declared)

        if normalized not in set(UNITS) | {"count"}:
            raise ValueError(f"单位不受支持: {declared}")

        field["unit"] = normalized

        if field["type"] == "text":
            field["type"] = "number"

    if not isinstance(field.get('required', True), bool):raise ValueError('required必须为布尔值')
    field.setdefault('required', True)
    return field

def numbers(text):return [float(n.replace(',', '')) for n in re.findall(NUMBER, text)]
def contains_extractive_text(value, quote):
    pattern = r'(?<![A-Za-z0-9_.])' + re.escape(value) + r'(?![A-Za-z0-9_.])'
    return re.search(pattern, quote) is not None


def source_dates(text):
    """Explicit calendar dates only; no locale, relative dates or model inference."""
    result={}
    months={name.lower():i for i,name in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],1)}
    patterns=[(r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b',lambda m:(int(m[1]),int(m[2]),int(m[3]))),
              (r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',lambda m:(int(m[1]),int(m[2]),int(m[3]))),
              (r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})\b',lambda m:(int(m[3]),months[m[1][:3].lower()],int(m[2])))]
    for pattern,parts in patterns:
        for match in re.finditer(pattern,text,re.I):
            try:result[date(*parts(match)).isoformat()]=match.group()
            except ValueError:continue
    return result

def normalize_entry(entry, field, source):
    """Return a scalar + audit metadata. Caller's observation check precedes this."""
    from .scalar_contract import prepare,canonical_unit,text_form,quantity_candidates,ScalarContractError
    field=dict(field)
    if field.get('unit'):field['unit']=canonical_unit(field['unit'])
    entry,input_audit=prepare(entry,field)
    value=entry.get('value');quote=entry.get('quote','');kind=field['type']
    status=entry.get('status', 'unverified' if value is None else 'verified')
    if status not in STATUS:raise ValueError('status只能verified/unverified/not_disclosed')
    audit={'normalized_value':value,'status':status,'unit':field.get('unit',''),
           'required':field['required'],'rule':'extractive_scalar','raw_value':entry.get('raw_value',value),**input_audit}
    if value is None:
        if status=='verified' or not isinstance(entry.get('reason'),str) or not entry['reason'].strip():
            raise ValueError('缺失值需value:null、未核实状态及具体reason')
        if status=='not_disclosed' and not (source and re.search(r'未公布|未披露|not (?:disclosed|published)',quote,re.I)):
            raise ValueError('官方未公布需要明确原文证据；页面没有找到参数请标为unverified')
        return STATUS[status],audit
    if status!='verified':raise ValueError('未核实数值不得当作已知值导出；使用null并说明reason')
    # Reconcile scalar JSON types only; retain evidence and original input.
    if kind=='text' and 'role' not in field and type(value) in (int,float) and math.isfinite(value):
        candidate=str(value)
        if any(m.group()==candidate for m in re.finditer(NUMBER,quote)):
            audit.update(input_value=value,format_rule='number_to_text_exact_quote')
            value=candidate
            audit['normalized_value']=value
    elif kind in {'number','price'} and isinstance(value,str) and re.fullmatch(NUMBER,value.strip()):
        literal=value.replace(',','').strip()
        number=int(literal) if '.' not in literal else float(literal)
        if math.isfinite(number) and Decimal(str(number))==Decimal(literal):
            converted=int(number) if type(number) is int or number.is_integer() else number
            audit.update(input_value=value,format_rule='numeric_string_to_number')
            entry={**entry,'value':converted}
            value=converted
            audit['normalized_value']=value
            audit['raw_value']=entry.get('raw_value',value)

    if kind in {'number','price'}:
        if type(value) not in (int,float) or not math.isfinite(value):
            raise ScalarContractError('scalar_required','数值字段必须是有限数字；多规格或多档价格应拆分行/列',
                expected_type=kind,required_price_fields=['currency','market','type','queried_at'] if kind=='price' else [])
        if len(Decimal(str(value)).normalize().as_tuple().digits)>15:raise ValueError('数值超过Excel可靠的15位有效数字；长编号应使用text字段保存，不得静默四舍五入')
        raw=entry.get('raw_value',value)
        unit=field.get('unit','');input_unit=entry.get('input_unit',unit)
        if isinstance(raw,str):
            literal=raw.strip()
            # A unit-bearing raw value is accepted only for the explicitly declared
            # input unit. Never infer a unit from a count column or free prose.
            numeric=re.fullmatch(NUMBER,literal)
            match=numeric
            if not match and input_unit in ALIASES:
                separator=r'[ \t]*[-\u2010\u2011]?[ \t]*' if input_unit=='inch' else r'[ \t]*'
                match=re.fullmatch('('+NUMBER+')'+separator+ALIASES[input_unit],literal)
            if match:
                token=match.group() if numeric else match.group(1)
                decimal=Decimal(token.replace(',',''))
                parsed=float(decimal)
                if math.isfinite(parsed) and Decimal(str(parsed))==decimal and len(decimal.normalize().as_tuple().digits)<=15:
                    raw=int(parsed) if parsed.is_integer() else parsed
                    audit.update(raw_input=entry.get('raw_value'),raw_value=raw,
                                 raw_format_rule='exact_numeric_scalar')
        if type(raw) not in (int,float) or not math.isfinite(raw) or raw not in numbers(quote):
            raise ValueError('raw_value需为摘录中的单个数值；不要包含多个参数、说明或不匹配的单位')
        if unit:
            if input_unit not in UNITS or UNITS[input_unit][0]!=UNITS[unit][0]:
                raise ValueError(f'字段目标单位{unit}与输入单位{input_unit}量纲不一致；用field_patches修正字段定义，不能把帧率/码率/时间写成count')
            # Bind the value to its unit, not an unrelated unit elsewhere in quote.
            # Bit rates are case-sensitive: MBps/MB/s are bytes, not Mbps bits.
            flags=0 if UNITS[input_unit][0]=='bit_rate' else re.I
            tokens=[m.group() for m in re.finditer(NUMBER,quote) if float(m.group().replace(',',''))==raw]
            # English specification adjectives bind with a hyphen: 6.3-inch.
            # This separator never applies to a sign or to other quantities.
            separator=r'[ \t]*[-\u2010\u2011]?[ \t]*' if input_unit=='inch' else r'[ \t]*'
            bound=any(re.search(r'(?<![A-Za-z0-9_.])'+re.escape(t)+separator+ALIASES[input_unit]+r'(?![A-Za-z])',quote,flags) for t in tokens)
            if not bound:
                bound=any(c['raw_value']==raw and c['input_unit']==input_unit and c['rule']=='unit_header_scalar'
                          for c in quantity_candidates(quote,unit))
            if not bound:
                # Chinese statistical tables often declare one unit for the
                # whole table ("单位：万吨") rather than beside every number.
                # Accept only when the exact value and the declared unit both
                # occur in the same saved quote.
                header=re.search(r'(?:单位|unit)\s*[:：]\s*'+ALIASES[input_unit],quote,flags)
                bound=bool(header and raw in numbers(quote))
            if not bound:raise ScalarContractError('unit_not_bound','摘录需包含数值与单位的直接关系，不能借用其他参数的单位')
            expected=raw*UNITS[input_unit][1]/UNITS[unit][1]
            if not math.isclose(value,expected,rel_tol=1e-6,abs_tol=1e-8):raise ValueError('规范化值与原值/单位换算不一致')
            audit.update(input_unit=input_unit,rule=f'{input_unit} -> {unit}')
        elif value!=raw:raise ValueError('没有单位换算时，value必须等于原始数值')
        if field.get('role') in {'screen_size','battery_capacity'} and value<=0:raise ValueError('尺寸和容量必须大于零')
        if kind=='price' and (value<0 or (value==0 and not re.search(r'\bfree\b|免费',quote,re.I))):
            raise ValueError('价格不能为负；零价格必须有明确免费原文及完整价格口径')
        if kind=='price':
            price=entry.get('price',{})
            if not isinstance(price,dict):raise ValueError('价格需price元数据')
            currency=price.get('currency',field.get('currency',''));market=price.get('market','');ptype=price.get('type','')
            if field.get('currency') and currency!=field['currency']:
                raise ScalarContractError('currency_conflict','字段币种与价格元数据不一致')
            if not re.fullmatch(r'[A-Z]{3}',currency) or not re.fullmatch(r'[A-Z]{2}',market):raise ValueError('价格必须提供ISO币种及市场代码，例如USD/US、SGD/SG、CNY/CN')
            if ptype not in {'launch','current','starting','promotional'}:raise ValueError('价格类型需launch/current/starting/promotional，无法核实用null')
            queried=price.get('queried_at','')
            try:date.fromisoformat(queried)
            except (TypeError,ValueError):raise ValueError('价格查询日期必须是YYYY-MM-DD')
            if not source or queried!=source.get('accessed_at','')[:10]:raise ValueError('价格查询日期必须等于实际来源记录日期')
            marker={'USD':r'USD|US\$|美元','SGD':r'SGD|S\$|新加坡元|新币','CNY':r'CNY|RMB|人民币|元','EUR':r'EUR|€|欧元','GBP':r'GBP|£|英镑','JPY':r'JPY|日元'}.get(currency,re.escape(currency))
            # Ambiguous dollar signs require an explicit recorded regional page.
            path=urlsplit(source['url']).path.lower()
            locale=re.search(r'/(?:[a-z]{2}-)?'+market.lower()+r'/',path+'/')
            context=entry.get('context_quote','')
            region={'US':r'United States|美国','SG':r'Singapore|新加坡','CN':r'中国|China','GB':r'United Kingdom|英国','JP':r'Japan|日本'}.get(market,r'(?!)')
            if not locale and not re.search(region,context,re.I):raise ValueError('市场需来源URL地区路径或context_quote中的国家原文证明')
            if not re.search(marker,quote,re.I) and not ('$' in quote and currency in {'USD','SGD'} and {'USD':'US','SGD':'SG'}[currency]==market):
                raise ValueError('价格币种与摘录不一致或未能核实')
            if ptype=='launch' and not re.search(r'发布价|首发价|上市价|launch price|at launch|starting price at launch',quote+' '+context,re.I):
                raise ValueError('发布价必须有明确发布价原文，商店当前售价不能替代')
            if ptype=='starting' and not re.search(r'from|起',quote,re.I):raise ValueError('起售价需from/起原文')
            if ptype=='promotional' and not re.search(r'sale|discount|优惠|促销',quote+' '+context,re.I):raise ValueError('促销价缺少促销依据')
            if ptype=='current' and not re.search(r'buy|shop|store|cart|购买|购物|售价',source['url']+' '+context,re.I):raise ValueError('当前售价需要商店页面依据')
            audit.update(currency=currency,market=market,price_type=ptype,queried_at=queried,
                         comparable_group=f'{market}/{currency}/{ptype}/{queried}',context_quote=context)
    elif kind=='text' and field.get('role')=='source_url':
        # URL provenance is a host-observed metadata value, not quoted body text.
        # Only recognized URL column names can opt into this special validation.
        if not re.fullmatch(r'(?:官方)?来源(?:URL|网址|链接)|官方(?:网页|页面)(?:URL|网址|链接)|(?:official\s+)?source\s*(?:url|link)|reference\s+url',field.get('_column',''),re.I):
            raise ScalarContractError('source_url_role_mismatch','来源URL校验要求明确的来源地址列')
        from .evidence_urls import url_key
        observed_url=source.get('url','') if isinstance(source,dict) else ''
        if (not isinstance(value,str) or not isinstance(observed_url,str)
                or not value.startswith(('https://','http://')) or not observed_url
                or url_key(value)!=url_key(observed_url)):
            raise ScalarContractError('source_url_mismatch',
                '来源URL必须与当前source_id实际观察并登记的网页URL一致')
        value=observed_url
        audit.update(normalized_value=value,raw_value=observed_url,
                     rule='observed_source_url')
    elif kind=='text' and field.get('role')=='verbatim_excerpt':
        # Source excerpt is a different data shape from a scalar text parameter.
        # The caller has already checked quote against this task's observation.
        # Never translate, summarize, concatenate or otherwise rewrite evidence.
        from .scalar_contract import text_form
        if (not isinstance(value,str) or not value.strip() or len(value)>1500
                or '\x00' in value):
            raise ValueError('原文摘录必须是1–1500字符的非空连续文本；长文章请拆成多个摘录')
        if not isinstance(quote,str) or text_form(value) not in text_form(quote):
            from .scalar_contract import ScalarContractError
            raise ScalarContractError('verbatim_excerpt_mismatch',
                '原文摘录值必须逐字来自已观察的连续quote；翻译与概括放在报告，不能当作原文事实')
        audit.update(rule='observed_verbatim_excerpt',raw_value=value,
                     normalized_value=value,text_normalization='unicode_nfkc_whitespace')
    elif kind=='boolean':
        if type(value) is not bool:raise ValueError('布尔字段必须为true/false')
        if str(value).lower() not in quote.lower():raise ValueError('布尔值缺少对应原文')
    elif kind=='date':
        date.fromisoformat(value)
        if value not in source_dates(quote):raise ValueError('日期必须直接出现在来源中')
        audit.update(raw_value=source_dates(quote)[value],rule='explicit_calendar_date_to_iso')
    else:
        if not isinstance(value,str):
            raise ValueError('此字段type=text，value必须是字符串；数值0应写成字符串"0"，不能传数字。若字段实际为数值，请修正字段定义')
        links = re.findall(r'(?m)^\s*-\s*/url:\s*(\S+)\s*$', value)
        if len(links) == 1 and links[0] in quote and isinstance(source, dict):
            resolved = urljoin(source.get('url', ''), links[0])
            if resolved.startswith(('http://', 'https://')):
                value = resolved
                audit.update(normalized_value=value, raw_value=links[0],
                             rule='observed_link_url')
                return value, audit
        if re.fullmatch(r'https?://\S+', value.strip()):
            from .evidence_urls import url_key
            observed_url = source.get('url', '') if isinstance(source, dict) else ''
            if observed_url and url_key(value.strip()) == url_key(observed_url):
                value = value.strip()
                audit.update(normalized_value=value, raw_value=observed_url,
                             rule='observed_entry_url')
                return value, audit
        if len(value) > 80:
            # A long business text is valid when it is literally the source
            # excerpt; translations/summaries still fail below.
            if len(value) <= 1500 and text_form(value) in text_form(quote):
                audit.update(rule='observed_verbatim_excerpt', raw_value=value,
                             text_normalization='unicode_nfkc_whitespace')
                return value, audit
            raise ValueError('文本业务值限80字且为单一参数；长原文放quote，多个参数分列')
        if not value.strip() or re.search(r'[\n\r;；|]',value):
            raise ValueError('文本业务值限80字且为单一参数；长原文放quote，多个参数分列')
        normalize=text_form
        if 'raw_value' in entry and isinstance(entry['raw_value'],str):
            raw_text=normalize(entry['raw_value'])
            components=[normalize(p) for p in re.split('[，、]',value)]
            related = contains_extractive_text(normalize(value), raw_text) or (
                    2 <= len(components) <= 4
                    and all(p and contains_extractive_text(p, raw_text) for p in components)
            )
            if raw_text not in normalize(quote) or not related:
                raise ScalarContractError('text_raw_mismatch','raw_value必须可在原文定位，且包含规范化文本值；不能用无关原文证明规范化值')
        audit['text_normalization']='unicode_nfkc_whitespace'
        if not contains_extractive_text(normalize(value), normalize(quote)):
            # Display descriptions may join multiple extractive components.
            # Every component must occur in the one verified continuous quote;
            # no translation, synonym inference or approximate matching.
            parts=[p.strip() for p in re.split('[，、]',value)]
            if (
                    field.get('role') != 'display_type'
                    or len(quote) > 600
                    or not 2 <= len(parts) <= 4
                    or not all(
                p and contains_extractive_text(normalize(p), normalize(quote))
                for p in parts
            )
            ):
                raise ScalarContractError('text_not_extractive','文本规范化只允许Unicode/空白格式等价；翻译、概括或新增事实需要明确原文分项',
                    raw_value=entry.get('raw_value'),input_value=value,
                    candidates=[{'value':line.strip(),'raw_value':line.strip(),'rule':'verbatim_candidate_requires_field_selection'}
                                for line in quote.splitlines() if 0<len(line.strip())<=80][:8])
            audit.update(raw_value=quote,evidence_parts=parts,rule='display_extractive_components',normalized_value=value)
        if field.get('role')=='processor' and re.search(r'GHz|MHz|manufactur|processCPU|核 CPU|CPU Speed|\d\s*nm',value,re.I):
            raise ValueError('处理器列只收芯片型号，不收频率/工艺/核心数；型号未核实用null')
    if kind!='price' and isinstance(entry.get('price'),dict):
        audit.update(price_status='unverified',price_metadata_input={
            k:str(v)[:120] for k,v in entry['price'].items() if k in {'currency','market','type','queried_at'}})
    return value,audit

def quality_notes(provenance):
    notes=[];groups=set();missing={}
    for p in provenance:
        if p.get('status') in {'unverified','not_disclosed','review_required'}:
            missing.setdefault((p['column'],p['status'],p.get('note','')),[]).append(p.get('label',''))
        if p.get('price_status')=='unverified':notes.append(p.get('label','')+' / '+p['column']+'：仅保留原文文本值，附带的价格币种、市场、类型和日期尚未通过结构化核验，不可据此进行价格比较。')
        if p.get('comparable_group'):groups.add(p['comparable_group'])
    for (column,status,note),labels in missing.items():notes.append('、'.join(dict.fromkeys(labels))+f' / {column}：'+('待人工复核' if status=='review_required' else STATUS[status])+f'。{note}')
    if len(groups)>1:notes.append('价格的市场、币种、价格类型或查询日期不同，不可直接排名、计算价差或宣称同口径比较；未进行汇率折算。')
    if any(p.get('price_type') and p['price_type']!='launch' for p in provenance):notes.append('已记录的商店价格不证明官方发布价；发布价需求仍需核实。')
    return list(dict.fromkeys(notes))
