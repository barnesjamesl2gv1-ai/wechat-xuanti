@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   WeChat Topic Studio - one-click setup
echo ============================================
echo.

if not exist "web_app.py" goto wrongdir

set "PY="
for %%C in (py python python3) do (
    if not defined PY (
        where %%C >nul 2>nul && set "PY=%%C"
    )
)
if not defined PY goto nopython

rem ? uv ???? uv??????????????? Python ??? pip ?????
set "UVEV="
where uv >nul 2>nul && set "UVEV=1"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment ...
    if defined UVEV (
        uv venv .venv --python 3.13
        if errorlevel 1 (
            echo       uv failed, falling back to python venv ...
            %PY% -m venv .venv
            set "UVEV="
        )
    ) else (
        %PY% -m venv .venv
    )
    if not exist ".venv\Scripts\python.exe" goto failed
) else (
    echo [1/3] Virtual environment already exists.
)

echo [2/3] Installing dependencies ...
if defined UVEV (
    uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
) else (
    if not exist ".pip-tmp" mkdir ".pip-tmp" >nul 2>nul
    set "TEMP=%CD%\.pip-tmp"
    set "TMP=%CD%\.pip-tmp"
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --retries 2 --timeout 30 -r requirements.txt
)
if errorlevel 1 goto pipfailed

if not exist ".env" (
    echo [3/3] Creating .env from .env.example ...
    copy /y ".env.example" ".env" >nul
    echo.
    echo   Next step: open .env with Notepad and fill in
    echo     DEEPSEEK_API_KEY, SEARCH_PROVIDER=bocha, BOCHA_API_KEY
    echo   Then double-click the start script again.
    echo.
    pause
    exit /b 0
)
echo [3/3] .env found.

echo.
echo Starting now - close this window to stop the service.
".venv\Scripts\python.exe" web_app.py
exit /b 0

:wrongdir
echo [ERROR] web_app.py not found. Please run this file inside the project folder.
pause
exit /b 1

:nopython
echo [ERROR] Python 3.11+ not found.
echo Install it from https://www.python.org/downloads/
echo and remember to check "Add python.exe to PATH" during installation.
pause
exit /b 1

:pipfailed
echo.
echo [ERROR] Dependency installation failed.
echo If the download is slow or blocked, try a mirror manually:
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pause
exit /b 1

:failed
echo.
echo [ERROR] Setup failed. Please read the messages above.
pause
exit /b 1
