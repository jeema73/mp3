@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 기나미 MP3 추출기

set "PY_EXE=python"
if exist "C:\Users\jeema\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" (
    set "PY_EXE=C:\Users\jeema\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
)

echo ====================================================================
echo   🎵 기나미 MP3 추출기 (4K High Quality Audio)
echo   유튜브 단일 영상 및 재생목록(Playlist) 초고음질 320kbps MP3 변환기
echo.
echo   접속 주소: http://127.0.0.1:8088
echo   (종료하려면 이 창에서 Ctrl+C 를 누르시거나 창을 닫으세요)
echo ====================================================================
echo.

start "" "http://127.0.0.1:8088"

"%PY_EXE%" app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo --------------------------------------------------------------------
    echo [ERROR] 서버 실행 중 오류가 발생했습니다 (코드: %ERRORLEVEL%).
    echo --------------------------------------------------------------------
    pause
)
