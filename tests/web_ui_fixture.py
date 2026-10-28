"""Browser-test-only server, isolated output/checkpoint, deterministic model, NO external API.
Never imported by production. UI prominently labels every generated task as offline test.
"""
import asyncio
import shutil
from dataclasses import replace
from pathlib import Path
from contextlib import asynccontextmanager
import uvicorn
from langchain_core.messages import AIMessage
from web_agent import api
from web_agent.config import Settings
from web_agent.runner import run_task
from web_agent.persistence import atomic_json

ROOT=Path(__file__).resolve().parents[1]
CFG=replace(Settings.load(),api_key='',output_dir=ROOT/'work/web-ui-smoke/outputs',checkpoint_path=ROOT/'work/web-ui-smoke/checkpoints.sqlite',max_model_calls=8)
CFG.output_dir.mkdir(parents=True,exist_ok=True)
Settings.load=classmethod(lambda cls:CFG)
# Advertise fixture availability without loading credentials or permitting real API calls.
api.public_settings.__globals__['Settings']=Settings

@asynccontextmanager
async def no_browser(settings,workspace):yield []

class OfflineModel:
    def __init__(self,resume=False,slow=False,permission=False):self.index=0;self.resume=resume;self.slow=slow;self.permission=permission
    def bind_tools(self,*args,**kwargs):return self
    async def ainvoke(self,messages):
        if self.slow:await asyncio.Event().wait()
        self.index+=1
        if self.permission:return AIMessage(content='',tool_calls=[{'id':'denied'+str(self.index),'name':'download_public_file','args':{'url':'http://127.0.0.1/private'},'type':'tool_call'}])
        if self.resume:return AIMessage(content='离线UI测试：已记录用户决定，未执行外部操作。')
        if self.index==1:return AIMessage(content='',tool_calls=[{'id':'plan','name':'update_plan','args':{'steps':['离线验证任务创建与真实状态持久化','通过LangGraph interrupt等待人工决定','从原Checkpoint恢复并保留工具记录']},'type':'tool_call'}])
        return AIMessage(content='',tool_calls=[{'id':'review','name':'request_human_review','args':{'action':'离线集成测试：请核对页面审批流程','reason':'测试实际interrupt与恢复，无网页写入、无外部模型'},'type':'tool_call'}])

async def offline_runner(task,task_id,**kwargs):
    folder=CFG.output_dir/task_id;folder.mkdir(exist_ok=True)
    atomic_json(folder/'demo_meta.json',{'execution_label':'离线交互测试 · 脚本模型 · 实际API请求0次','actual_api_requests':0})
    if 'startup_failure' in task:raise RuntimeError('离线验收注入：启动失败，未调用模型')
    return await run_task(task,task_id,CFG,model=OfflineModel(resume=kwargs.get('resume',False),slow='cancel' in task,permission='permission' in task),browser_factory=no_browser,**kwargs)
api.run_task=offline_runner
api.LOCAL_ORIGINS.add('http://127.0.0.1:8002')

# Actual public data artifacts, clearly marked as test copies; never mutate original demos.
source=ROOT/'outputs/penguins_public_demo';target=CFG.output_dir/'offline_delivery'
if source.exists() and not target.exists():
    shutil.copytree(source,target,ignore=shutil.ignore_patterns('.running','acceptance.json'))
    atomic_json(target/'demo_meta.json',{'execution_label':'离线UI验收副本 · 真实公开数据 · 非新模型执行','actual_api_requests':0,'read_only':False})
    import json
    manifest=json.loads((target/'task.json').read_text(encoding='utf-8'));manifest['task']='离线UI验收副本：真实企鹅数据分析文件';manifest['task_id']='offline_delivery';atomic_json(target/'task.json',manifest)
# Key presence boolean only for browser testing the paid-action confirmation. No real key or model is constructed.
original_public=api.public_settings
for route in api.app.routes:
    if getattr(route,'path',None)=='/settings':
        def test_settings():return {**original_public(),'api_key_configured':True,'model':'OFFLINE scripted test (no API)'}
        route.endpoint=test_settings;route.dependant.call=test_settings
if __name__=='__main__':uvicorn.run(api.app,host='127.0.0.1',port=8002)
