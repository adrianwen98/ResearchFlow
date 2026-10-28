"""No network/paid model: deterministic intake and actual local runner wiring."""
import json
import pytest
from langchain_core.messages import AIMessage
from web_agent import api
from web_agent.task_intake import prepare_task,context_brief,EXECUTION_POLICY
from web_agent.persistence import read_json
from web_agent.runner import run_task
from test_web_workbench import client
from test_graph import ScriptModel,empty_browser


def test_short_goal_asks_one_relevant_question_and_preserves_original():
    task='帮我比较一下 Notion 和飞书的免费版'
    brief=prepare_task(task)
    assert not brief['ready'] and [q['id'] for q in brief['questions']]==['use_case']
    resolved=prepare_task(task,{'use_case':'个人使用'})
    assert resolved['ready'] and resolved['task']==task and resolved['model_requests']==0
    assert context_brief(resolved,task)['user_clarifications'][0]['answer']=='个人使用'
    assert context_brief(resolved,'another task') is None


@pytest.mark.parametrize('task',[
    '比较 Notion 和飞书免费版，主要是个人使用',
    '调查 Python 和 Node.js 官方网站的用途和文档入口',
    '下载 https://example.com/data.csv 统计各组均值，生成 Excel',
    '比较两款手机在中国大陆的官方售价和屏幕尺寸',
])
def test_clear_goals_do_not_get_needless_questions(task):
    result=prepare_task(task)
    assert result['ready'] and not result['questions']
    assert result['defaults'] and result['scope']


def test_unknown_target_then_missing_file_can_be_clarified_without_looping():
    task='帮我分析一下'
    assert prepare_task(task)['unanswered']==['target']
    second=prepare_task(task,{'target':'分析这个文件'})
    assert second['unanswered']==['file_url']
    final=prepare_task(task,{'target':'分析这个文件','file_url':'https://example.com/data.csv'})
    assert final['ready'] and len(final['questions'])==2


def test_market_never_silently_defaults_to_china():
    brief=prepare_task('比较 iPhone 17 和小米15的价格')
    assert brief['unanswered']==['market'] and not brief['answers']
    explicit=prepare_task('比较 iPhone 17 和小米15在美国的价格')
    assert not explicit['questions']


@pytest.mark.parametrize('answer',[{'file_url':'file:///secret'}, {'file_url':'http://127.0.0.1/private'},
    {'file_url':'https://user:password@example.com/x'}, {'other_task':'anything'}, {'file_url':'x'*401}])
def test_invalid_or_unrelated_answers_rejected(answer):
    with pytest.raises(ValueError):prepare_task('帮我分析这份文件',answer)


def test_prepare_never_creates_a_job_or_calls_the_runner(client,monkeypatch):
    c,cfg=client
    def forbidden(*args,**kwargs):raise AssertionError('Paid runner must not start')
    monkeypatch.setattr(api,'run_task',forbidden)
    body={'task':'比较 Notion 和飞书的免费版','report_format':'both'}
    response=c.post('/tasks/prepare',json=body)
    assert response.status_code==200 and response.json()['model_requests']==0
    assert not api.jobs and not list(cfg.output_dir.iterdir())
    assert c.post('/tasks',json=body).status_code==409
    assert not api.jobs and not list(cfg.output_dir.iterdir())
    assert c.post('/tasks/prepare',json=body,headers={'Origin':'https://attacker.example'}).status_code==403
    assert c.post('/tasks/prepare',json={**body,'clarifications':{'use_case':'x'*401}}).status_code==422


def test_real_runner_receives_defaults_and_persisted_answers_without_an_extra_model_turn(client,monkeypatch):
    c,cfg=client
    seen=[]
    class CaptureModel(ScriptModel):
        async def ainvoke(self,messages):
            seen.append(messages)
            return await super().ainvoke(messages)
    model=CaptureModel([AIMessage(content='No sources; incomplete.'),AIMessage(content='Incomplete.')])
    async def local_runner(task,task_id,**kwargs):
        return await run_task(task,task_id,cfg,model=model,browser_factory=empty_browser,**kwargs)
    monkeypatch.setattr(api,'run_task',local_runner)
    body={'task':'比较 Notion 和飞书的免费版','clarifications':{'use_case':'小团队协作'},'report_format':'both'}
    headers={'Idempotency-Key':'offline-intake-task-00001'}
    response=c.post('/tasks',json=body,headers=headers)
    assert response.status_code==202
    task_id=response.json()['task_id']
    async def finish():return await api.jobs[task_id]
    c.portal.call(finish)
    folder=cfg.output_dir/task_id
    assert read_json(folder/'task_brief.json')['answers']=={'use_case':'小团队协作'}
    assert read_json(folder/'task.json')['task']==body['task']
    assert EXECUTION_POLICY in seen[0][0].content
    assert '小团队协作' in seen[0][1].content
    assert seen[0][2].content==body['task']
    view=c.get(f'/tasks/{task_id}/workspace').json()
    assert view['task_brief']['user_clarifications'][0]['answer']=='小团队协作'
    requests=view['metrics']['requests']
    assert requests==model.calls and requests<=2
    api.jobs.clear()  # Reloading disk state never schedules execution.
    assert c.get(f'/tasks/{task_id}/workspace').json()['task_brief']==view['task_brief']
    assert c.get(f'/tasks/{task_id}/workspace').json()['metrics']['requests']==requests
    assert c.post('/tasks',json=body,headers=headers).json()['reused']
    assert not api.jobs


def test_unnamed_comparison_does_not_invent_products():
    brief=prepare_task('帮我比较两款手机')
    assert brief['unanswered']==['target']
    assert prepare_task('帮我比较两款手机',{'target':'iPhone 17 和小米15'})['ready']


def test_existing_associated_task_flow_is_preserved():
    goal='后续任务：再比较一下\n[关联任务: original_task]\n新的要求：再比较一下\n\n原任务目标：比较 Notion 和飞书的免费版'
    assert prepare_task(goal)['ready']
