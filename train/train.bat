@echo off
title ClearFrame - training the network (close this window to pause; run again to resume)
cd /d "%~dp0.."
".venv\Scripts\python.exe" -u train\train.py --name cf64x4 --clean-iters 20000 --detail-iters 15000 %*
echo.
echo Finished. You can close this window.
pause >nul
