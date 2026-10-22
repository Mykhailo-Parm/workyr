@echo off
rem Builds a single dist\workyr.exe (needs Python 3.10+ installed on this PC, once).
cd /d "%~dp0"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

python make_icon.py
if errorlevel 1 goto :fail

python -m PyInstaller --noconfirm --clean --onefile --noconsole --name workyr ^
  --icon icon.ico --hidden-import pystray._win32 main.py
if errorlevel 1 goto :fail

echo.
echo DONE: dist\workyr.exe  (copy this single file to the other PC)
pause
exit /b 0

:fail
echo.
echo BUILD FAILED. Scroll up to see the error.
pause
exit /b 1
