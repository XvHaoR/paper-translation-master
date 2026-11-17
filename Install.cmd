@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" -X utf8 "%~dp0install.py"
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    python "%~dp0install.py"
  ) else (
    py -3 "%~dp0install.py"
  )
)
if errorlevel 1 (
  echo Installation failed. See the error above and README.md.
  pause
  exit /b 1
)
pause
