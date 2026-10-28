@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if not exist "%~dp0work\tmp" mkdir "%~dp0work\tmp"
set "TEMP=%~dp0work\tmp"
set "TMP=%~dp0work\tmp"
set "npm_config_cache=%~dp0work\npm-cache"
if not defined WEBTASK_PYTHON set "WEBTASK_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%WEBTASK_PYTHON%" (
  echo [ERROR] Run setup_windows.bat first.
  pause
  exit /b 1
)
if exist "%~dp0work\mcp\node_modules\@playwright\mcp\cli.js" set "PLAYWRIGHT_MCP_CLI=%~dp0work\mcp\node_modules\@playwright\mcp\cli.js"
"%WEBTASK_PYTHON%" -m scripts.restore_demo_tasks
if errorlevel 1 (
  echo [ERROR] Demo restore failed. Existing user tasks were not overwritten.
  pause
  exit /b 1
)
if /i "%~1"=="api" goto API
"%WEBTASK_PYTHON%" -u debug_launcher.py
goto END
:API
"%WEBTASK_PYTHON%" -m uvicorn web_agent.api:app --host 127.0.0.1 --port 8000
:END
pause
