"""Choose observation-only recovery only when a real saved observation exists."""
from __future__ import annotations
import json
import re


def needs_source_recovery(journal):
    """Restrict exactly the next turn after a recoverable mismatched quote.

    A URL without saved observations cannot be fixed by local retrieval alone,
    and must not be trapped in an observation-only tool subset.
    """
    if not isinstance(journal, dict) or not journal:
        return False
    latest = next(reversed(journal.values()))
    if not (isinstance(latest, dict)
            and latest.get('name') == 'record_source'
            and latest.get('error_code') == 'evidence_mismatch'
            and str(latest.get('output', '')).startswith('ERROR: ')):
        return False
    try:
        error = json.loads(latest['output'][7:])
        diagnostics = error.get('diagnostics') or {}
        ids = diagnostics.get('related_observations', [])
        return (diagnostics.get('kind') == 'source_quote_recovery'
                and isinstance(ids, list)
                and any(isinstance(oid, str) and re.fullmatch(r'[a-f0-9]{20}', oid) for oid in ids))
    except (ValueError, AttributeError, TypeError):
        return False
