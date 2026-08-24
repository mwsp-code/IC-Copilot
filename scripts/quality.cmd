@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_quality_checks.ps1" %*
exit /b %ERRORLEVEL%
