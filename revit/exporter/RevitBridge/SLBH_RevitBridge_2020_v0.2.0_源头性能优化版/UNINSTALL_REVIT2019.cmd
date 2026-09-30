@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo SLBH Revit Bridge 2019 - Uninstaller
echo ================================================
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_revit2019.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo UNINSTALL SUCCESSFUL.
) else (
    echo UNINSTALL FAILED. Error code: %RC%
    echo See install_log_revit2019.txt for details.
)
echo.
pause
exit /b %RC%
