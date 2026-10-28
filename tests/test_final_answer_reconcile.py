"""A stale partial answer is corrected after artifact reconciliation."""
from web_agent.completion import reconcile_final_answer

OLD = ('已生成部分交付，尚未满足需求：r3,r4\n'
       '已生成 Excel 与 Markdown 报告，来源已与实际工具观察核对。')


def test_stale_partial_prefix_is_removed_when_all_requirements_pass():
    answer = reconcile_final_answer(OLD, 'needs_review', {'missing_requirements': []})
    assert answer.startswith('交付已生成，等待你核对。')
    assert '尚未满足需求' not in answer
    assert '已生成 Excel' in answer


def test_partial_answer_is_kept_while_requirements_are_actually_missing():
    answer = reconcile_final_answer(OLD, 'partial', {'missing_requirements': ['r3', 'r4']})
    assert answer == OLD


def test_non_partial_answer_is_not_rewritten():
    answer = reconcile_final_answer('交付已生成，等待你核对。', 'needs_review',
                                    {'missing_requirements': []})
    assert answer == '交付已生成，等待你核对。'
