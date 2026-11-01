@echo off
rem WhisperX Subtitle Workbench - double-click to open, or drop files/folders onto this file.
rem Uses the Python venv in the folder "venv" next to this file (or one folder up).
set "PYW=%~dp0venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0..\venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
  echo Python venv not found next to this file: %~dp0venv
  echo Create it as described in README.md, or adjust the path in this file.
  pause
  exit /b 1
)
start "" "%PYW%" "%~dp0whisper_tool.py" %*
