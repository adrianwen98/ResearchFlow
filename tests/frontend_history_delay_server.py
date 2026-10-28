"""Manual browser regression harness: real historical API responses, injected latency.

Serves the built React app; only forwards GETs. No task creation or model calls.
Use POST /__test__/scenario with {"delays": {"/tasks/id/workbook?filename=task_result.xlsx": 3000}}
to delay the next matching response, without altering its status or body.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

PROJECT = Path(__file__).resolve().parents[1]
app = FastAPI()
delays: dict[str, int] = {}
requests: list[dict] = []
record: Path | None = None
upstream = "http://127.0.0.1:8010"


def log(row: dict):
    requests.append(row)
    if record:
        with record.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


@app.post("/__test__/scenario")
async def scenario(body: dict):
    delays.clear()
    for path, delay in body.get("delays", {}).items():
        if not path.startswith("/tasks/") or not isinstance(delay, int) or not 0 <= delay <= 5000:
            raise HTTPException(400, "Only bounded task-read delays are supported")
        delays[path] = delay
    return {"delays": delays}


@app.get("/__test__/requests")
async def observed():
    return {"requests": requests}


@app.get("/{path:path}")
async def proxy(path: str, request: Request):
    if path == "settings" or path == "health" or path == "tasks" or path.startswith("tasks/"):
        suffix = "/" + path + ("?" + str(request.url.query) if request.url.query else "")
        delay = delays.pop(suffix, 0)
        started = time.monotonic()
        serial = len(requests) + 1
        log({"id": serial, "phase": "started", "path": suffix, "delay_ms": delay})
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(upstream + suffix)
        if delay:
            await asyncio.sleep(delay / 1000)
        log({"id": serial, "phase": "returned", "path": suffix,
             "status": response.status_code, "elapsed_ms": round((time.monotonic() - started) * 1000),
             "sha256": hashlib.sha256(response.content).hexdigest()})
        return Response(response.content, status_code=response.status_code,
                        headers={"content-type": response.headers.get("content-type", "application/json"),
                                 "cache-control": "no-store"})
    base = (PROJECT / "frontend/dist").resolve()
    target = (base / (path or "index.html")).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path)
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    record = args.record
    if record:
        record.parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)
