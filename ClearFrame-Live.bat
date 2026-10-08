@echo off
rem ClearFrame: settings window + tray app that upscales any video already playing.
rem Needs the project environment (.venv, pip install -r desktop\requirements.txt) and mpv (python bench\bench.py setup).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0desktop\main.py"
