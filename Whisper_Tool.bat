@echo off
rem WhisperX Subtitle Workbench - double-click to open, or drop files/folders onto this file.
rem Uses the Python venv in the folder "venv" next to this file (or one folder up); without one install.bat asks.
set "PYW=%~dp0venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0..\venv\Scripts\pythonw.exe"
if not exist "%PYW%" goto :not_installed
start "" "%PYW%" "%~dp0whisper_tool.py" %*
exit /b 0

:not_installed
if exist "%~dp0install.bat" call "%~dp0install.bat" not-installed
if exist "%~dp0install.bat" exit /b
echo Python venv not found next to this file: %~dp0venv
pause
exit /b 1
