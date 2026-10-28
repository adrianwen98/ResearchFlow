"""任务独立工作区：研究证据、输出文件、可复核的执行轨迹。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from .security import excel_safe, safe_identifier, validate_public_url
from .persistence import atomic_json, read_json, redact, sanitize
import hashlib
import re
from .evidence_urls import url_key,legacy_navigation_targets
from .evidence_text import contains_observed_excerpt


def bounded_source_quote(quote: str, observed_text: str | None = None, limit: int = 1000) -> str:
    """Store only an excerpt that will *still* pass the observation matcher.

    A blind 1000-character slice can cut `Generics` to `Gener` and change
    a valid observation into an unbounded, unverifiable token fragment.
    Shrink the same *continuous prefix* at a safe boundary; never splice.
    """
    value = quote.strip()
    if len(value) <= limit:
        return value
    for end in range(limit, 0, -1):
        candidate = value[:end].rstrip()
        if not candidate:
            break
        pos = len(candidate)
        after = value[pos:pos + 1]
        last = candidate[-1]
        # Never cut through ASCII identifiers, product names or numbers.
        if (after and ((last.isascii() and last.isalnum() and after.isascii() and after.isalnum())
                or (last.isdigit() and after in ',.'))):
            continue
        if observed_text is None or contains_observed_excerpt(observed_text, candidate):
            return candidate
    raise ValueError('来源摘录超过1000字且没有可核验的安全截断位置；请提交较短的连续原文。')


class Workspace:
    def __init__(self, output_dir: Path, task_id: str, allowed_domains: tuple[str, ...] = ()):
        self.task_id = safe_identifier(task_id)
        self.path = output_dir / self.task_id
        self.path.mkdir(parents=True, exist_ok=True)
        self.data_file = self.path / "sources.json"
        self.allowed_domains = allowed_domains

    def observe(self, url: str, text: str, kind: str = "browser", *, requested_url: str | None = None) -> str:
        """Keep the exact text returned to the model, not model-provided evidence."""
        valid = validate_public_url(url, self.allowed_domains)
        requested = validate_public_url(requested_url,self.allowed_domains) if requested_url else None
        alias = requested if requested and url_key(requested)!=url_key(valid) else None
        key = hashlib.sha256((valid + text + ("\n"+alias if alias else "")).encode()).hexdigest()[:20]
        folder = self.path / "observations"
        folder.mkdir(exist_ok=True)
        atomic_json(folder / f"{key}.json", {"id": key, "url": valid, "text": text, "kind": kind, **({"requested_url":alias} if alias else {})})
        return key

    def evidence_for(self, url: str, quote: str, observation_id: str | None = None):
        if observation_id is not None and not re.fullmatch(r'[a-f0-9]{20}', observation_id):
            raise ValueError('无效的 observation_id')
        observed=[]
        for path in (self.path / "observations").glob("*.json"):
            if path.is_symlink() or (observation_id is not None and path.stem != observation_id):
                continue
            item=read_json(path)
            if not isinstance(item,dict) or item.get('id')!=path.stem:continue
            observed.append(item)
            candidates=[item.get("url",""),item.get("requested_url","")]
            if any(candidate and url_key(candidate)==url_key(url) for candidate in candidates) and contains_observed_excerpt(item.get("text", ""), quote):
                return item["id"]
        # Older tasks did not save requested_url. Only proven successful
        # navigation metadata may bridge an alias to an exact observed page.
        targets=legacy_navigation_targets(self.path,url,self.allowed_domains)
        for item in observed:
            if url_key(item.get("url","")) in targets and contains_observed_excerpt(item.get("text", ""), quote):
                return item["id"]
        matches=[item['url'] for item in observed if contains_observed_excerpt(item.get('text', ''), quote)]
        if matches:
            raise ValueError("摘录存在，但来源网址不匹配。请使用实际读取的最终网址："+matches[0][:420]+"；不要改写摘录。")
        raise ValueError("摘录不在本任务实际读取的该 URL 内容中；请先读取页面/PDF/表格，再复制连续原文摘录。")

    def source_excerpt(self, source_id: str, keyword: str = '') -> dict:
        """Read a bounded, exact slice of this task's stored observation only."""
        from .evidence_text import find_observed_span
        if not isinstance(source_id,str) or len(source_id)>40 or not isinstance(keyword,str) or len(keyword)>720:
            raise ValueError('source_id需已有来源ID；keyword最多720字')
        source=next((row for row in self.sources() if row.get('id')==source_id),None)
        if not source:raise ValueError('来源ID不属于当前任务')
        oid=source.get('observation_id','')
        if not isinstance(oid,str) or not re.fullmatch(r'[a-f0-9]{20}',oid):raise ValueError('来源未保存可读取的观察记录')
        path=self.path/'observations'/f'{oid}.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:raise ValueError('观察记录不可用')
        observed=read_json(path,{})
        if observed.get('id')!=oid or url_key(observed.get('url',''))!=url_key(source['url']):raise ValueError('来源与观察记录不匹配')
        text=observed.get('text','')
        if not isinstance(text,str):raise ValueError('观察记录内容无效')
        span=find_observed_span(text,keyword,ignore_case=True) if keyword else None
        start=max(0,span.start-800) if span else 0
        return {'source_id':source_id,'url':source['url'],'observation_id':oid,
                'keyword_found':bool(span) if keyword else None,
                'text':text[start:start+6000] if not keyword or span else '',
                'offset':start,'total_chars':len(text),'note':'本任务已保存网页原文片段；不含新网页请求，不是来源摘要fact。'}

    def sources(self) -> list[dict[str, str]]:
        if not self.data_file.exists():
            return []
        return json.loads(self.data_file.read_text(encoding="utf-8"))

    def record(self, title: str, url: str, fact: str, quote: str, category: str = "", *, require_observation=False, observation_id=None) -> dict[str, str]:
        valid_url = validate_public_url(url, self.allowed_domains)
        if not fact.strip() or not quote.strip():
            raise ValueError("必须提供具体观察到的事实和对应的网页原文摘录")
        records = self.sources()
        observation_id = self.evidence_for(valid_url, quote, observation_id) if require_observation else "manual-unverified"
        requested_url=valid_url
        observed_text=None
        if require_observation:
            observed=read_json(self.path/"observations"/f"{observation_id}.json",{})
            valid_url=validate_public_url(observed["url"],self.allowed_domains)
            observed_text=observed.get('text')
            if not isinstance(observed_text,str):
                raise ValueError('原始观察记录内容无效，不能登记来源')
        stored_quote=bounded_source_quote(quote, observed_text)
        # Verify the *exact persisted value*, not just the pre-truncation input.
        if require_observation and not contains_observed_excerpt(observed_text,stored_quote):
            raise ValueError('待保存的原文摘录不在对应观察记录中；未登记来源')
        for old in records:
            if old["url"] == valid_url and old["quote"] == stored_quote:
                if require_observation:
                    try:
                        if self.evidence_for(old['url'],old['quote'],old.get('observation_id'))!=old['observation_id']:
                            raise ValueError('来源观察ID不匹配')
                    except (ValueError,KeyError) as exc:
                        raise ValueError('已有重复来源但旧观察绑定无效；请离线核对并修复历史来源') from exc
                return old
        record = {
            "id": str(len(records) + 1),
            "title": title.strip()[:180],
            "url": valid_url,
            "fact": fact.strip()[:2000],
            "quote": stored_quote,
            "category": category.strip()[:80],
            "accessed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "observation_id": observation_id,
            **({"requested_url":requested_url} if url_key(requested_url)!=url_key(valid_url) else {}),
        }
        records.append(record)
        atomic_json(self.data_file, records)
        return record

    def log(self, event: str, detail="", **fields) -> None:
        row = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "detail": redact(detail)[:2000],
            **{k: sanitize(v) for k, v in fields.items()},
        }
        with (self.path / "trace.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def export_excel(self) -> Path:
        records = self.sources()
        if not records:
            raise ValueError("当前没有已记录的来源，不能导出空的证据表")
        columns = ["id", "title", "url", "fact", "quote", "category", "accessed_at"]
        df = pd.DataFrame([{k: excel_safe(r.get(k, "")) for k in columns} for r in records], columns=columns)
        output = self.path / "research_sources.xlsx"
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="来源证据")
            ws = writer.sheets["来源证据"]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col, width in {"A": 9, "B": 36, "C": 55, "D": 68, "E": 68, "F": 24, "G": 25}.items():
                ws.column_dimensions[col].width = width
        self.log("export_excel", str(output.name))
        return output

    def export_report(self, report: str) -> Path:
        from .report_export import export_report
        return export_report(self,report)

    def verify_delivery(self) -> dict:
        """Validate actual workbook cells and report bytes, not just file existence.

        This deliberately cannot prove that every natural-language requirement or
        interpretation is correct. A valid delivery still requires human review.
        """
        from .report_export import verify_report_formats
        issues = verify_report_formats(self)
        from .business_excel import verify_business_excel
        issues.extend(verify_business_excel(self))
        business_required=(self.path/'business_rows.json').exists() or (self.path/'business_draft.json').exists() or any(
            r.get('artifact')=='task_result.xlsx' for r in read_json(self.path/'requirements.json',[]))
        if business_required and not (self.path/'task_result.xlsx').is_file():issues.append('business_excel_missing')
        records = self.sources()
        if not records:
            issues.append('no_verified_sources')
        for row in records:
            try:
                observation = self.evidence_for(row['url'], row['quote'], row.get('observation_id',''))
                if row.get('observation_id') != observation:
                    issues.append('observation_id_mismatch:' + row['id'])
            except (ValueError, KeyError):
                issues.append('unobserved_source:' + row.get('id', '?'))
        try:
            columns = ['id', 'title', 'url', 'fact', 'quote', 'category', 'accessed_at']
            frame = pd.read_excel(self.path/'research_sources.xlsx', dtype=str, keep_default_na=False)
            expected = [{key: excel_safe(row.get(key, '')) for key in columns} for row in records]
            if list(frame.columns) != columns or frame.to_dict('records') != expected:
                issues.append('excel_content_mismatch')
        except Exception as exc:
            issues.append('excel_unreadable:' + type(exc).__name__)
        try:
            path = self.path/'research_report.md'
            report = path.read_text(encoding='utf-8')
            if not report.split('\n\n---\n', 1)[0].strip():
                issues.append('empty_report_body')
            if any(row['url'] not in report or row['quote'] not in report for row in records):
                issues.append('report_missing_references')
            meta = read_json(self.path/'report_meta.json', {})
            if meta.get('source_ids') != [row['id'] for row in records]:
                issues.append('report_sources_stale')
            if meta.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
                issues.append('report_hash_missing_or_mismatched')
        except Exception as exc:
            issues.append('report_unreadable:' + type(exc).__name__)
        return {'artifacts_valid': not issues, 'issues': issues,
                'semantic_review_required': True, 'all_requirements_verified': False}

    def domain_summary(self) -> dict[str, object]:
        records = self.sources()
        counts: dict[str, int] = {}
        for rec in records:
            domain = urlsplit(rec["url"]).hostname or "unknown"
            counts[domain] = counts.get(domain, 0) + 1
        return {"source_count": len(records), "domain_count": len(counts), "sources_by_domain": counts}

    def list_artifacts(self) -> list[str]:
        from .report_export import report_format
        pdf_requested_only = report_format(self) == 'pdf'
        pdf = self.path/'research_report.pdf'
        meta = read_json(self.path/'report_meta.json',{})
        pdf_valid = (pdf.is_file() and not pdf.is_symlink() and meta.get('pdf_sha256') and
                     meta['pdf_sha256']==hashlib.sha256(pdf.read_bytes()).hexdigest())
        return [str(p) for name in ('research_report.md','research_report.pdf','research_sources.xlsx','task_result.xlsx')
                if name!='research_report.md' or not pdf_requested_only or not pdf_valid
                for p in [self.path/name] if p.is_file() and not p.is_symlink()]
