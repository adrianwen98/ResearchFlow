"""Programmatic checkpoint for business-data staging, independent of the LLM prompt.

Do not create a workbook from source summaries: a non-empty staged, validated
business sheet (or a real compiled workbook) is required for deterministic export.
"""
from __future__ import annotations

from .persistence import read_json

import re

STAGING_TOOLS = frozenset({
    'save_business_rows', 'list_recorded_sources', 'inspect_saved_pages',
    'record_source_from_observation', 'inspect_downloaded_table',
    'aggregate_downloaded_table',
})


def requires_business_excel(workspace) -> bool:
    requirements = read_json(workspace.path / 'requirements.json', [])
    return (any(isinstance(item, dict) and item.get('artifact') == 'task_result.xlsx'
                for item in requirements)
            or (workspace.path / 'business_rows.json').is_file()
            or (workspace.path / 'business_draft.json').is_file()
            or (workspace.path / 'business_artifact.json').is_file())


def has_business_material(workspace) -> bool:
    staged = read_json(workspace.path / 'business_rows.json', {})
    draft = read_json(workspace.path / 'business_draft.json', {})
    return (bool(staged.get('sheets')) or bool(draft.get('sheets'))
            or (workspace.path / 'task_result.xlsx').is_file())


def needs_stage_checkpoint(workspace, used: int, maximum: int, attempts: int) -> bool:
    """Gate a bounded number of model turns after some actual sources exist.

    This is *not* a claim that data can be extracted from every source. The
    remaining budget stays available if the bounded staging attempts fail.
    """
    if not requires_business_excel(workspace) or has_business_material(workspace):
        return False
    if maximum < 8 or used >= maximum or attempts >= 3:
        return False
    if used < max(4, min(8, maximum // 4)):
        return False
    observation_ids = set()

    for source in workspace.sources():
        oid = source.get('observation_id')

        if not isinstance(oid, str) or not re.fullmatch(r'[a-f0-9]{20}', oid):
            continue

        try:
            if workspace.evidence_for(source['url'], source['quote'], oid) == oid:
                observation_ids.add(oid)
        except (KeyError, TypeError, ValueError):
            continue

        if len(observation_ids) >= 2:
            return True

    return False
