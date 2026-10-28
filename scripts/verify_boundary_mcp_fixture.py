"""Real MCP + controlled hostile page; NOT a public-site/model acceptance."""
import asyncio,json,time
from dataclasses import replace
from pathlib import Path
from web_agent.config import ROOT,Settings
from web_agent.browser import browser_client,load_safe_browser_tools
from web_agent.workspace import Workspace
from web_agent.harness import ToolFailure
from web_agent.persistence import atomic_json,read_json


async def main():
    hits=[]
    async def local(reader,writer):
        hits.append(True);writer.close()
    server=await asyncio.start_server(local,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    cfg=replace(Settings.load(),api_key='',allowed_domains=(),browser_headless=True,
                mcp_cli=str(ROOT/'work/mcp/node_modules/@playwright/mcp/cli.js'))
    if not Path(cfg.mcp_cli).is_file():cfg=replace(cfg,mcp_cli='')
    ws=Workspace(ROOT/'work/stability-fixtures','boundary_'+str(int(time.time())))
    record={'paid_model_requests':0,'real_mcp':True,'page':'controlled hostile test fixture','checks':{}}
    try:
        client=browser_client(cfg,ws);conn=client.connections['playwright']
        conn['args'][conn['args'].index('--init-page')+1]=str(ROOT/'tests/mcp_boundary_fixture.cjs')
        conn['env']['WEBTASK_PRODUCTION_GUARD']=str(ROOT/'scripts/browser_guard.cjs')
        conn['env']['WEBTASK_FIXTURE_LOCAL_TARGET']=f'http://127.0.0.1:{port}/sentinel'
        async with client.session('playwright') as session:
            tools={t.name:t for t in await load_safe_browser_tools(session,cfg,ws)}
            snap=await tools['browser_navigate'].ainvoke({'url':'https://example.com/__boundary/page'})
            assert 'Controlled security fixture' in snap
            assert 'RTC=undefined WT=undefined' in snap
            record['checks']['webrtc_webtransport_unavailable_before_scripts']=True
            denied=read_json(ws.path/'network_denial.json',{})
            assert denied and not hits
            record['checks']['image_fetch_websocket_no_local_connection']=True
            try:await tools['browser_navigate'].ainvoke({'url':'https://example.com/__boundary/redirect'})
            except ToolFailure as exc:record['checks']['redirect_classified']=exc.code=='permission_denied'
            else:raise AssertionError('Hostile redirect was not surfaced as blocked')
            assert record['checks']['redirect_classified'] and not hits
            record['checks']['controlled_redirect_to_local_blocked']=True
        record['local_sentinel_connections']=len(hits)
    finally:
        server.close();await server.wait_closed()
        atomic_json(ROOT/'evidence/stability_boundary_mcp_fixture.json',record)
        print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
