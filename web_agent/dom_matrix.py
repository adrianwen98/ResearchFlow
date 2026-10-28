"""DOM-observed multi-product table attribution. No model-inferred column order.

Browser sidecar is scoped to one source observation and its exact saved bytes;
a naked text quote or an attractive table title alone never proves ownership.
"""
from __future__ import annotations
import hashlib
import re
import unicodedata
from pathlib import Path
from .persistence import atomic_json, read_json
from .evidence_urls import url_key

# Host-authored, read-only browser evaluation. No model-supplied JS or selectors.
# Only true table/grid accessibility rows with a unique explicit column-header
# alignment can establish a product-to-cell binding. Uncertain spans are skipped.
DOM_MATRIX_JS = r'''() => {
  const output = [];
  const tidy = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!(el.getClientRects && el.getClientRects().length) &&
    !el.closest('[hidden],[aria-hidden="true"],template');
  const role = el => (el.getAttribute('role') || '').toLowerCase().trim().split(/\s+/)[0];
  const isCell = el => ['TD','TH'].includes(el.tagName) ||
    ['cell','gridcell','columnheader','rowheader'].includes(role(el));
  const isCol = el => role(el) === 'columnheader' ||
    (el.tagName === 'TH' && el.getAttribute('scope') === 'col');
  const isRowHeader = el => role(el) === 'rowheader' ||
    (el.tagName === 'TH' && el.getAttribute('scope') === 'row');
  const unitSpan = el => +(el.getAttribute('aria-colspan') || el.colSpan || 1) === 1 &&
    +(el.getAttribute('aria-rowspan') || el.rowSpan || 1) === 1;
  for (const table of Array.from(document.querySelectorAll('table,[role="table"],[role="grid"]')).slice(0,8)) {
    if (!visible(table)) continue;
    const rows = Array.from(table.querySelectorAll('tr,[role="row"]'))
      .filter(row => row.closest('table,[role="table"],[role="grid"]') === table && visible(row));
    const slots = row => Array.from(row.children).filter(c => isCell(c) && visible(c));
    let head = null;
    for (const row of rows.slice(0,12)) {
      const cells = slots(row);
      const hasCols = cells.filter(isCol).length >= 2 ||
        (row.closest('thead') && cells.length >= 2 && cells.every(c => c.tagName === 'TH'));
      if (!hasCols || cells.some(c => !unitSpan(c))) continue;
      const names = cells.map(tidy);
      if (names.length >= 2 && names.slice(1).every(Boolean) && new Set(names.slice(1)).size === names.length-1) {
        head = {row, names}; break;
      }
    }
    if (!head) continue;
    const hasRowHeader = rows.some(row => { const c = slots(row);
      return row !== head.row && c.length === head.names.length && isRowHeader(c[0]); });
    const productStart = (!head.names[0] || hasRowHeader) ? 1 : 0;
    const products = head.names.slice(productStart);
    if (products.length < 2 || products.length > 10 || new Set(products).size !== products.length) continue;
    const facts = [];
    for (const row of rows) {
      if (row === head.row || facts.length >= 300) continue;
      const cells = slots(row);
      if (!cells.length || cells.some(c => !unitSpan(c))) continue;
      let offset = 0, label = '';
      if (cells.length === products.length+1 && isRowHeader(cells[0])) {
        label = tidy(cells[0]); offset = 1;
      } else if (cells.length !== products.length || cells[0] && isRowHeader(cells[0])) {
        continue;
      }
      const texts = cells.slice(offset).map(tidy);
      if (texts.length !== products.length || texts.some(t => !t || t.length > 500)) continue;
      for (let i=0;i<texts.length;i++) facts.push({owner:products[i],field:label,value:texts[i]});
    }
    if (facts.length) output.push({headers:products, rows:facts.slice(0,300)});
  }
  return output;
}'''


def validate_tables(payload):
    """Reject malformed, unbounded, or ambiguous evaluated table rows."""
    if not isinstance(payload, list): return []
    output = []
    for table in payload[:8]:
        if not isinstance(table, dict): continue
        headers = table.get('headers')
        rows = table.get('rows')
        if not isinstance(headers,list) or not 2 <= len(headers) <= 10 or not isinstance(rows,list) or len(rows)>300:continue
        if any(not isinstance(h,str) or not h.strip() or len(h)>120 for h in headers):continue
        canonical=[' '.join(unicodedata.normalize('NFKC',h).split()).casefold() for h in headers]
        if len(set(canonical))!=len(headers):continue
        selected = []
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'owner','field','value'}:continue
            owner,field,value=row['owner'],row['field'],row['value']
            if not all(isinstance(x,str) for x in (owner,field,value)):continue
            if owner not in headers or len(field)>120 or not value.strip() or len(value)>500:continue
            selected.append({'owner':owner,'field':field,'value':value})
        if selected:output.append({'headers':headers,'rows':selected})
    return output


def compact_tables(tables, max_chars=3800):
    """Model preview; the bound JSON sidecar (not this string) is authoritative."""
    lines=['### DOM 表格列归属（从当前页面结构读取，不是模型推断）',
           '引用时仅复制一个单元格的原文；不同列不能合并；无明确列归属时不能据此核验。']
    for table in tables:
        for row in table['rows']:
            text=f"- 对象: {row['owner']} | 字段: {row['field'] or '（表格未标注）'} | 单元格原文: {row['value']}"
            if sum(map(len,lines)) + len(text)>max_chars: return '\n'.join(lines)+'\n[其余表格单元格未在本轮预览中显示]'
            lines.append(text)
    return '\n'.join(lines)


def save_table_binding(workspace, observation_id, url, observation_text, tables):
    if not tables:return
    folder=workspace.path/'dom_tables';folder.mkdir(exist_ok=True)
    atomic_json(folder/f'{observation_id}.json',{
        'observation_id':observation_id,'url':url,
        'observation_sha256':hashlib.sha256(observation_text.encode('utf-8')).hexdigest(),
        'tables':tables})


def bound_rows(workspace, source):
    """Load only host-extracted table data bound to the source's exact observation."""
    oid=source.get('observation_id','') if isinstance(source,dict) else ''
    if not isinstance(oid,str) or not re.fullmatch(r'[a-f0-9]{20}',oid):return []
    folder=workspace.path
    sidecar=folder/'dom_tables'/f'{oid}.json'
    observation=folder/'observations'/f'{oid}.json'
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size>2*1024*1024 for p in (sidecar,observation)):
        return []
    doc=read_json(observation,{});saved=read_json(sidecar,{})
    if doc.get('id')!=oid or saved.get('observation_id')!=oid or not isinstance(doc.get('text'),str):return []
    if not doc.get('url') or not source.get('url'):return []
    if url_key(doc['url'])!=url_key(source['url']) or url_key(saved.get('url',''))!=url_key(doc['url']):return []
    if hashlib.sha256(doc['text'].encode('utf-8')).hexdigest()!=saved.get('observation_sha256'):return []
    return [row for t in validate_tables(saved.get('tables')) for row in t['rows']]
