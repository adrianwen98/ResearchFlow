"""受限的结构化数据分析：不允许 Agent 直接执行任意 Python 脚本。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd


ALLOWED_METHODS = {"count", "sum", "mean", "max", "min"}


def json_cell(value):
    """JSON is data, not an Excel cell. Preserve numeric types and negative signs."""
    if pd.isna(value):return None
    if isinstance(value,(str,int,float,bool)):return value
    if hasattr(value,'item'):return value.item()
    return str(value)


def safe_table_path(workspace_dir: Path, filename: str) -> Path:
    if Path(filename).name != filename or not filename.startswith("public_"):
        raise ValueError("只允许读取任务目录里 public_ 开头的下载文件")
    if not filename.lower().endswith((".csv", ".xlsx")):
        raise ValueError("只支持 CSV 或 XLSX")
    filepath = workspace_dir / filename
    if filepath.is_symlink() or filepath.resolve().parent != workspace_dir.resolve():
        raise ValueError("禁止读取工作区外文件或符号链接")
    if not filepath.is_file():
        raise FileNotFoundError("文件不存在，请先调用 download_public_file")
    if filepath.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("文件过大")
    return filepath


def read_table(workspace_dir: Path, filename: str) -> tuple[pd.DataFrame, str]:
    filepath = safe_table_path(workspace_dir, filename)
    if filepath.suffix.lower() == ".xlsx":
        with zipfile.ZipFile(filepath) as archive:
            if sum(member.file_size for member in archive.infolist()) > 64*1024*1024 or len(archive.infolist()) > 2000:
                raise ValueError('XLSX 解压体积/文件数量超过限制')
        frame = pd.read_excel(filepath, engine="openpyxl", nrows=5000)
    else:
        try:
            frame = pd.read_csv(filepath, nrows=5000, encoding="utf-8-sig", on_bad_lines="error")
        except (UnicodeError, pd.errors.ParserError):
            frame = pd.read_csv(filepath, nrows=5000, encoding="gb18030", on_bad_lines="error")
    frame.columns = [str(c) for c in frame.columns]
    return frame, "仅分析前 5000 行样本，不代表完整文件" if len(frame) == 5000 else ""


def table_evidence_text(workspace_dir: Path, filename: str) -> str:
    """Canonical text for exact-row evidence; analysis is capped at 5000 rows."""
    frame, _ = read_table(workspace_dir, filename)
    return frame.to_csv(index=False)


def summarize_table(workspace_dir: Path, filename: str) -> str:
    df, notice = read_table(workspace_dir, filename)
    info = {
        "sampled_rows": len(df), "columns": list(df.columns),
        "null_counts": {str(k): int(v) for k, v in df.isna().sum().items()},
        "preview": [{str(k): json_cell(v) for k, v in r.items()} for r in df.head(5).to_dict("records")],
        "analysis_scope":"first worksheet" if filename.lower().endswith('.xlsx') else 'CSV',
        "row_limit":5000,"rows_complete":not bool(notice),
        "note": notice,
    }
    return json.dumps(info, ensure_ascii=False, default=str)[:15000]


def aggregate_table(
    workspace_dir: Path, filename: str, group_by: str,
    numeric_column: str = "", method: str = "count",
) -> str:
    if method not in ALLOWED_METHODS:
        raise ValueError("method 只能是 count、sum、mean、max、min")
    df, notice = read_table(workspace_dir, filename)
    if group_by not in df.columns:
        raise ValueError(f"分组字段不存在：{group_by}。实际字段：{list(df.columns)}")
    if method != "count":
        if numeric_column not in df.columns:
            raise ValueError("sum/mean/max/min 必须指定真实存在的 numeric_column")
        df[numeric_column] = pd.to_numeric(df[numeric_column], errors="coerce")
        grouped = df.groupby(group_by, dropna=False)[numeric_column].agg(method).sort_values(ascending=False)
    else:
        grouped = df.groupby(group_by, dropna=False).size().sort_values(ascending=False)
    result = {
        "group_by": group_by, "method": method, "numeric_column": numeric_column,
        "sampled_rows": len(df), "total_groups": len(grouped),
        "top_20": [{"group": json_cell(k), "value": None if pd.isna(v) else float(v)}
                   for k, v in grouped.head(20).items()],
        "note": notice,
    }
    return json.dumps(result, ensure_ascii=False, default=str)[:15000]
