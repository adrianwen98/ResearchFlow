"""Free real-public-data tool replay. Scripted decisions; NO LLM/API calls."""
import asyncio,json,re
from dataclasses import replace
from contextlib import asynccontextmanager
from langchain_core.messages import AIMessage
from web_agent.config import ROOT,Settings
from web_agent.runner import run_task
from web_agent.persistence import read_json,atomic_json

URL='https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv'
CASE='penguins_public_demo'
@asynccontextmanager
async def no_browser(settings,workspace):yield []

class DemoModel:
    def __init__(self):self.step=0;self.file='';self.info={};self.calcs=[];self.report=''
    def bind_tools(self,*args,**kwargs):return self
    async def ainvoke(self,messages):
        self.step+=1;calls=[]
        def add(name,**args):calls.append({'name':name,'args':args,'id':f'demo_{self.step}_{len(calls)}','type':'tool_call'})
        outputs=[str(m.content) for m in messages if m.type=='tool']
        if self.step==1:
            add('load_skill',skill_name='public_data');add('set_requirements',items=[{'description':'说明公开样本数量、缺失值及分析限制','kind':'research'},{'description':'按物种统计样本数和体重均值','kind':'calculation'},{'description':'交付有原始数据和统计的Excel','kind':'artifact','artifact':'task_result.xlsx'}]);add('update_plan',steps=['下载CC0公开企鹅观测数据','检查缺失值并按物种统计','导出数据、统计表及可追溯报告']);add('download_public_file',url=URL)
        elif self.step==2:
            self.file=re.search(r'public_[a-f0-9]+\.csv',outputs[-1]).group(0)
            add('inspect_downloaded_table',filename=self.file)
        elif self.step==3:
            self.info=json.loads(outputs[-1]);add('aggregate_downloaded_table',filename=self.file,group_by='species',method='count');add('aggregate_downloaded_table',filename=self.file,group_by='species',numeric_column='body_mass_g',method='mean')
        elif self.step==4:
            self.calcs=[json.loads(x) for x in outputs[-2:]]
            add('record_source',title='Palmer Penguins公开观测样本',url=URL,observed_fact='真实CSV结构与缺失值检查',verbatim_quote=json.dumps(self.info,ensure_ascii=False)[:180])
            add('record_source',title='按物种统计样本和体重',url=URL,observed_fact='体重均值由受限pandas工具计算，缺失值不参与均值',verbatim_quote=json.dumps(self.calcs[1],ensure_ascii=False)[:180])
            calc_id=self.calcs[0]['calculation_id']
            add('save_business_rows',sheets=[{'name':'物种样本数对比','mode':'comparison','columns':['样本数'],'fields':{'样本数':{'type':'number','unit':'count'}},'rows':[{'label':r['group'],'cells':{'样本数':{'value':r['value'],'calculation_id':calc_id}}} for r in self.calcs[0]['top_20']]}])
            add('export_business_excel',sheets=[{'name':'原始观测','mode':'table','filename':self.file},{'name':'物种样本数','mode':'calculation','calculation_id':self.calcs[0]['calculation_id']},{'name':'体重均值_g','mode':'calculation','calculation_id':self.calcs[1]['calculation_id']}])
        elif self.step==5:
            counts={r['group']:int(r['value']) for r in self.calcs[0]['top_20']}
            table='\n'.join(f"| {r['group']} | {counts[r['group']]} | {r['value']:.2f} |" for r in self.calcs[1]['top_20'])
            self.report=f"# 南极企鹅观测：样本结构与体重对比\n\n> 免费公开数据工具复现：决策顺序和报告模板由脚本给定，没有调用大模型；用于展示真实数据处理与交付，不作为Agent自主性验收。\n\n## 数据质检\n实际读取{self.info['sampled_rows']}条观测、{len(self.info['columns'])}列；体重缺失{self.info['null_counts']['body_mass_g']}条，性别缺失{self.info['null_counts']['sex']}条。无静默删除坏行；均值仅使用非缺失体重。\n\n## 样本对比\n| 物种 | 样本数（含体重缺失） | 非缺失体重均值(g) |\n|---|---:|---:|\n{table}\n\n体重均值计算ID：{self.calcs[1]['calculation_id']}。样本数不代表岛屿或物种的总体数量；观测数据不能据此得出因果关系。\n\n## 用途与局限\n可用来发现缺失记录、比较采集样本结构并检查分析口径。数据为2007–2009年观测样本，并非当前种群普查。读取范围为CSV最多5000行，本文件344行全部纳入；3组统计未触及前20组限制。\n\n## 来源及许可\n数据项目：https://allisonhorst.github.io/palmerpenguins/ 。原始CSV：{URL} 。项目声明数据以CC0提供。引用：Horst AM, Hill AP, Gorman KB (2020), palmerpenguins, doi:10.5281/zenodo.3960218。\n"
            add('export_excel');add('export_report',markdown_report=self.report)
        else:
            add('record_requirement_result',requirement_id='r1',source_ids=['1'],report_excerpt=f"实际读取{self.info['sampled_rows']}条观测、{len(self.info['columns'])}列")
            add('record_requirement_result',requirement_id='r2',source_ids=['2'],report_excerpt='样本数不代表岛屿或物种的总体数量；观测数据不能据此得出因果关系。',calculation_id=self.calcs[1]['calculation_id'])
            add('record_requirement_result',requirement_id='r3',source_ids=[])
        return AIMessage(content='',tool_calls=calls)

async def main():
    base=Settings.load();folder=base.output_dir/CASE
    if (folder/'task.json').exists():
        print('Demo exists; not downloading or rerunning.');return
    settings=replace(base,max_model_calls=16,max_tool_calls=40,task_timeout=120,allowed_domains=('raw.githubusercontent.com',))
    result=await run_task('公开企鹅数据质检：检查缺失值、按物种比较采集样本数和非缺失体重均值，导出业务Excel与报告。免费脚本工具复现，无LLM调用。',CASE,settings,model=DemoModel(),browser_factory=no_browser,report_format='both')
    assert result.status=='needs_review' and result.completion['requirements_supported'],result
    atomic_json(folder/'demo_meta.json',{'read_only':True,'execution_label':'免费公开数据工具复现 · 脚本决策 · 实际API请求0次','actual_api_requests':0,'scripted_decisions':True,'source_license':'CC0','source_project':'https://allisonhorst.github.io/palmerpenguins/'})
    print(json.dumps({'status':result.status,'actual_api_requests':0,'offline_decision_steps':result.metrics['requests'],'task_id':CASE},ensure_ascii=False))
if __name__=='__main__':asyncio.run(main())
