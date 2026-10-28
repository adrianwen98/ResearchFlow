"""Local data backup and safe storage maintenance for the single-user workbench."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, Settings


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _task_ids(output_dir: Path) -> list[str]:
    if not output_dir.is_dir():
        return []
    return sorted(
        folder.name
        for folder in output_dir.iterdir()
        if folder.is_dir()
        and not folder.is_symlink()
        and ((folder / "task.json").is_file() or (folder / "queued.json").is_file())
    )


def storage_summary(settings: Settings) -> dict:
    outputs = settings.output_dir
    checkpoint = settings.checkpoint_path
    work = ROOT / "work"
    logs = ROOT / "logs"
    checkpoint_bytes = sum(
        p.stat().st_size
        for p in (checkpoint, Path(str(checkpoint) + "-wal"), Path(str(checkpoint) + "-shm"))
        if p.is_file()
    )
    return {
        "tasks": len(_task_ids(outputs)),
        "outputs_bytes": _tree_size(outputs),
        "checkpoint_bytes": checkpoint_bytes,
        "work_bytes": _tree_size(work),
        "logs_bytes": _tree_size(logs),
    }


def _snapshot_database(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return True


def create_backup(settings: Settings) -> Path:
    """Archive task outputs and consistent SQLite snapshots. Never include secrets or evidence/."""
    output_dir = settings.output_dir
    backup_dir = ROOT / "work" / "tmp"
    backup_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="webtask-backup-", suffix=".zip", dir=backup_dir)
    os.close(fd)
    Path(name).unlink(missing_ok=True)
    destination = Path(name)
    try:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if output_dir.is_dir():
                for folder in sorted(output_dir.iterdir()):
                    if not folder.is_dir() or folder.is_symlink():
                        continue
                    if not ((folder / "task.json").is_file() or (folder / "queued.json").is_file()):
                        continue
                    for item in folder.rglob("*"):
                        if item.is_file() and not item.is_symlink():
                            archive.write(item, Path("outputs") / folder.name / item.relative_to(folder))
            with tempfile.TemporaryDirectory(dir=backup_dir) as temp:
                temp_path = Path(temp)
                if _snapshot_database(settings.checkpoint_path, temp_path / "checkpoints.sqlite"):
                    archive.write(temp_path / "checkpoints.sqlite", "checkpoints.sqlite")
                memory = output_dir / "memory.sqlite"
                if _snapshot_database(memory, temp_path / "memory.sqlite"):
                    archive.write(temp_path / "memory.sqlite", "memory.sqlite")
            manifest = {
                "format": "webtask-backup-v1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "tasks": _task_ids(output_dir),
                "included": ["outputs/", "checkpoints.sqlite", "memory.sqlite"],
                "excluded": [".env", "evidence/", "work/", "logs/"],
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def cleanup_temp(settings: Settings) -> dict:
    """Remove browser temp files while keeping the directory the launcher expects."""
    tmp = ROOT / "work" / "tmp"
    freed = 0
    if tmp.is_dir():
        for item in tmp.iterdir():
            if item.name.startswith("webtask-backup-"):
                continue  # an active download may still be streaming this archive
            try:
                if item.is_dir() and not item.is_symlink():
                    freed += _tree_size(item)
                    shutil.rmtree(item)
                else:
                    freed += item.stat().st_size
                    item.unlink()
            except OSError:
                continue
    return {"freed_bytes": freed, **storage_summary(settings)}


def compact_checkpoints(settings: Settings) -> dict:
    """Reclaim free pages after completed tasks. Callers must guarantee no task is active."""
    path = settings.checkpoint_path
    if not path.is_file():
        return {"freed_bytes": 0, **storage_summary(settings)}
    before = _tree_size(path.parent) if path.parent != ROOT else sum(
        p.stat().st_size
        for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
        if p.is_file()
    )
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
    finally:
        connection.close()
    after = sum(
        p.stat().st_size
        for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
        if p.is_file()
    )
    return {"freed_bytes": max(0, before - after), **storage_summary(settings)}
