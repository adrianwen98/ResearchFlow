"""Verified navigation aliases, never guessed from similar paths or domains."""
from urllib.parse import urlsplit,urlunsplit
import re
from .persistence import read_json,read_trace
from .security import validate_public_url
from .harness import signature


def url_key(url):
    parsed=urlsplit(url)
    return urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path.rstrip('/') or '/',parsed.query,''))


def legacy_navigation_targets(folder,requested_url,allowed_domains=()):
    """Recover same-origin aliases from successful host-recorded old MCP calls.

    Require the actual MCP Page header, its preceding navigate request, a
    matching successful journal entry, and subsequently an exact saved
    observation in Workspace. Mere search links or URL similarity never count.
    Does not mutate original evidence, logs, budgets or checkpoints.
    """
    path=folder/'trace.jsonl'
    journal_path=folder/'tool_journal.json'
    if path.is_symlink() or journal_path.is_symlink() or not path.is_file() or path.stat().st_size>16*1024*1024:return set()
    journal=read_json(journal_path,{})
    targets=set();current=None
    for event in read_trace(path):
        if event['event']=='tool_start':current=event
        if event['event']!='mcp_response' or event.get('tool')!='browser_navigate' or not current or current.get('detail')!='browser_navigate':continue
        args=current.get('arguments',{})
        row=journal.get(current.get('call_id'),{})
        if not isinstance(args,dict) or not isinstance(args.get('url'),str):continue
        if url_key(args['url'])!=url_key(requested_url):continue
        if row.get('name')!='browser_navigate' or row.get('error_code') or not row.get('output') or row['output'].startswith('ERROR:'):continue
        if row.get('args_signature')!=signature('browser_navigate',args):continue
        # Parse host metadata only, outside the YAML snapshot / page body.
        header=str(event.get('detail','')).split('### Snapshot',1)[0].split('```yaml',1)[0]
        match=re.search(r'(?m)^### Page\r?\n- Page URL: (\S+)',header)
        if not match:continue
        try:
            original=validate_public_url(args['url'],allowed_domains)
            final=validate_public_url(match.group(1),allowed_domains)
        except ValueError:continue
        a,b=urlsplit(original),urlsplit(final)
        if (a.scheme.lower(),a.netloc.lower())!=(b.scheme.lower(),b.netloc.lower()):continue
        targets.add(url_key(final))
    return targets
