@echo off
setlocal
cd /d "%~dp0"
title XuanTi Web MVP

if not exist ".venv\Scripts\python.exe" goto no_venv

echo Starting XuanTi Web MVP (browser mode)...
echo The URL is printed below. Open it in your browser manually.
echo This window does NOT touch your browser, and closing it stops the service.
echo.

".venv\Scripts\python.exe" web_app.py
goto done

:no_venv
echo [ERROR] Python venv not found: .venv\Scripts\python.exe
echo Please make sure this file sits in the project folder.
pause
exit /b 1

:done
echo.
echo Service stopped. Press any key to close.
pause >nul
