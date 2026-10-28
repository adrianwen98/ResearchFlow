"""Atomic task metadata; credentials never belong in these files."""
import json
import os
import re
import tempfile
import time
from threading import RLock
from pathlib import Path

_FILE_LOCKS = tuple(RLock() for _ in range(64))


def _file_lock(path):
    return _FILE_LOCKS[hash(os.path.normcase(os.path.abspath(path))) % len(_FILE_LOCKS)]


class PersistenceError(OSError):
    """Local task storage failed; never a website/security-policy rejection."""


def atomic_json(path: Path, value):
    # UI worker threads must not keep a Windows read handle open while the
    # task thread replaces the same file. Stripes keep this lock table bounded.
    with _file_lock(path):
        return _atomic_json(path, value)


def _atomic_json(path: Path, value):
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    temporary = None
    try:
        # A shared .tmp name allows concurrent writers to steal each other's data.
        # Close our handle before rename; Windows readers/scanners can briefly lock
        # the destination. Retry only that local replace, never a model/tool call.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.'+path.name+'.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.02 * (2 ** attempt))
    except OSError as exc:
        raise PersistenceError(f'无法保存任务文件 {path.name}；请检查文件占用、目录权限或磁盘空间。') from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Preserve the original error; never replace old valid data.


def read_json(path: Path, default=None):
    with _file_lock(path):
        return _read_json(path, default)


def _read_json(path: Path, default=None):
    for attempt in range(6):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default
        except PermissionError as exc:
            if attempt == 5:
                # Never treat unreadable usage as missing and reset a budget.
                raise PersistenceError(f'无法读取任务文件 {path.name}；请检查文件占用及目录权限。') from exc
            time.sleep(.02 * (2 ** attempt))


def read_trace(path: Path):
    """A torn final JSONL write must not prevent checkpoint recovery."""
    rows = []
    if path.exists():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                item = json.loads(line)
                if isinstance(item,dict) and 'event' in item: rows.append(item)
            except json.JSONDecodeError:
                continue
    return rows


def redact(value):
    text = str(value)
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        secret = os.environ.get(name, "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    text = re.sub(r"(?i)(authorization|api[_-]?key|token|password)([\s\"':=]+)[^\s\",}&]+", r"\1\2[REDACTED]", text)
    return text


def sanitize(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if re.search(r'(?i)api.?key|authorization|password|secret',str(k)) else sanitize(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [sanitize(v) for v in value]
    return redact(value) if isinstance(value,str) else value
