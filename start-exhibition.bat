@echo off
rem AP_DrawArt exhibition launcher entry.
rem Double-click to start the whole local system. A console window opens with the admin URLs
rem and live status; closing that window ends the exhibition.
rem Extra arguments are passed to the launcher, e.g. start-exhibition.bat --no-displays
setlocal EnableExtensions DisableDelayedExpansion

set "APP_ROOT=%~dp0"
if "%APP_ROOT:~-1%"=="\" set "APP_ROOT=%APP_ROOT:~0,-1%"

rem Python runtime (console python.exe): installed venv, bundled package, explicit override, development env.
set "PY=%APP_ROOT%\runtime\python\Scripts\python.exe"
if exist "%PY%" goto found_python
set "PY=%APP_ROOT%\runtime\python\python.exe"
if exist "%PY%" goto found_python
if defined AP_DRAWART_PYTHONW (
    set "PY=%AP_DRAWART_PYTHONW:pythonw.exe=python.exe%"
    if exist "%AP_DRAWART_PYTHONW:pythonw.exe=python.exe%" goto found_python
)
set "PY=%USERPROFILE%\miniconda3\envs\agent-use-latest\python.exe"
if exist "%PY%" goto found_python

echo [AP_DrawArt] Python runtime not found.
echo Run "%APP_ROOT%\install-environment.bat" first.
echo Expected: "%APP_ROOT%\runtime\python\Scripts\python.exe"
pause
exit /b 1

:found_python
rem Delivery package keeps code under app\; development keeps it at the root.
set "CODE_ROOT=%APP_ROOT%"
if exist "%APP_ROOT%\app\launcher\__init__.py" set "CODE_ROOT=%APP_ROOT%\app"
set "PYTHONPATH=%CODE_ROOT%"
set "AP_DRAWART_ROOT=%APP_ROOT%"

rem Run in a console of its own so Ctrl+C does not leave a "Terminate batch job" prompt behind.
start "AP_DrawArt" /D "%CODE_ROOT%" "%PY%" -m launcher %*
exit /b 0
