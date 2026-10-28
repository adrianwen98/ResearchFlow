"""Separate-process checkpoint probe. No real model or browser is constructed."""
import asyncio
import json
import os
import sys
from dataclasses import replace, asdict
from pathlib import Path
from contextlib import asynccontextmanager
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import AIMessage
from web_agent.config import Settings
from web_agent.runner import run_task


@asynccontextmanager
async def no_browser(settings,workspace):yield []


class ProbeModel:
    def __init__(self,mode,folder):self.mode,self.folder,self.calls=mode,folder,0
    def bind_tools(self,tools,**kwargs):return self
    async def ainvoke(self,messages):
        self.calls+=1
        if self.mode=='crash' and self.calls==2:
            (self.folder/'ready.json').write_text(json.dumps({'pid':os.getpid()}),encoding='utf-8')
            await asyncio.sleep(60)
        if self.mode in {'pause','crash'} and self.calls==1:
            calls=[{'name':'update_plan','args':{'steps':['persisted original plan']},'id':'plan','type':'tool_call'}]
            if self.mode=='pause':calls.append({'name':'request_human_review','args':{'action':'send form','reason':'external side effect'},'id':'approval','type':'tool_call'})
            return AIMessage(content='',tool_calls=calls)
        return AIMessage(content='No verified sources; incomplete. External action never executed.')


async def main():
    folder=Path(sys.argv[1]); mode=sys.argv[2]
    folder.mkdir(parents=True,exist_ok=True)
    settings=replace(Settings.load(),api_key='',output_dir=folder/'outputs',checkpoint_path=folder/'checkpoint.sqlite',
                     task_timeout=120,max_model_calls=10,allowed_domains=())
    decision={'decision':mode} if mode in {'handled','reject'} else None
    try:
        result=await run_task('checkpoint process test' if mode in {'pause','crash'} else '', 'restart',settings,
                              resume=mode not in {'pause','crash'},decision=decision,
                              model=ProbeModel(mode,folder),browser_factory=no_browser)
        print(json.dumps({'pid':os.getpid(),'result':asdict(result)},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'pid':os.getpid(),'exception':type(exc).__name__,'message':str(exc)},ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
