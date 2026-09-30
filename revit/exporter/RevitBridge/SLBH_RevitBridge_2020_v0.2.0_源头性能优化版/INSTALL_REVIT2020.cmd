@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo SLBH Revit Bridge 2020 - Installer
echo ================================================
echo.
echo Close Revit 2020 before continuing.
echo Installing...
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_revit2020.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo INSTALL SUCCESSFUL.
    echo Open Revit 2020 and look for the SLBH Tools ribbon tab.
) else (
    echo INSTALL FAILED. Error code: %RC%
    echo See install_log_revit2020.txt for details.
)
echo.
pause
exit /b %RC%
