"""Windows diagnostic launcher: keep errors visible and save a complete run log."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from web_agent.config import Settings
from web_agent.persistence import redact

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOGFILE = LOG_DIR / "last_run.log"

def main() -> int:
    print(f"Project: {ROOT}", flush=True)
    print(f"Python: {sys.version.split()[0]}", flush=True)
    try:
        print(f"Gradio: {metadata.version('gradio')}", flush=True)
    except metadata.PackageNotFoundError:
        print("[WARNING] Gradio is not installed in this environment.", flush=True)
    print(f"Node npx: {shutil.which('npx.cmd') or shutil.which('npx') or 'NOT FOUND'}", flush=True)
    print("Gradio page should be available at http://127.0.0.1:7860 (unless the port is occupied).", flush=True)
    print(f"Saving stdout and stderr to: {LOGFILE}", flush=True)
    print("-" * 65, flush=True)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.Popen(
            [sys.executable, "-X", "faulthandler", "-u", "app.py"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        print(f"Python process could not start: {exc}", flush=True)
        return 1

    code = 1
    try:
        with LOGFILE.open("w", encoding="utf-8") as log:
            log.write(f"Run started: {datetime.now().isoformat(timespec='seconds')}\n")
            log.flush()
            assert proc.stdout is not None
            for line in proc.stdout:
                line = redact(line)
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            code = proc.wait()
            log.write(f"\nEXIT CODE: {code}\n")
    except KeyboardInterrupt:
        print("\n[INFO] Stopping the service...", flush=True)
        proc.terminate()
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()

    print("\n" + "-" * 65, flush=True)
    print(f"Service exited (code {code}). Diagnostic log: {LOGFILE}", flush=True)
    return code

if __name__ == "__main__":
    raise SystemExit(main())
