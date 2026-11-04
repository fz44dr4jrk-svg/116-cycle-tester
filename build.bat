@echo off
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" not found. Install Python 3.11+ from python.org, then re-run this file.
  exit /b 1
)
py -m pip install -r requirements.txt
py -m PyInstaller --clean --onefile --noconsole --name L16CycleTester server.py --add-data "static;static" --collect-all Phidget22
if errorlevel 1 exit /b 1
echo.
echo Built dist\L16CycleTester.exe
echo Copy that single file to a USB key and run it on the Windows tablet.
