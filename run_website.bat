@echo off
cd /d "%~dp0"
start "E-Commerce Trust Analytics" python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8000/"
echo The website should open in your browser.
echo Keep the black "E-Commerce Trust Analytics" window open.
echo Close that window when you want to stop the site.
pause
