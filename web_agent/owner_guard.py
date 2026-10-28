"""Source-observed subject identity guards (reviewed V6 + owner overlay)."""
from __future__ import annotations
import re
import unicodedata
from urllib.parse import urlsplit
from .business_contract import source_text
from .scalar_contract import text_form

_SPEC_HEADER = re.compile(r'^(.{1,120}?)\s*[-—–|]\s*(?:技术规格|产品规格|规格参数|技术参数|technical specifications?|specifications?|specs)\s*$',re.I)
_SPEC_TYPE = r'(?:技术规格|产品规格|规格参数|技术参数|technical specifications?|specifications?|specs)'

def _canon(s: str) -> str:
    # NFKC unifies non-breaking spaces, full width spaces, and letter forms.
    return ' '.join(unicodedata.normalize('NFKC',s).split()).casefold()

def field_has_explicit_anchor(column, quote) -> bool:
    """Require a quoted field/semantic label, not just a coincidental number.

    These are generic technology vocabulary synonyms, never product names.
    Unrecognized fields must contain their actual name in the quote; otherwise
    the strict existing checker decides whether they are reviewable.
    """
    phrase=_canon(quote)
    field=_canon(column)
    if field in phrase:
        return True
    alias_patterns=(
        (r'屏幕尺寸|screen.?size|display.?size', r'屏幕|显示屏|全面屏|oled|lcd|display'),
        (r'屏幕类型|display.?type', r'显示屏|屏幕|oled|lcd|display'),
        (r'芯片型号|chip.?model|processor.?model', r'芯片|处理器|chip|processor|soc'),
        (r'cpu|中央处理器', r'中央处理器|\bcpu\b|processor cores?|性能核心|能效核心'),
        (r'gpu|图形处理器', r'图形处理器|\bgpu\b|graphics processor'),
        (r'神经网络引擎|neural.?engine', r'神经网络引擎|neural engine|\bnpu\b'),
        ('屏幕分辨率|screen.?resolution', '分辨率|\\bresolution\\b'),
        ('像素密度|\\bppi\\b|pixel.?density', '\\bppi\\b|像素密度'),
        ('机身重量|重量|\\bweight\\b', '重量|\\bweight\\b'),
        ('视频播放续航|video.?playback', '视频播放|video.?playback'),
        ('数码变焦|digital.?zoom', '数码变焦|digital.?zoom'),
    )
    for family,anchor in alias_patterns:
        if re.search(family,field,re.I):
            return bool(re.search(anchor,phrase,re.I))
    return False

def document_subject_bound(workspace, source, label, column, quote) -> bool:
    if not isinstance(source,dict) or not isinstance(label,str) or not isinstance(quote,str):
        return False
    observed=source_text(workspace,source)
    if not observed or not quote.strip() or len(label)>120 or not field_has_explicit_anchor(column,quote):
        return False
    # A dedicated specs document places its unique own-specs header at the top;
    # a catalog containing multiple specs headers is not a single-object page.
    first=observed[:2500]
    headings=[]
    for match in re.finditer(r'(?m)^([^\n]{1,160})\s*$',first):
        line=match.group(1).strip()
        header=_SPEC_HEADER.fullmatch(line)
        if header:
            headings.append((match.start(),_canon(header.group(1))))
    if len(headings)!=1 or headings[0][1]!=_canon(label):
        return False
    # Only evidence inside the actual saved observation following that header
    # can be attributed to that document. The model-supplied source title
    # alone is never proof.
    quote_regex=re.compile(r'\s+'.join(re.escape(part) for part in quote.split()))
    return any(m.start()>headings[0][0] for m in quote_regex.finditer(observed))

def wrap_original(guard):
    def require_subject_evidence(workspace, source, label, column, quote, definition, audit):
        if document_subject_bound(workspace,source,label,column,quote):
            return
        return guard(workspace,source,label,column,quote,definition,audit)
    return require_subject_evidence

def canonical(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()

def owner_matches(label, page_owner, source):
    """Match an observed page owner, optionally removing a *grounded* maker prefix."""
    label = canonical(label)
    owner = canonical(page_owner)
    if not owner or not label:
        return False
    if label == owner:
        return True
    # A display label may append an explicitly regional qualifier. Accept that
    # qualifier ONLY when its country-code suffix matches this observation URL.
    # Never drop product variants, prefixes, dates or free-form parentheses.
    region = re.fullmatch(re.escape(owner) + r'\s*\(([^()]{2,20})\)', label)
    if region and isinstance(source, dict):
        hostname = (urlsplit(source.get('url', '')).hostname or '').lower().rstrip('.')
        suffix_by_region = {
            '中国大陆': '.cn', '中国': '.cn', '日本': '.jp',
            '英国': '.uk', '德国': '.de', '法国': '.fr',
            '加拿大': '.ca', '澳大利亚': '.au', '新加坡': '.sg',
        }
        required_suffix = suffix_by_region.get(region.group(1))
        if required_suffix and hostname.endswith(required_suffix):
            return True
    if not label.endswith(' ' + owner):
        return False
    maker = label[:-(len(owner)+1)]
    host = (urlsplit(source.get('url', '')).hostname or '').casefold().split('.')
    # Never blindly strip a prefix.  A maker may be omitted only when the
    # observed URL's hostname independently identifies the maker.
    return bool(re.fullmatch(r'[a-z][a-z0-9-]{1,30}', maker) and maker in host)

def live_owner(document, label, source):
    """A unique saved specs heading or selected tabpanel proves ownership."""
    # Browser snapshot: a tab *is not* selection evidence; its visible tabpanel is.
    panels = [canonical(x) for x in re.findall(r'(?m)^- tabpanel "([^"\n]{1,120})"\s+\[ref=[^\n]+?\]:', document)]
    if panels:
        return len(panels) == 1 and owner_matches(label, panels[0], source)

    # An ordinary dedicated specifications document is also sufficient.
    first = document[:2500]
    owners = []
    for line in first.splitlines():
        line = line.strip()
        match = re.fullmatch(rf'(.{{2,100}}?)\s*(?:[-—–|]\s*)?{_SPEC_TYPE}', line, re.I)
        if match:
            owners.append(canonical(match[1].strip(' -—–|')))
    return len(set(owners)) == 1 and bool(owners) and owner_matches(label, owners[0], source)

def model_identity_value(label, quote, column, definition, audit):
    """A model-name field is its own identifier when its name is extractive.

    This is *not* applied to product attributes, prices, generic descriptions,
    or arbitrary row labels.  Earlier scalar checks establish exact value/quote.
    """
    if definition.get('type') != 'text':
        return False
    # Self-identifying model cells are appropriate only when the row is an
    # equipment/category grouping, not a specific named product.
    if not re.fullmatch(r'(?:[A-Z]{2,6}|[\u4e00-\u9fff]{2,6})', label.strip()):
        return False
    if not re.search(r'型号|\bmodel\b|\bmodel name\b', column, re.I):
        return False
    value = audit.get('normalized_value')
    if not isinstance(value, str) or len(value.strip()) < 6:
        return False
    original = canonical(text_form(quote))
    value = canonical(text_form(value))
    # The value must name the product at the START of a verbatim quoted item,
    # not appear inside another product's description or an unrelated note.
    if not original.startswith(value):
        return False
    remainder = original[len(value):]
    # Prevent prefix collisions such as model 17 vs 17 Pro.  Legitimate quotes
    # may continue with device specifications; category attribution stays a
    # presentation claim, NOT a verified fact inferred from this function.
    if remainder and remainder[0].isalnum():
        return False
    # A short model token followed by a different commercial variant is not
    # the same model: e.g., X9 is not X9 Pro.  Keep regular specification
    # continuations (socket, chipset, units) eligible for extraction.
    next_word = remainder.strip(' \t\n-—–:').split(' ', 1)[0].casefold()
    if next_word in {'pro', 'plus', 'ultra', 'max', 'mini', 'lite', 'fe', 'se'}:
        return False
    return True

_COMMERCIAL_VARIANTS={'pro','plus','ultra','max','mini','lite','fe','se'}


def conflicting_named_variant(label, quote):
    """Recognize an explicitly quoted conflicting commercial model suffix.

    Does not infer product identity from a source title and is deliberately
    limited to unambiguous full row-model tokens in the submitted quote.
    """
    if not isinstance(label,str) or not isinstance(quote,str):return False
    row=canonical(label)
    tokens=row.split()
    if len(tokens)<2:return False
    base=tokens[:]
    while base and base[-1] in _COMMERCIAL_VARIANTS:
        base.pop()
    if not base:return False
    stem=' '.join(base)
    expected=' '.join(tokens[len(base):])
    suffix=r'(?:pro|plus|ultra|max|mini|lite|fe|se)'
    pattern=r'(?<![\w])'+re.escape(stem)+r'(?P<suffix>(?:\s+'+suffix+r'){0,3})(?![\w])'
    for match in re.finditer(pattern,canonical(quote)):
        quoted_suffix=' '.join(match.group('suffix').split())
        if quoted_suffix!=expected:return True
    return False


def dom_matrix_subject_bound(workspace,source,label,column,quote,definition,audit):
    """One actual DOM table cell plus its column header, never flat-page proximity."""
    if not isinstance(quote,str) or not isinstance(label,str) or not isinstance(column,str):return False
    from .dom_matrix import bound_rows
    from .evidence_text import contains_observed_excerpt
    for row in bound_rows(workspace,source):
        if not owner_matches(label,row['owner'],source):continue
        if not field_has_explicit_anchor(column,quote):continue
        # The quote must be inside ONE observed cell, not a stitched row or
        # the surrounding page containing competing products' values.
        if not contains_observed_excerpt(row['value'],quote):continue
        if row['field'] and not field_has_explicit_anchor(column,row['field']+' '+row['value']):continue
        return True
    return False


def release_document_subject_bound(workspace, source, label, column, quote, definition, audit):
    """Conservatively bind English release-page lines to translated column labels.

    The evidence is the SAVED rendered page and its primary observed H1, never a
    model-supplied source title or guessed URL. Supports a bounded set of
    semantic release fields; arbitrary translated fields remain reviewable.
    """
    from .business_contract import primary_subject_quote
    if not all(isinstance(v, str) for v in (label, column, quote)):
        return False
    value = audit.get('normalized_value') if isinstance(audit, dict) else None
    if (definition.get('type') != 'text' or not isinstance(value, str)
            or not 4 <= len(quote.strip()) <= 200 or '\n' in quote.strip()
            or len(canonical(value)) < 10 or canonical(value) not in canonical(quote)):
        return False
    version = re.search(r'(\d+(?:\.\d+){1,3})$', label.strip())
    if not version:
        return False
    series = '.'.join(version.group(1).split('.')[:2])
    # Strong version-specific ownership: exact saved H1 and unique owner line.
    if not primary_subject_quote(workspace, source, label, quote, max_distance=15000):
        return False
    original = source_text(workspace, source)
    rendered = original.split('### Observed controls', 1)[0].split('### Page', 1)[0]
    rendered_lines = rendered.splitlines()
    owner_lines = [i for i, line in enumerate(rendered_lines) if canonical(line) == canonical(label)]
    if len(owner_lines) != 1:
        return False
    start = owner_lines[0]
    # Only a *complete observed line* (not a phrase inside another fact).
    positions = [i for i, line in enumerate(rendered_lines[start+1:], start+1)
                 if canonical(quote) in canonical(line)]
    if len(positions) != 1:
        return False
    at = positions[0]
    release_major = re.compile(r'^major new features of the\s+(\d+(?:\.\d+)?)\s+series\b', re.I)
    majors = [(i, match.group(1)) for i, line in enumerate(rendered_lines)
              if (match := release_major.match(line.strip()))]
    own = [i for i, v in majors if v == series and i > start]
    if len(own) != 1:
        return False
    major_at = own[0]
    next_major = min([i for i, _ in majors if i > major_at] or [len(rendered_lines)])
    heading = rendered_lines[at].strip()
    field = canonical(column)
    is_field = lambda *names: field in {canonical(name) for name in names}

    if is_field('发布日期', '正式发布日期', 'release date'):
        # The release date belongs to the top header, not a date inside changes.
        first_dates = [i for i in range(start+1, major_at)
                       if re.match(r'^release date\s*[:：]', rendered_lines[i].strip(), re.I)]
        return len(first_dates) == 1 and first_dates[0] == at
    if is_field('版本状态', '发布状态', 'release status'):
        return at < major_at and canonical(heading) == canonical(
            'This is the stable release of ' + label)
    if is_field('对比基线', 'comparison baseline'):
        return (at == major_at and bool(re.match(
            r'^major new features of the\s+' + re.escape(series)
            + r'\s+series\s*,?\s*compared to\s+\d+(?:\.\d+)?\s*$',
            heading, re.I)))
    end_sections = [i for i in range(major_at+1, next_major)
                    if re.fullmatch(r'(?:more resources|files|full changelog)',
                                    rendered_lines[i].strip(), re.I)]
    if not major_at < at < min(end_sections or [next_major]):
        return False
    if is_field('主要新特性（官方英文原文）', '主要新特性', 'major feature', 'major new feature'):
        # A removal item can mention PEP, but does not become a "new feature".
        end_feature = [i for i in range(major_at+1, next_major)
                       if re.fullmatch(r'(?:type annotations|typing and typing language changes|deprecations?)',
                                       rendered_lines[i].strip(), re.I)]
        if end_feature and at >= min(end_feature):
            return False
        # Keep actual feature lines, not generic introduction or source summaries.
        return bool(re.search(r'\bPEP\s*\d+\b|\b(?:support|improv|parsing|new|faster|typing)\w*\b',
                              heading, re.I))
    if is_field('性能说明（官方英文原文）', '性能说明', 'performance'):
        return bool(re.search(r'\b(?:performance|faster|speedup|speed-up)\b', heading, re.I))
    if is_field('类型/注解相关（官方英文原文）', '类型/注解相关', 'type annotations'):
        types = [i for i in range(major_at+1, at) if re.match(
            r'^(?:type annotations|typing and typing language changes)\s*$',
            rendered_lines[i].strip(), re.I)]
        if not types:
            return False
        end_types = [i for i in range(types[-1]+1, next_major)
                     if re.fullmatch(r'(?:deprecations?|more resources)',
                                     rendered_lines[i].strip(), re.I)]
        return at < min(end_types or [next_major]) and bool(re.search(
            r'\b(?:PEP\s*\d+|type annotation|typing|generics?)\b', heading, re.I))
    if is_field('移除与弃用（官方英文原文）', '移除与弃用', 'deprecations'):
        sections = [i for i in range(major_at+1, at) if re.fullmatch(
            r'(?:deprecations?|removals?|removed features?)',
            rendered_lines[i].strip(), re.I)]
        return bool(sections) and bool(re.search(r'\b(?:deprecated|removed|removal)\b',
                                                heading, re.I))
    return False


def wrap_owner_evidence(previous):
    def check(workspace, source, label, column, quote, definition, audit):
        if conflicting_named_variant(label,quote):
            from .business_excel import SubjectEvidenceMismatch
            raise SubjectEvidenceMismatch('摘录明确指向另一产品型号/变体，不能作为当前行证据')
        if source and dom_matrix_subject_bound(workspace,source,label,column,quote,definition,audit):
            return
        if source and release_document_subject_bound(workspace,source,label,column,quote,definition,audit):
            return
        original = source_text(workspace, source) if source else ''
        if original and isinstance(quote, str):
            # Only the currently rendered page content, never a secondary
            # search-result item, may be attributed to an active tabpanel.
            rendered = original.split('### Snapshot', 1)[0]
            quote_pattern = re.compile(r'\s+'.join(re.escape(part) for part in quote.split()))
            if (quote_pattern.search(original) is not None
                    and field_has_explicit_anchor(column, quote)
                    and live_owner(original, label, source)
                    and (not re.search(r'(?m)^- tabpanel ', original) or quote in rendered)):
                return
            if model_identity_value(label, quote, column, definition, audit):
                return
        return previous(workspace, source, label, column, quote, definition, audit)
    return check
