@echo off
chcp 65001 > nul
echo [exe ビルド] obs-translator.exe を生成します...

cd /d %~dp0

pyinstaller ^
  --onefile ^
  --windowed ^
  --name obs-translator ^
  --add-data "main.py;." ^
  --hidden-import langdetect ^
  --hidden-import pytchat ^
  --hidden-import flask ^
  gui.py

echo.
echo ビルド完了: dist\obs-translator.exe
pause
