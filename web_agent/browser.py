"""Persistent MCP browser, with only read tools exposed to the model."""
from __future__ import annotations
import os
import asyncio
import re
import shutil
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import urljoin,urlsplit,urlencode
from typing import Literal
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from .config import Settings, ROOT
from .security import validate_public_url,binary_document_url,image_document_url
from .workspace import Workspace
from .persistence import atomic_json, read_json, redact
from .harness import ToolFailure
from .forms import FormController,BrowserTools,decode_mcp_json
from .dom_matrix import DOM_MATRIX_JS,validate_tables,compact_tables,save_table_binding


class SafeBrowserClient:
    def __init__(self, settings, workspace):
        self.settings, self.workspace = settings, workspace
        self.client=_mcp_client(settings,workspace,'http://127.0.0.1:1')
        self.connections=self.client.connections

    @asynccontextmanager
    async def session(self, name):
        from .egress import PublicEgress
        def record(event, message, **fields):
            if self.workspace:
                self.workspace.log(event, message, **fields)
                atomic_json(self.workspace.path/'network_denial.json',{'time':time.time(),'message':message,**fields})

        async with PublicEgress(
                dns_mode=self.settings.public_dns_mode,
                log=record,
                allowed_domains=self.settings.allowed_domains
        ) as egress:
            _mcp_client(self.settings, self.workspace, egress.url)

            session_cm = self.client.session(name)
            session = await session_cm.__aenter__()
            try:
                yield session
            finally:
                if self.workspace:
                    self.workspace.log("mcp_close_start", "")
                # A browser/MCP teardown can hang after the graph has already
                # returned. Bound cleanup so it cannot consume the task timeout
                # and turn a completed graph into a false "timeout".
                try:
                    await asyncio.wait_for(
                        session_cm.__aexit__(None, None, None), timeout=15)
                except asyncio.TimeoutError:
                    if self.workspace:
                        self.workspace.log(
                            "mcp_close_timeout",
                            "浏览器/MCP 清理超过 15 秒，已放弃等待，保留任务结果")
            if self.workspace:
                self.workspace.log("mcp_close_done", "")
                self.workspace.log("egress_close_start", "")

        if self.workspace:
            self.workspace.log("egress_close_done", "")


def browser_client(settings: Settings, workspace: Workspace | None = None):
    return SafeBrowserClient(settings, workspace)


def _mcp_client(settings, workspace, proxy_url):
    binary = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not binary:
        raise RuntimeError("未找到 npx；请安装 Node.js 20+。")
    flags = ["--isolated", "--no-webmcp", "--image-responses=omit", "--block-service-workers",
             f"--browser={settings.browser_name}", "--init-page", str(ROOT / "scripts" / "browser_guard.cjs")]
    if workspace:
        flags += ["--output-dir", str(workspace.path / "browser")]
    config_path=(workspace.path/'browser_mcp_config.json') if workspace else ROOT/'work'/'browser_mcp_config.json'
    atomic_json(config_path,{'browser':{'launchOptions':{'args':[
        '--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--proxy-bypass-list=<-loopback>', '--disable-background-networking',
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']},
        'contextOptions':{'acceptDownloads':True,'serviceWorkers':'block',
                          'proxy':{'server':proxy_url,'bypass':'<-loopback>'}}}})
    flags += ['--config',str(config_path)]
    if settings.browser_headless:
        flags.append("--headless")
    if settings.mcp_cli:
        command, args = shutil.which("node"), [settings.mcp_cli, *flags]
    elif os.name == "nt":
        command, args = os.environ.get("COMSPEC", "cmd.exe"), ["/d", "/c", binary, "-y", f"@playwright/mcp@{settings.mcp_version}", *flags]
    else:
        command, args = binary, ["-y", f"@playwright/mcp@{settings.mcp_version}", *flags]
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"}}
    env["WEBTASK_ALLOWED_DOMAINS"] = ",".join(settings.allowed_domains)
    env['WEBTASK_PINNED_EGRESS'] = 'true'
    # MCP browser locks/cache must stay beside the project, including direct CLI runs.
    cache=ROOT/'work'/'playwright';cache.mkdir(parents=True,exist_ok=True)
    env['PLAYWRIGHT_BROWSERS_PATH']=str(cache)
    local_cache=ROOT/'work'/'localappdata';local_cache.mkdir(parents=True,exist_ok=True)
    env['LOCALAPPDATA']=str(local_cache)
    env['npm_config_cache']=str(ROOT/'work'/'npm-cache')
    env['TEMP']=env['TMP']=str(ROOT/'work'/'tmp')
    (ROOT/'work'/'tmp').mkdir(parents=True,exist_ok=True)
    if workspace:env['WEBTASK_ACTION_CAPABILITY']=str(workspace.path/'action_capability.json')
    if workspace:env['WEBTASK_DENIAL_FILE']=str(workspace.path/'network_denial.json')
    if workspace:
        env['WEBTASK_DOWNLOAD_DIR']=str(workspace.path/'browser'/'downloads')
        env['WEBTASK_DOWNLOAD_MAX_BYTES']=str(settings.max_pdf_bytes)
    return MultiServerMCPClient({"playwright": {"command": command, "args": args, "env": env, "transport": "stdio"}})


def text_result(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in result)
    return str(result)


def compact_snapshot(text,limit=16000):
    """Retain actionable refs/URLs, including late-page controls in large sites."""
    lines=[line.strip() for line in text.splitlines() if re.search(r'\[ref=|/url:|^- Page |^Page URL:|^###|^Error:',line)]
    lines=[line for line in lines if not re.match(r'- (generic|listitem|list|navigation|banner|contentinfo|group|paragraph)(?: |\[|:)',line)]
    text='\n'.join(lines)
    if len(text)>limit:text=text[:3000]+'\n[中间导航已压缩；可重新获取快照]\n'+text[-(limit-3100):]
    return text


def check_site_challenge(text):
    """Detect an interstitial, including rendered text; not an article about bots."""
    titles = re.findall(r'(?im)^- Page Title:\s*(.+)$', text)
    blocked_title = any(re.search(
        r'(?i)^(?:sign in|log in|login|authentication required|access denied|just a moment)[.!…\s]*$'
        r'|^(?:.*(?:安全验证|人机验证|验证码))$|^(?:captcha|verify (?:that )?you are human|security verification)[.!…\s]*$',
        title.strip()) for title in titles)
    challenge_control = re.search(
        r'(?im)^\s*(?:-\s*(?:heading|checkbox|button|paragraph|generic)\s*["\u201c]?)?'
        r'(?:please\s+)?(?:verify (?:that )?you are human|checking (?:your browser|if (?:the site connection is secure|you are human))|'
        r'请完成人机验证|请完成安全验证|请验证您是真人|请验证你是真人)(?:[.!…\s"\u201d]|$)', text)
    if blocked_title or challenge_control:
        match = re.search(r'(?m)^- Page URL: (\S+)|^Page URL: (\S+)', text)
        url = (match.group(1) or match.group(2)) if match else ''
        raise ToolFailure('site_challenge','网站要求登录或人工验证；已停止此访问，不会绕过验证。可选择其他公开来源。', details={'url':url} if url else None)


def search_url(query:str,engine:str='bing'):
    """Fixed public providers; query cannot choose a target or add URL parameters."""
    providers={'bing':'https://www.bing.com/search','duckduckgo':'https://duckduckgo.com/','baidu':'https://www.baidu.com/s'}
    if engine not in providers:raise ValueError('搜索引擎只支持bing、baidu或duckduckgo')
    query=query.strip()
    if not 1<=len(query)<=300 or any(ord(c)<32 for c in query) or redact(query)!=query or re.search(r'(?i)(?:authorization|password|api[_-]?key|cookie)\s*[:=]',query):
        raise ValueError('公开搜索词需1–300字符，不能含凭证或控制字符')
    return providers[engine]+'?'+urlencode({'wd' if engine=='baidu' else 'q':query})


async def load_safe_browser_tools(session, settings: Settings, workspace: Workspace):
    raw = {item.name: item for item in await load_mcp_tools(session)}
    if not {"browser_navigate", "browser_snapshot"} <= raw.keys():
        raise RuntimeError("MCP 未提供 navigate/snapshot。")
    current = {"url": "", "snapshot": ""}

    async def capture(name, args):
        request_started=time.time()
        text = text_result(await raw[name].ainvoke(args))
        workspace.log('mcp_response',text[:1800],tool=name)
        status=re.search(r'(?m)^- HTTP status: (\d+)',text)
        if status and int(status.group(1))>=400:
            code=int(status.group(1))
            raise ToolFailure('http_'+str(code),f'网站返回 HTTP {code}；未将错误页面作为有效研究来源',retryable=code in {408,429} or code>=500)
        if re.search(r"(?im)^### Error|^Error:|isError.*true", text):
            from .harness import browser_failure
            denied=read_json(workspace.path/'network_denial.json',{})
            if any(s in text for s in ('ERR_TUNNEL_CONNECTION_FAILED','ERR_BLOCKED_BY_CLIENT','ERR_HTTP_RESPONSE_CODE_FAILURE')) and denied.get('time',0)>=request_started-.05:
                raise ToolFailure('permission_denied',denied.get('message','安全策略拒绝'))
            raise browser_failure(text)
        check_site_challenge(text)
        if name!='browser_snapshot' and '```yaml' not in text:
            text += '\n' + text_result(await raw['browser_snapshot'].ainvoke({}))
        check_site_challenge(text)
        match = re.search(r"(?m)^- Page URL: (\S+)|^Page URL: (\S+)", text)
        if match:
            current["url"] = validate_public_url(match.group(1) or match.group(2), settings.allowed_domains)
        if image_document_url(current["url"]):
            raise ToolFailure(
                'image_document',
                '这是图片文件，当前不能提取文字（没有 OCR）；不要反复打开图片表格，'
                '请改用 XLSX/PDF/HTML 数据源或其它官方公开来源。')
        # Host-controlled constant expression: model cannot inject JavaScript.
        # Rendered text reconnects inline DOM fragments for reliable quotations.
        if 'browser_evaluate' in raw:
            rendered = text_result(await raw['browser_evaluate'].ainvoke({'function':"() => { const main = document.querySelector('main'); return main?.innerText?.trim() ? main.innerText : document.body.innerText; }"}))
            try:
                rendered = json.JSONDecoder().raw_decode(rendered.split('### Result\n',1)[1].lstrip())[0]
            except (ValueError,IndexError):
                pass
            text = '### Rendered page text\n' + rendered[:7500] + '\n### Observed controls\n' + compact_snapshot(text)
        # A real table/grid can bind each cell to its actual column header.
        # No invented product ordering from innerText and no model-supplied JS.
        tables=[]
        if 'browser_evaluate' in raw and re.search(r'(?m)^- (?:table|grid) ', text):
            try:
                tables=validate_tables(decode_mcp_json(await raw['browser_evaluate'].ainvoke({'function':DOM_MATRIX_JS})))
            except (ValueError,TypeError,KeyError,ToolFailure):
                # An unstructured page remains readable, but cannot assert a
                # product-cell binding without a validated DOM table.
                tables=[]
        if tables:
            text=compact_tables(tables)+'\n'+text
        requested_url=args.get("url") if name=="browser_navigate" and match else None
        if requested_url and current["url"] and requested_url!=current["url"]:
            text="实际读取地址："+current["url"]+"\n请求地址发生跳转，登记来源使用上面的实际地址。\n"+text
        text = text[:24000]
        check_site_challenge(text)
        if re.search(r'(?im)^- Page Title:.*(?:404|not found)',text):
            raise ToolFailure('http_404','页面不存在，不能作为有效来源。')
        oid=""
        if current["url"]:
            current["snapshot"] = text
            oid=workspace.observe(current["url"], text, requested_url=requested_url)
            if tables:
                save_table_binding(workspace,oid,current["url"],text,tables)
            atomic_json(workspace.path / "browser_state.json",
                        {"url": current["url"], "observation_id": oid})
        return text[:24000] + (f"\n\n[observation_id: {oid}]" if oid else "")

    @tool("browser_navigate")
    async def navigate(url: str) -> str:
        """用真实浏览器读取公开网页；返回 URL 和可访问性快照。禁止外部写操作。"""
        if binary_document_url(url):
            raise ToolFailure('file_tool_required','二进制文件请用下载/表格/PDF工具；不要用浏览器重复打开文件或其目录。')
        if re.search(r'/(?:logout|signout|delete|remove|unsubscribe|checkout|purchase|publish|send)(?:/|$)',urlsplit(url).path,re.I):
            raise ToolFailure('approval_required','链接可能执行远程变更，普通导航不执行；请请求人工处理。')
        valid=validate_public_url(url,settings.allowed_domains)
        if image_document_url(valid):
            raise ToolFailure(
                'image_document',
                '这是图片文件，当前不能提取文字（没有 OCR）；不要反复打开图片表格，'
                '请改用 XLSX/PDF/HTML 数据源或其它官方公开来源。')
        from .execution_budget import require_observed_path_after_not_found
        require_observed_path_after_not_found(workspace,valid)
        failed=read_json(workspace.path/'failed_urls.json',{})
        if valid in failed:
            prior=failed[valid] if isinstance(failed[valid],dict) else {}
            raise ToolFailure(
                'url_quarantined',
                f'该网址本任务已失败（{prior.get("code","hard_failure")}）；不要重试同一 URL，'
                '请换来源或改用搜索/下载工具。')
        try:
            return await capture("browser_navigate", {"url":valid})
        except ToolFailure as exc:
            if exc.code in {'http_404','http_403','http_410','image_document','unsupported_document'}:
                failed[valid]={'code':exc.code,'message':str(exc)[:240]}
                atomic_json(workspace.path/'failed_urls.json',failed)
            raise

    @tool('browser_search')
    async def search(query:str,engine:Literal['bing','baidu','duckduckgo']='bing')->str:
        """搜索公开网页，返回真实结果链接。query是关键词，可含site:和日期；engine可选bing/baidu/duckduckgo。Bing优先用RSS结果，避免只读到搜索页导航壳；打开真实结果原文后再record_source，不把搜索摘要当最终事实。遇到验证码/登录停止该路径。"""
        from .search_api import api_search
        try:
            return await api_search(settings,query,workspace)
        except Exception as exc:
            workspace.log('search_api_fallback',str(exc)[:300])
        from .search_rss import bing_rss, search_360, search_sogou
        try:
            return await search_360(settings,query,workspace)
        except Exception as exc:
            workspace.log('search_360_fallback',str(exc)[:300])
        try:
            return await search_sogou(settings,query,workspace)
        except Exception as exc:
            workspace.log('search_sogou_fallback',str(exc)[:300])
        if engine=='bing':
            try:
                return await bing_rss(settings,query,workspace)
            except Exception as exc:
                if isinstance(exc,ToolFailure) and exc.code not in {'search_empty','transport'}:
                    raise
                workspace.log('search_rss_fallback',str(exc)[:300])
        url=validate_public_url(search_url(query,engine),settings.allowed_domains)
        output=await capture('browser_navigate',{'url':url})
        # Search pages hydrate after DOMContentLoaded; observe once more, never resubmit.
        await asyncio.sleep(.6)
        output=await capture('browser_snapshot',{})
        if 'browser_evaluate' in raw:
            count=decode_mcp_json(await raw['browser_evaluate'].ainvoke({'function':"() => Array.from(document.querySelectorAll('li.b_algo h2 a, #content_left h3 a, a.result__a, [data-testid=\"result-title-a\"]')).filter(a => a.getClientRects().length > 0).length"}))
            if count==0:
                if engine!='bing':
                    try:
                        return await bing_rss(settings,query,workspace)
                    except ToolFailure:
                        pass
                raise ToolFailure('search_empty','搜索页已打开，但没有读取到可验证的结果项；可能无匹配结果、页面未加载完整或站点限制。不得把导航壳页面作为有效搜索结果；可调整关键词、使用其他公开搜索引擎或官网站内搜索，不绕过验证码。')
        return '搜索结果仅供发现来源；请打开真实结果原文再引用。\n'+output

    @tool("browser_snapshot")
    async def snapshot() -> str:
        """读取当前页面。进程恢复时重建最后 URL，旧 refs 不保留。"""
        if not current["url"]:
            saved = read_json(workspace.path / "browser_state.json", {})
            if saved.get("url"):
                return await navigate.ainvoke({"url": saved["url"]})
        return await capture("browser_snapshot", {})

    @tool("browser_follow_link")
    async def follow_link(ref: str) -> str:
        """跟随最近快照中真实存在的 link ref；读取链接，不点击按钮或提交表单。"""
        lines = current["snapshot"].splitlines()
        for i, line in enumerate(lines):
            if "link " in line and f"[ref={ref}]" in line:
                for child in lines[i+1:i+4]:
                    match = re.search(r"/url: (.+)", child)
                    if match:
                        href=match.group(1).strip()
                        if href.startswith('"'):
                            try: href=json.loads(href)
                            except ValueError: pass
                        elif href.startswith("'") and href.endswith("'"): href=href[1:-1].replace("''", "'")
                        return await navigate.ainvoke({"url": urljoin(current["url"],href)})
        raise ToolFailure('stale_ref',"ref 过期、不是链接或无可验证 URL；请重新 browser_snapshot，或用已读取的公开链接导航。")
    @tool('submit_public_form')
    async def submit_public_form(url:str,form_index:int,fields:dict[str,str],purpose:str)->str:
        """请求提交已观察的普通公开POST表单；form_index从0开始，fields为字段名和值。宿主先展示目标/字段并暂停，只有用户明确批准该次操作才执行；不支持登录、支付、上传和隐藏字段。"""
        raise RuntimeError('Only the approval dispatcher may execute form submissions')
    @tool('inspect_public_forms')
    async def inspect_public_forms(url:str)->str:
        """读取公开页的表单序号、action、method和字段name/type，不提交、不读取字段当前值；为受审批的普通表单操作提供真实结构。"""
        await navigate.ainvoke({'url':url})
        result=await raw['browser_evaluate'].ainvoke({'function':"() => Array.from(document.forms).slice(0,10).map((f,i)=>({form_index:i,action:f.action,method:f.method,fields:Array.from(f.elements).filter(e=>e.name).map(e=>({name:e.name,type:e.type,required:e.required}))}))"})
        return json.dumps(decode_mcp_json(result),ensure_ascii=False)
    controller=FormController(workspace,settings,raw['browser_evaluate'].ainvoke,navigate.ainvoke) if 'browser_evaluate' in raw else None
    from .browser_actions import BrowserActions
    actions=BrowserActions(raw,current,settings,workspace,capture)
    @tool('browser_click')
    async def click(ref:str)->str:
        """点击最近快照中实际存在的ref，限公开链接、搜索、翻页、展开等读取操作。敏感按钮被阻止并要求人工处理。"""
        return await actions.click(ref)
    @tool('browser_type')
    async def type_search(ref:str,text:str)->str:
        """向实际观察到的搜索框ref输入查询词，不自动按回车；之后查看返回快照并点击搜索按钮。不支持普通编辑、密码或文件输入。"""
        return await actions.type(ref,text)
    @tool('browser_select_option')
    async def select_option(ref:str,values:list[str])->str:
        """在最近快照的公开筛选下拉框ref选择真实option value；非GET或敏感表单禁止。"""
        return await actions.select(ref,values)
    @tool('browser_download')
    async def download(ref:str)->str:
        """实际点击最近快照中的下载链接/按钮ref，接收CSV/XLSX/PDF，安全导入任务目录并返回public_文件名。再用表格工具分析。"""
        return await actions.download(ref)
    interactive=[click,type_search,select_option,download] if {'browser_evaluate','browser_click','browser_type','browser_select_option'}<=raw.keys() else []
    return BrowserTools([navigate,search,snapshot,follow_link]+interactive+([inspect_public_forms,submit_public_form] if controller else []),controller)
