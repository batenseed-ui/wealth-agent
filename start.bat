@echo off
REM Double-click to start the Wealth Advisory Agent and open it in your browser.
cd /d "%~dp0"
REM Open the browser after a short delay so the server is ready first.
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000"
python app.py
pause
