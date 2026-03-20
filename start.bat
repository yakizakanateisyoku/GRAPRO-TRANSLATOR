@echo off
chcp 65001 > nul
echo [OBS翻訳ツール] 起動中...

REM 既存プロセスをポートごとクリア
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7788 " ^| findstr "LISTENING"') do (
    echo [INFO] PID %%a をポート解放のため終了します
    taskkill /F /PID %%a > nul 2>&1
)

cd /d %~dp0
if "%1"=="" (
    python main.py
) else (
    python main.py %1
)
pause
