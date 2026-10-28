"""Bind verbatim research excerpts to the *observed* page heading.

No semantic translation or URL/title guessing.  The browser's saved H1 and
rendered text are both required when available; other identifiers must match
exactly or be an unambiguous .0 patch-version alias.
"""
from __future__ import annotations
import re
import unicodedata
from urllib.parse import urlsplit
from .evidence_text import contains_observed_excerpt


def canon(text: str) -> str:
    text = unicodedata.normalize('NFKC', text)
    for source, target in [('’', "'"), ('‘', "'"), ('‐', '-'), ('‑', '-'), ('–', '-'), ('—', '-')]:
        text = text.replace(source, target)
    return ' '.join(text.split()).casefold()


def version_parts(value: str) -> tuple[int, ...] | None:
    versions = re.findall(r'(?<![\w.])\d+(?:\.\d+){1,3}(?![\w.])', value)
    if len(versions) != 1:
        return None
    return tuple(int(v) for v in versions[0].split('.'))


def same_version(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    # 3.12.0 and 3.12 denote the same zero-patch release family. Never
    # equate 3.12.1 with 3.12, or prefix-colliding 3.1 and 3.10.
    return left == right or (left + (0,) == right) or (right + (0,) == left)


def non_competing_h1s(headings) -> bool:
    """Allow unrelated section H1s; reject a competing same-family heading.

    A release page often has a section H1 such as "Major new features ..." in
    addition to the primary "Python 3.11.0".  A second "Python 3.12.0" is a
    competing owner and must not be accepted.
    """
    if not headings:
        return False
    primary = canon(headings[0])
    tokens = primary.split()
    if not tokens:
        return False
    family = tokens[0]
    return not any(
        canon(heading) != primary and canon(heading).split()[:1] == [family]
        for heading in headings[1:] if canon(heading)
    )


def site_scoped_owner(observed: str, label: str, url: str) -> bool:
    """Accept a comparison row such as "Python（python.org）" only when the
    label names that same site and the saved page title/H1 carries its name.

    This is for site-level comparison tables, not product/version attribution:
    the row is the site, the source URL must belong to that site, and the page
    identity must contain the label name.  It never accepts a different site.
    """
    if not all(isinstance(value, str) for value in (observed, label, url)):
        return False
    match = re.fullmatch(
        r'\s*(.+?)\s*[（(]\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})\s*[）)]\s*',
        label,
    )
    if match:
        name, domain = match.group(1).strip(), match.group(2).lower().strip('.')
    else:
        # A plain site label ("Python", "Node.js") is allowed only when the
        # source host itself carries the same site name.
        name, domain = label.strip(), ''
    if len(name) < 2:
        return False
    try:
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
    except ValueError:
        return False
    if not host:
        return False
    name_key = re.sub(r'[^a-z0-9一-鿿]', '', canon(name)
                      .replace('®', '').replace('™', ''))
    if len(name_key) < 2:
        return False
    if domain:
        if not (host == domain or host.endswith('.' + domain)):
            return False
    else:
        host_key = re.sub(r'[^a-z0-9一-鿿]', '', host)
        if name_key not in host_key:
            return False
    headings = re.findall(r'(?im)^- heading "([^"\n]{2,180})" \[level=1\]', observed)
    titles = re.findall(r'(?im)^- Page Title:?\s*(.+)$', observed)
    return any(name_key in re.sub(r'[^a-z0-9一-鿿]', '', canon(text)
                                 .replace('®', '').replace('™', ''))
               for text in headings + titles)


def owner_matches(label: str, observed_h1: str, supplied_h1: str | None) -> bool:
    if supplied_h1 is not None and canon(supplied_h1) != canon(observed_h1):
        return False
    target, row = canon(observed_h1), canon(label)
    if row == target:
        return True
    # Direct identity alias for a zero-patch release article, guarded by the
    # unique H1 and exact normalized subject prefix (no cross-product guessing).
    m = re.fullmatch(r"what(?:'s| is) new in (.+)", target)
    if m:
        subject=m.group(1)
        v1=version_parts(subject)
        v2=version_parts(row)
        source_name = re.sub(r'\s*\d+(?:\.\d+){1,3}$', '', subject).strip()
        row_name = re.sub(r'\s*\d+(?:\.\d+){1,3}$', '', row).strip()
        if v1 and v2 and same_version(v1,v2) and source_name and source_name==row_name:
            return True
    # Stable feature rows can be "Observed Heading - Feature".  If a version
    # appears in the suffix, it MUST match the H1, rather than merely sharing
    # a model prefix.  This also blocks cross-version article substitution.
    for separator in (' - ', ' | ', ' — '):
        if row.startswith(target + separator):
            suffix = row[len(target + separator):]
            heading_version = version_parts(observed_h1)
            suffix_version = version_parts(suffix)
            if suffix_version:
                return bool(heading_version and same_version(heading_version, suffix_version))
            # A suffix with multiple versions is ambiguous.
            if re.search(r'(?<![\w.])\d+(?:\.\d+){1,3}(?![\w.])', suffix):
                return False
            return bool(suffix.strip())
    # The H1 was read from the host's saved browser observation, not supplied
    # by the model. A model-supplied evidence_subject may additionally declare
    # the same title, but it is NOT needed to establish that trusted identity.
    # The alias is deliberately restricted to versioned What's-New articles.
    match = re.fullmatch(r"what(?:'s| is) new in (.+)", target)
    if not match:
        return False
    subject = match.group(1)
    observed_version = version_parts(subject)
    if not observed_version:
        return False
    for sep in (' - ', ' | '):
        if sep not in row:
            continue
        prefix, suffix = row.split(sep, 1)
        if not suffix.strip():
            return False
        # The feature suffix cannot silently claim another version on the
        # observed document, even when the owner prefix is correct.
        suffix_versions = re.findall(r'(?<![\w.])\d+(?:\.\d+){1,3}(?![\w.])', suffix)
        if len(suffix_versions) > 1 or any(
            not same_version(observed_version, tuple(int(x) for x in v.split('.')))
            for v in suffix_versions
        ):
            return False
        if prefix == subject:
            return True
        proposed = version_parts(prefix)
        if not proposed or not same_version(observed_version, proposed):
            continue
        # Require the complete subject name; no numeric-prefix or cross-brand
        # coincidence can establish ownership.
        source_name = re.sub(r'\s*\d+(?:\.\d+){1,3}$', '', subject).strip()
        row_name = re.sub(r'\s*\d+(?:\.\d+){1,3}$', '', prefix).strip()
        if source_name and source_name == row_name:
            return True
    return False

def observed_verbatim_owner(observed: str, label: str, quote: str, *, evidence_subject: str | None = None) -> tuple[bool, str]:
    """Verify a continuous quote is AFTER the saved document's actual H1.

    DOM H1 is authoritative when present. The one standalone line in the
    browser's rendered text also has to match, so a navigation link or
    unrelated snapshot cannot independently certify a fact.
    """
    if not isinstance(observed, str) or not isinstance(label, str) or not isinstance(quote, str):
        return False, '无效的网页原文、对象或摘录'
    if evidence_subject is not None and not isinstance(evidence_subject, str):
        return False, 'evidence_subject 必须是网页实际一级标题'
    # Snapshot is a secondary rendering of the SAME observed page, not a
    # separate source.  Require a single primary H1 rather than picking one.
    h1s = re.findall(r'(?im)^- heading "([^"\n]{3,180})" \[level=1\]', observed)
    if not h1s or sum(article_canon == canon(h1s[0]) for article_canon in map(canon, h1s)) != 1:
        return False, '无法从已保存网页确定唯一的正文一级标题'
    h1 = h1s[0]
    if not owner_matches(label, h1, evidence_subject):
        return False, '业务行对象与实际网页一级标题或版本不一致'
    rendered = observed.split('### Observed controls', 1)[0].split('### Page', 1)[0].split('### Snapshot', 1)[0]
    lines = rendered[:6000].splitlines(keepends=True)
    matches, offset = [], 0
    for line in lines:
        if canon(line.strip()) == canon(h1):
            matches.append(offset + len(line))
        offset += len(line)
    if len(matches) != 1:
        return False, '渲染正文未出现唯一且与实际H1一致的标题行'
    if not contains_observed_excerpt(rendered[matches[0]:], quote):
        return False, '来源摘录未出现在实际网页主标题之后的正文中'
    return True, ''


# Evidence-backed *article metadata*, not a free-form ownership heuristic.
# These roles are strictly extractive: the host-observed H1, body and
# (for sections) DOM heading all have to agree. No translation is certified.
def observed_article_scalar_owner(observed: str, label: str, column: str,
                                  value: str, quote: str,
                                  evidence_subject: str | None = None,
                                  context_quote: str | None = None) -> bool | None:
    """Return True/False for an applicable extractive article field; None otherwise.

    False means the claim MUST be reviewed; do not retry a weaker legacy
    subject guard. The scalar and exact source observation have already been
    checked by the caller.
    """
    if not all(isinstance(x, str) for x in (observed, label, column, value, quote)):
        return None
    key = canon(column)
    version_field = key in {'版本', '版本号', 'version', 'release version'}
    original_field = bool(re.fullmatch(
        r'(?:官方)?(?:英文)?原文|(?:original|verbatim|official)\s+(?:quote|excerpt|text)|direct\s+quotation',
        key, re.I))
    section_field = key in {'章节', '出处章节', '原文出处章节', 'section', 'source section', 'section heading', 'section title'}
    if not (version_field or original_field or section_field):
        return None
    h1s = re.findall(r'(?im)^- heading "([^"\n]{3,180})" \[level=1\]', observed)
    if not h1s:
        # Non-article pages keep their original conservative checker.
        return None
    if not non_competing_h1s(h1s) or not owner_matches(label, h1s[0], evidence_subject):
        return False
    # A section title is a structural claim; keep the single-H1 requirement so
    # a page with an unrelated second H1 cannot prove a section binding.
    if section_field and len(h1s) != 1:
        return False
    # Verifies a real continuous quote comes from the rendered body after
    # the one saved H1, excluding navigation-only matches.
    matches, _ = observed_verbatim_owner(
        observed, label, quote, evidence_subject=evidence_subject)
    if not matches:
        return False
    if original_field:
        # Scalar stage already checked normalized value is a literal span of
        # quote; this role only supplies the observed page-to-row association.
        from .scalar_contract import text_form
        return bool(value.strip() and text_form(value) in text_form(quote))
    if version_field:
        # Only self-described article version headers can establish version
        # metadata. 3.11.0 may label a page whose header is Python 3.11,
        # but the quoted value itself must still be literal extractive text.
        if canon(value) not in canon(quote):
            return False
        h1=canon(h1s[0])
        article=re.fullmatch(r"what(?:'s| is) new in (.+)",h1)
        if article:
            return canon(value)==article.group(1)
        # A plain versioned release heading ("Python 3.11.0") is also a valid
        # document identity. The value must match its version; if the value
        # carries a product name, that name must match the heading's name.
        value_version=version_parts(value)
        h1_version=version_parts(h1)
        if not (value_version and h1_version and same_version(value_version,h1_version)):
            return False
        value_name=re.sub(r'\s*\d+(?:\.\d+){1,3}$','',canon(value)).strip()
        h1_name=re.sub(r'\s*\d+(?:\.\d+){1,3}$','',h1).strip()
        return not value_name or value_name==h1_name
    # An article section is a verbatim section title, not a model summary.
    # Require a DOM heading (level 2/3), rather than a navigation link whose
    # title happens to occur in the rendering or the table of contents.
    if canon(value)!=canon(quote):
        return False
    headings=re.findall(r'(?im)^- heading "([^"\n]{2,180})" \[level=[23]\]',observed)
    if sum(canon(h)==canon(value) for h in headings)==1:
        return True
    # In long browser snapshots the semantic heading may be omitted from the
    # captured DOM tree, while a real body heading and its paragraph survive
    # in the rendered text. Do NOT trust a bare title or a TOC link: require
    # an independently verified continuous context beginning with the title
    # and followed immediately by substantial body prose.
    if not isinstance(context_quote, str) or not context_quote.strip():
        return False
    rendered = observed.split('### Observed controls', 1)[0].split('### Page', 1)[0].split('### Snapshot', 1)[0]
    if not contains_observed_excerpt(rendered, context_quote):
        return False
    raw = context_quote.strip()
    lines = raw.splitlines()
    if not lines or canon(lines[0].strip()) != canon(value):
        return False
    prose = '\n'.join(lines[1:]).strip()
    if (len(prose) < 80 or len(re.findall(r'[A-Za-z\u4e00-\u9fff]', prose)) < 60
            or not re.search(r'[.。;；:：]', prose)):
        return False
    body = rendered.splitlines()
    # Only one rendered standalone title can introduce the claim; repeated
    # nav/sidebar titles are ambiguous. The next non-empty rendered line must
    # begin the exact, source-observed prose, with no other heading intervening.
    matches = [idx for idx, line in enumerate(body) if canon(line.strip()) == canon(value)]
    if len(matches) != 1:
        return False
    title_idx = matches[0]
    subsequent = [line.strip() for line in body[title_idx+1:] if line.strip()]
    if not subsequent or not canon(prose).startswith(canon(subsequent[0])):
        return False
    # A source transcript with section titles only is not enough to certify
    # body ownership, and a mere navigation entry can never satisfy this rule.
    if len(subsequent[0]) < 60:
        return False
    return True
