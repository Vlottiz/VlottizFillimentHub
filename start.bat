@echo off
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
.venv\Scripts\python run.py
pause
