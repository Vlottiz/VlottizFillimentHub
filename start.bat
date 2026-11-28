@echo off
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up...
  py -3 -m venv .venv || python -m venv .venv
)
rem Install or update packages whenever requirements.txt has changed since the last run.
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
  echo Installing/updating packages...
  .venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt && copy /y requirements.txt .venv\requirements.installed >nul
)
.venv\Scripts\python run.py
pause
