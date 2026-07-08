@echo off
chcp 65001 > nul
echo [exe ビルド] grapro-translator.exe を生成します...

cd /d %~dp0

REM ビルド定義は grapro-translator.spec に一本化（CLI引数との二重管理を防ぐ）
pyinstaller --noconfirm grapro-translator.spec

echo.
echo ビルド完了: dist\grapro-translator.exe
echo リリース時は grapro-translator-vX.X.X.exe にリネームすること
pause
