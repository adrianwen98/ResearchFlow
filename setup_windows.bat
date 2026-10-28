@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if not exist "%~dp0work\tmp" mkdir "%~dp0work\tmp"
set "TEMP=%~dp0work\tmp"
set "TMP=%~dp0work\tmp"
set "PIP_CACHE_DIR=%~dp0work\pip-cache"
set "npm_config_cache=%~dp0work\npm-cache"
echo [1/5] Locate Python 3.12...
if exist "%~dp0.runtime\python312\python.exe" set "PYTHON312_EXE=%~dp0.runtime\python312\python.exe"
if defined PYTHON312_EXE if exist "%PYTHON312_EXE%" goto CHECK_PY
py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>nul
if not errorlevel 1 (
  py -3.12 -m venv .venv
  goto INSTALL
)
for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON312_EXE set "PYTHON312_EXE=%%P"
if defined PYTHON312_EXE (
  "%PYTHON312_EXE%" -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>nul
  if not errorlevel 1 goto CHECK_PY
)
goto NO_PY
:CHECK_PY
"%PYTHON312_EXE%" -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>nul
if errorlevel 1 goto NO_PY
"%PYTHON312_EXE%" -m venv .venv
:INSTALL
if not exist ".venv\Scripts\python.exe" goto FAIL
echo [2/5] Install pinned Windows Python dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements-lock-win-py312.txt
if errorlevel 1 goto FAIL
echo [3/5] Check Node.js 20+ and install pinned Playwright MCP...
node -e "if (Number(process.versions.node.split('.')[0]) < 20) process.exit(1)"
if errorlevel 1 goto FAIL
if not exist work\mcp mkdir work\mcp
copy /y mcp\package.json work\mcp\package.json >nul
copy /y mcp\package-lock.json work\mcp\package-lock.json >nul
call npm.cmd ci --prefix work\mcp --ignore-scripts
if errorlevel 1 goto FAIL
echo [4/5] Prepare private configuration...
if not exist .env copy .env.example .env >nul
echo [5/5] Run offline tests...
".venv\Scripts\python.exe" -m pytest -q --basetemp work\setup-tests
if errorlevel 1 goto FAIL
echo Setup complete. Set DEEPSEEK_API_KEY in .env, then run run_windows.bat.
if /i not "%~1"=="--no-pause" pause
exit /b 0
:NO_PY
echo [ERROR] Python 3.12 required. Install it, or set PYTHON312_EXE to its full path.
echo Python 3.14 environments are intentionally not reused.
goto FAIL
:FAIL
echo [ERROR] Setup failed. Review the error above. No API calls were made by setup.
if /i not "%~1"=="--no-pause" pause
exit /b 1
