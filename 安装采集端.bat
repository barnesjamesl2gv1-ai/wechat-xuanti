@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Install the article collector (optional)
echo ============================================
echo.
echo This pulls the upstream collector (CC BY-NC-SA 4.0,
echo non-commercial). The analysis tool itself stays MIT.
echo.

if not exist "web_app.py" goto wrongdir
where git >nul 2>nul || goto nogit
where uv >nul 2>nul || goto nouv

if exist "collector\main.py" (
    echo [1/3] Collector already present, skipping download.
) else (
    echo [1/3] Downloading collector ...
    git clone --depth 1 https://github.com/yeximm/Access_wechat_article.git collector
    if errorlevel 1 goto clonefail
)

echo [2/3] Installing collector dependencies ...
pushd collector
uv sync
if errorlevel 1 (
    popd
    goto syncfail
)
echo       Installing browser for capture ...
uv run playwright install chromium
popd

echo [3/3] Pointing this project at the collector output ...
if not exist ".env" copy /y ".env.example" ".env" >nul
findstr /C:"XUANTI_STORAGE_ROOT=" ".env" >nul 2>nul
if errorlevel 1 (
    echo XUANTI_STORAGE_ROOT=collector/storages>>".env"
    echo       Added XUANTI_STORAGE_ROOT=collector/storages to .env
) else (
    echo       .env already has XUANTI_STORAGE_ROOT
)

echo.
echo Done. Next steps:
echo   1. cd collector
echo   2. uv run python main.py
echo      ^(follow the collector UI to capture articles^)
echo   3. come back and double-click the start script of this project
echo.
echo If capture fails, read the collector docs first;
echo it needs a proxy certificate and WeChat PC client.
pause
exit /b 0

:wrongdir
echo [ERROR] web_app.py not found. Run this inside the project folder.
pause
exit /b 1

:nogit
echo [ERROR] git not found. Install Git for Windows, or download a ZIP:
echo   https://github.com/yeximm/Access_wechat_article/archive/refs/heads/main.zip
echo   unzip it into a folder named collector, then run this again.
pause
exit /b 1

:nouv
echo [ERROR] uv not found. Install it first:
echo   powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
echo   then run this again.
pause
exit /b 1

:clonefail
echo [ERROR] Download failed. Check your network, or download the ZIP manually.
pause
exit /b 1

:syncfail
echo [ERROR] Dependency install failed. Try a mirror:
echo   set UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
echo   then run this file again.
pause
exit /b 1
