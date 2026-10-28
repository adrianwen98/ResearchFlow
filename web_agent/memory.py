"""Local cross-task memory. Only human-origin preferences/accepted summaries persist."""
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from .persistence import redact


def terms(text):
    words=set(re.findall(r'[a-z0-9_]{2,}',text.lower()))
    for part in re.findall(r'[\u4e00-\u9fff]+',text):
        words.update(part[i:i+2] for i in range(len(part)-1))
    return words


class MemoryStore:
    def __init__(self,path:Path):self.path=path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        connection=sqlite3.connect(self.path,timeout=10)
        connection.row_factory=sqlite3.Row
        connection.execute('CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, task_id TEXT NOT NULL, created REAL NOT NULL, metadata TEXT NOT NULL)')
        try:
            with connection:yield connection
        finally:connection.close()

    def save(self,content,*,kind='preference',task_id='',metadata=None):
        content=content.strip()
        if kind not in {'preference','accepted_task'} or not 2<=len(content)<=1500:
            raise ValueError('记忆类型无效或内容长度不在2–1500字符内')
        if redact(content)!=content:
            raise ValueError('记忆不能包含密钥、密码或令牌')
        with self.connect() as db:
            existing=db.execute('SELECT id FROM memories WHERE kind=? AND content=? AND task_id=?',(kind,content,task_id)).fetchone()
            if existing:return existing['id']
            key=uuid.uuid4().hex[:16]
            db.execute('INSERT INTO memories VALUES (?,?,?,?,?,?)',(key,kind,content,task_id,time.time(),json.dumps(metadata or {},ensure_ascii=False)))
        return key

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,kind,content,task_id,created FROM memories ORDER BY created DESC LIMIT 200')]

    def search(self,query,limit=5):
        query_terms=terms(query)
        rows=self.list()
        ranked=sorted(rows,key=lambda row:(len(query_terms & terms(row['content'])),row['created']),reverse=True)
        prefs=[row for row in ranked if row['kind']=='preference']
        matches=[row for row in ranked if row['kind']=='accepted_task' and query_terms & terms(row['content'])]
        selected=prefs[:2]+matches[:3]+prefs[2:]
        return [{key:row[key] for key in ('id','kind','content','task_id')} for row in selected[:min(max(limit,1),5)]]

    def delete(self,key):
        with self.connect() as db:return db.execute('DELETE FROM memories WHERE id=?',(key,)).rowcount>0

    def invalidate_task(self,task_id):
        with self.connect() as db:db.execute("DELETE FROM memories WHERE kind='accepted_task' AND task_id=?",(task_id,))


def memory_for(workspace):return MemoryStore(workspace.path.parent/'memory.sqlite')
