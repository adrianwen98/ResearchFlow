"""Bounded closure eligibility for already exported and actually verified facts.

This only grants the model a chance to record remaining research requirements.
It NEVER sets requirement statuses or invents report quotes / source IDs.
"""
from __future__ import annotations
import hashlib
from .persistence import read_json

CLOSURE_MAX_TURNS = 4


def closure_should_continue(state, requirements):
    """Honor the bounded registration window before declaring a partial result."""
    return bool(
        state.get('requirement_closure')
        and int(state.get('requirement_closure_turns', 0) or 0) < CLOSURE_MAX_TURNS
        and requirements.get('missing_requirements')
    )


def research_closure_candidates(workspace):
    rows=read_json(workspace.path/'requirements.json',[])
    remaining=[r for r in rows if r.get('kind')=='research' and
               r.get('result',{}).get('status')!='satisfied' and r.get('id')]
    if not remaining:return []
    check=workspace.verify_delivery()
    if not check.get('artifacts_valid'):return []
    path=workspace.path/'task_result.xlsx'
    meta=read_json(workspace.path/'business_artifact.json',{})
    if path.is_symlink() or not path.is_file() or meta.get('sha256')!=hashlib.sha256(path.read_bytes()).hexdigest():
        return []
    cells=meta.get('provenance',[])
    if not isinstance(cells,list) or not cells:return []
    if any(p.get('required',True) and p.get('status')!='verified' for p in cells if isinstance(p,dict)):
        return []
    # These are *only* candidate IDs to propose to the model for an explicit
    # report+source+fact-ref check, never a host-generated success decision.
    return [{'id':r['id'],'description':r.get('description','')} for r in remaining]
