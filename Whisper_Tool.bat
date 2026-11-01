@echo off
rem WhisperX Subtitle Workbench - double-click to open, or drop files/folders onto this file.
rem Uses the Python venv in the folder "venv" next to this file (see README).
set "PYW=%~dp0venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
  echo Python venv not found: %PYW%
  echo Create it as described in README.md, or adjust the path in this file.
  pause
  exit /b 1
)
start "" "%PYW%" "%~dp0whisper_tool.py" %*
