@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
  echo uv was not found.
  echo Install uv from https://docs.astral.sh/uv/getting-started/installation/
  echo Then reopen the terminal and run this launcher again.
  pause
  exit /b 1
)

uv run python launcher.py %*
set "launcher_exit=%errorlevel%"
if not "%launcher_exit%"=="0" pause
exit /b %launcher_exit%
