"""Offline regressions for public egress, correction and completion; no model API."""
import asyncio
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from test_graph import settings,ScriptModel,call,empty_browser
from test_memory_completion import prepared_workspace
from test_web_workbench import client
from web_agent import egress
from web_agent.config import Settings
from web_agent.security import UnsafeURL,validate_public_url
from web_agent.harness import ToolFailure,browser_failure,classify_error,stop_reason,error_output
from web_agent.completion import (completion_decision,automatic_receipt,current_result,
    record_result,request_delivery_revision,review_binding,verify_existing_delivery,define_requirements)
from web_agent.persistence import atomic_json,read_json,read_trace
from web_agent.runner import run_task
from web_agent.workspace import Workspace
from web_agent.documents import validate_document


@pytest.mark.parametrize('url',[
    'http://127.1/','http://2130706433/','http://0x7f000001/',
    'http://[::1]/','http://[::ffff:127.0.0.1]/','http://[::ffff:8.8.8.8]/',
    'http://[fe80::1%25eth0]/','http://[64:ff9b::7f00:1]/','http://[2002:7f00:1::]/',
    'http://[2001::1]/','http://metadata.google.internal/','http://100.100.100.200/',
    'https://example.com:8443/','file:///D:/secret','http://224.0.0.1/',
    'http://169.254.169.254/latest/meta-data','http://198.18.0.1/','http://192.88.99.1/',
    'http://localhost./','http://example.com\\@localhost/'])
def test_public_boundary_rejects_sensitive_and_ambiguous_urls(url):
    with pytest.raises((UnsafeURL,ValueError)):validate_public_url(url)


def test_default_public_and_explicit_strict_mode(monkeypatch):
    monkeypatch.setenv('NETWORK_MODE','public');monkeypatch.setenv('ALLOWED_DOMAINS','python.org')
    assert Settings.load().allowed_domains==()
    assert validate_public_url('https://www.mozilla.org/')
    monkeypatch.setenv('NETWORK_MODE','strict')
    cfg=Settings.load();assert cfg.allowed_domains==('python.org',)
    with pytest.raises(UnsafeURL):validate_public_url('https://www.mozilla.org/',cfg.allowed_domains)


@pytest.mark.asyncio
@pytest.mark.parametrize('addresses',[['127.0.0.1'],['8.8.8.8','10.0.0.1'],['::1'],['198.18.1.1'],['64:ff9b::a00:1']])
async def test_all_dns_answers_checked_and_no_fake_ip_exception(monkeypatch,addresses):
    monkeypatch.setenv('ALLOW_FAKE_IP_PROXY','true')
    loop=asyncio.get_running_loop()
    monkeypatch.setattr(loop,'getaddrinfo',AsyncMock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,0,'',(ip,443)) for ip in addresses]))
    with pytest.raises(UnsafeURL):await egress.resolve_public('example.com')


@pytest.mark.asyncio
async def test_validated_ip_is_pinned_and_second_dns_never_used(monkeypatch):
    resolved=[];connected=[]
    async def resolve(host,mode):resolved.append(host);return ['8.8.8.8']
    class Writer:
        def get_extra_info(self,key):return ('8.8.8.8',443)
    async def connect(address,port):connected.append((address,port));return object(),Writer()
    monkeypatch.setattr(egress,'resolve_public',resolve)
    monkeypatch.setattr(asyncio,'open_connection',connect)
    await egress.PublicEgress().connect('rebind.example.com',443)
    assert resolved==['rebind.example.com'] and connected==[('8.8.8.8',443)]


@pytest.mark.asyncio
async def test_real_proxy_socket_blocks_http_connect_and_redirect_follow(monkeypatch):
    requests=[]
    async def endpoint(reader,writer):
        requests.append(await reader.readuntil(b'\r\n\r\n'))
        writer.write(b'HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/meta-data\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        await writer.drain();writer.close()
    server=await asyncio.start_server(endpoint,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    proxy=egress.PublicEgress()
    actual_connect=proxy.connect
    async def controlled_public(host,target_port):
        # Only the public fixture dial is replaced; redirect uses production denial.
        if host=='fixture.example.com':return await asyncio.open_connection('127.0.0.1',port)
        return await actual_connect(host,target_port)
    monkeypatch.setattr(proxy,'connect',controlled_public)
    try:
        async with proxy:
            async with httpx.AsyncClient(proxy=proxy.url,trust_env=False,follow_redirects=True) as c:
                r=await c.get('http://fixture.example.com/')
                assert r.status_code==403 and r.headers['x-webtask-blocked']=='1'
                with pytest.raises(httpx.ProxyError):await c.get('https://[::1]/')
        assert len(requests)==1 and proxy.last_denial['code']=='permission_denied'
    finally:server.close();await server.wait_closed()


@pytest.mark.parametrize('text,code',[
    ('Ref e1 not found in the current page snapshot','stale_ref'),
    ('ERR_CONNECTION_RESET','transport'),('Timeout 30000ms','timeout'),
    ('Please verify you are human captcha','site_challenge'),('ERR_BLOCKED_BY_CLIENT','permission_denied')])
def test_observable_browser_failure_classification(text,code):
    failure=browser_failure(text)
    assert failure.code==code and 'next_action' in json.loads(error_output(failure)[7:])
    assert classify_error(ValueError('摘录与来源原文不匹配')).code=='evidence_mismatch'


def test_different_recoverable_errors_are_not_four_error_loop():
    journal={str(i):{'error_code':code,'output':'ERROR: failed','target_domain':''} for i,code in enumerate(['stale_ref','invalid_input','evidence_mismatch','timeout'])}
    assert stop_reason(journal) is None
    journal.update({str(i):{'error_code':'permission_denied','output':'ERROR: denied','target_domain':'blocked.example'} for i in range(4,6)})
    assert stop_reason(journal)[0]=='permission_denied'


@pytest.mark.asyncio
async def test_stale_ref_reobserves_then_uses_new_ref_with_same_graph(settings):
    invoked=[]
    @asynccontextmanager
    async def browser(cfg,ws):
        @tool('browser_click')
        async def click(ref:str)->str:
            """Offline ref invalidation fixture."""
            invoked.append(ref)
            if ref=='e1':raise ToolFailure('stale_ref','page changed; get snapshot')
            return 'new result'
        @tool('browser_snapshot')
        async def snapshot()->str:
            """Fresh ref fixture."""
            return 'button Search [ref=e2]'
        yield [click,snapshot]
    script=ScriptModel([call('browser_click',{'ref':'e1'},'old'),call('browser_snapshot',id='snap'),call('browser_click',{'ref':'e2'},'new')])
    result=await run_task('stale ref recovery','stale',replace(settings,max_model_calls=3),model=script,browser_factory=browser)
    assert result.status=='budget_exceeded' and invoked==['e1','e2']
    assert result.metrics['tool_attempts']==3 and result.metrics['retries']==0


@pytest.mark.asyncio
async def test_automatic_retry_debits_tool_budget_and_resume_does_not_reset(settings):
    invoked=[]
    @asynccontextmanager
    async def browser(cfg,ws):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Offline transient network fixture."""
            invoked.append(url);raise TimeoutError('temporary')
        yield [navigate]
    cfg=replace(settings,max_tool_calls=2)
    result=await run_task('tool attempt budget','attempts',cfg,model=ScriptModel([call('browser_navigate',{'url':'https://example.com'})]),browser_factory=browser)
    assert result.status=='budget_exceeded' and result.metrics['tool_attempts']==2 and len(invoked)==2
    next_model=ScriptModel([])
    again=await run_task('', 'attempts',cfg,resume=True,model=next_model,browser_factory=browser)
    assert again.status=='budget_exceeded' and next_model.calls==0 and len(invoked)==2


def objective_workspace(root):
    ws=prepared_workspace(root)
    rows=read_json(ws.path/'requirements.json')
    rows[0]['machine_check']={'type':'source_quote','url':'https://example.com','quote':'Actual observed project text.'}
    atomic_json(ws.path/'requirements.json',rows)
    return ws


def test_auto_completion_only_for_explicit_objective_contract_and_invalidates(tmp_path):
    ws=objective_workspace(tmp_path)
    assert completion_decision(ws)['status']=='completed'
    result=verify_existing_delivery(ws)
    assert result['status']=='completed'
    receipt=read_json(ws.path/'acceptance.json')
    assert receipt['verification_mode']=='automatic_objective_checks'
    assert current_result(ws)['status']=='completed'
    rows=read_json(ws.path/'requirements.json');rows[0]['result']['status']='missing'
    atomic_json(ws.path/'requirements.json',rows)
    assert current_result(ws)['status']=='partial'


def test_subjective_manual_model_defined_and_missing_cannot_auto_complete(tmp_path):
    ws=prepared_workspace(tmp_path)
    assert completion_decision(ws)['status']=='needs_review'
    rows=read_json(ws.path/'requirements.json');rows[0]['machine_check']={'type':'source_quote','url':'https://example.com','quote':'Actual observed project text.'}
    rows[0]['origin']='model';atomic_json(ws.path/'requirements.json',rows)
    assert completion_decision(ws)['status']=='needs_review'
    rows[0]['origin']='user';atomic_json(ws.path/'requirements.json',rows)
    atomic_json(ws.path/'task.json',{'task':'人工验收这个公开任务'})
    assert completion_decision(ws)['status']=='needs_review'
    record_result(ws,'r1',[],status='missing',reason='No verified answer')
    assert completion_decision(ws)['status']=='partial'
    with pytest.raises(ValueError):automatic_receipt(ws)


def test_revision_is_version_bound_no_model_and_demo_is_read_only(client):
    c,cfg=client;ws=prepared_workspace(cfg.output_dir)
    response=c.post('/tasks/study/revision',json={'binding':review_binding(ws),'note':'补充另一来源后再验收'})
    assert response.status_code==200 and response.json()['status']=='partial'
    assert c.get('/tasks/study/workspace').json()['failure']['label']
    assert not (ws.path/'usage.json').exists()
    ws2=prepared_workspace(cfg.output_dir,'demo');atomic_json(ws2.path/'demo_meta.json',{'read_only':True})
    assert c.post('/tasks/demo/revision',json={'binding':review_binding(ws2),'note':'拒绝结果'}).status_code==409


def test_failed_requirements_expose_reasons_and_real_completion_mode(client):
    c,cfg=client;ws=objective_workspace(cfg.output_dir)
    verify_existing_delivery(ws)
    view=c.get('/tasks/study/workspace').json()
    assert view['status']=='completed' and view['completion_mode']=='automatic_objective_checks'
    record_result(ws,'r1',[],status='missing',reason='Source missing')
    view=c.get('/tasks/study/workspace').json()
    assert view['status']=='partial' and not view['capabilities']['can_accept']
    assert view['requirements']['requirements'][0]['reason']=='Source missing'


@pytest.mark.asyncio
async def test_strict_egress_blocks_redirect_host_before_dns(monkeypatch):
    resolve=AsyncMock(return_value=['8.8.8.8'])
    monkeypatch.setattr(egress,'resolve_public',resolve)
    with pytest.raises(UnsafeURL):await egress.PublicEgress(allowed_domains=('python.org',)).connect('example.com',443)
    resolve.assert_not_called()


@pytest.mark.parametrize('data,suffix,mime',[(b'<html>login</html>','.csv','text/html'),(b'PKbad archive','.xlsx','application/octet-stream'),(b'MZexecutable','.csv','application/octet-stream')])
def test_download_content_cannot_impersonate_safe_files(data,suffix,mime):
    with pytest.raises(ToolFailure):validate_document(data,suffix,mime)


@pytest.mark.asyncio
async def test_time_budget_is_not_reset_on_resume(settings):
    await run_task('timer budget task','timer',settings,model=ScriptModel([AIMessage(content='No evidence'),AIMessage(content='Incomplete')]),browser_factory=empty_browser)
    timer_path=settings.output_dir/'timer'/'time_budget.json'
    atomic_json(timer_path,{'spent':settings.task_timeout,'active_since':None})
    next_model=ScriptModel([])
    result=await run_task('', 'timer',settings,resume=True,model=next_model,browser_factory=empty_browser)
    assert result.status=='timeout' and next_model.calls==0


@pytest.mark.asyncio
async def test_four_different_correctable_errors_can_return_to_plan(settings):
    responses=[call('record_source',{'title':'No observed source','url':'https://example.com','observed_fact':'no','verbatim_quote':'not observed'},'source'),
               call('inspect_downloaded_table',{'filename':'public_missing.csv'},'table'),
               call('load_skill',{'skill_name':'unknown'},'skill'),
               call('aggregate_downloaded_table',{'filename':'public_missing2.csv','group_by':'x'},'calc'),
               call('update_plan',{'steps':['use verified public sources instead']},'fix')]
    result=await run_task('recover from distinct failures','correction',replace(settings,max_model_calls=5),model=ScriptModel(responses),browser_factory=empty_browser)
    assert result.status=='budget_exceeded' and result.metrics['errors']==4
    assert read_json(settings.output_dir/'correction'/'plan.json')==['use verified public sources instead']
    assert not any(e['event']=='loop_guard' for e in read_trace(settings.output_dir/'correction'/'trace.jsonl'))


@pytest.mark.asyncio
async def test_interrupted_read_cannot_replay_outside_original_attempt_budget(settings):
    invoked=[]
    @asynccontextmanager
    async def browser(cfg,ws):
        @tool('browser_snapshot')
        async def snapshot()->str:
            """Fault injection after dispatch reservation, before journal completion."""
            invoked.append(True)
            raise asyncio.CancelledError()
        yield [snapshot]
    cfg=replace(settings,max_tool_calls=1)
    first=await run_task('interrupted tool budget','read_interrupt',cfg,model=ScriptModel([call('browser_snapshot',id='read')]),browser_factory=browser)
    assert first.status=='interrupted'
    again=await run_task('', 'read_interrupt',cfg,resume=True,model=ScriptModel([]),browser_factory=browser)
    assert again.status=='budget_exceeded' and len(invoked)==1 and again.metrics['tool_attempts']==1


@pytest.mark.asyncio
async def test_real_graph_and_runner_record_auto_completion_after_final_exports(settings):
    @asynccontextmanager
    async def browser(cfg,ws):
        @tool('browser_navigate')
        async def navigate(url:str)->str:
            """Offline observed text, actual graph and file verification."""
            ws.observe(url,'A verified source quotation.')
            return 'A verified source quotation.'
        yield [navigate]
    responses=[call('browser_navigate',{'url':'https://example.com'},'nav'),
               call('record_source',{'title':'Source','url':'https://example.com','observed_fact':'quotation','verbatim_quote':'A verified source quotation.'},'source'),
               call('export_excel',id='excel'),call('export_report',{'markdown_report':'# Extract\nA verified source quotation.'},'report'),
               call('record_requirement_result',{'requirement_id':'r1','source_ids':['1'],'report_excerpt':'A verified source quotation.'},'requirement')]
    contract=[{'description':'Extract this specified quotation','kind':'research','machine_check':{'type':'source_quote','url':'https://example.com','quote':'A verified source quotation.'}}]
    result=await run_task('Extract the specified source quotation','auto_graph',settings,requirements=contract,model=ScriptModel(responses),browser_factory=browser)
    assert result.status=='completed' and result.completion['all_requirements_verified']
    ws=Workspace(settings.output_dir,'auto_graph')
    assert current_result(ws)['status']=='completed'
    assert read_json(ws.path/'acceptance.json')['verification_mode']=='automatic_objective_checks'
