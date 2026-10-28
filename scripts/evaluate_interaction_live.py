"""One authorized <=20-call live run. Durable ledger, actual process kill/restart.

Without --run/--worker this prints the plan and does not call a model.
--run refuses any existing budget; never use a new task id to evade its limit.
"""
import argparse,asyncio,hashlib,json,os,sqlite3,subprocess,sys,time
from dataclasses import replace,asdict
from pathlib import Path
from langchain_openai import ChatOpenAI
from web_agent.config import ROOT,Settings
from web_agent.runner import run_task
from web_agent.workspace import Workspace
from web_agent.persistence import atomic_json,read_json,read_trace,sanitize

FOLDER=ROOT/'work'/'interaction_live'
CASE='interaction_end_to_end'
LEDGER=FOLDER/'api_budget.json'
TASK='''完成一个网页研究和数据处理任务：
1. 访问Python官网首页 https://www.python.org/，使用页面搜索框输入csv并点击搜索按钮，再点击一个实际搜索结果。记录一条关于该结果的实际页面来源，不能直接导航到预设搜索结果网址。
2. 访问pandas官方GitHub文件页面 https://github.com/pandas-dev/pandas/blob/main/pandas/tests/io/data/excel/test1.xlsx ，实际点击网页下载控件下载XLSX；不要用HTTP下载直链替代。此文件是pandas测试样本，没有业务统计含义。
3. 用表格工具检查真实文件列名、行数、负数，按实际日期列分组对A列求mean。生成task_result.xlsx，至少包含原始数据和分组统计两个业务工作表；另交付来源证据Excel和中文Markdown报告，列出实际数字与局限。
按需选择Skill，拆分需求，登记真实来源和逐项结果。下载完成后验收宿主会终止并重启Python进程；恢复时使用同一任务已保存文件，不重新研究或重复下载。不要请求无关网站/操作，遇到权限重复拒绝或相同工具持续失败立即结束。'''
REQUIREMENTS=[
 {'description':'Python官网实际输入csv搜索并点击一个搜索结果，记录真实来源','kind':'research'},
 {'description':'浏览器下载pandas测试XLSX，报告实际行列与负数','kind':'research'},
 {'description':'真实日期列分组计算A列mean，报告结果及测试样本限制','kind':'calculation'},
 {'description':'交付原始数据及统计业务Excel','kind':'artifact','artifact':'task_result.xlsx'},
 {'description':'交付中文报告和来源','kind':'artifact','artifact':'research_report.md'}]


class AuditModel:
    def __init__(self,model,phase):self.model,self.phase=model,phase
    def bind_tools(self,tools,**kwargs):return AuditModel(self.model.bind_tools(tools,**kwargs),self.phase)
    async def ainvoke(self,messages):
        ws=FOLDER/'outputs'/CASE
        journal=read_json(ws/'tool_journal.json',{})
        if self.phase=='initial' and any(r['name']=='browser_download' and not r['output'].startswith('ERROR:') for r in journal.values()):
            # execute's checkpoint is already committed before decide invokes us.
            atomic_json(FOLDER/'ready_to_kill.json',{'pid':os.getpid(),'tool_ids':list(journal),
                        'downloads':read_json(ws/'downloads.json',{}),'api_requests':read_json(LEDGER)['requests'],
                        'note':'Graph reserved a request before this test-only gate; no API call made at the gate.'})
            await asyncio.Event().wait()
        errors=[r for r in journal.values() if r.get('error_code')]
        if len(errors)>=2 and (errors[-1].get('error_code') in {'permission_denied','approval_required'} or errors[-1].get('signature')==errors[-2].get('signature')):
            raise RuntimeError('Live evaluation stopped after repeated permission/same-operation errors; no additional API call')
        budget=read_json(LEDGER)
        if budget['requests']>=20:raise RuntimeError('Authorized 20 API attempts exhausted')
        budget['requests']+=1
        entry={'request':budget['requests'],'pid':os.getpid(),'phase':self.phase,'status':'started'}
        budget.setdefault('calls',[]).append(entry);atomic_json(LEDGER,budget)
        try:response=await self.model.ainvoke(messages)
        except Exception as exc:
            entry.update(status='failed',error=type(exc).__name__);atomic_json(LEDGER,budget);raise
        usage=response.usage_metadata or {};metadata=response.response_metadata or {};provider=metadata.get('token_usage',{})
        entry.update(status='returned',usage=usage,tools=[t['name'] for t in response.tool_calls])
        with (FOLDER/'model_turns.jsonl').open('a',encoding='utf-8') as audit:
            audit.write(json.dumps(sanitize({'request':budget['requests'],'phase':self.phase,'content':response.content,'tool_calls':response.tool_calls,'usage':usage}),ensure_ascii=False)+'\n')
        for key in ('input_tokens','output_tokens'):budget[key]=budget.get(key,0)+usage.get(key,0)
        budget['cached_input_tokens']=budget.get('cached_input_tokens',0)+usage.get('input_token_details',{}).get('cache_read',provider.get('prompt_cache_hit_tokens',0) or 0)
        budget.setdefault('reported_costs',[])
        for field in ('cost','total_cost','request_cost'):
            if field in provider:budget['reported_costs'].append({field:provider[field]})
            elif field in metadata:budget['reported_costs'].append({field:metadata[field]})
        budget['billing_note']='Only provider-returned costs recorded; no bill queried; missing cost is unknown, not zero.'
        atomic_json(LEDGER,budget)
        print(json.dumps({'api_request':budget['requests'],'phase':self.phase,'tools':entry['tools']},ensure_ascii=False),flush=True)
        return response


async def worker(phase):
    if not LEDGER.exists():raise ValueError('Authorized durable budget not initialized; refusing')
    base=Settings.load();base.validate()
    settings=replace(base,output_dir=FOLDER/'outputs',checkpoint_path=FOLDER/'checkpoint.sqlite',
                     allowed_domains=('python.org','github.com','githubusercontent.com','githubassets.com'),browser_headless=True,
                     max_model_calls=20,max_tool_calls=90,max_graph_steps=85,task_timeout=900)
    model=AuditModel(ChatOpenAI(model=settings.model,api_key=settings.api_key,base_url=settings.base_url,temperature=0,
                       timeout=60,max_retries=0,max_tokens=3000,extra_body={'thinking':{'type':'disabled'}}),phase)
    followup=''
    if phase=='finish':
        report=(FOLDER/'outputs'/CASE/'research_report.md').read_text(encoding='utf-8').split('\n\n---',1)[0]
        followup='上次文件核验已通过，但r1/r2/r3的report_excerpt不是报告里的连续摘录，登记被拒绝。只剩一次真实模型请求。保持现有报告和Excel不动，不重新浏览或下载；只调用record_requirement_result补正r1、r2、r3，分别引用现有真实来源1、3、4，计算引用已有calculation_id。每项report_excerpt直接复制下列实际报告中的一段连续文字，不要拼接跨段句子或改写。宿主提供的是已有文件内容，不是新的研究结论。\n\n'+report
    result=await run_task(TASK if phase=='initial' else followup,CASE,settings,resume=phase!='initial',model=model,
                          requirements=REQUIREMENTS if phase=='initial' else None)
    atomic_json(FOLDER/(phase+'_result.json'),{'pid':os.getpid(),**asdict(result)})
    print(json.dumps({'phase':phase,'pid':os.getpid(),'status':result.status,'actual_api_requests':read_json(LEDGER)['requests']},ensure_ascii=False),flush=True)


def launch(phase):
    output=(FOLDER/(phase+'.log')).open('w',encoding='utf-8')
    # Use the base executable directly: Windows venv redirectors can create an extra PID.
    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'.venv'/'Lib'/'site-packages')])}
    bootstrap="import site,runpy; site.addsitedir("+repr(str(ROOT/'.venv'/'Lib'/'site-packages'))+"); runpy.run_module('scripts.evaluate_interaction_live',run_name='__main__')"
    child=subprocess.Popen([sys._base_executable,'-u','-c',bootstrap,'--worker',phase],cwd=ROOT,
                           stdout=output,stderr=subprocess.STDOUT,env=env,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    output.close();return child


def checkpoint_info():
    with sqlite3.connect(FOLDER/'checkpoint.sqlite') as db:
        rows=db.execute('select checkpoint_id from checkpoints where thread_id=? order by checkpoint_id',(CASE,)).fetchall()
    return {'task_id':CASE,'checkpoint_count':len(rows),'latest_checkpoint':rows[-1][0] if rows else ''}


def orchestrate(continuing=False):
    FOLDER.mkdir(parents=True,exist_ok=True)
    if LEDGER.exists():
        if not continuing or read_json(LEDGER)['requests']!=0 or (FOLDER/'outputs'/CASE/'task.json').exists():
            raise ValueError('Budget already exists; refusing reset or automatic rerun')
        previous=FOLDER/'initial.log'
        if previous.exists():previous.replace(FOLDER/'initial_bootstrap_failure.log')
    else:
        if continuing:raise ValueError('No prior budget to continue')
        with LEDGER.open('x',encoding='utf-8') as file:json.dump({'requests':0,'limit':20,'authorized_scope':'one interaction/download/analysis/restart task'},file)
    started=time.monotonic();child=launch('initial')
    deadline=time.monotonic()+960
    while child.poll() is None and not (FOLDER/'ready_to_kill.json').exists() and time.monotonic()<deadline:time.sleep(.2)
    resumed=None
    if (FOLDER/'ready_to_kill.json').exists():
        ready=read_json(FOLDER/'ready_to_kill.json');assert ready['pid']==child.pid
        before=checkpoint_info();assert before['checkpoint_count']>1
        child.kill();child.wait(timeout=15)
        assert child.poll() is not None
        ws=FOLDER/'outputs'/CASE;lock=read_json(ws/'.running',{})
        assert lock.get('pid')==child.pid
        hashes={f:hashlib.sha256((ws/f).read_bytes()).hexdigest() for f in ready['downloads']}
        # This is the exact child launched by this test and wait confirmed it exited.
        (ws/'.running').unlink()
        restart={'killed_pid':child.pid,'killed_exit_code':child.returncode,'checkpoint_before':before,
                 'download_hashes_before':hashes,'manual_unlock_after_confirmed_child_exit':True,
                 'reserved_not_sent_requests':1}
        atomic_json(FOLDER/'process_restart.json',restart)
        resumed=launch('resume');assert resumed.pid!=child.pid
        try:resumed.wait(timeout=960)
        except subprocess.TimeoutExpired:resumed.kill();resumed.wait(timeout=15)
        restart.update(resumed_pid=resumed.pid,resumed_exit_code=resumed.returncode,checkpoint_after=checkpoint_info(),
                       download_hashes_after={f:hashlib.sha256((ws/f).read_bytes()).hexdigest() for f in hashes})
        atomic_json(FOLDER/'process_restart.json',restart)
    elif child.poll() is None:child.kill();child.wait(timeout=15)
    ws=Workspace(FOLDER/'outputs',CASE,('python.org','github.com','githubusercontent.com'))
    trace=read_trace(ws.path/'trace.jsonl')
    journal=read_json(ws.path/'tool_journal.json',{})
    record={'actual_api_budget':read_json(LEDGER),'elapsed_seconds':round(time.monotonic()-started,2),
            'result':read_json(ws.path/'result.json',{}),'process_restart':read_json(FOLDER/'process_restart.json',{}),
            'successful_tools':[r['name'] for r in journal.values() if not r['output'].startswith('ERROR:')],
            'browser_download_dispatch_count':sum(r['event']=='tool_dispatch' and r['detail']=='browser_download' for r in trace),
            'tool_errors':[r for r in trace if r['event']=='tool_error'],'report_edited_by_host':False,
            'human_final_acceptance_performed':False}
    atomic_json(ROOT/'evidence'/'interaction_live_evaluation.json',record)
    print(json.dumps({'actual_requests':record['actual_api_budget']['requests'],'status':record['result'].get('status'),
                      'process_restart':bool(record['process_restart']),'evidence':'evidence/interaction_live_evaluation.json'},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');parser.add_argument('--continue-run',action='store_true');parser.add_argument('--finish',action='store_true');parser.add_argument('--worker',choices=['initial','resume','finish']);args=parser.parse_args()
    if args.worker:asyncio.run(worker(args.worker))
    elif args.run:orchestrate()
    elif args.continue_run:orchestrate(continuing=True)
    elif args.finish:
        budget=read_json(LEDGER);restart=read_json(FOLDER/'process_restart.json',{})
        ws=FOLDER/'outputs'/CASE;usage=read_json(ws/'usage.json')
        if budget['requests']!=19 or budget['limit']!=20 or restart.get('reserved_not_sent_requests')!=1 or (ws/'.running').exists():raise ValueError('Finish requires exactly one authorized API request remaining and no running task')
        if (FOLDER/'unused_reservation_reconciled.json').exists():raise ValueError('Unused reservation already reconciled; refusing repeated adjustment')
        if usage['requests']!=budget['requests']+1:raise ValueError('Cannot prove exactly one reserved-but-unsent request')
        atomic_json(FOLDER/'unused_reservation_reconciled.json',{'usage_before':usage['requests'],'actual_api_before':budget['requests'],
                    'unused_test_gate_reservation':1,'budget_limit_unchanged':20,'reason':'Known test gate blocked before AuditModel sent a request; not a failed API request refund'})
        usage['requests']-=1;atomic_json(ws/'usage.json',usage)
        child=launch('finish');child.wait(timeout=180)
        record=read_json(ROOT/'evidence'/'interaction_live_evaluation.json')
        record['initial_status']=record['result'].get('status')
        record.update(result=read_json(ws/'result.json'),actual_api_budget=read_json(LEDGER),
                      host_feedback='One follow-up supplied the actual generated report after excerpt mismatch; no report/data edits',
                      unused_reservation_reconciliation=read_json(FOLDER/'unused_reservation_reconciled.json'))
        atomic_json(ROOT/'evidence'/'interaction_live_evaluation.json',record)
        print(json.dumps({'status':record['result']['status'],'actual_requests':record['actual_api_budget']['requests']},ensure_ascii=False))
    else:print(json.dumps({'request_limit':20,'task':TASK,'requires_explicit_authorization':True},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
