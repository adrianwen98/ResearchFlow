@echo off
setlocal
cd /d "%~dp0"
if not exist "work\tmp" mkdir "work\tmp"
set "TEMP=%CD%\work\tmp"
set "TMP=%TEMP%"
set "npm_config_cache=%CD%\work\npm-cache"
set "npm_config_registry=https://registry.npmjs.org"
set "PIP_CACHE_DIR=%CD%\work\pip-cache"
set "PYTHONIOENCODING=utf-8"
where node >nul 2>nul
if errorlevel 1 if exist "D:\dev\sdk\nodejs\node.exe" set "PATH=D:\dev\sdk\nodejs;%PATH%"
where node >nul 2>nul
if errorlevel 1 (
 echo Install Node.js 22.12+ or 24 LTS from https://nodejs.org/ on your chosen drive.
 exit /b 1
)
node -e "let [a,b]=process.versions.node.split('.').map(Number);if(a<22||(a===22&&b<12)){console.error('Node 22.12+ required');process.exit(1)}"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
 call setup_windows.bat
 if errorlevel 1 exit /b 1
)
call npm --prefix frontend ci --ignore-scripts
if errorlevel 1 exit /b 1
call npm --prefix frontend run build
if errorlevel 1 exit /b 1
call npm --prefix frontend test
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip check
if errorlevel 1 exit /b 1
echo Web workbench installed. No model requests have been made.
exit /b 0
