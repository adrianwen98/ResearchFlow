"""Presentation metadata and explicitly scoped deletion; never edits agent goals.

Tombstones live outside task folders so retained checkpoints or a repeated
idempotency key cannot resurrect a deleted task with a fresh execution budget.
"""
import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from .persistence import atomic_json, read_json


class HistoryConflict(ValueError):
    pass


def _reparse(path):
    info=path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def meta_path(root, task_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,56}',task_id):raise ValueError('任务ID无效')
    root=Path(root).resolve()
    folder=root/'.task_meta'
    if folder.exists() and _reparse(folder):raise HistoryConflict('任务管理目录不能是链接')
    path=folder/(task_id+'.json')
    if path.exists() and _reparse(path):raise HistoryConflict('任务管理文件不能是链接')
    temporary=path.with_suffix(path.suffix+'.tmp')
    if temporary.is_symlink() or (temporary.exists() and _reparse(temporary)):raise HistoryConflict('任务管理临时文件不能是链接')
    return path


def metadata(root,task_id):
    return read_json(meta_path(root,task_id),{})


def deleted(root,task_id):
    return bool(metadata(root,task_id).get('deleted'))


def update_metadata(root,task_id,**changes):
    path=meta_path(root,task_id);path.parent.mkdir(parents=True,exist_ok=True)
    value={**metadata(root,task_id),**changes,'updated_at':datetime.now(timezone.utc).isoformat()}
    atomic_json(path,value)
    return value


def rename(root,task_id,title):
    title=' '.join(title.split())
    if not 1<=len(title)<=160:raise ValueError('任务名称需1–160字符')
    if deleted(root,task_id):raise HistoryConflict('任务已删除')
    return update_metadata(root,task_id,title=title)


def archive(root,task_id,archived):
    if deleted(root,task_id):raise HistoryConflict('任务已删除')
    return update_metadata(root,task_id,archived=bool(archived))


def inventory(folder):
    """Validate the entire tree before any destructive action, including junctions."""
    root=folder.parent.resolve();expected=root/folder.name
    if folder.resolve()!=expected or not folder.resolve().is_relative_to(root) or _reparse(folder):
        raise HistoryConflict('任务目录不能越界或指向链接')
    files=[];directories=[]
    def walk(path):
        for entry in os.scandir(path):
            p=Path(entry.path)
            if _reparse(p):raise HistoryConflict('任务内存在符号链接或重解析点，拒绝自动清理')
            if not p.resolve().is_relative_to(expected):raise HistoryConflict('任务清理路径越界')
            if entry.is_dir(follow_symlinks=False):
                walk(p);directories.append(p)
            elif entry.is_file(follow_symlinks=False):files.append(p)
            else:raise HistoryConflict('任务中存在不支持的文件类型')
    walk(folder)
    return files,directories


def is_artifact(relative):
    return relative.parts[0]=='browser' or relative.suffix.lower() in {'.xlsx','.csv','.pdf','.md'}


def checkpoint_counts(settings,task_id):
    path=settings.checkpoint_path
    if not path.is_file():return {'checkpoints':0,'writes':0}
    if _reparse(path):raise HistoryConflict('Checkpoint 数据库不能是链接')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {table:db.execute('SELECT COUNT(*) FROM '+table+' WHERE thread_id=?',(task_id,)).fetchone()[0]
                if table in tables else 0 for table in ('checkpoints','writes')}


def ensure_inactive(folder,jobs):
    job=jobs.get(folder.name)
    if job and not job.done():raise HistoryConflict('任务正在执行或排队，请先安全停止后删除')
    if (folder/'.running').exists():raise HistoryConflict('任务被运行锁占用；请确认其他进程已停止，不能强制删除')


def preview(settings,folder,jobs):
    files,_=inventory(folder)
    groups={'records':{'files':0,'bytes':0},'files':{'files':0,'bytes':0}}
    for path in files:
        if path.name=='.running':continue
        key='files' if is_artifact(path.relative_to(folder)) else 'records'
        groups[key]['files']+=1;groups[key]['bytes']+=path.stat().st_size
    job=jobs.get(folder.name);active=bool(job and not job.done());lock=(folder/'.running').exists()
    return {'task_id':folder.name,'groups':groups,'checkpoint':checkpoint_counts(settings,folder.name),
            'active':active,'locked':lock,'can_stop':active,'can_delete':not active and not lock,
            'cleanup_pending':metadata(folder.parent,folder.name).get('cleanup_pending',False)}


async def delete_task(settings,folder,jobs,confirmation,*,delete_records=False,delete_checkpoint=False,delete_files=False):
    if confirmation!=folder.name:raise ValueError('删除确认与任务ID不匹配')
    ensure_inactive(folder,jobs)
    options={'delete_records':delete_records,'delete_checkpoint':delete_checkpoint,'delete_files':delete_files}
    prior=metadata(folder.parent,folder.name)
    if prior.get('deleted') and not prior.get('cleanup_pending'):
        if prior.get('cleanup_options')!=options:raise HistoryConflict('任务已删除，不能扩大原删除范围')
        return {'task_id':folder.name,'deleted':True,'reused':True}
    if prior.get('cleanup_pending') and prior.get('cleanup_options')!=options:raise HistoryConflict('清理未完成，请保持原选项重试')
    files,directories=inventory(folder)
    # Validate shared database paths before committing the deletion tombstone.
    if delete_checkpoint:checkpoint_counts(settings,folder.name)
    memory=folder.parent/'memory.sqlite'
    if delete_records and memory.exists() and _reparse(memory):raise HistoryConflict('Memory 数据库不能是链接')
    lock=folder/'.running'
    try:lock.open('x').close()
    except FileExistsError as exc:raise HistoryConflict('任务已开始运行，请先停止') from exc
    removed=0
    try:
        update_metadata(folder.parent,folder.name,deleted=True,title='',archived=False,cleanup_pending=True,cleanup_options=options)
        if delete_checkpoint and settings.checkpoint_path.is_file():
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
                await saver.setup()
                await saver.adelete_thread(folder.name)
        if delete_records and memory.is_file():
            from .memory import MemoryStore
            MemoryStore(memory).invalidate_task(folder.name) # only this task's accepted summary
        for path in files:
            if path.name=='.running':continue
            artifact=is_artifact(path.relative_to(folder))
            if not ((artifact and delete_files) or (not artifact and delete_records)):continue
            if _reparse(path) or not path.resolve().is_relative_to(folder.resolve()):raise HistoryConflict('清理时检测到路径变化，已停止')
            path.unlink();removed+=1
        if delete_records or delete_files:
            for path in directories:
                if _reparse(path):raise HistoryConflict('清理目录发生变化，已停止')
                if not any(path.iterdir()):path.rmdir()
        update_metadata(folder.parent,folder.name,cleanup_pending=False,removed_files=removed)
        jobs.pop(folder.name,None)
        return {'task_id':folder.name,'deleted':True,'removed_files':removed,'cleanup_options':options}
    except Exception as exc:
        # Fail closed on partial deletion; preserve tombstone and original options
        # so an explicit identical retry may finish, never recreate the task.
        raise HistoryConflict('清理未完成，任务已从历史隐藏；请保持原选项重试。'+str(exc)[:200]) from exc
    finally:
        lock.unlink(missing_ok=True)
        if folder.exists() and not any(folder.iterdir()):folder.rmdir()
