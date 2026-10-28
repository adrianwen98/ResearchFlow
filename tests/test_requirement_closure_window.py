"""The bounded closure window must be exhausted before a partial result."""
from web_agent.requirement_closure import CLOSURE_MAX_TURNS, closure_should_continue


def test_continues_while_closure_window_has_turns_and_missing_requirements():
    state = {'requirement_closure': True, 'requirement_closure_turns': 3}
    assert closure_should_continue(state, {'missing_requirements': ['r3', 'r4']})


def test_stops_after_the_last_closure_turn():
    state = {'requirement_closure': True, 'requirement_closure_turns': CLOSURE_MAX_TURNS}
    assert not closure_should_continue(state, {'missing_requirements': ['r3']})


def test_stops_when_no_requirement_is_missing_or_gate_is_inactive():
    assert not closure_should_continue(
        {'requirement_closure': True, 'requirement_closure_turns': 1},
        {'missing_requirements': []})
    assert not closure_should_continue(
        {'requirement_closure': False, 'requirement_closure_turns': 1},
        {'missing_requirements': ['r3']})
