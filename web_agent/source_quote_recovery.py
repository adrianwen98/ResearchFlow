"""Read-only, same-URL recovery hints for rejected model-assembled quotes.

Hints are NEVER automatically registered as sources or treated as proof of
model-written facts. They come exclusively from the task's saved observations.
"""
from __future__ import annotations
import re
from .persistence import read_json
from .evidence_urls import url_key
from .evidence_text import contains_observed_excerpt


def _anchors(quote: str) -> list[str]:
    """Find meaningful fragments even when a model joins lines into one blob."""
    candidates=[]
    for line in re.split(r'[\n\r;；。.!?！？]+', quote):
        line=line.strip()
        if 10<=len(line)<=120:
            candidates.append(line)
        elif len(line)>120:
            # Try bounded consecutive word windows, NOT reconstructed facts.
            words=line.split()
            if len(words)>=5:
                for i in range(0, min(len(words),40), 4):
                    window=' '.join(words[i:i+14])
                    if len(window)>120:window=' '.join(words[i:i+8])
                    if 28<=len(window)<=120:candidates.append(window)
            else:
                for i in range(0,min(len(line),180),40):
                    chunk=line[i:i+64].strip()
                    if len(chunk)>=28:candidates.append(chunk)
    return sorted(dict.fromkeys(candidates),key=len,reverse=True)[:30]


def _exact_line(text: str, pos: int) -> str:
    left=text.rfind('\n',max(0,pos-300),pos)+1
    right=text.find('\n',pos,min(len(text),pos+500))
    if right<0:right=min(len(text),pos+300)
    line=text[left:right].strip()
    if len(line)>240:
        # Keep the prefix of the actual line with a word-boundary-safe end;
        # never splice text on opposite sides of the anchor.
        from .workspace import bounded_source_quote
        if pos-left>=220:
            line=text[max(0,pos-40):min(len(text),pos+160)].strip()
        else:
            line=bounded_source_quote(line,text,240)
    return line if contains_observed_excerpt(text,line) else ''


def quote_recovery_diagnostics(workspace, url: str, quote: str) -> dict:
    result={'kind':'source_quote_recovery','candidates':[],
            'related_observations':[],
            'note':'只读原文定位；candidate.exact_quote来自相同URL的观察记录，但不代表模型概括的事实已被核实。请检查后用observation_id和keyword重新登记，勿拼接摘录。'}
    if not isinstance(url,str) or not isinstance(quote,str):return result
    try:target=url_key(url)
    except (ValueError,TypeError):return result
    folder=workspace.path/'observations'
    if folder.is_symlink() or not folder.is_dir():return result
    anchors=_anchors(quote)
    for path in sorted(folder.glob('*.json'))[:500]:
        if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{20}',path.stem):continue
        if not path.is_file() or path.stat().st_size>2*1024*1024:continue
        saved=read_json(path,{})
        if not isinstance(saved,dict) or saved.get('id')!=path.stem or not isinstance(saved.get('text'),str):continue
        if not any(isinstance(u,str) and u and url_key(u)==target
                   for u in (saved.get('url'),saved.get('requested_url'))):continue
        result['related_observations'].append(path.stem)
        original=saved['text']
        anchored=next(((a,original.index(a)) for a in anchors
                       if original.count(a)==1 and contains_observed_excerpt(original,a)),None)
        if not anchored:continue
        anchor,pos=anchored
        candidate=_exact_line(original,pos)
        result['candidates'].append({
            'observation_id':path.stem,'url':saved['url'],
            'keyword':anchor[:120],
            'exact_quote':candidate,
            'context_preview':original[max(0,pos-100):min(len(original),pos+len(anchor)+120)]})
        if len(result['candidates'])>=3:break
    result['related_observations']=result['related_observations'][:10]
    return result
