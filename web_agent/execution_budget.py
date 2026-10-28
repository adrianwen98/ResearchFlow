"""Budget-aware handoff and saved material inspection; no paid/model IO."""
import json
import re
import unicodedata
from urllib.parse import urlsplit,urljoin
from html import unescape
from .evidence_text import find_observed_span
from .persistence import read_json
from .harness import ToolFailure

_OBS_ID = re.compile(r'[a-f0-9]{20}')
_MAX_OBS_BYTES = 2 * 1024 * 1024
_CJK_TERM = re.compile(r'[㐀-鿿]{2,}')
_LATIN_TERM = re.compile(r'[A-Za-z][A-Za-z0-9.+#_-]{2,}')
_GENERIC_TITLE_TERMS = frozenset({
    'iphone', 'apple', 'pro', 'max', 'plus', 'mini', 'air',
    '苹果', '中国', '中国大陆', '官方', '网站', '页面', '手机',
})


def handoff(workspace, maximum, used):
    # For a 30-call ceiling, start closing at call 20, independently of whether
    # the model remembered to register sources. This is a reserve, not a promise
    # that every research task can finish within the ceiling.
    reserve=min(max(0,maximum-1),max(2,min(10,maximum//3)))
    remaining=max(0,maximum-used)
    delivery_reserve=min(2,reserve)
    downloads=read_json(workspace.path/'downloads.json',{})
    downloadable=any(
        (workspace.path/name).is_file() and not (workspace.path/name).is_symlink()
        for name in downloads if isinstance(name,str) and re.fullmatch(r'public_[A-Za-z0-9_-]+\.(?:csv|xlsx|pdf)',name))
    staged=read_json(workspace.path/'business_rows.json',{})
    validation=staged.get('validation',{})
    # Merely registering one source does not establish coverage. Keep bounded
    # research available while staged facts remain unresolved. File production
    # itself is deterministic and does not need six unused model turns.
    ready=(validation.get('verified_cells',0)>0 and validation.get('pending_cells',1)==0) or downloadable
    closing=remaining<=reserve
    return {'remaining':remaining,'reserve':reserve,'closing':closing,
            'phase':'delivery' if remaining<=delivery_reserve else 'summary' if closing else 'research',
            'allocation':{'research':maximum-reserve,'summary':reserve-delivery_reserve,'delivery':delivery_reserve},
            'local_ready':ready,'local_only':closing and remaining<=delivery_reserve}


def _norm_title(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip().casefold()


def _title_terms(title):
    """Distinctive CJK/Latin terms used only to disambiguate saved pages."""
    if not isinstance(title, str):
        return []
    normalized = _norm_title(title[:300])
    terms = _CJK_TERM.findall(normalized) + _LATIN_TERM.findall(normalized)
    result = []
    for term in dict.fromkeys(terms):
        if term in _GENERIC_TITLE_TERMS:
            continue
        if re.fullmatch(r'[㐀-鿿]+', term):
            if len(term) >= 4:
                result.append(term)
        elif len(term) >= 7:
            result.append(term)
    return result[:32]


def _scan_observations(workspace, phrases):
    """Usable saved observations containing every phrase, bounded and read-only."""
    from .evidence_text import find_observed_span

    candidates = []
    folder = workspace.path / 'observations'
    if not folder.is_dir():
        return candidates
    for path in sorted(folder.glob('*.json')):
        if not _OBS_ID.fullmatch(path.stem):
            continue
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_OBS_BYTES:
                continue
            observed = read_json(path, {})
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(observed, dict) or observed.get('id') != path.stem:
            continue
        text = observed.get('text')
        url = observed.get('url')
        if not isinstance(text, str) or not isinstance(url, str):
            continue
        if all(find_observed_span(text, phrase, ignore_case=True) is not None for phrase in phrases):
            candidates.append(observed)
    return candidates


def _title_scores(candidates, title):
    from .evidence_text import find_observed_span

    terms = _title_terms(title)
    scores = {item['id']: 0 for item in candidates}
    matched = {item['id']: [] for item in candidates}
    for term in terms:
        hits = [item for item in candidates
                if find_observed_span(item['text'], term, ignore_case=True) is not None]
        if len(hits) == 1:
            cid = hits[0]['id']
            scores[cid] += 1
            matched[cid].append(term)
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked or ranked[0][1] < 1:
        return None, matched
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, matched
    return ranked[0][0], matched


def _resolve_observation(workspace, submitted_id, phrases, title):
    """Resolve a usable observation without guessing or weakening evidence.

    A valid 20-hex id is kept when it actually contains every phrase.  If it
    does not, the phrases may still uniquely identify another saved page; that
    page is used only when exactly one saved observation contains all phrases.
    Ambiguity refuses with candidate ids, and a phrase found nowhere still
    fails.  No quote is invented or rewritten.
    """
    explicit = None
    if isinstance(submitted_id, str) and _OBS_ID.fullmatch(submitted_id):
        path = workspace.path / 'observations' / f'{submitted_id}.json'
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_OBS_BYTES:
                raise ValueError("此ID不属于当前任务保存的网页")
            observed = read_json(path, {})
            if (not isinstance(observed, dict) or observed.get('id') != submitted_id
                    or not isinstance(observed.get('text'), str)
                    or not isinstance(observed.get('url'), str)):
                raise ValueError("网页内容无效")
            if all(find_observed_span(observed['text'], phrase, ignore_case=True)
                   for phrase in phrases):
                return observed, None
            explicit = observed

    candidates = _scan_observations(workspace, phrases)
    if len(candidates) == 1:
        reason = 'keyword_unique_other_page' if explicit is not None else 'unique_keyword'
        return candidates[0], {
            'resolved_observation_id': candidates[0]['id'],
            'submitted_observation_id': str(submitted_id)[:40],
            'reason': reason,
            'candidate_count': 1,
        }

    if len(candidates) > 1:
        state = read_json(workspace.path / 'browser_state.json', {})
        if isinstance(state, dict):
            state_id = state.get('observation_id')
            if isinstance(state_id, str):
                hit = next((item for item in candidates if item['id'] == state_id), None)
                if hit is not None:
                    return hit, {
                        'resolved_observation_id': hit['id'],
                        'submitted_observation_id': str(submitted_id)[:40],
                        'reason': 'current_browser_page_id',
                        'candidate_count': len(candidates),
                    }
            state_url = state.get('url')
            if isinstance(state_url, str):
                hits = [item for item in candidates if item['url'] == state_url]
                if len(hits) == 1:
                    return hits[0], {
                        'resolved_observation_id': hits[0]['id'],
                        'submitted_observation_id': str(submitted_id)[:40],
                        'reason': 'current_browser_page_url',
                        'candidate_count': len(candidates),
                    }
        chosen, matched = _title_scores(candidates, title)
        if chosen is not None:
            item = next(candidate for candidate in candidates if candidate['id'] == chosen)
            return item, {
                'resolved_observation_id': item['id'],
                'submitted_observation_id': str(submitted_id)[:40],
                'reason': 'title_unique_term',
                'candidate_count': len(candidates),
                'matched_title_terms': matched[chosen],
            }
        listing = '；'.join(f"{item['id']} {item['url']}" for item in candidates[:10])
        raise ValueError(
            f"keyword 命中{len(candidates)}个已保存网页，无法自动确定 observation_id；"
            f"候选：{listing}。请显式传入候选ID，或先inspect_saved_pages核对。"
        )

    raise ValueError(
        "keyword 未出现在保存的网页原文中（已按空白渲染差异匹配）；"
        "请先inspect_saved_pages核对并直接复制返回的连续原文"
    )


def record_source_from_observation(
    workspace, title, observation_id, observed_fact,
    keyword, category=''
):
    """从保存的网页中提取真实证据，支持自动拆分多行关键词。"""
    from .evidence_text import contains_observed_excerpt, find_observed_span

    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 720:
        raise ValueError("keyword 不能为空，总长度不能超过720字")

    # 自动将多行关键词拆分，去重并限制数量

    phrases = list(dict.fromkeys(
        p.strip()
        for p in keyword.splitlines()
        if len(p.strip()) >= 3
        and any(ch.isalnum() for ch in p)
    ))

    if not 1 <= len(phrases) <= 6 or any(
        not 3 <= len(p) <= 720 for p in phrases
    ):
        raise ValueError("每个关键词需要3–720字，总长度最多720字，一次最多6个")

    observed, normalization = _resolve_observation(
        workspace, observation_id, phrases, title
    )
    text = observed["text"]
    url = observed["url"]
    bound_id = normalization.get('resolved_observation_id') if normalization else None

    def save_one(phrase, fact, item_title):
        span = find_observed_span(text, phrase, ignore_case=True)

        if span is None:
            return None

        line_start = text.rfind("\n", 0, span.start) + 1
        starts = (
            [line_start, span.start]
            if span.start - line_start <= 80
            else [span.start]
        )

        limit = min(len(text), span.end + 240)

        # 多关键词时，不跨越下一行拼接不同产品的内容
        if len(phrases) > 1:
            line_end = text.find("\n", span.end)
            if line_end != -1:
                limit = min(limit, line_end)

        for sep in ("|\n", "\n\n", "\n关注\n对比"):
            stop = text.find(sep, span.end, limit)
            if stop != -1:
                limit = min(limit, stop)

        ends = {span.end, limit}

        for pos in range(span.end, limit):
            if text[pos].isspace() or text[pos] in "，。；;：:|":
                ends.add(pos)
                ends.add(pos + 1)

        # 用现有的严格证据规则筛选连续原文
        for start in starts:
            for end in sorted(ends, reverse=True):
                if end < span.end:
                    continue

                quote = text[start:end].strip()

                if (
                    3 <= len(quote) <= 1000
                    and contains_observed_excerpt(text, quote)
                ):
                    row = workspace.record(
                        item_title,
                        url,
                        fact,
                        quote,
                        category,
                        require_observation=True,
                        observation_id=bound_id,
                    )

                    return {
                        "source_id": row["id"],
                        "url": row["url"],
                        "quote": row["quote"],
                        "observation_id": row["observation_id"],
                    }

        raise ValueError(
            f"关键词“{phrase}”存在，但无法提取可核验的连续原文"
        )

    # 单关键词：保持原来的工具返回结构
    if len(phrases) == 1:
        result = save_one(phrases[0], observed_fact, title)

        if result is None:
            raise ValueError(
                "keyword 未出现在保存的网页原文中（已按空白渲染差异匹配）；"
                "请先inspect_saved_pages核对并直接复制返回的连续原文"
            )

        result["note"] = (
            "已提取真实连续原文；observed_fact仍需独立核验。"
        )
        if normalization:
            result["input_normalization"] = normalization
        return result

    # 多关键词：分别登记，部分失败不影响其他有效证据
    recorded = []
    missing = []
    failed = []

    for phrase in phrases:
        try:
            # 不把原来的复合结论直接认定为已核实
            result = save_one(
                phrase,
                f"页面原文包含：{phrase}",
                f"{title}（{phrase}）",
            )

            if result is None:
                missing.append(phrase)
            else:
                recorded.append({
                    "keyword": phrase,
                    **result,
                })

        except ValueError as exc:
            failed.append({
                "keyword": phrase,
                "reason": str(exc),
            })

    if not recorded:
        raise ValueError(
            f"没有可登记的证据；未命中：{missing}；"
            f"截取失败：{failed}"
        )

    result = {
        "status": "partial" if missing or failed else "recorded",
        "recorded_sources": recorded,
        "missing_keywords": missing,
        "failed_keywords": failed,
        "original_observed_fact_verified": False,
        "note": (
            "已登记的仅是实际观察到的原文证据。"
            "原始复合结论未自动核实；"
            "缺失项需要其他真实来源证明。"
        ),
    }
    if normalization:
        result["input_normalization"] = normalization
    return result



def inspect_saved_pages(workspace,observation_id='',keyword='',offset=0):
    from .evidence_text import find_observed_span
    if type(offset) is not int or not 0<=offset<=20000:raise ValueError('offset需为0–20000的整数')
    if not isinstance(keyword,str) or len(keyword)>720:raise ValueError('keyword最多720字')
    folder=workspace.path/'observations'
    if observation_id:
        if not isinstance(observation_id,str) or not re.fullmatch(r'[a-f0-9]{20}',observation_id):
            choices=sorted(p.stem for p in folder.glob('*.json') if re.fullmatch(r'[a-f0-9]{20}',p.stem) and not p.is_symlink())[:10]
            raise ValueError('observation_id 无效，请使用 inspect_saved_pages 返回的真实ID。候选ID：'+', '.join(choices))
        path=folder/f'{observation_id}.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:raise ValueError('观察记录不存在或不可读取')
        item=read_json(path,{})
        if item.get('id')!=observation_id or not isinstance(item.get('text'),str):raise ValueError('观察记录无效')

        text = item["text"]

        span = (
            find_observed_span(text, keyword, ignore_case=True)
            if keyword else None
        )

        # 指定关键词时定位匹配位置；
        # 没有关键词时，按 offset 读取指定位置。
        start = (
            max(0, span.start - 800)
            if span
            else min(offset, len(text))
        )

        return {
            "observation_id": observation_id,
            "url": item["url"],
            "text": text[start:start + 6000]
                    if not keyword or span else "",
            "keyword_found": bool(span) if keyword else None,
            "offset": start,
            "total_chars": len(text),
            "note": (
                "这是保存的真实网页原文。"
                "指定keyword时优先定位匹配内容；"
                "否则使用offset翻页。"
            ),
        }

    if keyword:
        # Search only observations already stored in this task's workspace.
        # Candidate previews do not constitute evidence or source records.
        matches=[]
        candidates=sorted((p for p in folder.glob('*.json')
                          if re.fullmatch(r'[a-f0-9]{20}',p.stem) and not p.is_symlink()),
                          key=lambda p:p.name)
        for path in candidates:
            if not path.is_file() or path.stat().st_size>2*1024*1024:
                continue
            try:
                item=read_json(path,{})
            except json.JSONDecodeError:
                continue
            if not isinstance(item,dict) or item.get('id')!=path.stem:
                continue
            text=item.get('text')
            url=item.get('url')
            if not isinstance(text,str) or not isinstance(url,str):
                continue
            span=find_observed_span(text,keyword,ignore_case=True)
            if span is None:
                continue
            # Show a short exact fragment around the match, even when the
            # keyword occurs well beyond the first 160 characters.
            left=max(0,span.start-60)
            right=min(len(text),max(span.end+60,left+160))
            matches.append({'observation_id':path.stem,'url':url,
                            'preview':text[left:right][:240],
                            'match_offset':span.start})
        total=len(matches)
        return {'observations':matches[offset:offset+10], 'total':total,
                'offset':offset,'next_offset':offset+10 if offset+10<total else None,
                'keyword':keyword,
                'note':'仅在当前任务已保存的观察记录中搜索；预览不是已核实证据。请用返回的observation_id读取完整原文后再登记来源。'}
    paths=sorted((p for p in folder.glob('*.json') if re.fullmatch(r'[a-f0-9]{20}',p.stem) and not p.is_symlink()),key=lambda p:p.name)
    selected=[]
    for path in paths[offset:offset+10]:
        if path.stat().st_size>2*1024*1024:continue
        item=read_json(path,{})
        if item.get('id')!=path.stem:continue
        # Index is deliberately small; full exact text is available by ID.
        selected.append({'observation_id':path.stem,'url':item.get('url',''),
                         'preview':str(item.get('text',''))[:160]})
    return {'observations':selected,'total':len(paths),'offset':offset,
            'next_offset':offset+10 if offset+10<len(paths) else None}


def require_observed_path_after_not_found(workspace,url):
    """After two 404s on one host, new paths need saved/user-provided evidence.

    This is a strategy guard, not domain authorization. Existing URL/network
    security still runs first. Search, documentation on another host, and real
    observed links remain available.
    """
    host=urlsplit(url).hostname
    failed=[r for r in read_json(workspace.path/'tool_journal.json',{}).values()
            if r.get('target_domain')==host and r.get('error_code')=='http_404']
    if len(failed)<2:return
    task=read_json(workspace.path/'task.json',{}).get('task','')
    if url in re.findall(r'https?://[^\s<>"\]]+',str(task)):return
    for path in (workspace.path/'observations').glob('*.json'):
        if path.is_symlink() or path.stat().st_size>2*1024*1024:continue
        item=read_json(path,{})
        text=unescape(str(item.get('text','')))
        if item.get('url')==url or re.search(re.escape(url)+r'(?=$|[\s<>\"\'\]})])',text):return
        for match in re.finditer(r'/url: ([^\n]+)',text):
            href=match.group(1).strip().strip('\"\'')
            if urljoin(item.get('url',''),href)==url:return
    raise ToolFailure('unobserved_path','该站点已有多个404，当前新地址没有已读页面或用户提供的网址依据。停止猜接口；用browser_search查官方API文档，或跟随已观察到的真实链接。')
