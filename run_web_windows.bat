@echo off
setlocal
cd /d "%~dp0"
if not exist "work\tmp" mkdir "work\tmp"
set "TEMP=%CD%\work\tmp"
set "TMP=%TEMP%"
set "PYTHONIOENCODING=utf-8"
set "npm_config_cache=%CD%\work\npm-cache"
if exist "D:\dev\sdk\nodejs\node.exe" set "PATH=D:\dev\sdk\nodejs;%PATH%"
if not exist ".venv\Scripts\python.exe" goto install
if not exist "frontend\dist\index.html" goto install
goto serve
:install
call setup_web_windows.bat
if errorlevel 1 goto failed
:serve
.venv\Scripts\python.exe -m scripts.serve_web
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Check the message above. No budget was reset.
pause
exit /b 1
