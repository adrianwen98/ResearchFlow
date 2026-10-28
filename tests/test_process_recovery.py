"""Actual process boundaries and abrupt termination, with offline model fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

WORKER=Path(__file__).with_name('restart_worker.py')


def command(folder,mode):return [sys.executable,str(WORKER),str(folder),mode]


def worker(folder,mode):
    done=subprocess.run(command(folder,mode),capture_output=True,text=True,encoding='utf-8',timeout=30,
                        env={**os.environ,'PYTHONIOENCODING':'utf-8','DEEPSEEK_API_KEY':''})
    assert done.returncode==0,done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('decision',['reject','handled'])
def test_approval_checkpoint_survives_full_process_restart(tmp_path,decision):
    first=worker(tmp_path,'pause')
    assert first['result']['status']=='awaiting_review'
    second=worker(tmp_path,decision)
    assert second['pid']!=first['pid']
    result=second['result']
    assert result['status']=='incomplete' and result['metrics']['requests']==3
    folder=tmp_path/'outputs'/'restart'
    trace=[json.loads(line) for line in (folder/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert sum(r['event']=='plan_updated' for r in trace)==1
    decisions=[r for r in trace if r['event']=='human_decision']
    assert len(decisions)==1 and decision in decisions[0]['detail']
    assert '"external_action_executed_by_agent": false' in decisions[0]['detail']


def test_killed_process_keeps_checkpoint_and_requires_confirmed_stale_unlock(tmp_path):
    process=subprocess.Popen(command(tmp_path,'crash'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              env={**os.environ,'PYTHONIOENCODING':'utf-8','DEEPSEEK_API_KEY':''})
    try:
        deadline=time.monotonic()+15
        while not (tmp_path/'ready.json').exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.05)
        assert (tmp_path/'ready.json').is_file(),'worker failed to reach durable checkpoint'
        process.kill();process.wait(timeout=10)
        assert process.poll() is not None
        lock=tmp_path/'outputs'/'restart'/'.running'
        assert lock.is_file()
        denied=worker(tmp_path,'resume')
        assert denied['exception']=='RuntimeError' and '--unlock' in denied['message']
        # Test-only manual recovery after wait() confirmed this exact child exited.
        lock.unlink()
        resumed=worker(tmp_path,'resume')['result']
        assert resumed['status']=='incomplete' and resumed['metrics']['requests']==4
        trace=(lock.parent/'trace.jsonl').read_text(encoding='utf-8')
        assert trace.count('"event": "plan_updated"')==1
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        process.communicate(timeout=10)
