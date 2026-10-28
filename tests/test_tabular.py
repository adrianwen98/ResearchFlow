import pandas as pd
import json
import pytest
from web_agent.tabular import aggregate_table, safe_table_path, summarize_table


def test_group_aggregation(tmp_path):
    target = tmp_path / "public_sales.csv"
    pd.DataFrame({"brand": ["A", "A", "B"], "price": [100, 200, 50]}).to_csv(target, index=False)
    result = aggregate_table(tmp_path, target.name, "brand", "price", "sum")
    assert '"value": 300.0' in result
    assert '"total_groups": 2' in result
    assert "columns" in summarize_table(tmp_path, target.name)


def test_reject_unsafe_file(tmp_path):
    with pytest.raises(ValueError):
        safe_table_path(tmp_path, "../secret.csv")
    with pytest.raises(ValueError):
        aggregate_table(tmp_path, "public_a.csv", "brand", method="__import__")


def test_json_preview_preserves_negative_numbers_instead_of_excel_escaping(tmp_path):
    target=tmp_path/'public_values.xlsx'
    pd.DataFrame({'value':[-1.25,2.5],'label':['=not_executed','normal']}).to_excel(target,index=False)
    data=json.loads(summarize_table(tmp_path,target.name))
    assert data['preview'][0]['value']==-1.25
    assert data['rows_complete'] and data['analysis_scope']=='first worksheet'
