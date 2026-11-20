@echo off
cd /d %~dp0
rem Starts Media Sorter in the background (no window) and opens it in the browser. Stop it with "Quit app".
if exist .venv\Scripts\pythonw.exe (start "" .venv\Scripts\pythonw.exe sorter.py --open) else (start "" pythonw sorter.py --open)
