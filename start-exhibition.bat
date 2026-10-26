@echo off
rem AP_DrawArt exhibition launcher entry.
rem Double-click to start the whole local system in the background; this window closes itself.
rem Extra arguments are passed to the launcher, e.g. start-exhibition.bat --no-displays
setlocal EnableExtensions

set "APP_ROOT=%~dp0"
if "%APP_ROOT:~-1%"=="\" set "APP_ROOT=%APP_ROOT:~0,-1%"

rem Python runtime: bundled package first, then AP_DRAWART_PYTHONW, then the development env.
set "PYW=%APP_ROOT%\runtime\python\pythonw.exe"
if exist "%PYW%" goto found_python
if defined AP_DRAWART_PYTHONW (
    set "PYW=%AP_DRAWART_PYTHONW%"
    if exist "%AP_DRAWART_PYTHONW%" goto found_python
)
set "PYW=C:\Users\Chrono\miniconda3\envs\agent-use-latest\pythonw.exe"
if exist "%PYW%" goto found_python

echo [AP_DrawArt] Python runtime not found.
echo Expected: "%APP_ROOT%\runtime\python\pythonw.exe"
pause
exit /b 1

:found_python
rem Delivery package keeps code under app\; development keeps it at the root.
set "CODE_ROOT=%APP_ROOT%"
if exist "%APP_ROOT%\app\launcher\__init__.py" set "CODE_ROOT=%APP_ROOT%\app"
set "PYTHONPATH=%CODE_ROOT%"
set "AP_DRAWART_ROOT=%APP_ROOT%"

start "" /D "%CODE_ROOT%" "%PYW%" -m launcher %*
exit /b 0
