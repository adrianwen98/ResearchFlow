"""Local-only React + FastAPI entry point. Opening the UI never runs a task."""
import argparse
import socket
import threading
import webbrowser
import json
from urllib.request import build_opener,ProxyHandler
from pathlib import Path
import uvicorn


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    if not (Path(__file__).resolve().parent.parent/'frontend/dist/index.html').is_file():raise SystemExit('Run setup_web_windows.bat first.')
    # Windows listeners may enable SO_REUSEADDR, so bind() alone is not an occupancy test.
    with socket.socket() as probe:
        probe.settimeout(1)
        occupied=probe.connect_ex(('127.0.0.1',8000))==0
    if occupied:
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:8000/health',timeout=2) as response:
                existing=json.load(response)
            if existing.get('app')=='webtask-agent' and existing.get('ui')=='react':
                print('ResearchFlow is already running: http://127.0.0.1:8000/')
                if not args.no_browser:webbrowser.open('http://127.0.0.1:8000/')
                return
        except Exception:pass
        raise SystemExit('Port 8000 is occupied by another service. No process was stopped.')
    if not args.no_browser:
        timer=threading.Timer(2,lambda:webbrowser.open('http://127.0.0.1:8000/'));timer.daemon=True;timer.start()
    uvicorn.run('web_agent.api:app',host='127.0.0.1',port=8000,workers=1)

if __name__=='__main__':main()
