import hashlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlencode
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from test_graph import settings,ScriptModel,call
from web_agent.forms import FormController,BrowserTools
from web_agent.runner import run_task
from web_agent.workspace import Workspace
from web_agent.harness import ToolFailure
from web_agent.persistence import read_json,atomic_json


ARGS={'url':'https://example.com/feedback','form_index':0,'fields':{'topic':'agent test','message':'Controlled test only'},'purpose':'Submit test feedback'}


class RuntimeFixture:
    def __init__(self):self.submissions=0;self.changed=False;self.fail=False
    async def navigate(self,args):return args['url']
    async def evaluate(self,args):
        spec=json.JSONDecoder().raw_decode(args['function'].rsplit('return await webtaskForm(',1)[1])[0]
        descriptor={'page_url':ARGS['url'],'action':'https://example.com/submit','method':'POST',
                    'enctype':'application/x-www-form-urlencoded','title':'Feedback','text':'Changed' if self.changed else 'Feedback',
                    'controls':[{'name':'topic','type':'text'},{'name':'message','type':'textarea'}]}
        if spec['mode']=='inspect':response={'descriptor':descriptor,'descriptor_json':json.dumps(descriptor),'body':urlencode(spec['fields'])}
        else:
            self.submissions+=1
            if self.fail:raise TimeoutError('Response lost after possible submission')
            response={'status':200,'url':descriptor['action'],'text':'Recorded'}
        return '### Result\n'+json.dumps(response)


@pytest.mark.asyncio
@pytest.mark.parametrize('decision,submissions',[('approve',1),('reject',0),('handled',0)])
async def test_form_interrupt_only_executes_matching_explicit_approval(settings,decision,submissions):
    runtime=RuntimeFixture()
    @asynccontextmanager
    async def browser(config,ws):
        @tool('submit_public_form')
        async def submit(url:str,form_index:int,fields:dict[str,str],purpose:str)->str:
            """Only graph gate can call the controller."""
            raise AssertionError('raw tool bypassed gate')
        controller=FormController(ws,config,runtime.evaluate,runtime.navigate)
        yield BrowserTools([submit],controller)
    config=replace(settings,allowed_domains=('example.com',))
    initial=await run_task('Submit controlled feedback','form_'+decision,config,
                           model=ScriptModel([call('submit_public_form',ARGS,'form')]),browser_factory=browser)
    assert initial.status=='awaiting_review' and runtime.submissions==0
    approval=initial.pending_review[0]
    assert approval['preview']['fields']==ARGS['fields'] and approval['preview']['action_url']=='https://example.com/submit'
    result=await run_task('',initial.task_id,config,resume=True,
                          decision={'decision':decision,'approval_id':approval['approval_id']},
                          model=ScriptModel([AIMessage(content='No research sources'),AIMessage(content='Incomplete')]),browser_factory=browser)
    assert runtime.submissions==submissions and result.status=='incomplete'
    journal=read_json(config.output_dir/initial.task_id/'tool_journal.json')
    assert json.loads(journal['form']['output'])['external_action_executed_by_agent'] is (decision=='approve')


@pytest.mark.asyncio
async def test_stale_tampered_or_expired_approval_cannot_submit(settings,tmp_path):
    runtime=RuntimeFixture();config=replace(settings,allowed_domains=('example.com',))
    ws=Workspace(tmp_path,'scope')
    controller=FormController(ws,config,runtime.evaluate,runtime.navigate)
    prepared=await controller.prepare('call',ARGS)
    with pytest.raises(ToolFailure) as mismatch:await controller.execute(prepared,{'decision':'approve','approval_id':'wrong'})
    assert mismatch.value.code=='approval_mismatch'
    runtime.changed=True
    with pytest.raises(ToolFailure) as stale:await controller.execute(prepared,{'decision':'approve','approval_id':prepared['approval_id']})
    assert stale.value.code=='approval_stale' and runtime.submissions==0
    runtime.changed=False
    path=controller.folder/(prepared['approval_id']+'.json')
    saved=read_json(path);saved['expires_at']=time.time()-1;atomic_json(path,saved)
    with pytest.raises(ToolFailure) as expired:await controller.execute(prepared,{'decision':'approve','approval_id':prepared['approval_id']})
    assert expired.value.code=='approval_expired'


@pytest.mark.asyncio
async def test_completed_receipt_replays_but_uncertain_attempt_never_retries(settings,tmp_path):
    config=replace(settings,allowed_domains=('example.com',))
    runtime=RuntimeFixture();ws=Workspace(tmp_path,'receipt')
    first=FormController(ws,config,runtime.evaluate,runtime.navigate)
    prepared=await first.prepare('call',ARGS);decision={'decision':'approve','approval_id':prepared['approval_id']}
    receipt=await first.execute(prepared,decision)
    second=FormController(ws,config,runtime.evaluate,runtime.navigate)
    assert await second.execute(prepared,decision)==receipt and runtime.submissions==1
    uncertain=await second.prepare('uncertain',ARGS)
    runtime.fail=True
    with pytest.raises(TimeoutError):await second.execute(uncertain,{'decision':'approve','approval_id':uncertain['approval_id']})
    third=FormController(ws,config,runtime.evaluate,runtime.navigate)
    with pytest.raises(ToolFailure) as failure:await third.execute(uncertain,{'decision':'approve','approval_id':uncertain['approval_id']})
    assert failure.value.code=='action_uncertain' and runtime.submissions==2
    with pytest.raises(ToolFailure) as new_call:await third.prepare('different-call-id',ARGS)
    assert new_call.value.code=='action_uncertain' and runtime.submissions==2
    assert not (ws.path/'action_capability.json').exists()


@pytest.mark.asyncio
async def test_high_risk_or_secret_payloads_rejected_before_preview(settings,tmp_path):
    runtime=RuntimeFixture();controller=FormController(Workspace(tmp_path,'risk'),replace(settings,allowed_domains=('example.com',)),runtime.evaluate,runtime.navigate)
    for args in [{**ARGS,'purpose':'Purchase and payment'},{**ARGS,'fields':{'password':'something'}},{**ARGS,'fields':{'message':'api_key: sensitive-value'}}]:
        with pytest.raises(ValueError):await controller.prepare('risk',args)
    assert runtime.submissions==0
