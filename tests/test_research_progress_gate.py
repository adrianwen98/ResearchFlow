import importlib
import json
from pathlib import Path

from web_agent.research_progress import local_read_streak, needs_source_capture, staging_tool_names


def j(names):
    return {str(i): {'name': name, 'output':'ok'} for i,name in enumerate(names)}


def test_initial_inspections_do_not_force_source():
    assert not needs_source_capture(j(['inspect_saved_pages']*3), business_required=True,has_unrecorded_observations=True)


def test_four_saved_reads_force_source():
    assert needs_source_capture(j(['browser_navigate']+['inspect_saved_pages']*4),business_required=True,has_unrecorded_observations=True)


def test_ordinary_research_not_forced():
    assert not needs_source_capture(j(['inspect_saved_pages']*12),business_required=False,has_unrecorded_observations=True)


def test_no_observation_never_forces_source():
    assert not needs_source_capture(j(['inspect_saved_pages']*12),business_required=True,has_unrecorded_observations=False)


def test_all_observations_already_recorded_disables_gate():
    assert not needs_source_capture(j(['inspect_saved_pages']*9), business_required=True,has_unrecorded_observations=False)

def test_one_source_but_unrecorded_observation_triggers_gate():
    assert needs_source_capture(j(['inspect_saved_pages']*9), business_required=True,has_unrecorded_observations=True)


def test_progress_resets_inspection_streak():
    assert local_read_streak(j(['inspect_saved_pages']*7+['record_source'])) == 0
    assert local_read_streak(j(['inspect_saved_pages']*7+['save_business_rows','inspect_saved_pages'])) == 1


def test_source_checkpoint_staging_initially_inspects():
    allowed=staging_tool_names(0)
    assert 'inspect_saved_pages' in allowed and 'save_business_rows' in allowed


def test_subsequent_staging_turns_only_accept_staging():
    assert staging_tool_names(1) == frozenset({'save_business_rows'})
    assert staging_tool_names(2) == frozenset({'save_business_rows'})


def test_replay_read_streak_is_stable():
    original=j(['browser_navigate']+['inspect_saved_pages']*6)
    assert local_read_streak(original)==6
    assert local_read_streak(dict(original))==6


def test_graph_dispatch_enforces_stage_and_source_gates():
    graph=(Path(__file__).resolve().parents[1]/'web_agent'/'graph.py').read_text(encoding='utf-8')
    assert "source_capture_required" in graph
    assert "source_capture_gate" in graph

    assert "_capture_reinspection_allowed" in graph
    assert "targeted_lookup" in graph
    assert 'name == "inspect_saved_pages"' in graph

    assert 'name != "record_source_from_observation"' in graph
    assert "and not targeted_lookup" in graph

    assert "state.get('business_stage_calls',0)>=2" in graph
    assert "'source_capture_gate':source_capture_gate" in graph
