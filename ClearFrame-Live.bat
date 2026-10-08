@echo off
rem ClearFrame Live: tray app that upscales any video already playing (Ctrl+Alt+U).
rem Needs the project environment (.venv) and mpv (python bench\bench.py setup).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0live\tray.py"
