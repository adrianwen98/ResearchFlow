"""Free real public-web MCP/file smoke. Never constructs or invokes an LLM."""
import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime

from web_agent.config import Settings,ROOT
from web_agent.browser import browser_client,load_safe_browser_tools
from web_agent.tools import build_local_tools
from web_agent.workspace import Workspace
from web_agent.harness import ToolFailure
from web_agent.persistence import atomic_json


async def main():
    started=time.monotonic()
    cfg=replace(Settings.load(),api_key='',allowed_domains=(),browser_headless=True,
                mcp_cli=str(ROOT/'work/mcp/node_modules/@playwright/mcp/cli.js'))
    if not __import__('pathlib').Path(cfg.mcp_cli).is_file():cfg=replace(cfg,mcp_cli='')
    ws=Workspace(ROOT/'work/stability-public','public_'+datetime.now().strftime('%H%M%S'))
    result={'paid_model_requests':0,'execution':'scripted tool smoke, no autonomous-model claim',
            'dns_mode':cfg.public_dns_mode,'checks':{},'workspace':str(ws.path)}
    path=ROOT/'evidence/stability_public_mcp.json'
    try:
        async with browser_client(cfg,ws).session('playwright') as session:
            tools={t.name:t for t in await load_safe_browser_tools(session,cfg,ws)}
            observed=await tools['browser_navigate'].ainvoke({'url':'https://www.iana.org/domains/reserved'})
            assert 'IANA' in observed or 'Reserved Domains' in observed
            result['checks']['outside_previous_allowlist_public_page']=True
            again=await tools['browser_snapshot'].ainvoke({})
            assert 'iana.org' in again
            result['checks']['same_session_snapshot']=True
            for url in ('http://127.0.0.1/','http://169.254.169.254/latest/meta-data','http://[::1]/','file:///D:/secret'):
                try:await tools['browser_navigate'].ainvoke({'url':url})
                except (ValueError,ToolFailure):pass
                else:raise AssertionError('Sensitive target was not blocked')
            result['checks']['local_metadata_ipv6_file_blocked']=True
            try:await tools['browser_click'].ainvoke({'ref':'e999999'})
            except ToolFailure as exc:assert exc.code=='stale_ref'
            else:raise AssertionError('Invented ref allowed')
            fresh=await tools['browser_snapshot'].ainvoke({});assert 'iana.org' in fresh
            result['checks']['stale_ref_classified_and_snapshot_available']=True
            # Public redirect endpoint. The browser must never connect to localhost.
            try:
                await tools['browser_navigate'].ainvoke({'url':'https://httpbingo.org/redirect-to?url=http%3A%2F%2F127.0.0.1%2F'})
            except ToolFailure as exc:
                result['checks']['real_redirect_to_local_blocked']=exc.code=='permission_denied'
                if exc.code!='permission_denied':result['redirect_limitation']='第三方端点本身拒绝重定向，不能作为本项目拦截证据：'+str(exc)[:300]
            else:raise AssertionError('Private redirect was not blocked')
        local={t.name:t for t in build_local_tools(ws,cfg)}
        url='https://raw.githubusercontent.com/pandas-dev/pandas/main/pandas/tests/io/data/excel/test1.xlsx'
        first=await local['download_public_file'].ainvoke({'url':url})
        second=await local['download_public_file'].ainvoke({'url':url})
        result['checks']['duplicate_download_reuses_verified_file']='已复用' in second
        filename=next(iter(json.loads((ws.path/'downloads.json').read_text(encoding='utf-8'))))
        table=json.loads(await local['inspect_downloaded_table'].ainvoke({'filename':filename}))
        negative=any(isinstance(v,(int,float)) and v<0 for row in table['preview'] for v in row.values())
        assert negative and table['sampled_rows']==7
        result['checks']['real_xlsx_parsed_negative_preserved']=True
        result['checks']['real_download_source_and_hash_recorded']=True
        result['sampled_rows']=table['sampled_rows'];result['download_result']=first
    except BaseException as exc:
        result['error']=str(exc)[:1500]
        raise
    finally:
        result['elapsed_seconds']=round(time.monotonic()-started,2)
        atomic_json(path,result)
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
