import pytest
from web_agent.browser_actions import authorize_control,BrowserActions
from web_agent.harness import ToolFailure
from web_agent.security import image_document_url

def meta(**fields):return {'tag':'input','type':'search','name':'q','label':'Search','href':'','form':None,**fields}


def test_image_urls_are_not_text_pages():
    assert image_document_url('https://www.stats.gov.cn/sj/ndsj/2024/html/C12-08.jpg')
    assert image_document_url('https://example.org/chart.PNG')
    assert not image_document_url('https://example.org/data.xlsx')
    assert not image_document_url('https://example.org/report.html')

@pytest.mark.parametrize('action,item',[
 ('type',meta(type='password')),('type',meta(type='email',name='email')),
 ('click',meta(tag='button',type='submit',label='Send message')),
 ('click',meta(tag='a',href='https://example.com/delete/123',label='View')),
 ('click',meta(tag='button',label='Search',form={'method':'post','action':'https://example.com/search','fields':[]})),
 ('select',meta(tag='select',options=[{'value':'pay','label':'Pay'}]))])
def test_restricted_controls(action,item):
    with pytest.raises(ToolFailure):authorize_control(item,action,('example.com',),['pay'])

def test_allowed_read_controls_and_get_search():
    authorize_control(meta(),'type',('example.com',))
    authorize_control(meta(tag='button',label='Search',form={'method':'get','action':'https://example.com/search','fields':[{'name':'q','type':'search'}]}),'click',('example.com',))
    authorize_control(meta(tag='select',label='Category',options=[{'value':'all','label':'All'}]),'select',('example.com',),['all'])
    authorize_control(meta(tag='a',href='https://example.com/data.xlsx',label='Data'),'download',('example.com',))
    authorize_control(meta(tag='button',label='Details'),'click',('example.com',))
    authorize_control(meta(tag='button',label='查看详情'),'click',('example.com',))

@pytest.mark.asyncio
async def test_unobserved_selectors_never_dispatch():
    actions=BrowserActions({}, {'snapshot':'link [ref=e1]'},None,None,None)
    for ref in ('#search','e2','e1; alert(1)'):
        with pytest.raises(ToolFailure,match='最近观察'):await actions.inspect(ref,'click')
