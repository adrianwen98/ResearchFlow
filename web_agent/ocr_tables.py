"""Deterministic conversion of an OCR'd yearbook table into a CSV/business sheet."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from .persistence import atomic_json, read_json


def _rows_from_ocr(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if '|' not in line:
            continue
        cells = [cell.strip() for cell in line.split('|')]
        if len(cells) >= 2 and any(cells):
            rows.append(cells)
    return rows


def _headers(rows: list[list[str]], text: str) -> list[str]:
    """Only tables whose OCR actually shows the year/nutrient headers qualify.

    A table image without headers (e.g. a continuation page) must not turn its
    first numeric row into column names.
    """
    if all(term in text for term in ('年份', '氮肥', '磷肥', '钾肥', '复合肥')):
        return ['年份', '耕地灌溉面积', '农用化肥施用量（万吨）',
                '氮肥（万吨）', '磷肥（万吨）', '钾肥（万吨）', '复合肥（万吨）']
    return []


def save_ocr_table(workspace, url: str, observation_id: str, text: str) -> str:
    """Save year rows from OCR text as a CSV; return its filename."""
    rows = _rows_from_ocr(text)
    year_rows = [row for row in rows
                 if row and re.fullmatch(r'(?:19|20)\d{2}', row[0])]
    if len(year_rows) < 2:
        return ''
    headers = _headers(rows, text)
    if not headers:
        return ''
    header_rows = [row for row in rows
                   if row and not re.fullmatch(r'(?:19|20)\d{2}', row[0])]
    header_text = '\n'.join(' | '.join(row) for row in header_rows[:3])
    width = len(headers)
    data = []
    raw_rows = []
    for row in year_rows:
        cells = (row + [''] * width)[:width]
        data.append(cells)
        raw_rows.append(' | '.join(row))
    filename = 'ocr_' + hashlib.sha256(
        (url + observation_id + '\n'.join(raw_rows)).encode()).hexdigest()[:12] + '.csv'
    frame = pd.DataFrame(data, columns=headers)
    path = workspace.path / filename
    if path.exists() or path.is_symlink():
        return filename if path.is_file() and not path.is_symlink() else ''
    frame.to_csv(path, index=False, encoding='utf-8-sig')
    manifest = read_json(workspace.path / 'downloads.json', {})
    manifest[filename] = {
        'url': url,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'ocr': True,
    }
    atomic_json(workspace.path / 'downloads.json', manifest)
    tables = read_json(workspace.path / 'ocr_tables.json', {})
    tables[filename] = {'url': url, 'observation_id': observation_id,
                        'headers': headers, 'header_text': header_text,
                        'raw_rows': raw_rows}
    atomic_json(workspace.path / 'ocr_tables.json', tables)
    return filename


def ocr_business_sheet(workspace, filename: str, source_ids_by_row=None):
    """Build a comparison sheet from one OCR CSV using exact row quotes."""
    meta = read_json(workspace.path / 'ocr_tables.json', {}).get(filename)
    if not isinstance(meta, dict):
        raise ValueError('这不是本任务的 OCR 表格')
    frame = pd.read_csv(workspace.path / filename, dtype=str, keep_default_na=False)
    headers = [str(c) for c in frame.columns]
    raw_rows = meta.get('raw_rows') or []
    rows = []
    for index, record in enumerate(frame.to_dict('records')):
        if index >= len(raw_rows):
            break
        year = str(record.get(headers[0], '')).strip()
        if not re.fullmatch(r'(?:19|20)\d{2}', year):
            continue
        quote = raw_rows[index]
        source = workspace.record(
            f'统计年鉴 OCR {year}', meta['url'],
            f'OCR 表格 {year} 年行；列名与单位：' + '、'.join(headers),
            quote, '官方统计年鉴（OCR）', require_observation=True,
            observation_id=meta['observation_id'])
        cells = {}
        for column in headers:
            raw = str(record.get(column, '')).strip()
            if column == headers[0]:
                cells[column] = {'value': year, 'source_id': source['id'],
                                 'quote': quote}
                continue
            if not raw:
                continue
            try:
                value = float(raw.replace(',', ''))
            except ValueError:
                continue
            cells[column] = {'value': value, 'source_id': source['id'],
                             'quote': quote}
        for column in headers:
            if column not in cells:
                cells[column] = {
                    'value': None, 'status': 'unverified',
                    'reason': 'OCR 表格该单元格为空或未识别到数值',
                }
        if cells:
            rows.append({'label': year, 'cells': cells})
    if not rows:
        raise ValueError('OCR 表格没有可用的年份数据行')
    fields = {}
    for column in headers:
        if column == headers[0]:
            fields[column] = {'type': 'text'}
        else:
            unit = '万吨' if '（万吨）' in column or '万吨' in column else ''
            fields[column] = ({'type': 'number', 'unit': unit}
                              if unit else {'type': 'text'})
    return [{'name': '统计年鉴OCR', 'mode': 'comparison',
             'columns': headers, 'fields': fields, 'rows': rows}]
