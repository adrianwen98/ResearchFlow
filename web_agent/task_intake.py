"""Free, deterministic task intake. No model, network, task creation or tool calls."""
from __future__ import annotations
import re
from .security import validate_public_url

VERSION = 1
DEFAULTS = [
    '优先官方或一手来源，保留原文、链接和查询日期。',
    '数据按字段整理，统一单位；价格注明市场、币种、配置与价格类型。',
    '缺失或冲突信息明确披露，不估算、不编造；有计算时使用实际工具。',
    '未指定语言时使用中文；结论附证据，比较或统计任务另附业务表。',
]
EXECUTION_POLICY = '''需求理解规则：用户只需说明目标，不要求完整提示词。用户明确的范围、格式和口径优先于默认值。
默认优先官方/一手来源，记录URL、连续原文与查询日期；数值用独立字段和可比较单位，价格附币种、市场、配置和类型；缺失或冲突须披露，禁止估算或编造；计算用真实工具，中文报告由同一内容按任务选定格式导出。
持久任务记忆中的task_brief是执行前的范围与用户补充信息，不是事实证据，也不是已完成的计划。不要重复询问已经回答的问题，不得把默认范围当成用户明确偏好。
按实际任务决定工具与验收项，不要为了套模板而增加目标。先记录简短真实计划再执行。若执行中发现会实质改变结果的新歧义，不得自行捏造关键条件：可先完成明确部分，在结果中说明未决范围；必须用户处理时使用现有人工处理入口。登录、购买、提交和预算增加仍服从原审批规则。'''


def _questions(task):
    # Existing associated-task submissions already include their original goal
    # as context. Preserve that flow; this first intake UI applies to new goals.
    if re.match(r'^后续任务：[^\n]*\n\[关联任务: [A-Za-z0-9_-]{1,56}\]\n',task):
        return []
    questions=[]
    add=lambda key,prompt,options=None: questions.append({'id':key,'prompt':prompt,'options':options or []})
    has_url=bool(re.search(r'https?://\S+',task,re.I))
    generic=re.sub(r'[\s，。！？!?：:]+','',task)
    unnamed_comparison=bool(re.fullmatch(r'(?:帮我)?(?:比较|对比)(?:一下)?(?:这|那)?(?:两|几|多)(?:款|种|个)(?:产品|手机|软件|工具)(?:的(?:价格|功能|参数|区别|免费版))?',generic))
    if unnamed_comparison or generic in {'帮我分析一下','帮我研究一下','帮我比较一下','帮我做个报告','帮我做一份报告',
                    '分析一下数据','帮我处理数据','帮我比较两个产品','帮我查一下','帮我做个对比'}:
        add('target','你想研究什么对象，或分析哪份数据？')
        return questions
    if re.search(r'这个(?:公开)?文件|这份(?:文件|数据|表格)|分析文件|分析数据|处理文件',task) and not has_url:
        add('file_url','请提供要分析的公开文件链接（当前支持公开网址）。')
    compare=bool(re.search(r'比较|对比|compare',task,re.I))
    if compare and re.search(r'免费版|免费方案|免费计划|free (?:plan|tier)',task,re.I) and not re.search(r'个人|小团队|团队|企业|组织|personal|team',task,re.I):
        add('use_case','主要用于个人使用，还是团队协作？',['个人使用','小团队协作','不限定，分别说明'])
    if compare and re.search(r'价格|售价|报价|比价|price',task,re.I) and not re.search(r'中国|大陆|人民币|CNY|美国|USD|新加坡|SGD|日本|JPY|欧洲|EUR|英国|GBP|香港|HKD|德国|法国|加拿大|澳大利亚|韩国|台湾|不同国家|不同市场|各官网市场|不限地区',task,re.I):
        add('market','价格按哪个市场比较？',['中国大陆','美国','分别标注各官网市场，不直接比价'])
    if re.search(r'实习|招聘|岗位|找工作',task) and re.search(r'查找|寻找|搜索|找.*(?:实习|工作|岗位)|招聘网站',task) and not re.search(r'不限|城市|远程|北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|重庆|天津|长沙|香港',task):
        add('location','职位有城市要求吗？',['不限城市','北京 / 上海 / 深圳 / 杭州','仅远程岗位'])
    return questions[:2]


def prepare_task(task,answers=None,report_format='both'):
    task=task.strip()
    if not 5<=len(task)<=6000:raise ValueError('任务目标需 5–6000 个字符')
    if report_format not in {'pdf','markdown','both'}:raise ValueError('报告格式不受支持')
    if answers is None:answers={}
    if not isinstance(answers,dict) or len(answers)>3:raise ValueError('补充信息格式不正确')
    questions=_questions(task)
    allowed={q['id'] for q in questions}
    resolved={}
    for key,value in answers.items():
        if not isinstance(value,str) or len(value)>400 or any(ord(c)<32 and c not in '\n\t' for c in value):
            raise ValueError('每项补充信息最多 400 字符')
        if value.strip():resolved[key]=value.strip()
    # One generic answer can reveal a missing source or comparison context. Check
    # once more without inventing entities, silently assuming a market, or looping.
    if resolved.get('target'):
        followup=_questions(task+'\n研究对象：'+resolved['target'])
        for question in followup:
            if question['id'] not in allowed and len(questions)<2:
                questions.append(question);allowed.add(question['id'])
    if set(answers)-allowed:raise ValueError('补充答案与当前任务不匹配，请重新检查任务')
    if 'file_url' in resolved:
        validate_public_url(resolved['file_url'])
    # Every accepted answer must belong to a question, including a revealed one.
    unanswered=[q['id'] for q in questions if q['id'] not in resolved]
    scope_text=task+'\n'+resolved.get('target','')
    kind='comparison' if re.search(r'比较|对比|compare',scope_text,re.I) else 'data' if re.search(r'数据|统计|CSV|Excel|表格',scope_text,re.I) else 'research'
    scope={'comparison':'核对主要差异与限制，整理有来源的对比表和报告。',
           'data':'检查数据质量，使用工具分析，交付统计表和报告。',
           'research':'围绕目标查找可靠资料，核对证据并整理报告。'}[kind]
    return {'version':VERSION,'task':task,'report_format':report_format,'scope':scope,
            'defaults':DEFAULTS,'questions':questions,'answers':resolved,'ready':not unanswered,
            'unanswered':unanswered,'model_requests':0}


def context_brief(brief,task):
    """Bound a persisted brief and keep it attached to its original goal."""
    if not isinstance(brief,dict) or brief.get('version')!=VERSION or brief.get('task')!=task:return None
    answers=brief.get('answers',{})
    if not isinstance(answers,dict):return None
    return {'scope':str(brief.get('scope',''))[:200],
            'user_clarifications':[{ 'question':str(q.get('prompt',''))[:200],
              'answer':str(answers[q['id']])[:400] } for q in brief.get('questions',[])[:2]
              if isinstance(q,dict) and q.get('id') in answers],
            'report_format':brief.get('report_format','both')}
