"""Exact setup-call reuse; never suppress new requirements or changed plans.

This cannot save the model request that issued a duplicate tool call. It avoids
redundant file writes while returning the same JSON result and preserves budgets.
"""
from __future__ import annotations
import json
from .persistence import read_json


def unchanged_setup_output(workspace, name, args):
    if not isinstance(args,dict):
        return None
    if name == 'set_requirements':
        requested=args.get('items')
        if not isinstance(requested,list) or not 1 <= len(requested) <= 16:
            return None
        existing=read_json(workspace.path/'requirements.json',[])
        if not isinstance(existing,list) or not existing:
            return None
        lookup={}
        for row in existing:
            if not isinstance(row,dict) or not isinstance(row.get('description'),str):
                return None
            lookup[row['description']]=row
        for item in requested:
            if not isinstance(item,dict):
                return None
            desc=item.get('description')
            if not isinstance(desc,str) or not 3 <= len(desc.strip()) <= 600:
                return None
            row=lookup.get(desc.strip())
            if row is None or row.get('kind') != item.get('kind','research') or row.get('artifact','') != item.get('artifact',''):
                return None
        # Same output shape as define_requirements: list of existing rows.
        return json.dumps(existing,ensure_ascii=False)
    if name == 'update_plan':
        steps=args.get('steps')
        if not isinstance(steps,list) or not all(isinstance(s,str) for s in steps):
            return None
        path=workspace.path/'plan.json'
        if not path.is_file() or path.is_symlink():
            return None
        current=read_json(path,None)
        new=[s[:400] for s in steps[:8]]
        if current != new:
            return None
        return json.dumps(current,ensure_ascii=False)
    return None
