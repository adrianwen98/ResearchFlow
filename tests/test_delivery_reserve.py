"""Offline regressions: saved evidence reuse and delivery before budget exhaustion."""
import json
from dataclasses import replace
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest
from test_graph import settings, empty_browser
from web_agent.context import bounded_context, prompt_size, CONTEXT_CHAR_LIMIT
from web_agent.runner import run_task
from web_agent.workspace import Workspace


def test_first_evidence_survives_seventeen_sources_and_context_trimming(tmp_path):
    ws=Workspace(tmp_path,'source-index')
    for i in range(17):
        text=f'Observed item {i}: exact value {i+100}'
        url=f'https://example.com/{i}'
        ws.observe(url,text)
        ws.record(str(i),url,text,text,require_observation=True)
    messages=[HumanMessage('Compare saved values')]
    for i in range(18):
        messages += [AIMessage(content='',tool_calls=[{'id':str(i),'name':'browser_navigate','args':{},'type':'tool_call'}]),
                     ToolMessage(content='Large observed page '+('x'*12000),tool_call_id=str(i),name='browser_navigate')]
    output=bounded_context(messages,ws,'System')
    memory=json.loads(output[1].content.split('：',1)[1])
    assert len(memory['recent_sources'])==17
    assert memory['recent_sources'][0]['id']=='1'
    assert memory['recent_sources'][0]['quote_preview']=='Observed item 0: exact value 100'
    assert prompt_size(output)<=CONTEXT_CHAR_LIMIT


@pytest.mark.asyncio
async def test_delivery_tools_are_offered_before_budget_is_used_up(settings):
    task_id='reserve-offline'
    ws=Workspace(settings.output_dir,task_id)
    url='https://example.com/item'
    ws.observe(url,'Screen: 6.3 inches')
    ws.record('Test source',url,'Observed screen size','Screen: 6.3 inches',require_observation=True)
    class Model:
        def __init__(self):self.names=set();self.turn=0;self.prompts=[]
        def bind_tools(self,tools,**kwargs):self.names={t.name for t in tools};return self
        async def ainvoke(self,messages):
            self.turn+=1;self.prompts.append(messages[0].content)
            if self.turn==1:
                return AIMessage(content='',tool_calls=[{'id':'plan','name':'update_plan','args':{'steps':['Use saved source; export table and report']},'type':'tool_call'}])
            assert 'browser_navigate' not in self.names
            assert {'export_business_excel','repair_business_excel','list_recorded_sources','export_report','export_excel'}<=self.names
            assert 'task_result.xlsx' in messages[0].content
            calls=[('export_business_excel',{'sheets':[{'name':'Comparison','mode':'comparison','columns':['Screen'],'rows':[{'label':'Sample','cells':{'Screen':{'value':'6.3 inches','source_id':'1','quote':'Screen: 6.3 inches'}}}]}]}),
                   ('export_excel',{}),('export_report',{'markdown_report':'# Offline comparison\nObserved screen: 6.3 inches. Test-only fixture.'})]
            return AIMessage(content='',tool_calls=[{'id':f'finish-{i}','name':name,'args':args,'type':'tool_call'} for i,(name,args) in enumerate(calls)])
    model=Model()
    result=await run_task('Compare saved test values',task_id,replace(settings,max_model_calls=4),model=model,browser_factory=empty_browser)
    assert result.status=='needs_review'
    assert result.metrics['requests']==2
    assert (ws.path/'task_result.xlsx').is_file()
    assert 'delivery_phase' in (ws.path/'trace.jsonl').read_text(encoding='utf-8')
