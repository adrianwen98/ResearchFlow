"""Free tests: call ceiling is a guardrail, and material collection must converge."""
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool
from test_graph import settings,ScriptModel,call
from web_agent.execution_budget import handoff,inspect_saved_pages,record_source_from_observation,require_observed_path_after_not_found
from web_agent.harness import ToolFailure
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json
from web_agent.runner import run_task
from web_agent import browser


def test_reserve_starts_even_without_registered_sources(tmp_path):
    ws=Workspace(tmp_path,'unregistered')
    before=handoff(ws,30,19);closing=handoff(ws,30,20)
    assert not before['closing'] and closing['closing'] and closing['reserve']==10
    assert not closing['local_only'] # missing material can still be recovered
    assert handoff(ws,30,28)['local_only']
    name='public_fixture.csv';(ws.path/name).write_text('x\n1\n')
    atomic_json(ws.path/'downloads.json',{name:{'url':'https://example.com/data.csv'}})
    # A saved download is useful local material, but does not prove task coverage.
    assert handoff(ws,30,20)['local_ready']
    assert not handoff(ws,30,20)['local_only']
    assert handoff(ws,50,20)['remaining']==30
    assert handoff(ws,50,20)['reserve']==10


def test_saved_pages_are_available_before_record_source_and_never_mutated(tmp_path):
    ws=Workspace(tmp_path,'pages')
    text='Public dataset. 530 rows. Download and metadata.'
    oid=ws.observe('https://example.com/data',text)
    before=(ws.path/'observations'/f'{oid}.json').read_bytes()
    assert ws.sources()==[]
    index=inspect_saved_pages(ws)
    assert index['observations'][0]['observation_id']==oid
    excerpt=inspect_saved_pages(ws,oid,'530')
    assert excerpt['keyword_found'] and excerpt['text']==text
    assert not inspect_saved_pages(ws,oid,'not in page')['text']
    assert (ws.path/'observations'/f'{oid}.json').read_bytes()==before
    assert not (ws.path/'sources.json').exists()
    for invalid in ('../other','missing','a'*21):
        with pytest.raises(ValueError):inspect_saved_pages(ws,invalid)
    with pytest.raises(ValueError):inspect_saved_pages(Workspace(tmp_path,'other'),oid)


def seed_404s(ws):
    atomic_json(ws.path/'tool_journal.json',{str(i):{
        'name':'browser_navigate','error_code':'http_404','target_domain':'api.example.com',
        'output':'ERROR: missing endpoint'} for i in range(2)})


def test_guess_guard_keeps_real_documented_links_and_other_paths_available(tmp_path):
    ws=Workspace(tmp_path,'lookup');seed_404s(ws)
    with pytest.raises(ToolFailure,match='停止猜接口'):
        require_observed_path_after_not_found(ws,'https://api.example.com/guessed')
    require_observed_path_after_not_found(ws,'https://docs.example.com/api')
    ws.observe('https://docs.example.com/api','Endpoint "https://api.example.com/real"')
    require_observed_path_after_not_found(ws,'https://api.example.com/real')
    # A prefix is not proof of a different URL.
    ws.observe('https://docs.example.com/other','https://api.example.com/guessed-other')
    with pytest.raises(ToolFailure):require_observed_path_after_not_found(ws,'https://api.example.com/guessed')
    ws.observe('https://api.example.com/docs','- link [ref=a]\n  - /url: /relative')
    require_observed_path_after_not_found(ws,'https://api.example.com/relative')
    atomic_json(ws.path/'task.json',{'task':'Read https://api.example.com/user-requested'})
    require_observed_path_after_not_found(ws,'https://api.example.com/user-requested')


@pytest.mark.asyncio
async def test_real_browser_wrapper_blocks_guesses_before_mcp_dispatch(settings,monkeypatch):
    ws=Workspace(settings.output_dir,'wrapper');seed_404s(ws);dispatch=[]
    async def navigate(args):
        dispatch.append(args['url'])
        return '### Page\n- Page URL: '+args['url']+'\n### Snapshot\n- paragraph: Public API documentation'
    async def snapshot(args):return 'Snapshot'
    async def load(session):return [SimpleNamespace(name='browser_navigate',ainvoke=navigate),SimpleNamespace(name='browser_snapshot',ainvoke=snapshot)]
    monkeypatch.setattr(browser,'load_mcp_tools',load)
    tools={t.name:t for t in await browser.load_safe_browser_tools('offline',settings,ws)}
    with pytest.raises(ToolFailure):await tools['browser_navigate'].ainvoke({'url':'https://api.example.com/guessed'})
    assert dispatch==[]
    await tools['browser_navigate'].ainvoke({'url':'https://docs.example.com/api'})
    assert dispatch==['https://docs.example.com/api']


@pytest.mark.asyncio
async def test_real_runner_can_register_saved_evidence_and_deliver_before_ceiling(settings):
    task_id='late_registration';ws=Workspace(settings.output_dir,task_id)
    text='Saved public evidence. The dataset has 530 rows.'
    oid=ws.observe('https://example.com/data',text)
    filename='public_fixture.csv';(ws.path/filename).write_text('value\n1\n',encoding='utf-8')
    atomic_json(ws.path/'downloads.json',{filename:{'url':'https://example.com/data.csv'}})
    atomic_json(ws.path/'usage.json',{'requests':27,'input_tokens':0,'output_tokens':0})
    @asynccontextmanager
    async def fixture_browser(cfg,workspace):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Forbidden live navigation in this test."""
            raise AssertionError('Should reuse saved evidence')
        yield [navigate]
    class BudgetAwareReplay(ScriptModel):
        def __init__(self):self.calls=0;self.bindings=[]
        def bind_tools(self,tools,**kwargs):self.bindings.append({t.name for t in tools});return self
        async def ainvoke(self,messages):
            turn=self.calls;self.calls+=1
            assert '进入交付收尾阶段' in str(messages[0].content)
            if turn==0:
                assert 'record_source' in self.bindings[-1] and 'inspect_saved_pages' in self.bindings[-1]
                assert 'browser_navigate' not in self.bindings[-1]
                return call('inspect_saved_pages',{'observation_id':oid},'read')
            if turn==1:
                saved=json.loads(next(m.content for m in reversed(messages) if m.type=='tool'))
                return call('record_source',{'title':'Saved data','url':saved['url'],'observed_fact':'Dataset size',
                    'verbatim_quote':saved['text']},'source')
            if turn==2:
                from langchain_core.messages import AIMessage
                return AIMessage(content='',tool_calls=[
                    {'name':'export_excel','args':{},'id':'excel','type':'tool_call'},
                    {'name':'export_report','args':{'markdown_report':'# Saved dataset\n530 rows; reuse saved public evidence.'},'id':'report','type':'tool_call'}])
            raise AssertionError('No need to consume all 30 calls')
    model=BudgetAwareReplay();cfg=replace(settings,max_model_calls=30,max_graph_steps=85)
    result=await run_task('Read saved public evidence and deliver a short report',task_id,cfg,model=model,browser_factory=fixture_browser)
    assert result.status=='needs_review' and model.calls==3 and result.metrics['requests']==30
    assert result.metrics['errors']==0 and ws.verify_delivery()['artifacts_valid']
    phase=[json.loads(line) for line in (ws.path/'trace.jsonl').read_text(encoding='utf-8').splitlines() if json.loads(line)['event']=='delivery_phase']
    assert phase and phase[0]['remaining_requests']==3


def test_record_source_accepts_layout_space_variants(tmp_path):
    nbsp=chr(0xA0)
    ws=Workspace(tmp_path,'layout-record')
    text=('iPhone 17 Pro 规格\n\n'
          '4800 万像素 Pro'+nbsp+'级融合式摄像头系统\n\n'
          'USB'+nbsp+'3 (最高可达 10Gb/s)13\n\n'
          'A19'+nbsp+'Pro chip\n')
    oid=ws.observe('https://support.apple.com/zh-cn/125090',text)
    cases=[
        ('4800 万像素 Pro 级融合式摄像头系统','4800 万像素 Pro'+nbsp+'级融合式摄像头系统'),
        ('USB 3 (最高可达 10Gb/s)','USB'+nbsp+'3 (最高可达 10Gb/s)'),
        ('A19 Pro chip','A19'+nbsp+'Pro chip'),
    ]
    for keyword,expected in cases:
        row=record_source_from_observation(ws,'Apple 规格',oid,'fixture fact',keyword)
        assert row['observation_id']==oid
        assert expected in row['quote']
        assert row['quote'] in text
    assert len(ws.sources())==3
    with pytest.raises(ValueError,match='未出现'):
        record_source_from_observation(ws,'Apple 规格',oid,'fact','USB 4 (最高可达 10Gb/s)')


def test_invalid_id_unique_keyword_recovers(tmp_path):
    ws=Workspace(tmp_path,'recover-unique')
    oid=ws.observe('https://example.org/spec','iPhone 17 重量\n177 克 (6.24 盎司)')
    result=record_source_from_observation(
        ws,'规格','f4','iPhone 17 重 177 克','177 克 (6.24 盎司)')
    assert result['observation_id']==oid
    assert result['input_normalization']['reason']=='unique_keyword'
    assert ws.sources()[0]['observation_id']==oid


def test_invalid_id_multiple_matches_uses_current_browser_page(tmp_path):
    ws=Workspace(tmp_path,'recover-browser')
    compare=ws.observe('https://example.org/compare',
                       'iPhone 机型比较\n177 克 (6.24 盎司)\n204 克 (7.20 盎司)')
    specs=ws.observe('https://example.org/specs',
                     'iPhone 17 技术规格\n177 克 (6.24 盎司)')
    atomic_json(ws.path/'browser_state.json',
                {'url':'https://example.org/compare','observation_id':compare})
    result=record_source_from_observation(
        ws,'机型比较','f4','iPhone 17 重 177 克','177 克 (6.24 盎司)')
    assert result['observation_id']==compare
    assert result['input_normalization']['reason']=='current_browser_page_id'
    assert ws.sources()[0]['observation_id']==compare
    assert specs!=compare


def test_invalid_id_multiple_matches_uses_distinctive_title(tmp_path):
    ws=Workspace(tmp_path,'recover-title')
    compare=ws.observe('https://example.org/compare',
                       'iPhone 机型比较\n177 克 (6.24 盎司)\n204 克 (7.20 盎司)')
    ws.observe('https://example.org/specs',
               'iPhone 17 技术规格\n177 克 (6.24 盎司)')
    result=record_source_from_observation(
        ws,'iPhone 机型比较：iPhone 17 vs iPhone 17 Pro（Apple 中国大陆）',
        'f4','iPhone 17 重 177 克','177 克 (6.24 盎司)')
    assert result['observation_id']==compare
    assert result['input_normalization']['reason']=='title_unique_term'
    assert result['input_normalization']['matched_title_terms']==['机型比较']


def test_invalid_id_ambiguous_title_lists_candidates_without_source(tmp_path):
    ws=Workspace(tmp_path,'recover-ambiguous')
    first=ws.observe('https://example.org/a','iPhone 机型比较\n177 克 (6.24 盎司)')
    second=ws.observe('https://example.org/b','iPhone 技术规格\n177 克 (6.24 盎司)')
    with pytest.raises(ValueError) as info:
        record_source_from_observation(
            ws,'Apple 中国大陆','f4','iPhone 17 重 177 克','177 克 (6.24 盎司)')
    message=str(info.value)
    assert first in message and second in message
    assert '原文' not in message
    assert ws.sources()==[]


def test_valid_id_wrong_page_resolves_unique_keyword_elsewhere(tmp_path):
    ws=Workspace(tmp_path,'wrong-page')
    wrong=ws.observe('https://example.org/specs','iPhone 17 技术规格\n177 克 (6.24 盎司)')
    other=ws.observe('https://example.org/other','独立页面\n204 克 (7.20 盎司)')
    result=record_source_from_observation(
        ws,'技术规格',wrong,'iPhone 17 Pro 重 204 克','204 克 (7.20 盎司)')
    assert result['observation_id']==other
    assert result['input_normalization']['reason']=='keyword_unique_other_page'
    assert ws.sources()[0]['observation_id']==other


def test_valid_id_keyword_absent_everywhere_still_fails(tmp_path):
    ws=Workspace(tmp_path,'absent-keyword')
    wrong=ws.observe('https://example.org/specs','iPhone 17 技术规格\n177 克 (6.24 盎司)')
    with pytest.raises(ValueError,match='未出现'):
        record_source_from_observation(
            ws,'技术规格',wrong,'不存在的事实','这句话在全部观察记录中都不存在')
    assert ws.sources()==[]


def test_valid_id_wrong_page_ambiguous_keyword_never_guesses(tmp_path):
    ws=Workspace(tmp_path,'ambiguous-keyword')
    wrong=ws.observe('https://example.org/specs','iPhone 17 技术规格\n177 克 (6.24 盎司)')
    first=ws.observe('https://example.org/compare','机型比较\n204 克 (7.20 盎司)')
    second=ws.observe('https://example.org/other','独立页面\n204 克 (7.20 盎司)')
    with pytest.raises(ValueError,match='命中2个'):
        record_source_from_observation(
            ws,'技术规格',wrong,'iPhone 17 Pro 重 204 克','204 克 (7.20 盎司)')
    assert ws.sources()==[]
    assert first != second


def test_invalid_json_candidate_is_skipped(tmp_path):
    ws=Workspace(tmp_path,'bad-json')
    oid=ws.observe('https://example.org/spec','iPhone 17 重量\n177 克 (6.24 盎司)')
    (ws.path/'observations'/'aaaaaaaaaaaaaaaaaaaa.json').write_text('{broken',encoding='utf-8')
    result=record_source_from_observation(
        ws,'规格','f4','iPhone 17 重 177 克','177 克 (6.24 盎司)')
    assert result['observation_id']==oid


def test_full_sentence_keyword_longer_than_old_limit_is_accepted(tmp_path):
    keyword=('A new and improved interactive interpreter, based on PyPy, '
             'featuring multi-line editing and color support, as well as '
             'colorized exception tracebacks.')
    assert 120 < len(keyword) <= 240
    ws=Workspace(tmp_path,'long-keyword')
    oid=ws.observe('https://example.org/release','Python 3.13\n'+keyword+'\n')
    result=record_source_from_observation(ws,'3.13 新特性',oid,'新交互解释器',keyword)
    assert result['observation_id']==oid and keyword in result['quote']
