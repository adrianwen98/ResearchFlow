"""Configuration reload, visible permissions and durable request-budget regressions."""
from dataclasses import replace
import pytest
from langchain_core.messages import AIMessage
from web_agent import config
from web_agent.runner import run_task
from web_agent.security import validate_public_url,UnsafeURL
from test_graph import settings,ScriptModel,call,empty_browser


def test_refresh_file_owned_settings(tmp_path,monkeypatch):
    path=tmp_path/'.env';path.write_text('MAX_MODEL_CALLS=20\n',encoding='utf-8')
    monkeypatch.setenv('WEBTASK_ENV_FILE',str(path));monkeypatch.delenv('MAX_MODEL_CALLS',raising=False)
    monkeypatch.setattr(config,'_MANAGED_ENV',{})
    assert config.Settings.load().max_model_calls==20
    path.write_text('MAX_MODEL_CALLS=30\n',encoding='utf-8')
    assert config.Settings.load().max_model_calls==30


def test_explicit_environment_override_survives_refresh(tmp_path,monkeypatch):
    path=tmp_path/'.env';path.write_text('MAX_MODEL_CALLS=20\n',encoding='utf-8')
    monkeypatch.setenv('WEBTASK_ENV_FILE',str(path));monkeypatch.setenv('MAX_MODEL_CALLS','7')
    monkeypatch.setattr(config,'_MANAGED_ENV',{})
    assert config.Settings.load().max_model_calls==7
    path.write_text('MAX_MODEL_CALLS=30\n',encoding='utf-8')
    assert config.Settings.load().max_model_calls==7


def test_ui_site_scope_preserves_private_blocks():
    from app import build_run_settings
    value=build_run_settings(30,'https://nodejs.org, python.org')
    assert value.allowed_domains==('nodejs.org','python.org')
    assert value.max_graph_steps>=65
    assert validate_public_url('https://nodejs.org/en/docs',value.allowed_domains)
    with pytest.raises(UnsafeURL): validate_public_url('https://outside.example.org',value.allowed_domains)
    with pytest.raises(UnsafeURL): build_run_settings(30,'127.0.0.1')


def test_ui_limits_reject_excess_budget():
    from app import build_run_settings
    with pytest.raises(ValueError): build_run_settings(51,'nodejs.org')


@pytest.mark.asyncio
async def test_model_informed_permissions_and_finalization_budget(settings):
    class Spy(ScriptModel):
        def __init__(self):super().__init__([AIMessage(content='No sources'),AIMessage(content='Incomplete')]);self.prompts=[]
        async def ainvoke(self,messages):
            self.prompts.append(messages[0].content)
            return await super().ainvoke(messages)
    model=Spy()
    result=await run_task('policy context task','policy',replace(settings,max_model_calls=2,allowed_domains=('nodejs.org',)),
                          model=model,browser_factory=empty_browser)
    # Budget exhaustion is an execution state, independent of delivery failure.
    assert result.status=='budget_exceeded'
    assert result.completion['delivery']['outcome']=='failed'
    assert all('nodejs.org' in prompt and '进入交付收尾阶段' in prompt for prompt in model.prompts)
    assert result.metrics['limits']['model_requests']==2


@pytest.mark.asyncio
async def test_resume_increased_budget_keeps_prior_usage(settings):
    first=ScriptModel([call('update_plan',{'steps':['research']})])
    result=await run_task('durable enlarged budget','enlarge',replace(settings,max_model_calls=1),model=first,browser_factory=empty_browser)
    assert result.status=='budget_exceeded' and '1/1' in result.answer
    resumed=ScriptModel([AIMessage(content='No sources'),AIMessage(content='Incomplete')])
    result=await run_task('', 'enlarge', replace(settings,max_model_calls=3),resume=True,model=resumed,browser_factory=empty_browser)
    assert resumed.calls==2 and result.metrics['requests']==3
