@echo off
setlocal
cd /d "%~dp0"
call "%~dp0CHECK_INSTALL_REVIT2020.cmd"
exit /b %ERRORLEVEL%
