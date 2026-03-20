@echo off
chcp 65001 > nul
echo [OBS翻訳ツール] 起動中...

REM 既存プロセスをポートごとクリア
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7788 " ^| findstr "LISTENING"') do (
    echo [INFO] PID %%a をポート解放のため終了します
    taskkill /F /PID %%a > nul 2>&1
)

cd /d %~dp0

REM GUIモード（引数なし）かCLIモード（video_id指定）かを選択
if "%1"=="--cli" (
    echo [CLIモード] python main.py %2
    python main.py %2
    pause
) else (
    echo [GUIモード] python gui.py
    pythonw gui.py
)
