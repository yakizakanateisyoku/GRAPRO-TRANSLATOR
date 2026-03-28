@echo off
chcp 65001 > nul
echo [exe ビルド] obs-translator.exe を生成します...

cd /d %~dp0

pyinstaller ^
  --onefile ^
  --windowed ^
  --name grapro-translator ^
  --add-data "main.py;." ^
  --hidden-import langdetect ^
  --hidden-import flask ^
  --hidden-import requests ^
  gui.py

echo.
echo ビルド完了: dist\grapro-translator.exe
pause
