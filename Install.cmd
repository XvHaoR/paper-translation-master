@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python scripts\install_unified.py
) else (
  py -3 scripts\install_unified.py
)
set "setup_status=%ERRORLEVEL%"
if not "%setup_status%"=="0" echo Installation failed. See the error above and README.md.
pause
exit /b %setup_status%
