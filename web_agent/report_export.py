"""One canonical Markdown source, deterministic offline HTML/CSS PDF rendering."""
from __future__ import annotations
import hashlib
import html
import re
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
from markdown_it import MarkdownIt
import pymupdf
from .persistence import atomic_json, read_json
from .security import validate_public_url
from .delivery_schema import quality_notes

class ReportRenderError(ValueError):
    """The PDF export failed; saved Markdown remains available but PDF delivery is incomplete."""


FORMATS={'markdown','pdf','both'}
CSS='''
body { font-family: sans-serif; font-size: 11pt; line-height: 1.6; color: #1f2024; }
h1 { font-size: 28pt; font-weight: bold; line-height: 1.25; color: #16171a; margin: 0 0 14pt; padding: 18pt 0 16pt; border-bottom: 3px solid #5b5bd6; }
h2 { font-size: 15pt; color: #5b5bd6; margin: 19pt 0 9pt; padding-bottom: 4pt; border-bottom: 1px solid #e3e5ea; }
h3 { font-size: 12pt; margin: 12pt 0 6pt; color: #16171a; }
p { margin: 5pt 0 9pt; } a { color: #5b5bd6; text-decoration: none; }
.report-meta { color: #6b6f78; font-size: 9pt; margin-bottom: 6pt; }
.report-lead { font-size: 11.5pt; line-height: 1.65; border-left: 3px solid #5b5bd6; padding: 8pt 12pt; margin: 8pt 0 16pt; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 10pt; margin: 10pt 0 14pt; }
th { color: #5b5bd6; font-weight: bold; border-bottom: 2px solid #5b5bd6; }
td, th { border-bottom: 1px solid #e3e5ea; padding: 3pt 2pt; vertical-align: top; overflow-wrap: anywhere; word-break: break-word; }
th:first-child, td:first-child { width: 15%; }
blockquote { margin: 8pt 0; padding-left: 12pt; border-left: 2px solid #d7d9e0; color: #4b4f57; }
pre { white-space: pre-wrap; font-size: 9pt; padding: 7pt; }
ul,ol { padding-left: 17pt; } li { margin-bottom: 5pt; }
'''


def report_format(workspace):
    return read_json(workspace.path/'task.json',{}).get('report_format','markdown')

def table_markdown(headers,rows):
    def esc(v):return str('未核实' if v is None else v).replace('|','\\|').replace('\n',' ')
    return '\n'.join(['| '+' | '.join(map(esc,headers))+' |','| '+' | '.join('---' for _ in headers)+' |']+
                     ['| '+' | '.join(map(esc,row))+' |' for row in rows])


def canonical_report(workspace, report):
    if not isinstance(report, str) or not report.strip() or len(report) > 200000:
        raise ValueError("报告需1–200000字符")

    records = workspace.sources()
    business = read_json(workspace.path / "business_artifact.json", {})

    title_match = re.search(r"^#\s+(.+)", report, re.M)
    title = (
        title_match.group(1)
        if title_match
        else read_json(workspace.path / "task.json", {}).get("task", "研究报告")[:120]
    )

    body = (
        re.sub(r"^#\s+.+\n?", "", report, count=1).strip()
        if report.lstrip().startswith("# ")
        else report.strip()
    )

    dates = sorted({
        r.get("accessed_at", "")[:10]
        for r in records
        if r.get("accessed_at")
    })
    scope = (
        " 至 ".join([dates[0], dates[-1]]) if len(dates) > 1 else dates[0]
    ) if dates else "未记录"

    sections = [
        f"# {title}",
        f"生成日期：{datetime.now(timezone.utc).date().isoformat()}\n\n"
        f"数据范围：{scope} 保存的 {len(records)} 条来源记录。",
        body,
    ]

    # 质量说明始终保留，不能因为正文相似而省略风险披露。
    notes = quality_notes(business.get("provenance", []))
    if notes:
        sections.extend([
            "## 数据口径与未核实事项",
            "\n".join("- " + n for n in notes),
        ])

    # 只识别正文中真实存在的 Markdown 表格行。
    body_table_rows = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line[1:-1].split("|")]
            body_table_rows.append(cells)

    def table_already_covered(sheet):
        rows = sheet.get("rows", [])
        if not rows:
            return False

        for row in rows:
            expected = [
                "未核实" if value is None else str(value).strip()
                for value in row
            ]

            matched = any(
                len(cells) >= len(expected)
                and all(
                    value and value in cell
                    for value, cell in zip(expected, cells)
                )
                for cells in body_table_rows
            )
            if not matched:
                return False

        return True

    # 只补充正文尚未完整覆盖的业务表格。
    if business.get("schema_version") == 2:
        missing_tables = [
            sheet for sheet in business["sheets"]
            if not table_already_covered(sheet)
        ]

        if missing_tables:
            sections.append("## 规范化数据与核验说明")
            sections.append(
                "下表与业务 Excel 使用同一份数据。"
                "来源、字段状态和规范化规则见 Provenance，"
                "限制及未核实事项见 Metadata。"
            )

            for sheet in missing_tables:
                sections.extend([
                    "### " + sheet["name"],
                    table_markdown(sheet["columns"], sheet["rows"]),
                ])

    # 完整来源附录是可机器核验的交付契约，不因正文中出现零散原文而删除。
    # 正文可使用摘要和分析；来源事实和连续原文始终保留在附录。
    sections.extend(["---", "## 已记录的来源与原文摘录"])

    if not records:
        sections.append("本次未记录可引用来源；分析结论仍需核实。")

    for rec in records:
        sections.append(
            f"### 来源 {rec['id']} · {rec['title']}\n\n"
            f"[查看原始页面](<{rec['url']}>)\n\n"
            f"记录的事实：{rec['fact']}\n\n"
            f"原文摘录：{rec['quote']}\n\n"
            f"查阅时间：{rec['accessed_at']}"
        )

    return "\n\n".join(sections) + "\n"


def safe_html(markdown):
    parser=MarkdownIt('commonmark',{'html':False,'linkify':False}).enable('table')
    parser.validateLink=lambda url: safe_url(url)
    parser.renderer.rules['image']=lambda tokens,idx,options,env: html.escape(tokens[idx].content)
    tokens=parser.parse(markdown)
    # Repeat headers in bounded table sections. Wide tables become column groups,
    # always retaining the identifying first column. No cells are dropped.
    parts=[];i=0
    while i<len(tokens):
        if tokens[i].type!='table_open':
            start=i;i+=1
            while i<len(tokens) and tokens[i].type!='table_open':i+=1
            parts.append(parser.renderer.render(tokens[start:i],parser.options,{}));continue
        rows=[];row=[];i+=1
        while i<len(tokens) and tokens[i].type!='table_close':
            t=tokens[i]
            if t.type=='tr_open':row=[]
            if t.type=='inline':row.append(parser.renderer.renderInline(t.children or [],parser.options,{}))
            if t.type=='tr_close':rows.append(row)
            i+=1
        i+=1
        if not rows:continue
        # Keep the yearly table together. Splitting an 8-column table into
        # 5+4 column groups made the PDF look misaligned; landscape A4 can hold
        # up to 10 readable columns. Only truly huge matrices need column groups.
        columns=len(rows[0]);groups=([list(range(columns))] if columns<=10
                                  else [[0]+list(range(k,min(k+4,columns)))
                                        for k in range(1,columns,4)])
        for cols in groups:
            if len(cols) > 1:
                rest = round(86 / (len(cols) - 1), 2)
                colgroup = ('<colgroup><col style="width:14%">'
                            + ''.join(f'<col style="width:{rest}%">' for _ in cols[1:])
                            + '</colgroup>')
            else:
                colgroup = ''
            for start in range(1,max(2,len(rows)),8):
                head='<tr>'+''.join('<th>'+rows[0][c]+'</th>' for c in cols)+'</tr>'
                body=''.join('<tr>'+''.join('<td>'+(r[c] if c<len(r) else '')+'</td>' for c in cols)+'</tr>' for r in rows[start:start+8])
                parts.append('<table>'+colgroup+head+body+'</table>')
    rendered=''.join(parts)
    rendered=re.sub(r'<p>(生成日期：.*?)</p>',r'<p class="report-meta">\1</p>',rendered,count=1,flags=re.S)
    rendered=re.sub(r'<p>(数据范围：.*?)</p>',r'<p class="report-meta">\1</p>',rendered,count=1,flags=re.S)
    rendered=re.sub(r'(<h2>执行摘要</h2>\s*)<p>(.*?)</p>',r'\1<p class="report-lead">\2</p>',rendered,count=1,flags=re.S)
    return rendered

def safe_url(url):
    try:validate_public_url(url);return True
    except (ValueError,TypeError):return False

class _WrapLongText(HTMLParser):
    """Add PDF-only line-break opportunities to visible long tokens, not link URLs.

    Canonical Markdown and stored evidence remain byte-for-byte unchanged.
    HTML attributes (especially href) remain unchanged; only text nodes are wrapped.
    """
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        self.parts.append('</' + tag + '>')

    def handle_entityref(self, name):
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')

    def handle_data(self, data):
        # MuPDF can silently clip even *successfully rendered* unbroken tokens.
        # Zero-width space gives line breaks without changing visible characters.
        self.parts.append(re.sub(r'\S{56,}', lambda m: '&#8203;'.join(
            m.group()[i:i+32] for i in range(0, len(m.group()), 32)), data))


def pdf_html(markdown):
    parser = _WrapLongText()
    parser.feed(safe_html(markdown))
    parser.close()
    return '<body>' + ''.join(parser.parts) + '</body>'


def _appendix_groups(parts, budget=4500, min_sources=2):
    """Group source blocks so several short sources share one PDF page.

    A separate MuPDF Story starts a new page; rendering one source per Story
    produced mostly blank appendix pages.  Keep groups small enough to avoid
    the historical long-appendix truncation, but never one source per page.
    """
    groups, current, length, sources = [], [], 0, 0
    for part in parts:
        is_source = part.lstrip().startswith('### 来源')
        if (is_source and sources >= min_sources
                and length + len(part) > budget):
            groups.append('\n'.join(current))
            current, length, sources = [], 0, 0
        current.append(part)
        length += len(part)
        if is_source:
            sources += 1
    if current:
        groups.append('\n'.join(current))
    return groups


def _max_table_columns(markdown):
    maximum = 0
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith('|') and stripped.endswith('|'):
            maximum = max(maximum, len(stripped.strip('|').split('|')))
    return maximum


def render_pdf(markdown,path):
    """Render body and full-width flowing evidence appendix without text loss."""
    # An 8-column statistical table cannot fit a portrait A4 at a readable
    # font; MuPDF otherwise splits it by columns across pages. Landscape keeps
    # all headers/data on the same page.
    box=pymupdf.paper_rect('a4-l' if _max_table_columns(markdown) >= 7 else 'a4')
    doc=pymupdf.open()
    marker='## 已记录的来源与原文摘录'
    chunks=markdown.split(marker,1) if len(markdown)>3000 else [markdown]
    if len(chunks)==2:chunks[1]=marker+chunks[1]
    if len(chunks)>1 and '## 数据口径与未核实事项' in chunks[0]:
        intro,quality=chunks[0].split('## 数据口径与未核实事项',1)
        chunks=[intro,'## 数据口径与未核实事项'+quality,*chunks[1:]]
    try:
        for section in chunks:
            if not section.strip():continue
            appendix=section.startswith(marker)
            # Do not render an arbitrarily long appendix as ONE MuPDF Story:
            # some versions silently drop middle reference sections.
            parts=(re.split(r'(?m)(?=^### 来源 )',section) if appendix else [section])
            if appendix:
                parts=_appendix_groups(parts)
            for chunk in parts:
                if not chunk.strip():continue
                css=CSS+('body {font-size:8pt;line-height:1.5;} h2 {font-size:12pt;} h3 {font-size:9pt;margin-top:7pt;} p {margin-bottom:6pt;}' if appendix else '')
                def rectfn(n,filled):
                    if n+len(doc)>=80:
                        raise ValueError('报告超过80页，请减少冗余摘录或拆分任务')
                    return box,pymupdf.Rect(46,60,box.width-46,box.height-52),None
                # Let Story paginate naturally. A fit_height -> insert_htmlbox pair is
                # NOT layout-equivalent and may fail or silently truncate long tokens.
                part=pymupdf.Story(html=pdf_html(chunk),user_css=css).write_with_links(rectfn)
                try:doc.insert_pdf(part)
                finally:part.close()
        for index,page in enumerate(doc):
            page.draw_line((46,37),(box.width-46,37),color=(.357,.357,.839),width=1)
            page.insert_text((46,29),'RESEARCHFLOW  /  RESEARCH',fontsize=8,color=(.357,.357,.839))
            page.insert_text((46,box.height-27),'Evidence-based research | Local deterministic export',fontsize=7,color=(.35,.4,.42))
            page.insert_text((box.width-90,box.height-27),f'{index+1} / {len(doc)}',fontsize=8,color=(.35,.4,.42))
        doc.set_metadata({'title':markdown.splitlines()[0].lstrip('# '),'producer':'ResearchFlow / PyMuPDF','subject':'Deterministic rendering of canonical Markdown'})
        doc.save(path,garbage=4,deflate=True)
    finally:doc.close()

def export_report(workspace,report):
    fmt=report_format(workspace)
    if fmt not in FORMATS:raise ValueError('无效报告格式')
    text=canonical_report(workspace,report)
    md=workspace.path/'research_report.md';pdf=workspace.path/'research_report.pdf'
    previous=read_json(workspace.path/'report_meta.json',{})
    old_sha=previous.get('sha256') if md.is_file() and not md.is_symlink() and hashlib.sha256(md.read_bytes()).hexdigest()==previous.get('sha256') else None
    pending=workspace.path/'research_report.pending.pdf'
    if pending.is_symlink() or pdf.is_symlink():raise ValueError('PDF目标不能是符号链接')
    pdf_error=None
    try:
        if fmt in {'pdf','both'}:
            try:render_pdf(text,pending)
            except Exception as exc:
                # Never fake PDF success; still preserve the independently valid
                # Markdown report and its source-bound hash below.
                pdf_error=exc
        if md.is_symlink():raise ValueError('报告目标不能是符号链接')
        temporary_md=workspace.path/'research_report.pending.md'
        if temporary_md.is_symlink():raise ValueError('报告临时文件不能是符号链接')
        temporary_md.write_text(text,encoding='utf-8');temporary_md.replace(md)
        if pdf_error is None and pending.exists():pending.replace(pdf)
        meta={'source_ids':[r['id'] for r in workspace.sources()], 'sha256':hashlib.sha256(md.read_bytes()).hexdigest(),'format':fmt,
              'business_sha256':read_json(workspace.path/'business_artifact.json',{}).get('sha256')}
        if fmt in {'pdf','both'} and pdf_error is None:
            meta.update(pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),pdf_source_sha256=meta['sha256'])
        atomic_json(workspace.path/'report_meta.json',meta)
        from .completion import rebind_unchanged_excerpts
        rebind_unchanged_excerpts(workspace,old_sha,meta['sha256'])
        if pdf_error is not None:
            workspace.log('report_pdf_render_failed',type(pdf_error).__name__,markdown_saved=True,report_format=fmt)
            raise ReportRenderError('PDF排版失败；Markdown报告与来源校验元数据已保存，PDF交付仍未完成：'+str(pdf_error)[:300]) from pdf_error
        workspace.log('export_report',pdf.name if fmt=='pdf' else md.name,report_format=fmt,additional_model_requests=0)
        return pdf if fmt=='pdf' else md
    finally:pending.unlink(missing_ok=True)

def verify_report_formats(workspace):
    meta=read_json(workspace.path/'report_meta.json',{});issues=[]
    if report_format(workspace) in {'pdf','both'}:
        try:
            pdf=workspace.path/'research_report.pdf'
            if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest()!=meta.get('pdf_sha256'):issues.append('pdf_hash_mismatch')
            if meta.get('pdf_source_sha256')!=meta.get('sha256'):issues.append('pdf_content_source_mismatch')
            with pymupdf.open(pdf) as doc:
                if not 1<=len(doc)<=80 or any(not p.get_text().strip() for p in doc):issues.append('pdf_empty_pages')
        except Exception:issues.append('pdf_missing_or_unreadable')
    business=read_json(workspace.path/'business_artifact.json',{})
    if business.get('schema_version')==2 and meta.get('business_sha256')!=business.get('sha256'):issues.append('report_normalized_data_stale')
    return issues
