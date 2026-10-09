@echo off
title ClearFrame - building training data
cd /d "%~dp0.."
".venv\Scripts\python.exe" -u train\make_data.py %*
echo.
echo Finished. You can close this window.
pause >nul
