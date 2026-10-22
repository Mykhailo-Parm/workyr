@echo off
rem Runs WorkTracker from source without building an exe (needs Python 3.10+).
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Could not install requirements.
  pause
  exit /b 1
)
start "" pythonw main.py
