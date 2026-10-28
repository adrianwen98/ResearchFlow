"""Version-bound approval and a durable at-most-one-attempt form submission gate."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit
from .config import ROOT
from .persistence import atomic_json,read_json,redact
from .security import validate_public_url
from .harness import ToolFailure


RISKY=re.compile(r'password|passwd|token|secret|auth|credit|card|bank|account|payment|purchase|checkout|login|register|sign.?up|sign.?in|delete|transfer|\bbuy\b|\bpay\b|\border\b|支付|购买|下单|银行|身份证|密码|验证码|账户',re.I)


def decode_mcp_json(result):
    if isinstance(result,list):text='\n'.join(p.get('text','') if isinstance(p,dict) else str(p) for p in result)
    else:text=str(result)
    if re.search(r'(?im)^### Error|^Error:',text):raise ToolFailure('form_error',text[:600])
    try:return json.JSONDecoder().raw_decode(text.split('### Result\n',1)[-1].lstrip())[0]
    except (ValueError,IndexError) as exc:raise ToolFailure('form_error','MCP未返回可核验的表单结果') from exc


class BrowserTools(list):
    def __init__(self,items,form_controller):super().__init__(items);self.form_controller=form_controller


class FormController:
    def __init__(self,workspace,settings,evaluate,navigate):
        self.workspace,self.settings,self.evaluate,self.navigate=workspace,settings,evaluate,navigate
        self.folder=workspace.path/'approvals';self.folder.mkdir(exist_ok=True)
        self.capability=workspace.path/'action_capability.json'
        self.runtime=(ROOT/'scripts'/'form_runtime.js').read_text(encoding='utf-8')

    async def call_runtime(self,spec):
        expression='async () => { '+self.runtime+'; return await webtaskForm('+json.dumps(spec,ensure_ascii=True)+'); }'
        return decode_mcp_json(await self.evaluate({'function':expression}))

    async def prepare(self,call_id,args):
        url=validate_public_url(args.get('url',''),self.settings.allowed_domains)
        fields=args.get('fields',{})
        if not isinstance(fields,dict) or not 1<=len(fields)<=12:raise ValueError('表单字段需1–12项')
        if not isinstance(args.get('form_index'),int) or not 0<=args['form_index']<=20:raise ValueError('无效的form_index')
        for key,value in fields.items():
            if not isinstance(value,str) or len(value)>1500 or len(key)>100 or RISKY.search(key):raise ValueError('字段类型、长度或敏感类型不支持')
            if key.lower() not in {'topic','subject','message','feedback','comment','name','email'}:raise ValueError('当前只支持普通文字反馈/联系表单字段')
            if redact(value)!=value:raise ValueError('不允许提交密钥或密码')
        if RISKY.search(url+' '+args.get('purpose','')):raise ValueError('账户、支付、购买、删除等高风险操作需人工完成')
        operation=hashlib.sha256(json.dumps([self.workspace.task_id,call_id,args],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        path=self.folder/(operation+'.json')
        saved=read_json(path)
        if saved:
            validate_public_url(saved['action_url'],self.settings.allowed_domains)
            return saved
        await self.navigate({'url':url})
        inspected=await self.call_runtime({'mode':'inspect','form_index':args['form_index'],'fields':fields})
        descriptor=inspected['descriptor']
        target=validate_public_url(descriptor['action'],self.settings.allowed_domains)
        if (urlsplit(target).scheme,urlsplit(target).netloc)!=(urlsplit(descriptor['page_url']).scheme,urlsplit(descriptor['page_url']).netloc):
            raise ValueError('只支持同源公开表单')
        if descriptor['method']!='POST' or descriptor['enctype']!='application/x-www-form-urlencoded':raise ValueError('仅支持普通POST编码表单')
        if RISKY.search(target+' '+descriptor['text']+' '+descriptor['title']):raise ValueError('敏感表单需人工处理')
        if not re.search(r'feedback|contact|comment|反馈|留言|联系|建议|评论',descriptor['text']+' '+descriptor['title'],re.I):raise ValueError('只支持可识别的普通反馈/联系表单')
        body_hash=hashlib.sha256(inspected['body'].encode()).hexdigest()
        for prior_path in self.folder.glob('*.json'):
            prior=read_json(prior_path,{})
            if prior.get('state') in {'started','uncertain'} and prior.get('action_url')==target and prior.get('body_sha256')==body_hash:
                raise ToolFailure('action_uncertain','相同目标和内容存在未确认的提交，即使换调用ID也禁止自动重发；请人工核对')
        saved={'approval_id':operation,'url':url,'action_url':target,'method':'POST','fields':fields,
               'purpose':args.get('purpose','')[:500],'form_index':args['form_index'],
               'descriptor_json':inspected['descriptor_json'],'body_sha256':body_hash,
               'expires_at':time.time()+600,'state':'prepared'}
        atomic_json(path,saved)
        return saved

    async def execute(self,prepared,decision):
        key=prepared['approval_id'];path=self.folder/(key+'.json');saved=read_json(path)
        if decision.get('decision')!='approve':raise ToolFailure('approval_required','执行表单必须有明确的approve决策')
        if decision.get('approval_id')!=key:raise ToolFailure('approval_mismatch','审批ID与具体操作不匹配')
        if saved['state']=='completed':return saved['receipt']
        if saved['state'] in {'started','uncertain'}:raise ToolFailure('action_uncertain','上次提交可能已生效；禁止自动重发，请人工核对')
        if saved['expires_at']<time.time():raise ToolFailure('approval_expired','审批预览已过期，请重新发起')
        validate_public_url(saved['action_url'],self.settings.allowed_domains)
        await self.navigate({'url':saved['url']})
        spec={'mode':'inspect','form_index':saved['form_index'],'fields':saved['fields']}
        inspected=await self.call_runtime(spec)
        if inspected['descriptor_json']!=saved['descriptor_json'] or hashlib.sha256(inspected['body'].encode()).hexdigest()!=saved['body_sha256']:
            raise ToolFailure('approval_stale','表单/目标/内容已变化，原审批失效')
        saved['state']='started';atomic_json(path,saved)
        atomic_json(self.capability,{'approval_id':key,'url':saved['action_url'],'method':'POST',
                                    'body_sha256':saved['body_sha256'],'expires_at':time.time()+30})
        try:
            response=await self.call_runtime({**spec,'mode':'submit','expected_descriptor_json':saved['descriptor_json']})
            if not 200<=response.get('status',0)<300:raise ToolFailure('action_uncertain','服务端返回非成功状态，不能确认副作用；请人工核对')
            receipt={'approval_id':key,'external_action_executed_by_agent':True,'url':saved['action_url'],
                     'http_status':response['status'],'response_preview':str(response.get('text',''))[:1000]}
            saved.update(state='completed',receipt=receipt);atomic_json(path,saved)
            self.workspace.log('approved_action_completed','',approval_id=key,url=saved['action_url'],http_status=response['status'])
            return receipt
        except BaseException:
            saved['state']='uncertain';atomic_json(path,saved)
            raise
        finally:self.capability.unlink(missing_ok=True)
