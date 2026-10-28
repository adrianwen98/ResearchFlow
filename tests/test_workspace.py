import json
from pathlib import Path

import pandas as pd
import pytest

from web_agent.workspace import Workspace


def test_workflow_persists_sources_and_exports(tmp_path):
    ws = Workspace(tmp_path, "case_001")
    saved = ws.record("官方页面", "https://www.python.org/", "Python 是一种语言", "Python is a programming language", "产品")
    assert saved["id"] == "1"
    reloaded = Workspace(tmp_path, "case_001")
    assert len(reloaded.sources()) == 1
    assert reloaded.domain_summary()["domain_count"] == 1

    excel = ws.export_excel()
    assert excel.is_file()
    df = pd.read_excel(excel)
    assert df.loc[0, "title"] == "官方页面"

    report = ws.export_report("# 调查结果\n\n这是一份示例报告。")
    text = report.read_text(encoding="utf-8")
    assert "https://www.python.org/" in text
    assert "Python is a programming language" in text
    assert len(ws.list_artifacts()) == 2


def test_no_export_without_sources(tmp_path):
    ws = Workspace(tmp_path, "empty")
    with pytest.raises(ValueError):
        ws.export_excel()


def test_excel_hardened(tmp_path):
    ws = Workspace(tmp_path, "formula")
    ws.record("=HYPERLINK(\"evil\")", "https://example.org", "+10", "@cmd")
    frame = pd.read_excel(ws.export_excel())
    assert str(frame.loc[0, "title"]).startswith("'")
    assert str(frame.loc[0, "fact"]).startswith("'")


def test_source_excerpt_finds_layout_space_keyword(tmp_path):
    nbsp = chr(0xA0)
    ws = Workspace(tmp_path, "excerpt-layout")
    url = "https://example.org/spec"
    text = "Intro\n\nUSB" + nbsp + "3 (最高可达 10Gb/s)13"
    oid = ws.observe(url, text)
    row = ws.record("规格", url, "fact", "USB" + nbsp + "3 (最高可达 10Gb/s)",
                    require_observation=True)
    found = ws.source_excerpt(row["id"], "USB 3 (最高可达 10Gb/s)")
    assert found["keyword_found"] is True
    assert "USB" + nbsp + "3" in found["text"]
    assert found["offset"] == 0
    assert ws.source_excerpt(row["id"], "USB 4 (最高可达 10Gb/s)")["keyword_found"] is False


def test_record_binds_specific_observation_id(tmp_path):
    ws = Workspace(tmp_path, "bind-observation")
    url = "https://example.org/spec"
    first = ws.observe(url, "Intro A\n177 克 (6.24 盎司)")
    second = ws.observe(url, "Intro B\n177 克 (6.24 盎司)")
    assert first != second
    row = ws.record("规格", url, "fact", "177 克 (6.24 盎司)",
                    require_observation=True, observation_id=second)
    assert row["observation_id"] == second
    with pytest.raises(ValueError):
        ws.record("规格", url, "fact", "204 克 (7.20 盎司)",
                  require_observation=True, observation_id=second)
