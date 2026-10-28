"""Offline wrapper regression; no API, browser or filesystem writes."""
import ast
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def tool_function():
    src=(ROOT/'web_agent'/'tools.py').read_text(encoding='utf-8')
    tree=ast.parse(src)
    outer=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='build_local_tools')
    fn=next(node for node in outer.body if isinstance(node,ast.FunctionDef) and node.name=='record_requirement_result')
    fn.decorator_list=[]
    ns={'json':json,'workspace':object()}
    seen=[]
    def record_result(*args):
        seen.append(args)
        if args[5]=='satisfied' and (args[7] is not None and args[3]):
            raise ValueError('报告行引用仅用于satisfied，不能同时填写report_excerpt')
        if args[2]==['not_real']:
            raise ValueError('引用了不存在的来源ID')
        return {'id':args[1],'result':{'status':args[5],'source_ids':args[2],
                'report_excerpt':args[3],'reason':args[6]}}
    ns['record_result']=record_result
    code=compile(ast.Module(body=[fn],type_ignores=[]),str(ROOT/'web_agent'/'tools.py'),'exec')
    exec(code,ns)
    return ns['record_requirement_result'],seen

def test_missing_ignores_report_line_reference_and_preserves_reason():
    fn,seen=tool_function()
    out=json.loads(fn('r2',['1'],status='missing',reason='证据不足',report_line_ids=[22],report_sha256='f'*64))
    assert out['result']['status']=='missing' and out['result']['reason']=='证据不足'
    assert set(out['input_normalization']['ignored_fields'])=={'report_line_ids','report_sha256'}
    assert seen[0][3]=='' and seen[0][7] is None and seen[0][8]==''

def test_missing_strips_excerpt_without_ever_claiming_satisfied():
    fn,seen=tool_function()
    out=json.loads(fn('r3',[],status='missing',reason='未观察到',report_excerpt='some text'))
    assert out['result']['status']=='missing' and out['result']['report_excerpt']==''
    assert out['input_normalization']['ignored_fields']==['report_excerpt']
    assert seen[0][2]==[]

def test_normal_missing_preserves_existing_return_shape():
    fn,_=tool_function()
    out=json.loads(fn('r1',['1'],status='missing',reason='尚无可信资料'))
    assert 'input_normalization' not in out and out['result']['status']=='missing'

def test_satisfied_citations_still_forwarded_unchanged():
    fn,seen=tool_function()
    out=json.loads(fn('r1',['1'],status='satisfied',report_line_ids=[8],report_sha256='h'*64))
    assert out['result']['status']=='satisfied'
    assert seen[0][7]==[8] and seen[0][8]=='h'*64

def test_satisfied_mixed_citations_prefers_validated_line_ids():
    fn,seen=tool_function()
    out=json.loads(fn('r1',['1'],status='satisfied',report_excerpt='fake',report_line_ids=[8]))
    assert out['result']['status']=='satisfied'
    assert out['input_normalization']['ignored_fields']==['report_excerpt']
    assert seen[0][3]=='' and seen[0][7]==[8]

def test_invalid_source_is_not_silently_accepted_for_missing():
    fn,_=tool_function()
    with pytest.raises(ValueError,match='来源ID'):
        fn('r2',['not_real'],status='missing',reason='未核实',report_line_ids=[8])
