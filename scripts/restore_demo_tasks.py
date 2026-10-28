"""Restore bundled evidence as local demos without overwriting user tasks or calling APIs."""
import asyncio
import json
import shutil
import sqlite3
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from web_agent.config import ROOT,Settings
from web_agent.persistence import read_json,atomic_json
from web_agent.task_history import deleted


async def restore(settings=None,root=ROOT):
    settings=settings or Settings.load()
    settings.output_dir.mkdir(parents=True,exist_ok=True)
    candidates=[];restored=[]
    for task_id in ['live_A','live_B','live_C','completion_research','completion_xlsx','interaction_end_to_end','penguins_public_demo']:
        if deleted(settings.output_dir,task_id):continue
        source=root/'demo_bundle'/task_id
        if not source.is_dir():source=root/'evidence'/task_id
        target=settings.output_dir/task_id
        manifest=read_json(source/'task.json',{})
        if not manifest:continue
        if not target.exists():
            shutil.copytree(source,target);restored.append(task_id)
            result=read_json(target/'result.json',{})
            if result:
                result['files']=[str(target/name) for name in ['research_report.md','research_sources.xlsx','task_result.xlsx'] if (target/name).is_file()]
                atomic_json(target/'result.json',result)
        # An existing user task with the same ID is never overwritten or given another checkpoint.
        if read_json(target/'task.json',{}).get('task')==manifest.get('task') and not (target/'.running').exists():candidates.append(task_id)
    if not candidates:return {'restored':restored,'checkpoint_threads_imported':[],'model_requests':0}
    settings.checkpoint_path.parent.mkdir(parents=True,exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:await saver.setup()
    imported=[]
    with sqlite3.connect(settings.checkpoint_path) as destination:
        for filename in ['live_checkpoints.sqlite','completion_checkpoints.sqlite','interaction_checkpoints.sqlite']:
            snapshot=root/'evidence'/filename
            if not snapshot.exists():continue
            with sqlite3.connect('file:'+snapshot.as_posix()+'?mode=ro',uri=True) as source:
                available={row[0] for row in source.execute('SELECT DISTINCT thread_id FROM checkpoints')}
                for task_id in candidates:
                    if task_id not in available or destination.execute('SELECT 1 FROM checkpoints WHERE thread_id=? LIMIT 1',(task_id,)).fetchone():continue
                    for table in ['checkpoints','writes']:
                        columns=[r[1] for r in destination.execute('PRAGMA table_info('+table+')')]
                        assert columns==[r[1] for r in source.execute('PRAGMA table_info('+table+')')],'Checkpoint schema mismatch'
                        rows=source.execute('SELECT * FROM '+table+' WHERE thread_id=?',(task_id,)).fetchall()
                        destination.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in columns)+')',rows)
                    imported.append(task_id)
    return {'restored':restored,'checkpoint_threads_imported':imported,'model_requests':0}


if __name__=='__main__':print(json.dumps(asyncio.run(restore()),ensure_ascii=False))
