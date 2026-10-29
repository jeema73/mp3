@echo off
cd /d "%~dp0"
title Ginami MP3 Studio

set "PY_EXE=python"
if exist "C:\Users\jeema\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" (
    set "PY_EXE=C:\Users\jeema\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
)

echo ====================================================================
echo   [Ginami MP3 Studio] 4K High Quality YouTube MP3 Extractor
echo   Starting server and Cloudflare Mobile Tunnel...
echo.
echo   - PC Access:     http://127.0.0.1:8088
echo   - Mobile Access: Click [SmartPhone QR] on top-right of web page
echo.
echo   (Press Ctrl+C or close this window to stop the server)
echo ====================================================================
echo.

start "" "http://127.0.0.1:8088"

"%PY_EXE%" app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo --------------------------------------------------------------------
    echo [ERROR] Server terminated with error code %ERRORLEVEL%.
    echo --------------------------------------------------------------------
    pause
)
