"""Restore a WebTask backup ZIP. Stop the workbench first; a safety backup is taken automatically."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web_agent import backup  # noqa: E402
from web_agent.config import Settings  # noqa: E402


def fail(message: str) -> None:
    print(f"拒绝恢复：{message}", file=sys.stderr)
    raise SystemExit(1)


def validate(archive: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, json.JSONDecodeError) as exc:
        fail(f"不是 WebTask 备份（缺少 manifest.json）：{exc}")
    if manifest.get("format") != "webtask-backup-v1":
        fail("备份格式版本不受支持")
    for info in archive.infolist():
        name = info.filename
        if name == "manifest.json" or name.endswith("/"):
            continue
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            fail(f"备份包含不安全路径：{name}")
        if name not in {"checkpoints.sqlite", "memory.sqlite"} and not name.startswith("outputs/"):
            fail(f"备份包含意外文件：{name}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            fail(f"备份包含符号链接：{name}")
    return manifest


def server_is_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1):
            return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="恢复 WebTask 备份")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--yes", action="store_true", help="确认执行；恢复前会自动再备份当前数据")
    args = parser.parse_args()
    if not args.archive.is_file():
        fail(f"找不到文件：{args.archive}")
    if not args.yes:
        print("这是试运行。确认工作台已关闭后，重新运行并加 --yes。")
        return
    if server_is_running():
        fail("工作台仍在运行，请先关闭工作台（不是只关浏览器标签）")

    with zipfile.ZipFile(args.archive) as archive:
        manifest = validate(archive)
        settings = Settings.load()
        safety_dir = ROOT / "work" / "backups"
        safety_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safety = backup.create_backup(settings)
        shutil.copy2(safety, safety_dir / f"pre-restore-{stamp}.zip")
        safety.unlink(missing_ok=True)

        moved: list[tuple[Path, Path]] = []
        try:
            if settings.output_dir.exists():
                previous = settings.output_dir.with_name(f"outputs.pre-restore-{stamp}")
                shutil.move(settings.output_dir, previous)
                moved.append((settings.output_dir, previous))
            for suffix in ("", "-wal", "-shm"):
                current = Path(str(settings.checkpoint_path) + suffix)
                if current.exists():
                    previous = current.with_name(current.name + f".pre-restore-{stamp}")
                    shutil.move(current, previous)
                    moved.append((current, previous))
            settings.output_dir.mkdir(parents=True, exist_ok=True)
            for info in archive.infolist():
                if info.filename == "manifest.json" or info.filename.endswith("/"):
                    continue
                if info.filename == "memory.sqlite":
                    target = settings.output_dir / "memory.sqlite"
                elif info.filename == "checkpoints.sqlite":
                    target = settings.checkpoint_path
                else:
                    target = ROOT / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        except Exception:
            if settings.output_dir.exists():
                shutil.rmtree(settings.output_dir, ignore_errors=True)
            for original, previous in reversed(moved):
                if previous.exists():
                    shutil.move(previous, original)
            fail("恢复中断，已回滚到恢复前状态；安全备份仍在 work/backups")
        print(f"恢复完成：{len(manifest.get('tasks', []))} 个任务；恢复前数据已备份到 {safety_dir}")


if __name__ == "__main__":
    main()
