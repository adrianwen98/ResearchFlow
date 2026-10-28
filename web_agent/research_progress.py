"""Progress-sensitive tool gates for evidence-backed business research.

Pure decisions only: never synthesize a source, cell or spreadsheet.
"""

READ_ONLY = frozenset({'inspect_saved_pages', 'list_recorded_sources'})
STAGING_FIRST = frozenset({
    'save_business_rows', 'list_recorded_sources', 'inspect_saved_pages',
    'record_source_from_observation', 'inspect_downloaded_table',
    'aggregate_downloaded_table',
})


def local_read_streak(journal):
    """Consecutive terminal read-only calls since any other tool, replay neutral."""
    if not isinstance(journal, dict):
        return 0
    count = 0
    for entry in reversed(list(journal.values())):
        if not isinstance(entry, dict) or entry.get('name') not in READ_ONLY:
            break
        count += 1
    return count


def needs_source_capture(journal, *, business_required, has_unrecorded_observations):
    """Limit research-without-recording to avoid burning the model budget."""
    return (bool(business_required) and bool(has_unrecorded_observations) and local_read_streak(journal) >= 4)


def staging_tool_names(previous_staging_turns):
    """One inspection opportunity, then offer only verified-row staging.

    Without sufficient proof, the model may decline tool use; the host must
    disclose incompletion rather than fabricate data.
    """
    return STAGING_FIRST if previous_staging_turns < 1 else frozenset({'save_business_rows'})
