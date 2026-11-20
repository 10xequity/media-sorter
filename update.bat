@echo off
cd /d %~dp0
rem Update without internet: drag a Media Sorter zip onto this file. With no zip, it downloads the newest version.
rem Quit the app first, then start it again afterwards.
if exist .venv\Scripts\python.exe (.venv\Scripts\python.exe update.py %1) else (python update.py %1)
pause
