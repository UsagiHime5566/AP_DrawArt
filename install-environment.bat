@echo off
rem Install the project runtime and build the local frontend.
setlocal EnableExtensions DisableDelayedExpansion

set "APP_ROOT=%~dp0"
set "INSTALL_SCRIPT=%APP_ROOT%launcher\install-environment.ps1"
if exist "%APP_ROOT%app\launcher\install-environment.ps1" set "INSTALL_SCRIPT=%APP_ROOT%app\launcher\install-environment.ps1"

if not exist "%INSTALL_SCRIPT%" (
    echo [AP_DrawArt] Installer script not found: "%INSTALL_SCRIPT%"
    set "INSTALL_RESULT=1"
    goto finished
)

rem Let Windows PowerShell resolve its own modules, even when called from PowerShell 7.
set "PSModulePath="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_SCRIPT%" -ProjectRoot "%APP_ROOT%." %*
set "INSTALL_RESULT=%ERRORLEVEL%"

:finished
echo.
if not "%INSTALL_RESULT%"=="0" echo [AP_DrawArt] Installation failed. See the error above and runtime\install-environment.txt.
if "%INSTALL_RESULT%"=="0" echo [AP_DrawArt] Ready. Run start-exhibition.bat to start the exhibition.
if not "%AP_DRAWART_INSTALL_NO_PAUSE%"=="1" pause
exit /b %INSTALL_RESULT%
