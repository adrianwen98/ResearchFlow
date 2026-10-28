"""Offline graph setup guard regression; no DeepSeek/browser dependency."""
import json
from pathlib import Path
from types import SimpleNamespace
from web_agent.setup_idempotency import unchanged_setup_output


def ws(tmp_path):
    return SimpleNamespace(path=tmp_path)


def save(p,data):
    p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')


def test_same_requirements_reuses_persisted_rows(tmp_path):
    rows=[{'id':'r1','description':'检查官网价格','kind':'research','artifact':'research_report.md','result':{'status':'satisfied'}}]
    path=tmp_path/'requirements.json';save(path,rows);before=path.read_bytes()
    out=unchanged_setup_output(ws(tmp_path),'set_requirements',{'items':[{'description':'检查官网价格','kind':'research','artifact':'research_report.md'}]})
    assert json.loads(out)==rows and path.read_bytes()==before


def test_added_requirement_must_reach_original_tool(tmp_path):
    save(tmp_path/'requirements.json',[{'id':'r1','description':'检查官网价格','kind':'research','artifact':'research_report.md'}])
    assert unchanged_setup_output(ws(tmp_path),'set_requirements',{'items':[{'description':'检查官网价格','kind':'research','artifact':'research_report.md'},{'description':'核实官网型号','kind':'research','artifact':'research_report.md'}]}) is None


def test_changed_requirement_metadata_must_reach_original_tool(tmp_path):
    save(tmp_path/'requirements.json',[{'id':'r1','description':'检查官网价格','kind':'research','artifact':'research_report.md'}])
    assert unchanged_setup_output(ws(tmp_path),'set_requirements',{'items':[{'description':'检查官网价格','kind':'artifact','artifact':'task_result.xlsx'}]}) is None


def test_invalid_requirements_not_silently_accepted(tmp_path):
    save(tmp_path/'requirements.json',[{'id':'r1','description':'检查官网价格','kind':'research','artifact':'research_report.md'}])
    assert unchanged_setup_output(ws(tmp_path),'set_requirements',{'items':[]}) is None
    assert unchanged_setup_output(ws(tmp_path),'set_requirements',{'items':[{'description':'a'}]}) is None


def test_identical_plan_reused_without_writes(tmp_path):
    path=tmp_path/'plan.json';save(path,['查官网','保存来源']);before=path.read_bytes()
    result=unchanged_setup_output(ws(tmp_path),'update_plan',{'steps':['查官网','保存来源']})
    assert json.loads(result)==['查官网','保存来源'] and path.read_bytes()==before


def test_changed_plan_still_runs_normal_tool(tmp_path):
    save(tmp_path/'plan.json',['查官网','保存来源'])
    assert unchanged_setup_output(ws(tmp_path),'update_plan',{'steps':['查官网','补充新来源']}) is None
    assert unchanged_setup_output(ws(tmp_path),'update_plan',{'steps':['查官网','保存来源','新步骤']}) is None


def test_missing_state_does_not_reuse(tmp_path):
    assert unchanged_setup_output(ws(tmp_path),'set_requirements',{'items':[{'description':'新需求','kind':'research'}]}) is None
    assert unchanged_setup_output(ws(tmp_path),'update_plan',{'steps':['新计划']}) is None


def test_other_tools_untouched(tmp_path):
    save(tmp_path/'plan.json',['查官网'])
    assert unchanged_setup_output(ws(tmp_path),'record_source',{'steps':['查官网']}) is None


def test_graph_policy_and_dispatch_are_integrated():
    graph=(Path(__file__).resolve().parents[1]/'web_agent'/'graph.py').read_text(encoding='utf-8')
    assert "reused=unchanged_setup_output(workspace,name,args)" in graph
    assert "workspace.log('setup_reused',name,call_id=key)" in graph
    assert "只有发现新验收项才追加" in graph
