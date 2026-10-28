import sqlite3
import zipfile
from dataclasses import replace

from web_agent import backup
from web_agent.config import Settings


def _settings(tmp_path):
    return replace(
        Settings.load(),
        output_dir=tmp_path / "outputs",
        checkpoint_path=tmp_path / "checkpoints.sqlite",
    )


def test_storage_and_backup_include_tasks_and_sqlite_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    settings = _settings(tmp_path)
    task = settings.output_dir / "task-1"
    task.mkdir(parents=True)
    (task / "task.json").write_text('{"task_id":"task-1"}', encoding="utf-8")
    (task / "research_report.md").write_text("# Report", encoding="utf-8")
    with sqlite3.connect(settings.checkpoint_path) as conn:
        conn.execute("create table checkpoint (id integer primary key, value text)")
        conn.execute("insert into checkpoint(value) values ('saved')")
    with sqlite3.connect(settings.output_dir / "memory.sqlite") as conn:
        conn.execute("create table memory (id integer primary key)")

    summary = backup.storage_summary(settings)
    assert summary["tasks"] == 1
    assert summary["outputs_bytes"] > 0
    assert summary["checkpoint_bytes"] > 0

    archive = backup.create_backup(settings)
    with zipfile.ZipFile(archive) as zipped:
        names = set(zipped.namelist())
        assert "manifest.json" in names
        assert "checkpoints.sqlite" in names
        assert "memory.sqlite" in names
        assert "outputs/task-1/task.json" in names
        assert "outputs/task-1/research_report.md" in names
        assert not any(name.startswith(".env") or name.startswith("evidence/") for name in names)
        import json
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["tasks"] == ["task-1"]


def test_cleanup_removes_temp_files_but_keeps_current_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    settings = _settings(tmp_path)
    temp = tmp_path / "work" / "tmp"
    temp.mkdir(parents=True)
    (temp / "browser.tmp").write_text("x", encoding="utf-8")
    current = temp / "webtask-backup-current.zip"
    current.write_text("zip", encoding="utf-8")

    result = backup.cleanup_temp(settings)
    assert result["freed_bytes"] > 0
    assert not (temp / "browser.tmp").exists()
    assert current.exists()
