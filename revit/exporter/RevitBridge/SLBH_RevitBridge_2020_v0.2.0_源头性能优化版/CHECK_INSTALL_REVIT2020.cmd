@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo SLBH Revit Bridge 2020 - Install Check
echo ================================================
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_install_revit2020.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo CHECK COMPLETED.
) else (
    echo CHECK FAILED. Error code: %RC%
    echo See install_log_revit2020.txt for details.
)
echo.
pause
exit /b %RC%
