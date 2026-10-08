@echo off
rem ClearFrame control panel. First run once: python bench\bench.py setup  (downloads mpv)
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0app\clearframe_app.py") || (start "" pythonw "%~dp0app\clearframe_app.py")
