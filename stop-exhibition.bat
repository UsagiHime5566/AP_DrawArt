@echo off
rem AP_DrawArt exhibition stop entry.
rem Double-click to close the whole exhibition properly: windows, web service and AI worker.
rem Works like the admin "end exhibition" button; if the launcher does not respond, it is forced to stop.
rem Also clears processes left behind by a forced close, so start-exhibition.bat can run again.
setlocal EnableExtensions DisableDelayedExpansion

set "APP_ROOT=%~dp0"
if "%APP_ROOT:~-1%"=="\" set "APP_ROOT=%APP_ROOT:~0,-1%"

rem Same runtime search order as start-exhibition.bat, using the console python.exe to show the result.
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
pause
exit /b 1

:found_python
set "CODE_ROOT=%APP_ROOT%"
if exist "%APP_ROOT%\app\launcher\__init__.py" set "CODE_ROOT=%APP_ROOT%\app"
set "PYTHONPATH=%CODE_ROOT%"
set "AP_DRAWART_ROOT=%APP_ROOT%"

pushd "%CODE_ROOT%"
"%PY%" -m launcher --stop %*
set "STOP_RESULT=%ERRORLEVEL%"
popd
if "%STOP_RESULT%"=="0" (
    timeout /t 3 /nobreak >nul
) else (
    pause
)
exit /b %STOP_RESULT%
