@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo SLBH Revit Bridge 2019 - Install Check
echo ================================================
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_install_revit2019.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo CHECK COMPLETED.
) else (
    echo CHECK FAILED. Error code: %RC%
    echo See install_log_revit2019.txt for details.
)
echo.
pause
exit /b %RC%
