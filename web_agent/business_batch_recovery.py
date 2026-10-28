"""Deterministic downgrade of invalid business cells; never invent source proof."""
from __future__ import annotations
import copy


def disclose_invalid_cells(sheets, issues):
    """Return (new_sheets, number_changed).

    Only exact cell positions reported by the strict validator are changed.
    Missing data are *not* verified. All other cells/definitions are retained.
    If a diagnostic cannot be mapped safely, it is not changed and the caller
    retains the original validation error (no silent bypass).
    """
    if not isinstance(sheets, list) or not isinstance(issues, list):
        return sheets, 0
    result = copy.deepcopy(sheets)
    changes = 0
    seen = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        name, number, column = issue.get('sheet'), issue.get('row'), issue.get('column')
        if not isinstance(name, str) or type(number) is not int or number < 2 or not isinstance(column, str):
            continue
        key = (name, number, column)
        if key in seen:
            continue
        seen.add(key)
        sheet = next((s for s in result if isinstance(s, dict) and s.get('name') == name
                      and s.get('mode') == 'comparison'), None)
        if not sheet or column not in sheet.get('columns', ()):
            continue
        rows = sheet.get('rows', [])
        index = number - 2
        if not isinstance(rows, list) or index >= len(rows):
            continue
        cells = rows[index].get('cells', {}) if isinstance(rows[index], dict) else {}
        if not isinstance(cells, dict) or column not in cells:
            continue
        previous = cells[column]
        if (isinstance(previous, dict) and previous.get('value') is None
                and previous.get('reason') and not previous.get('source_id')
                and not previous.get('quote')):
            continue
        message = str(issue.get('message') or '当前来源无法核实该字段')[:180]
        # No source_id or quote: a missing value does not carry fabricated proof.
        # Do not assert "official not disclosed" without explicit source evidence.
        cells[column] = {
            'value': None,
            'status': 'unverified',
            'reason': '自动保守披露：' + message,
        }
        changes += 1
    return result, changes
