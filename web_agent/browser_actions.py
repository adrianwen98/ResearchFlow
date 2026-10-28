"""Observed-ref browser interactions. No model JavaScript or arbitrary selectors."""
import asyncio
import hashlib
import json
import re
from pathlib import Path
from .forms import decode_mcp_json, RISKY
from .harness import ToolFailure
from .persistence import atomic_json, read_json, redact
from .security import validate_public_url
from .documents import validate_document,write_download

INSPECT = r"""e => ({tag:e.tagName.toLowerCase(),type:e.type||'',name:e.name||'',
 label:(e.getAttribute('aria-label')||e.innerText||e.placeholder||e.getAttribute('title')||
 (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ')||'').slice(0,300),
 href:e.href||'',disabled:!!e.disabled,editable:e.isContentEditable,
 form:e.form?{method:e.form.method,action:e.form.action,
 fields:Array.from(e.form.elements).map(x=>({name:x.name,type:x.type})).slice(0,30)}:null,
 role:e.getAttribute('role')||'',searchRegion:!!e.closest('[role="search"]'),
 options:e.tagName==='SELECT'?Array.from(e.options).map(o=>({value:o.value,label:o.text})).slice(0,100):[]})"""
READ_ACTION=re.compile(r'search|filter|next|previous|more|expand|collapse|details?|view|show|download|export|^go$|搜索|检索|筛选|下一|上一|更多|展开|收起|详情|查看|下载|导出',re.I)
SIDE_EFFECT=re.compile(r'logout|log.?out|sign.?out|unsubscribe|subscribe|publish|upload|\bsend\b|\bremove\b|\breset\b|提交|发送|订阅|退订|注销|退出登录|删除|上传',re.I)
QUERY=re.compile(r'^(q|query|search|searchword|searchterm|keywords?|s|text)$',re.I)


def authorize_control(meta,action,allowed_domains,values=None):
    if meta.get('disabled') or meta.get('editable'):raise ToolFailure('permission_denied','禁用/富文本控件不支持')
    descriptor=' '.join(str(meta.get(k,'')) for k in ('label','href','name','type'))
    if RISKY.search(descriptor) or SIDE_EFFECT.search(descriptor):raise ToolFailure('approval_required','敏感控件需人工确认/处理；普通公开POST请使用inspect_public_forms和submit_public_form审批流程')
    form=meta.get('form')
    if form:
        if form['method'].lower()!='get':raise ToolFailure('approval_required','非GET表单不能通过普通点击或输入执行；请使用审批工具')
        validate_public_url(form['action'],allowed_domains)
        if RISKY.search(form['action']) or any(x['type'] in {'password','file'} or RISKY.search(x['name']) for x in form['fields']):
            raise ToolFailure('approval_required','表单涉及敏感字段或目标，交人工处理')
    tag=meta.get('tag')
    if action=='type':
        if tag!='input' or meta.get('type') not in {'text','search'} or not (meta.get('type')=='search' or QUERY.fullmatch(meta.get('name','')) or meta.get('searchRegion')):
            raise ToolFailure('permission_denied','仅支持搜索框输入；联系、账号及普通编辑框需审批/人工处理')
    elif action=='select':
        if tag!='select' or not values or len(values)>5 or set(values)-{x['value'] for x in meta.get('options',[])}:raise ToolFailure('invalid_input','必须选择实际存在的下拉选项value')
        if any(RISKY.search(x['label']+' '+x['value']) for x in meta['options'] if x['value'] in values):raise ToolFailure('approval_required','敏感下拉操作需人工处理')
    elif action in {'click','download'}:
        if tag=='a' and meta.get('href'):
            href=meta['href']
            validate_public_url(href[5:] if action=='download' and href.startswith('blob:') else href,allowed_domains)
        elif tag not in {'button','input','summary'} or not READ_ACTION.search(meta.get('label','')):
            raise ToolFailure('approval_required','只允许可识别的搜索、筛选、翻页、展开和下载按钮；其它操作交人工确认')
        if form and (meta.get('type')=='submit' or tag=='button') and not (READ_ACTION.search(meta.get('label','')) and any(QUERY.fullmatch(x['name']) or x['type']=='search' for x in form['fields'])):
            raise ToolFailure('approval_required','只支持GET搜索/筛选表单，不执行一般表单提交')
        if action=='download' and not (re.search(r'\.(csv|xlsx|pdf)(?:[?#]|$)',meta.get('href',''),re.I) or re.search(r'download|export|下载|导出',meta.get('label',''),re.I)):
            raise ToolFailure('invalid_input','这不是已观察到的文件下载链接/按钮')


class BrowserActions:
    def __init__(self,raw,current,settings,workspace,capture):
        self.raw,self.current,self.settings,self.workspace,self.capture=raw,current,settings,workspace,capture

    async def inspect(self,ref,action,values=None):
        if not re.fullmatch(r'(?:f\d+)?e\d+',ref) or f'[ref={ref}]' not in self.current['snapshot']:
            raise ToolFailure('stale_ref','ref不在最近观察中；先browser_snapshot，不允许猜测CSS选择器')
        result=await self.raw['browser_evaluate'].ainvoke({'target':ref,'function':INSPECT})
        meta=decode_mcp_json(result)
        if not meta.get('label'):
            observed=next(line for line in self.current['snapshot'].splitlines() if f'[ref={ref}]' in line)
            match=re.search(r'"([^"\n]+)"',observed)
            if match:meta['label']=match.group(1)
        self.workspace.log('browser_control_inspected',meta,ref=ref,action=action)
        authorize_control(meta,action,self.settings.allowed_domains,values)
        return meta

    async def click(self,ref):
        await self.inspect(ref,'click')
        return await self.capture('browser_click',{'target':ref})

    async def type(self,ref,text):
        if not isinstance(text,str) or not 1<=len(text)<=300 or redact(text)!=text or any(c in text for c in '\n\r\x00'):
            raise ValueError('搜索词需1–300字符，不能包含凭证或控制字符')
        await self.inspect(ref,'type')
        return await self.capture('browser_type',{'target':ref,'text':text,'submit':False})

    async def select(self,ref,values):
        await self.inspect(ref,'select',values)
        return await self.capture('browser_select_option',{'target':ref,'values':values})

    async def download(self,ref):
        meta=await self.inspect(ref,'download')
        for name,prior in read_json(self.workspace.path/'downloads.json',{}).items():
            file=self.workspace.path/name
            if meta.get('href')==prior.get('url') and prior.get('sha256') and file.is_file() and not file.is_symlink() and file.resolve().parent==self.workspace.path.resolve():
                if hashlib.sha256(file.read_bytes()).hexdigest()==prior['sha256']:
                    return json.dumps({'filename':name,'url':prior['url'],'reused':True,'bytes':file.stat().st_size},ensure_ascii=False)
        folder=self.workspace.path/'browser'/'downloads';folder.mkdir(parents=True,exist_ok=True)
        before={p.name for p in folder.glob('*.json')}
        # Native browser click triggers the actual browser download, including JS buttons.
        output=await self.capture('browser_click',{'target':ref})
        records=[]
        for _ in range(100):
            records=[read_json(p,{}) for p in folder.glob('*.json') if p.name not in before]
            if records:break
            await asyncio.sleep(.1)
        if not records:raise ToolFailure('download_not_observed','没有观察到完成的CSV/XLSX/PDF下载；请刷新页面检查真实下载入口，不要盲目重试')
        if len(records)!=1:raise ToolFailure('download_ambiguous','一次点击触发多个下载，未自动入库；需人工检查')
        item=records[0]
        if item.get('error'):raise ToolFailure('invalid_document',item['error'])
        url=item['url']
        if url.startswith('blob:'):
            from urllib.parse import urlsplit
            origin=urlsplit(url[5:]);page=urlsplit(item.get('page_url',''))
            if (origin.scheme,origin.netloc)!=(page.scheme,page.netloc):raise ToolFailure('permission_denied','Blob来源不匹配')
            url=item['page_url']
        url=validate_public_url(url,self.settings.allowed_domains)
        file=folder/item['file'];suffix=file.suffix.lower()
        if file.resolve().parent!=folder.resolve() or file.is_symlink() or suffix not in {'.csv','.xlsx','.pdf'} or not file.is_file():raise ToolFailure('invalid_document','下载路径/类型无效')
        if file.stat().st_size>self.settings.max_pdf_bytes:raise ToolFailure('size_limit','下载文件超过任务体积限制，未入库')
        data=file.read_bytes()
        validate_document(data,suffix)
        name='public_'+hashlib.sha256(data).hexdigest()[:12]+suffix
        destination=write_download(self.workspace,name,data)
        manifest=read_json(self.workspace.path/'downloads.json',{})
        manifest[name]={'url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'method':'browser_click','suggested_filename':item.get('suggested_filename','')}
        atomic_json(self.workspace.path/'downloads.json',manifest)
        self.workspace.log('browser_download',name,url=url,bytes=len(data))
        return json.dumps({'filename':name,'url':url,'bytes':len(data),'page_snapshot':output[:16000]},ensure_ascii=False)
