"""Free tests for the paid evaluator. Never construct a real API model."""
import asyncio,json,os,subprocess,sys
import pytest
from langchain_core.messages import AIMessage
from scripts import evaluate_interaction_live as evaluation
from web_agent.persistence import atomic_json,read_json

class Fake:
    def bind_tools(self,*args,**kwargs):return self
    async def ainvoke(self,messages):return AIMessage(content='offline',usage_metadata={'input_tokens':3,'output_tokens':2,'total_tokens':5})

@pytest.fixture
def ledger(tmp_path,monkeypatch):
    folder=tmp_path/'evaluation';folder.mkdir()
    monkeypatch.setattr(evaluation,'FOLDER',folder);monkeypatch.setattr(evaluation,'LEDGER',folder/'budget.json')
    atomic_json(evaluation.LEDGER,{'requests':19,'limit':20})
    return evaluation.LEDGER

@pytest.mark.asyncio
async def test_twentieth_attempt_allowed_twenty_first_blocked_and_no_reset(ledger):
    model=evaluation.AuditModel(Fake(),'resume')
    await model.ainvoke([])
    assert read_json(ledger)['requests']==20 and read_json(ledger)['input_tokens']==3
    with pytest.raises(RuntimeError,match='exhausted'):await model.ainvoke([])
    with pytest.raises(ValueError,match='refusing reset'):evaluation.orchestrate()
    assert read_json(ledger)['requests']==20

@pytest.mark.asyncio
async def test_failed_request_is_charged(ledger):
    class Failure(Fake):
        async def ainvoke(self,messages):raise TimeoutError('offline injected')
    with pytest.raises(TimeoutError):await evaluation.AuditModel(Failure(),'resume').ainvoke([])
    saved=read_json(ledger)
    assert saved['requests']==20 and saved['calls'][0]['status']=='failed'

@pytest.mark.asyncio
async def test_download_gate_blocks_api_before_real_process_kill(ledger):
    path=evaluation.FOLDER/'outputs'/evaluation.CASE;path.mkdir(parents=True)
    atomic_json(path/'tool_journal.json',{'download':{'name':'browser_download','output':'download complete'}})
    task=asyncio.create_task(evaluation.AuditModel(Fake(),'initial').ainvoke([]))
    for _ in range(20):
        if (evaluation.FOLDER/'ready_to_kill.json').exists():break
        await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert read_json(ledger)['requests']==19
    assert read_json(evaluation.FOLDER/'ready_to_kill.json')['pid']==os.getpid()

def test_direct_runtime_child_pid_is_the_actual_python_process():
    bootstrap='import site;site.addsitedir('+repr(str(evaluation.ROOT/'.venv'/'Lib'/'site-packages'))+');import mcp,os;print(os.getpid())'
    child=subprocess.Popen([sys._base_executable,'-c',bootstrap],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    output,error=child.communicate(timeout=15)
    assert child.returncode==0,error
    assert int(output)==child.pid
