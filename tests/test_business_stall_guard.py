"""Offline regression for issue-level progress, independent of tool-call arguments."""
import json
from web_agent.harness import stop_reason


def failed(cells, name='save_business_rows', code='fact_validation', omitted=0):
    issues=[{'sheet':'Compare', 'row':row,'column':col, 'diagnostic':{'code':code}}
            for row,col in cells]
    return {'name':name, 'error_code':'business_validation',
            'output':'ERROR: '+json.dumps({'diagnostics':{
                'issues_total':len(issues)+omitted,
                'issues_omitted':omitted,
                'issues':issues}},ensure_ascii=False)}


def okay(name):
    return {'name':name,'error_code':'','output':'successful'}


def locs(n, row=2):
    return [(row, f'field{i}') for i in range(n)]


def test_interleaved_browser_does_not_clear_same_business_failure():
    journal={
        'a':failed(locs(4)),
        'nav1':okay('browser_navigate'),
        'b':failed(locs(4)),
        'nav2':okay('browser_navigate'),
    }
    assert stop_reason(journal) is None
    journal['c']=failed(locs(4))
    result=stop_reason(journal)
    assert result and result[0]=='stalled'


def test_changed_args_and_calls_still_cannot_hide_unchanged_error_cells():
    journal={str(i):failed(locs(4)) for i in range(2)}
    assert stop_reason(journal) is None
    journal['third']=failed(locs(4))
    assert stop_reason(journal)[0]=='stalled'


def test_real_shrinking_issues_get_another_attempt():
    journal={'a':failed(locs(4)), 'b':failed(locs(4)), 'c':failed(locs(2))}
    assert stop_reason(journal) is None
    journal['d']=failed(locs(1))
    assert stop_reason(journal) is None


def test_unimproved_after_previous_progress_stops():
    journal={'a':failed(locs(4)), 'b':failed(locs(2)), 'c':failed(locs(2))}
    assert stop_reason(journal)[0]=='stalled'


def test_three_shrinking_attempts_stay_allowed():
    journal={'a':failed(locs(12)), 'b':failed(locs(3)), 'c':failed(locs(2))}
    assert stop_reason(journal) is None


def test_regressed_last_attempt_stops():
    journal={'a':failed(locs(12)), 'b':failed(locs(2)), 'c':failed(locs(3))}
    assert stop_reason(journal)[0]=='stalled'


def test_unrelated_new_worksheet_rows_are_not_a_stalled_correction():
    journal={'a':failed(locs(4,row=2)), 'b':failed(locs(4,row=2)),
             'c':failed(locs(4,row=3))}
    assert stop_reason(journal) is None


def test_successful_business_save_starts_new_correction_sequence():
    journal={'a':failed(locs(4)), 'b':failed(locs(4)),
             'saved':okay('save_business_rows'), 'c':failed(locs(4))}
    assert stop_reason(journal) is None


def test_export_and_repair_share_same_issue_level_loop_guard():
    journal={'a':failed(locs(4),'save_business_rows'),
             'b':failed(locs(4),'export_business_excel'),
             'c':failed(locs(4),'repair_business_excel')}
    assert stop_reason(journal)[0]=='stalled'


def test_omitted_diagnostics_trigger_conservative_stall_after_three():
    journal={str(i):failed(locs(4),omitted=1) for i in range(2)}
    assert stop_reason(journal) is None
    journal['third']=failed(locs(3),omitted=1)
    assert stop_reason(journal)[0]=='stalled'


def test_incomplete_diagnostics_with_real_last_round_progress_continue():
    journal={'a':failed(locs(5),omitted=1),
             'b':failed(locs(4)), 'c':failed(locs(2))}
    assert stop_reason(journal) is None


def test_missing_diagnostics_cannot_hide_three_failed_attempts():
    journal={str(i):{'name':'save_business_rows','error_code':'business_validation',
                     'output':'ERROR: '+json.dumps({'code':'business_validation'})}
             for i in range(3)}
    assert stop_reason(journal)[0]=='stalled'


def test_different_error_code_is_not_the_same_problem():
    journal={'a':failed(locs(4)), 'b':failed(locs(4)),
             'c':failed(locs(4),code='text_not_extractive')}
    assert stop_reason(journal) is None


def test_existing_eight_continuous_failures_fallback_still_operates():
    journal={str(i):failed(locs(3),omitted=1) for i in range(8)}
    assert stop_reason(journal)[0]=='stalled'
