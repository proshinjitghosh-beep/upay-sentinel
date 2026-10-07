@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.10 or newer from python.org, then run this file again.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist data\transactions.csv .venv\Scripts\python.exe -m data.generate_data
if errorlevel 1 goto failed
echo Open http://127.0.0.1:8000 after the server starts.
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
pause
exit /b
:failed
echo Setup failed. Check the error above and your internet connection.
pause
exit /b 1
