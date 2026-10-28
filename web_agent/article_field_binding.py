"""Conservative field-to-section binding for a single-owner, observed release article.

A literal source quote does not establish an arbitrarily translated category.
We require the exact quoted statement to occur in the *rendered body* of a
uniquely headed versioned release article, under the explicit observed source
section for that category, or to carry an unambiguous self-describing cue.
No model-supplied title/URL or cross-document citation is trusted as proof.
"""
from __future__ import annotations
import re
from .observed_article import canon, non_competing_h1s, observed_verbatim_owner
from .scalar_contract import text_form

# Language/field vocabulary is deliberately bounded. Unknown field names fall
# through to the existing conservative checker; no general semantic guessing.
_SECTION_HEADINGS = {
    '语法新特性': ('new syntax features',),
    '解释器改进': ('interpreter improvements',),
    '标准库变化': ('new standard library modules', 'significant improvements in the standard library', 'standard library improvements'),
    '类型提示新特性': ('new typing features',),
    '重要弃用与移除': ('important deprecations, removals and restrictions', 'important deprecations, removals or restrictions', 'important deprecations and removals'),
}
_FIELD_ALIASES = {
    'new syntax features': '语法新特性', 'syntax features': '语法新特性',
    'interpreter improvements': '解释器改进',
    'standard library changes': '标准库变化', 'standard library improvements': '标准库变化',
    'typing features': '类型提示新特性', 'new typing features': '类型提示新特性',
    'deprecations and removals': '重要弃用与移除',
}
_SECTION_NAMES = {s for choices in _SECTION_HEADINGS.values() for s in choices}


def observed_release_field(workspace, source, label, column, quote, definition, audit):
    """Return True only for an independently anchored literal article field."""
    if not (isinstance(source, dict) and isinstance(label,str) and isinstance(column,str)
            and isinstance(quote,str) and isinstance(audit,dict)
            and definition.get('type')=='text' and definition.get('role') not in {'price','source_url'}):
        return False
    c = canon(column).strip('：:')
    if c in _FIELD_ALIASES:c = _FIELD_ALIASES[c]
    value = audit.get('normalized_value')
    if not isinstance(value,str) or not value.strip() or text_form(value) not in text_form(quote):
        return False
    if c not in _SECTION_HEADINGS and c not in {'发布日期','性能改进','release date','performance improvements'}:
        return False
    from .business_contract import source_text
    observed = source_text(workspace,source)
    if not observed:return False
    raw_input = audit.get('original_input') or {}
    supplied = raw_input.get('evidence_subject') if isinstance(raw_input,dict) else None
    ok,_ = observed_verbatim_owner(observed,label,quote,evidence_subject=supplied)
    if not ok:return False
    # Restrict to a versioned release article. The primary H1 may be just
    # "Python 3.11.0"; the browser's saved Page Title carries the release cue.
    from .observed_article import version_parts, same_version
    h1s = re.findall(r'(?im)^- heading "([^"\n]{3,180})" \[level=1\]', observed)
    if not h1s or not non_competing_h1s(h1s) or version_parts(h1s[0]) is None:
        return False
    page_titles = re.findall(r'(?im)^- Page Title:?\s*(.+)$', observed)
    page_title = page_titles[0] if page_titles else ''
    if not re.search(r'(?i)\b(?:new in|release|changes in)\b', h1s[0] + ' ' + page_title):
        return False
    h1_version = version_parts(h1s[0])
    title_version = version_parts(page_title)
    if title_version is not None and not same_version(h1_version, title_version):
        return False
    rendered=observed.split('### Observed controls',1)[0].split('### Page',1)[0].split('### Snapshot',1)[0]
    lines=rendered.splitlines()
    title_indices=[i for i,line in enumerate(lines) if canon(line.strip())==canon(h1s[0])]
    if len(title_indices)!=1:return False
    # Quote must match a distinct continuous original span, never a nav item.
    from .evidence_text import contains_observed_excerpt
    after='\n'.join(lines[title_indices[0]+1:])
    if not contains_observed_excerpt(after,quote):return False
    # A source passage may appear twice (once in highlights, again in the
    # detailed article). Accept only a span inside the correct section.
    pattern=r'\s+'.join(re.escape(chunk) for chunk in quote.split())
    spans=list(re.finditer(pattern,after))[:32]
    if not spans:return False
    if c in ('发布日期','release date'):
        from .delivery_schema import source_dates
        if not (re.search(r'(?i)\b(?:released on|release date)\b',quote) and source_dates(quote)):
            return False
        from .observed_article import same_version
        h1_version = version_parts(h1s[0])
        label_version = version_parts(label)
        if not (h1_version and label_version and same_version(h1_version,label_version)):
            return False
        quote_version = version_parts(quote)
        if quote_version is not None:
            # A self-identifying sentence such as
            # "Python 3.11 was released on ..." names its own version.
            return same_version(h1_version,quote_version)
        # A bare date has no version; it must be the unique Release date header
        # line between the primary H1 and the first Major-new-features section,
        # never a date inside the body or a later note.
        quote_line = next((i for i in range(title_indices[0]+1, len(lines))
                           if canon(quote) in canon(lines[i])), None)
        major_lines = [i for i in range(title_indices[0]+1, len(lines))
                       if re.match(r'^major new features of the\s+\d+(?:\.\d+)?\s+series\b',
                                   lines[i].strip(), re.I)]
        end = major_lines[0] if major_lines else len(lines)
        release_lines = [i for i in range(title_indices[0]+1, end)
                         if re.match(r'^release date\s*[:：]', lines[i].strip(), re.I)]
        return len(release_lines) == 1 and quote_line == release_lines[0]
    if c in ('性能改进','performance improvements'):
        return bool(re.search(r'(?i)\b(?:faster|speed\s*up|speedup|performance|latency|throughput)\b',quote))
    candidates=_SECTION_HEADINGS[c]
    for match in spans:
        preceding=after[:match.start()].splitlines()
        # A quote can itself contain the observed heading as its first line.
        first=quote.splitlines()[0] if '\n' in quote else ''
        if first and canon(first).strip(' :：-–—') in candidates:
            return True
        headings=[]
        for line in preceding:
            key=canon(line).strip(' :：-–—')
            if key in _SECTION_NAMES:headings.append(key)
        if headings and headings[-1] in candidates:
            return True
    return False
