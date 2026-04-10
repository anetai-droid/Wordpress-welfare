@echo off
chcp 65001 > nul
echo ==========================================
echo 最新の叡智（ニュースとコード）を収集しています...
echo ==========================================

cd /d "%~dp0"
docker compose up
docker compose down

echo.
echo 送信完了！マイチャットを確認してください。
echo 何かキーを押すとこの画面を閉じます。
pause > nul