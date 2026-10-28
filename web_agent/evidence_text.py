"""Literal observation matching, tolerant of presentation-only whitespace.

This answers only 'was this continuous text observed on this URL?'.  It does NOT
certify the association of a numerical value with a product: business field,
unit, and owner verification must remain independent checks.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

_WHITESPACE = re.compile(r'\s+')
_CJK = r'\u3400-\u9fff'
_LAYOUT_BOUNDARIES = (
    # Chinese layouts frequently introduce/remove separators between Han text
    # and a product code, a number or another Han character.
    re.compile(fr'(?<=[{_CJK}]) (?=[{_CJK}a-zA-Z0-9¥￥$€£])'),
    re.compile(fr'(?<=[a-zA-Z0-9¥￥$€£]) (?=[{_CJK}])'),
    # Price/currency and label punctuation are often separate HTML nodes.
    re.compile(r'(?<=[¥￥$€£]) (?=[0-9])'),
    re.compile(r'(?<=[0-9a-zA-Z]) (?=[¥￥$€£])'),
    re.compile(fr'(?<=[：:]) (?=[{_CJK}0-9])'),
)


def _canonical(value: str, *, layout: bool) -> str:
    clean = _WHITESPACE.sub(' ', unicodedata.normalize('NFC', value)).strip()
    if layout:
        for pattern in _LAYOUT_BOUNDARIES:
            clean = pattern.sub('', clean)
    return clean


def _has_bounded_substring(haystack: str, needle: str) -> bool:
    """Reject a quote that slices through an ASCII ID or a number token."""
    start = 0
    while True:
        offset = haystack.find(needle, start)
        if offset < 0:
            return False
        end = offset + len(needle)
        before = haystack[offset - 1] if offset else ''
        after = haystack[end] if end < len(haystack) else ''
        left_ok = not (
            before and (
                (needle[0].isascii() and needle[0].isalnum() and before.isascii() and before.isalnum())
                or (needle[0].isdigit() and before in ',.')
            )
        )
        right_ok = not (
            after and (
                (needle[-1].isascii() and needle[-1].isalnum() and after.isascii() and after.isalnum())
                or (
                        needle[-1].isdigit()
                        and after in ',.'
                        and haystack[end + 1:end + 2].isdigit()
                )
            )
        )
        if left_ok and right_ok:
            return True
        start = offset + 1


# Every whitespace character Python's \s recognises, which is also what
# _canonical() collapses.  The 1:1 translation below preserves length, so
# offsets found in the translated copy remain valid in the original text.
_LAYOUT_SPACES = (
    '\t\n\r\f\v \u0085  '
    '           '
    '    　'
)
_LAYOUT_SPACE_TRANSLATION = str.maketrans({ch: ' ' for ch in _LAYOUT_SPACES})
_LAYOUT_SPACE_SEARCH = re.compile('[' + re.escape(_LAYOUT_SPACES) + ']')


@dataclass(frozen=True)
class ObservedSpan:
    """Location of one excerpt in the ORIGINAL observation text."""

    start: int
    end: int
    strategy: str  # 'exact' | 'space-translation' | 'layout'


def normalize_layout_spaces(value: str) -> str:
    """Map every Unicode whitespace character to U+0020, one-for-one.

    Length is preserved, so offsets in the result are valid in ``value``.
    """
    return value.translate(_LAYOUT_SPACE_TRANSLATION)


def _space_is_removable(left: str, right: str) -> bool:
    """Would _canonical(..., layout=True) delete a space between these chars?

    Every current _LAYOUT_BOUNDARIES pattern matches exactly one space with
    zero-width lookarounds; keep that invariant if patterns are added.
    """
    if not left or not right:
        return False
    sample = left + ' ' + right
    return any(pattern.search(sample) for pattern in _LAYOUT_BOUNDARIES)


@lru_cache(maxsize=512)
def _layout_regex_source(canonical_quote: str) -> str:
    """Build a raw-text regex equivalent to the two canonical passes.

    - A space retained by canonical layout is required: \\s+
    - Adjacent characters at a layout boundary may have had a space inserted
      or removed by canonical layout: \\s*
    - Every other adjacency is literal: no whitespace may be inserted, so
      ASCII word boundaries are never merged.
    """
    parts = []
    previous = ''
    for char in canonical_quote:
        if char == ' ':
            parts.append(r'\s+')
        else:
            if previous and _space_is_removable(previous, char):
                parts.append(r'\s*')
            parts.append(re.escape(char))
        previous = char
    return ''.join(parts)


def find_observed_span(
    observed_text: object, quote: object, *, ignore_case: bool = True
) -> ObservedSpan | None:
    """Find one literal excerpt, tolerating presentation-only whitespace.

    Three stages, in order:
      1. raw literal (the pre-existing behaviour)
      2. one-for-one Unicode whitespace translation (NBSP U+00A0, U+202F,
         U+2009, U+3000, tabs/newlines), offsets unchanged
      3. canonical whitespace-run + CJK/currency layout tolerance

    Paraphrases, changed digits and skipped text still fail.  No offset map is
    needed because the translated copy is length-preserving and the fallback
    regex runs directly on the original text.
    """
    if not isinstance(observed_text, str) or not isinstance(quote, str):
        return None
    needle = quote.strip()
    if not needle:
        return None
    flags = re.IGNORECASE if ignore_case else 0

    match = re.search(re.escape(needle), observed_text, flags)
    if match:
        return ObservedSpan(match.start(), match.end(), 'exact')

    if _LAYOUT_SPACE_SEARCH.search(observed_text) or _LAYOUT_SPACE_SEARCH.search(needle):
        translated = normalize_layout_spaces(observed_text)
        translated_needle = normalize_layout_spaces(needle)
        match = re.search(re.escape(translated_needle), translated, flags)
        if match:  # translated has len(observed_text)
            return ObservedSpan(match.start(), match.end(), 'space-translation')

    canonical_quote = _canonical(needle, layout=True)
    if canonical_quote:
        pattern = re.compile(_layout_regex_source(canonical_quote), flags)
        match = pattern.search(observed_text)
        if match:
            return ObservedSpan(match.start(), match.end(), 'layout')
    return None


def contains_observed_excerpt(observed_text: object, quote: object) -> bool:
    """Require one continuous excerpt, allowing whitespace-only rendering shifts.

    No paraphrases, changed digits, removed intervening text or different URL
    can be approved here.  URL and observation-id binding stays in Workspace.
    """
    if not isinstance(observed_text, str) or not isinstance(quote, str) or not quote.strip():
        return False
    verbatim = _canonical(quote, layout=False)
    observed = _canonical(observed_text, layout=False)
    if _has_bounded_substring(observed, verbatim):
        return True
    # Second, narrower tolerance: spacing at CJK/currency/layout boundaries.
    return _has_bounded_substring(_canonical(observed_text, layout=True), _canonical(quote, layout=True))
