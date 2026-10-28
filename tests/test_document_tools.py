"""Real parsers and file exports using HTTP fixtures; not live-site acceptance."""
import hashlib
import io
import json
from dataclasses import replace
from contextlib import asynccontextmanager
import httpx
import pandas as pd
import pymupdf
import pytest
from langchain_core.messages import AIMessage
from test_graph import settings, ScriptModel, call, empty_browser
from web_agent import tools as module
from web_agent.completion import define_requirements, evaluate_requirements, record_result
from web_agent.harness import ToolFailure
from web_agent.runner import run_task
from web_agent.security import validate_public_url
from web_agent.tools import build_local_tools
from web_agent.workspace import Workspace


@pytest.fixture
def http_fixtures(monkeypatch):
    doc=pymupdf.open();page=doc.new_page();page.insert_text((72,72),'Public PDF fixture content.')
    pdf=doc.tobytes();doc.close()
    stream=io.BytesIO()
    pd.DataFrame({'species':['a','b','a'],'value':[1,2,3]}).to_excel(stream,index=False)
    from PIL import Image, ImageDraw, ImageFont
    image=Image.new('RGB',(520,140),'white')
    try:
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',48)
    except OSError:
        font=ImageFont.load_default()
    ImageDraw.Draw(image).text((20,30),'2015 100',fill='black',font=font)
    image_stream=io.BytesIO();image.save(image_stream,'PNG')
    payloads={'/data.csv':b'species,value\na,1\nb,2\na,3\n','/data.xlsx':stream.getvalue(),
              '/doc.pdf':pdf,'/bad.pdf':b'not PDF','/table.png':image_stream.getvalue()}
    requests=[]
    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200,content=payloads[request.url.path],request=request)
    original=httpx.AsyncClient
    @asynccontextmanager
    async def fixture_client(settings):
        async with original(transport=httpx.MockTransport(handle),trust_env=False) as client:yield client
    monkeypatch.setattr(module,'public_http_client',fixture_client)
    monkeypatch.setattr(module,'validate_public_url',validate_public_url)
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix',['csv','xlsx'])
async def test_real_table_parser_download_inspect_aggregate(settings,tmp_path,http_fixtures,suffix):
    ws=Workspace(tmp_path,'table')
    tools={t.name:t for t in build_local_tools(ws,replace(settings,allowed_domains=('example.com',)))}
    url='https://example.com/data.'+suffix
    await tools['download_public_file'].ainvoke({'url':url})
    filename='public_'+hashlib.sha256(url.encode()).hexdigest()[:12]+'.'+suffix
    assert (ws.path/filename).is_file()
    info=json.loads(await tools['inspect_downloaded_table'].ainvoke({'filename':filename}))
    result=json.loads(await tools['aggregate_downloaded_table'].ainvoke({'filename':filename,'group_by':'species','method':'count'}))
    assert info['sampled_rows']==3 and info['columns']==['species','value']
    assert result['top_20']==[{'group':'a','value':2.0},{'group':'b','value':1.0}]
    assert ws.evidence_for(url,'"sampled_rows": 3')
    assert ws.evidence_for(url,'"calculation_id": "'+result['calculation_id']+'"')
    # A real data row, not only the parsed JSON summary, is valid exact evidence.
    assert ws.evidence_for(url,'a,1')
    raw_source=ws.record('raw row',url,'first fixture row','a,1',require_observation=True)
    assert raw_source['id']
    calculation=json.loads((ws.path/'calculations.json').read_text(encoding='utf-8'))[result['calculation_id']]
    assert calculation.get('observation_id') and calculation.get('source_id')
    assert any(source['id']==calculation['source_id'] and source['category']=='calculation'
               for source in ws.sources())
    assert len(http_fixtures)==1


@pytest.mark.asyncio
async def test_calculation_requirement_autofills_derived_source(settings,tmp_path,http_fixtures):
    ws=Workspace(tmp_path,'calculation-requirement')
    tools={t.name:t for t in build_local_tools(ws,replace(settings,allowed_domains=('example.com',)))}
    url='https://example.com/data.csv'
    await tools['download_public_file'].ainvoke({'url':url})
    filename='public_'+hashlib.sha256(url.encode()).hexdigest()[:12]+'.csv'
    await tools['inspect_downloaded_table'].ainvoke({'filename':filename})
    result=json.loads(await tools['aggregate_downloaded_table'].ainvoke(
        {'filename':filename,'group_by':'species','method':'count'}))
    define_requirements(ws,[{'description':'按 species 统计样本数','kind':'calculation'}])
    (ws.path/'research_report.md').write_text('# Report\n\n| species | count |\n| a | 2 |\n',encoding='utf-8')
    with pytest.raises(ValueError,match='连续原文'):
        record_result(ws,'r1',[],'',result['calculation_id'],'satisfied')
    row=record_result(ws,'r1',[],'| a | 2 |',result['calculation_id'],'satisfied')
    assert row['result']['source_ids'] and row['result'].get('auto_source_ids')
    assert evaluate_requirements(ws)['requirements'][0]['passed']


@pytest.mark.asyncio
async def test_public_image_ocr_returns_grouped_text_and_observation(settings,tmp_path,http_fixtures):
    ws=Workspace(tmp_path,'ocr')
    tools={t.name:t for t in build_local_tools(ws,replace(settings,allowed_domains=('example.com',)))}
    text=await tools['read_public_image'].ainvoke({'url':'https://example.com/table.png'})
    assert '2015' in text and 'OCR' in text and '[observation_id:' in text
    assert ws.evidence_for('https://example.com/table.png','2015')


@pytest.mark.asyncio
async def test_pdf_real_parser_and_invalid_format_are_classified(settings,tmp_path,http_fixtures):
    ws=Workspace(tmp_path,'pdf')
    tools={t.name:t for t in build_local_tools(ws,replace(settings,allowed_domains=('example.com',)))}
    text=await tools['read_public_pdf'].ainvoke({'url':'https://example.com/doc.pdf'})
    assert 'Public PDF fixture content.' in text
    assert ws.evidence_for('https://example.com/doc.pdf','Public PDF fixture content.')
    with pytest.raises(ToolFailure) as failure:
        await tools['read_public_pdf'].ainvoke({'url':'https://example.com/bad.pdf'})
    assert failure.value.code=='invalid_document' and not failure.value.retryable


@pytest.mark.asyncio
async def test_pdf_and_table_tools_cooperate_in_graph_and_verify_artifacts(settings,http_fixtures):
    csv='https://example.com/data.csv';pdf='https://example.com/doc.pdf'
    filename='public_'+hashlib.sha256(csv.encode()).hexdigest()[:12]+'.csv'
    model=ScriptModel([
        call('download_public_file',{'url':csv},'download'),
        call('inspect_downloaded_table',{'filename':filename},'inspect'),
        call('aggregate_downloaded_table',{'filename':filename,'group_by':'species','method':'count'},'aggregate'),
        call('read_public_pdf',{'url':pdf},'pdf'),
        AIMessage(content='',tool_calls=[
            {'name':'record_source','args':{'title':'CSV','url':csv,'observed_fact':'3 fixture rows','verbatim_quote':'"sampled_rows": 3'},'id':'s1','type':'tool_call'},
            {'name':'record_source','args':{'title':'PDF','url':pdf,'observed_fact':'Text fixture','verbatim_quote':'Public PDF fixture content.'},'id':'s2','type':'tool_call'}]),
        call('export_excel',id='excel'),call('export_report',{'markdown_report':'# Result\na=2, b=1. PDF fixture extracted.'},'report')])
    result=await run_task('fixture multi tool study','documents',replace(settings,allowed_domains=('example.com',)),
                          model=model,browser_factory=empty_browser)
    assert result.status=='needs_review' and len(result.sources)==3
    assert any(source['category']=='calculation' for source in result.sources)
    assert result.completion['artifacts_valid'] and result.completion['outcome']=='needs_review'
    assert not result.completion['all_requirements_verified']
