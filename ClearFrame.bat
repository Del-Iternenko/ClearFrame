@echo off
rem ClearFrame control panel. First run once: python bench\bench.py setup  (downloads mpv)
start "" pythonw "%~dp0app\clearframe_app.py" || start "" pyw -3 "%~dp0app\clearframe_app.py"
