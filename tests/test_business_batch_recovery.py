"""Regression: valid cells survive a partial evidence failure."""
from web_agent.business_batch_recovery import disclose_invalid_cells


def sample():
    return [{
        'name': '对比', 'mode': 'comparison', 'columns': ['重量', '芯片'],
        'fields': {'重量': {'type': 'number', 'unit': 'g'}, '芯片': {'type': 'text'}},
        'rows': [
            {'label': 'A', 'cells': {
                '重量': {'value': 177, 'source_id': '1', 'quote': '177 克'},
                '芯片': {'value': 'A19', 'source_id': '1', 'quote': 'A19'}}},
            {'label': 'B', 'cells': {
                '重量': {'value': 206, 'source_id': '2', 'quote': '206 克'},
                '芯片': {'value': 'A19 Pro', 'source_id': '2', 'quote': 'A19 Pro'}}},
        ],
    }]


def test_only_rejected_cell_is_null_and_disclosed():
    original = sample()
    updated, changed = disclose_invalid_cells(original, [
        {'sheet': '对比', 'row': 3, 'column': '重量', 'message': '对象与数值没有绑定'}])
    assert changed == 1
    assert updated[0]['rows'][1]['cells']['重量']['value'] is None
    assert updated[0]['rows'][1]['cells']['重量']['status'] == 'unverified'
    assert '对象与数值没有绑定' in updated[0]['rows'][1]['cells']['重量']['reason']
    assert 'source_id' not in updated[0]['rows'][1]['cells']['重量']
    assert updated[0]['rows'][0]['cells'] == original[0]['rows'][0]['cells']
    assert updated[0]['rows'][1]['cells']['芯片'] == original[0]['rows'][1]['cells']['芯片']
    assert original[0]['rows'][1]['cells']['重量']['value'] == 206


def test_invalid_or_ambiguous_diagnostic_cannot_silently_modify_data():
    original = sample()
    for issue in [
        {'sheet':'不存在', 'row':2, 'column':'重量'},
        {'sheet':'对比', 'row':'2', 'column':'重量'},
        {'sheet':'对比', 'row':2, 'column':'不存在'},
        {'sheet':'对比', 'row':100, 'column':'重量'},
    ]:
        updated, changed = disclose_invalid_cells(original, [issue])
        assert changed == 0
        assert updated == original


def test_idempotent_and_no_verified_upgrade():
    first, changed = disclose_invalid_cells(sample(), [
        {'sheet':'对比', 'row':2, 'column':'重量', 'message':'mismatch'}])
    second, changed_again = disclose_invalid_cells(first, [
        {'sheet':'对比', 'row':2, 'column':'重量', 'message':'mismatch'}])
    assert changed == 1 and changed_again == 0
    assert second == first
