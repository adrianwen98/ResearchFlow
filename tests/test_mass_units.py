"""Chinese statistical mass units and table-level unit headers."""
from web_agent.delivery_schema import normalize_entry
from web_agent.scalar_contract import canonical_unit


def test_chinese_mass_units_are_supported():
    assert canonical_unit('万吨') == '万吨'
    assert canonical_unit('亿吨') == '亿吨'
    assert canonical_unit('吨') == 't'


def test_table_level_unit_header_binds_a_remote_value():
    entry = {'value': 6022.6,
             'quote': '全国化肥施用量\n单位：万吨\n2015 6022.60'}
    field = {'type': 'number', 'unit': '万吨', 'required': True}
    value, audit = normalize_entry(entry, field, None)
    assert value == 6022.6
    assert audit['unit'] == '万吨'
    assert audit['status'] == 'verified'
