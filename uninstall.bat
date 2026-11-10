@echo off
rem WhisperX Subtitle Workbench - removal; Settings - Apps - Uninstall starts this file too.
rem It finds a Python for the window (wt_setup_ui.py); without Python it removes only venv and temporary files.
rem The Python search is the same as in install.bat.
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "WT=%~dp0"
set "PYTHONPATH="
set "PYTHONHOME="
call :texts

set "PY="
if exist "%WT%wt_setup_ui.py" call :find_python
if not defined PY goto :without_python
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" -I "%WT%wt_setup_ui.py" uninstall
exit /b 0

:without_python
echo(
echo %T_U_NOPY%
echo    %WT%
choice /c %T_YN% /n /m "%T_U_ASK%"
if errorlevel 2 exit /b 1
if exist "%WT%venv" rd /s /q "%WT%venv"
for /d %%D in ("%WT%venv-alt-*") do rd /s /q "%%D"
for %%D in (__pycache__ temp update .pytest_cache) do if exist "%WT%%%D" rd /s /q "%WT%%%D"
for %%F in (install.log install.lock update_check.json whisper_tool_error.log whisper_tool_shutdown.log) do if exist "%WT%%%F" del /q "%WT%%%F"
echo(
echo %T_U_DONE1%
echo %T_U_DONE2%
echo    %APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\WhisperX_Subtitles.lua
echo(
pause
exit /b 0

:find_python
call :from_registry HKCU
if not defined PY call :from_registry HKLM
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :accept "%%D\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do call :accept "%%D\python.exe"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do call :accept "%%P"
exit /b 0

:from_registry
for /f "tokens=2,*" %%A in ('reg query "%~1\Software\Python" /s /v ExecutablePath 2^>nul ^| findstr /i /c:"ExecutablePath"') do call :accept "%%B"
exit /b 0

:accept
if defined PY exit /b 0
if not exist "%~1" exit /b 0
echo "%~1" | findstr /i /c:"WindowsApps" >nul && exit /b 0
"%~1" -I -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul || exit /b 0
set "PY=%~1"
exit /b 0

:texts
set "UI="
for /f "tokens=3" %%L in ('reg query "HKCU\Control Panel\Desktop" /v PreferredUILanguages 2^>nul ^| findstr /i "PreferredUILanguages"') do set "UI=%%L"
if not defined UI for /f "tokens=3" %%L in ('reg query "HKCU\Control Panel\Desktop\MuiCached" /v MachinePreferredUILanguages 2^>nul ^| findstr /i "MachinePreferredUILanguages"') do set "UI=%%L"
if not defined UI for /f "tokens=3" %%L in ('reg query "HKCU\Control Panel\International" /v LocaleName 2^>nul ^| findstr /i "LocaleName"') do set "UI=%%L"
if not defined UI set "UI=en"
set "UI=%UI:~0,2%"
if /i "%UI%"=="de" goto :texts_de
if /i "%UI%"=="tr" goto :texts_tr
set "T_YN=YN"
set "T_U_NOPY=No Python with Tkinter found. Only the Python environment (venv) and the temporary files of this folder can be removed:"
set "T_U_ASK=Remove them now? [Y/N] "
set "T_U_DONE1=Done. Delete the other files of the folder in Explorer; keep whisper_tool.ini if you want to keep your settings."
set "T_U_DONE2=The plugin for DaVinci Resolve, if installed:"
exit /b 0
:texts_de
set "T_YN=JN"
set "T_U_NOPY=Kein Python mit Tkinter gefunden. Entfernt werden können nur die Python-Umgebung (venv) und die temporären Dateien dieses Ordners:"
set "T_U_ASK=Jetzt entfernen? [J/N] "
set "T_U_DONE1=Fertig. Lösche die übrigen Dateien des Ordners im Explorer; behalte whisper_tool.ini, wenn du deine Einstellungen behalten willst."
set "T_U_DONE2=Das Plugin für DaVinci Resolve, falls installiert:"
exit /b 0
:texts_tr
set "T_YN=EH"
set "T_U_NOPY=Tkinter içeren Python bulunamadı. Yalnızca bu klasörün Python ortamı (venv) ve geçici dosyaları kaldırılabilir:"
set "T_U_ASK=Şimdi kaldırılsın mı? [E/H] "
set "T_U_DONE1=Bitti. Klasördeki diğer dosyaları Gezgin'de silin; ayarlarınızı korumak istiyorsanız whisper_tool.ini'yi saklayın."
set "T_U_DONE2=DaVinci Resolve eklentisi, kuruluysa:"
exit /b 0
