"""Offline evidence URL regressions: exact quotes and observed navigation only."""
from types import SimpleNamespace
from dataclasses import replace
import pytest
from langchain_core.messages import AIMessage
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json
from web_agent.harness import signature,stop_reason,preflight,ToolFailure
from web_agent import browser
from web_agent.runner import run_task
from test_graph import settings,ScriptModel,call,empty_browser
from test_web_workbench import client

SHORT='https://example.com/help/123'
FINAL='https://example.com/help/123-official-document'
QUOTE='基础版单次上传上限 20 MB，认证后 10 GB。'


def legacy_navigation(ws,*,success=True,header=None,args=None):
    args=args or {'url':SHORT}
    ws.log('tool_start','browser_navigate',call_id='nav',arguments=args)
    ws.log('mcp_response',header or '### Ran Playwright code\n```js\nawait page.goto();\n```\n### Page\n- Page URL: '+FINAL+'\n### Snapshot\n- text',tool='browser_navigate')
    atomic_json(ws.path/'tool_journal.json',{'nav':{'name':'browser_navigate','output':'OK' if success else 'ERROR: denied',
        'args_signature':signature('browser_navigate',args),'error_code':'' if success else 'permission_denied'}})


def test_observed_alias_binds_exact_quote_to_final_url_and_deduplicates(tmp_path):
    ws=Workspace(tmp_path,'alias')
    observation=ws.observe(FINAL,QUOTE,requested_url=SHORT)
    first=ws.record('Doc',SHORT,'Upload limit',QUOTE,require_observation=True)
    second=ws.record('Doc',FINAL,'Upload limit',QUOTE,require_observation=True)
    assert first==second and first['url']==FINAL and first['requested_url']==SHORT
    assert first['observation_id']==observation and len(ws.sources())==1
    ws.observe(FINAL,QUOTE)  # A later snapshot must not erase the earlier alias.
    assert ws.evidence_for(SHORT,QUOTE)==observation
    with pytest.raises(ValueError):ws.record('Doc',SHORT,'Invented','Unlimited uploads',require_observation=True)
    with pytest.raises(ValueError):ws.record('Doc','https://example.com/help/124','Wrong URL',QUOTE,require_observation=True)
    ws.export_excel();ws.export_report('# Findings\nUpload limit observed.')
    assert ws.verify_delivery()['artifacts_valid']


def test_legacy_navigation_is_reused_without_mutating_original_observation(tmp_path):
    ws=Workspace(tmp_path,'old')
    observed=ws.observe(FINAL,QUOTE)
    before=(ws.path/'observations'/f'{observed}.json').read_bytes()
    legacy_navigation(ws)
    row=ws.record('Doc',SHORT,'Upload limit',QUOTE,require_observation=True)
    assert row['url']==FINAL
    assert (ws.path/'observations'/f'{observed}.json').read_bytes()==before


@pytest.mark.parametrize('case',['no-navigation','failed','unbound','page-body','other-query'])
def test_no_alias_from_similar_paths_failed_reads_or_page_body(tmp_path,case):
    ws=Workspace(tmp_path,'unsafe')
    ws.observe(FINAL,QUOTE)
    if case=='failed':legacy_navigation(ws,success=False)
    if case=='unbound':legacy_navigation(ws,args={'url':SHORT+'?different=true'})
    if case=='page-body':legacy_navigation(ws,header='### Snapshot\n```yaml\n### Page\n- Page URL: '+FINAL)
    if case=='other-query':ws.observe(FINAL,QUOTE,requested_url=SHORT+'?other=1')
    with pytest.raises(ValueError):ws.record('Doc',SHORT,'Must not pass',QUOTE,require_observation=True)


@pytest.mark.asyncio
async def test_real_browser_wrapper_saves_final_and_requested_addresses(settings,monkeypatch):
    async def navigate(args):return f'### Page\n- Page URL: {FINAL}\n```yaml\n- paragraph: {QUOTE}\n```'
    async def snapshot(args):return await navigate(args)
    async def load(session):return [SimpleNamespace(name='browser_navigate',ainvoke=navigate),SimpleNamespace(name='browser_snapshot',ainvoke=snapshot)]
    monkeypatch.setattr(browser,'load_mcp_tools',load)
    ws=Workspace(settings.output_dir,'wrapper')
    tools={tool.name:tool for tool in await browser.load_safe_browser_tools('offline',settings,ws)}
    result=await tools['browser_navigate'].ainvoke({'url':SHORT})
    assert '实际读取地址：'+FINAL in result
    assert ws.record('Doc',SHORT,'Upload limit',QUOTE,require_observation=True)['url']==FINAL


def test_changed_quote_cannot_spend_eight_turns_on_the_same_failure():
    journal={str(i):{'name':'record_source','error_code':'evidence_mismatch','output':'ERROR: mismatch'} for i in range(3)}
    assert stop_reason(dict(list(journal.items())[:2])) is None
    assert stop_reason(journal)[0]=='stalled'
    journal['progress']={'name':'browser_navigate','output':'New verified observation'}
    assert stop_reason(journal) is None


@pytest.mark.asyncio
async def test_legacy_false_negatives_can_be_rechecked_without_resetting_usage(settings):
    ws=Workspace(settings.output_dir,'recheck')
    ws.observe(FINAL,QUOTE,requested_url=SHORT)
    args={'title':'Doc','url':SHORT,'observed_fact':'Upload limit','verbatim_quote':QUOTE}
    old={str(i):{'name':'record_source','signature':signature('record_source',args),'args_signature':signature('record_source',args),
        'output':'ERROR: mismatch','error_code':'evidence_mismatch'} for i in range(2)}
    atomic_json(ws.path/'tool_journal.json',old)
    atomic_json(ws.path/'usage.json',{'requests':2,'input_tokens':0,'output_tokens':0})
    with pytest.raises(ToolFailure):preflight('record_source',args,old,())
    model=ScriptModel([call('record_source',args,'fresh-call'),AIMessage(content='Partial result; no business comparison yet.'),AIMessage(content='Incomplete.')])
    result=await run_task('Check official source','recheck',replace(settings,max_model_calls=5),model=model,browser_factory=empty_browser)
    assert ws.sources()[0]['url']==FINAL
    assert not read_json(ws.path/'tool_journal.json')['fresh-call']['output'].startswith('ERROR:')
    assert result.metrics['requests']==2+model.calls
    assert all(read_json(ws.path/'tool_journal.json')[str(i)]==old[str(i)] for i in range(2))


def test_stalled_source_registration_shows_the_root_cause(client):
    from test_memory_completion import prepared_workspace
    c,cfg=client
    ws=prepared_workspace(cfg.output_dir)
    result=read_json(ws.path/'result.json');result['status']='stalled'
    atomic_json(ws.path/'result.json',result)
    atomic_json(ws.path/'tool_journal.json',{'failed-source':{'name':'record_source','error_code':'evidence_mismatch','output':'ERROR: evidence mismatch'}})
    view=c.get('/tasks/study/workspace').json()
    assert view['status']=='stalled'
    assert view['failure']['root_code']=='evidence_mismatch'
    assert '最终网址' in view['failure']['next_action']
