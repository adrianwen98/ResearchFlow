"""Zero-model, zero-network regression for task-scoped saved observation search."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from web_agent.execution_budget import inspect_saved_pages


def observation(root:Path, idnum:int, url:str, text:str):
    oid=f'{idnum:020x}'
    folder=root/'observations'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'{oid}.json'
    path.write_text(json.dumps({'id':oid,'url':url,'text':text},ensure_ascii=False),encoding='utf-8')
    return oid,path


def test_keyword_without_id_finds_saved_page_without_network_or_mutation(tmp_path):
    oid,path=observation(tmp_path,1,'https://example.org/report','Intro '+('A'*230)+'结论是530行数据')
    original=path.read_bytes()
    result=inspect_saved_pages(SimpleNamespace(path=tmp_path),keyword='530行')
    assert result['total']==1 and result['next_offset'] is None
    assert result['observations'][0]['observation_id']==oid
    assert result['observations'][0]['url']=='https://example.org/report'
    assert '530行' in result['observations'][0]['preview']
    assert result['observations'][0]['match_offset']>160
    assert '预览不是已核实证据' in result['note']
    assert path.read_bytes()==original
    assert not (tmp_path/'sources.json').exists()


def test_keyword_filters_before_paging_and_stays_bounded(tmp_path):
    for i in range(1,28):
        observation(tmp_path,i,f'https://example.org/{i}',f'Page {i} '+('MatchTerm' if i%2 else 'OTHER'))
    ws=SimpleNamespace(path=tmp_path)
    first=inspect_saved_pages(ws,keyword='matchterm')
    second=inspect_saved_pages(ws,keyword='MatchTerm',offset=first['next_offset'])
    assert first['total']==14 and len(first['observations'])==10
    assert first['next_offset']==10 and len(second['observations'])==4
    assert second['next_offset'] is None
    ids=[x['observation_id'] for x in first['observations']+second['observations']]
    assert len(set(ids))==14
    assert inspect_saved_pages(ws,keyword='nonexistent')['observations']==[]


def test_without_keyword_keeps_original_index_behavior(tmp_path):
    oid,_=observation(tmp_path,1,'https://example.org/a','This page contains KEY')
    ws=SimpleNamespace(path=tmp_path)
    index=inspect_saved_pages(ws)
    assert index['total']==1 and index['observations'][0]['observation_id']==oid
    assert index['observations'][0]['preview'].startswith('This page')
    assert 'keyword' not in index
    full=inspect_saved_pages(ws,observation_id=oid,keyword='KEY')
    assert full['keyword_found'] and full['text']=='This page contains KEY'
    assert inspect_saved_pages(ws,observation_id=oid,keyword='missing')['text']==''


def test_keyword_search_tolerates_nbsp_and_layout_spaces(tmp_path):
    nbsp=chr(0xA0)
    text='Intro\n\nUSB'+nbsp+'3 (最高可达 10Gb/s)13\n\n屏幕 6.3英寸'
    oid,_=observation(tmp_path,1,'https://example.org/spec',text)
    ws=SimpleNamespace(path=tmp_path)
    one=inspect_saved_pages(ws,oid,'USB 3 (最高可达 10Gb/s)')
    assert one['keyword_found'] is True and 'USB'+nbsp+'3' in one['text']
    found=inspect_saved_pages(ws,keyword='USB 3 (最高可达 10Gb/s)')
    assert found['total']==1
    assert found['observations'][0]['observation_id']==oid
    assert 'USB'+nbsp+'3' in found['observations'][0]['preview']
    assert inspect_saved_pages(ws,oid,'USB 4 (最高可达 10Gb/s)')['keyword_found'] is False
    assert inspect_saved_pages(ws,keyword='USB 4 (最高可达 10Gb/s)')['total']==0
    layout=inspect_saved_pages(ws,oid,'屏幕6.3英寸')
    assert layout['keyword_found'] is True and '屏幕 6.3' in layout['text']


def test_search_never_crosses_tasks_or_follows_symlinks(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'
    oid,path=observation(a,1,'https://example.org/one','secret label')
    observation(b,2,'https://example.org/two','secret label')
    ws=SimpleNamespace(path=a)
    found=inspect_saved_pages(ws,keyword='secret')
    assert found['total']==1 and found['observations'][0]['observation_id']==oid
    link=a/'observations'/('f'*20+'.json')
    try:link.symlink_to(path)
    except (OSError,NotImplementedError): pass # Some Windows accounts disallow symlinks.
    else:assert inspect_saved_pages(ws,keyword='secret')['total']==1


def test_invalid_or_oversized_pages_are_not_search_results(tmp_path):
    oid,path=observation(tmp_path,1,'https://example.org/a','VALID term')
    bad=tmp_path/'observations'/('f'*20+'.json')
    bad.write_text('{wrong json',encoding='utf-8')
    oversized=tmp_path/'observations'/('e'*20+'.json')
    oversized.write_bytes(b'x'*(2*1024*1024+1))
    impostor=tmp_path/'observations'/('d'*20+'.json')
    impostor.write_text(json.dumps({'id':'not matching name','url':'https://wrong.org','text':'VALID term'}),encoding='utf-8')
    result=inspect_saved_pages(SimpleNamespace(path=tmp_path),keyword='VALID')
    assert result['total']==1 and result['observations'][0]['observation_id']==oid


def test_invalid_inputs_keep_limits(tmp_path):
    ws=SimpleNamespace(path=tmp_path)
    for kw in (None,'x'*721):
        with pytest.raises(ValueError):inspect_saved_pages(ws,keyword=kw)
    for offset in (-1,20001,1.5,True):
        with pytest.raises(ValueError):inspect_saved_pages(ws,keyword='x',offset=offset)
    with pytest.raises(ValueError):inspect_saved_pages(ws,observation_id='../traversal')
