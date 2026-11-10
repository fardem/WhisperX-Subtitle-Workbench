@echo off
rem WhisperX Subtitle Workbench - installation, repair and update: double-click, no administrator rights needed.
rem This file only finds a Python for the installer window (wt_setup_ui.py); that window does everything else.
rem Paths stay in quotes and outside of ( ) blocks: folder names may contain spaces, ( ) and &.
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "WT=%~dp0"
set "PYTHONPATH="
set "PYTHONHOME="
call :texts

if not exist "%WT%wt_setup_ui.py" goto :not_extracted
if not exist "%WT%whisper_tool.py" goto :not_extracted
if not exist "%WT%requirements.txt" goto :not_extracted
if not exist "%WT%lang\en.json" goto :not_extracted

if /i "%~1"=="not-installed" goto :ask_install
:find
set "PY="
set "NOTK="
call :find_python
if not defined PY goto :no_python
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" -I "%WT%wt_setup_ui.py" install
exit /b 0

:ask_install
echo(
echo %T_NOTINST%
choice /c %T_YN% /n /m "%T_ASK_INSTALL%"
if errorlevel 2 exit /b 1
goto :find

:not_extracted
echo(
echo %T_NOTEXT1%
echo    %WT%
echo %T_NOTEXT2%
echo(
pause
exit /b 1

:no_python
if defined NOTK goto :no_tkinter
echo(
echo %T_NOPY%
where winget >nul 2>nul
if errorlevel 1 goto :manual
choice /c %T_YN% /n /m "%T_ASK_PY%"
if errorlevel 2 goto :manual
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
call :find_python
if defined PY goto :find
echo(
echo %T_AGAIN%
pause
exit /b 1

:manual
echo(
echo %T_MANUAL%
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:no_tkinter
echo(
echo %T_NOTK1%
echo    %NOTK%
echo %T_NOTK2%
echo(
pause
exit /b 1

rem ─── Python: registry (python.org and the install manager register there), then the usual folders ───
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
"%~1" -I -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul || exit /b 0
"%~1" -I -c "import tkinter" >nul 2>nul && goto :accept_ok
set "NOTK=%~1"
exit /b 0
:accept_ok
set "PY=%~1"
exit /b 0

rem ─── Texts before Python runs; everything after them is in lang\*.json ───
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
set "T_NOTINST=The Workbench is not installed yet."
set "T_ASK_INSTALL=Install it now? [Y/N] "
set "T_NOTEXT1=Files of the Workbench are missing in this folder:"
set "T_NOTEXT2=The ZIP file is probably not extracted. Right-click the ZIP file - Extract All, then start install.bat in the extracted folder."
set "T_NOPY=Python is missing. The Workbench needs Python 3.13."
set "T_ASK_PY=Install Python 3.13 now with winget, without administrator rights? [Y/N] "
set "T_AGAIN=Python is installed but not found yet. Close this window and start install.bat again."
set "T_MANUAL=The download page of Python opens. Download Python 3.13 (Windows installer, 64-bit), install it and start install.bat again."
set "T_NOTK1=This Python has no Tkinter, which the Workbench needs for its windows:"
set "T_NOTK2=Python installer - Modify - tick tcl/tk and IDLE, then start install.bat again."
exit /b 0
:texts_de
set "T_YN=JN"
set "T_NOTINST=Die Workbench ist noch nicht installiert."
set "T_ASK_INSTALL=Jetzt installieren? [J/N] "
set "T_NOTEXT1=In diesem Ordner fehlen Dateien der Workbench:"
set "T_NOTEXT2=Wahrscheinlich ist die ZIP-Datei nicht entpackt. Rechtsklick auf die ZIP-Datei - Alle extrahieren, dann install.bat im entpackten Ordner starten."
set "T_NOPY=Python fehlt. Die Workbench braucht Python 3.13."
set "T_ASK_PY=Python 3.13 jetzt mit winget installieren, ohne Administratorrechte? [J/N] "
set "T_AGAIN=Python ist installiert, wird aber noch nicht gefunden. Schließe dieses Fenster und starte install.bat erneut."
set "T_MANUAL=Die Download-Seite von Python öffnet sich. Lade Python 3.13 (Windows installer, 64-bit), installiere es und starte install.bat erneut."
set "T_NOTK1=Diesem Python fehlt Tkinter, das die Workbench für ihre Fenster braucht:"
set "T_NOTK2=Python-Installer - Ändern - Häkchen bei tcl/tk and IDLE setzen, dann install.bat erneut starten."
exit /b 0
:texts_tr
set "T_YN=EH"
set "T_NOTINST=Workbench henüz kurulu değil."
set "T_ASK_INSTALL=Şimdi kurulsun mu? [E/H] "
set "T_NOTEXT1=Bu klasörde Workbench dosyaları eksik:"
set "T_NOTEXT2=ZIP dosyası muhtemelen açılmadı. ZIP dosyasına sağ tıklayın - Tümünü ayıkla, ardından açılan klasördeki install.bat'ı başlatın."
set "T_NOPY=Python eksik. Workbench, Python 3.13'e ihtiyaç duyar."
set "T_ASK_PY=Python 3.13 şimdi winget ile, yönetici hakları olmadan kurulsun mu? [E/H] "
set "T_AGAIN=Python kuruldu ancak henüz bulunamıyor. Bu pencereyi kapatın ve install.bat'ı yeniden başlatın."
set "T_MANUAL=Python indirme sayfası açılıyor. Python 3.13'ü (Windows installer, 64-bit) indirin, kurun ve install.bat'ı yeniden başlatın."
set "T_NOTK1=Bu Python'da Workbench'in pencereleri için gereken Tkinter yok:"
set "T_NOTK2=Python yükleyicisi - Değiştir - tcl/tk and IDLE işaretleyin, ardından install.bat'ı yeniden başlatın."
exit /b 0
