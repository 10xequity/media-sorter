@echo off
rem Media Sorter installer for Windows. Double-click to install or repair. Safe to run again:
rem it keeps your library, settings and AI models. Installs into %LOCALAPPDATA%\MediaSorter and
rem brings its own Python and ffmpeg, so nothing else on the computer is changed.
setlocal
title Media Sorter installer
if not defined MS_DIR set "MS_DIR=%LOCALAPPDATA%\MediaSorter"
set "SRC=https://github.com/10xequity/media-sorter/archive/refs/heads/main.zip"
set "UV=https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
set "FF=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

echo Installing Media Sorter into %MS_DIR%
if not exist "%MS_DIR%\bin" mkdir "%MS_DIR%\bin"
cd /d "%MS_DIR%" || goto fail

echo.
echo [1/5] Downloading Media Sorter...
curl -fsSL -o app.zip "%SRC%" || goto fail
tar -xf app.zip --strip-components=1 || goto fail
del app.zip

echo [2/5] Setting up Python (first time only)...
if not exist bin\uv.exe (
  curl -fsSL -o uv.zip "%UV%" || goto fail
  tar -xf uv.zip -C bin || goto fail
  del uv.zip
)
bin\uv.exe venv --quiet --python 3.12 --seed --allow-existing .venv || goto fail

echo [3/5] Installing the add-ons. This takes a few minutes the first time...
bin\uv.exe pip install --quiet --python .venv\Scripts\python.exe -r requirements.txt || goto fail

echo [4/5] Getting ffmpeg, the video tool (first time only, about 120 MB)...
if not exist bin\ffmpeg.exe (
  curl -fL -# -o ff.zip "%FF%" || goto fail
  tar -xf ff.zip --strip-components=2 -C bin "*/bin/ffmpeg.exe" "*/bin/ffprobe.exe" || goto fail
  del ff.zip
)

echo [5/5] Checking everything works...
.venv\Scripts\python.exe sorter.py --selftest || goto fail
if not defined MS_NO_SHORTCUT powershell -NoProfile -Command "$s = New-Object -ComObject WScript.Shell; foreach ($d in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) { $l = $s.CreateShortcut((Join-Path $d 'Media Sorter.lnk')); $l.TargetPath = Join-Path $env:MS_DIR '.venv\Scripts\pythonw.exe'; $l.Arguments = 'sorter.py --open'; $l.WorkingDirectory = $env:MS_DIR; $l.Description = 'Media Sorter'; $l.Save() }"

echo.
echo Done. Media Sorter is on your Desktop and in the Start menu. Opening it now...
if not defined MS_NO_SHORTCUT start "" "%MS_DIR%\.venv\Scripts\pythonw.exe" sorter.py --open
if not defined MS_NO_SHORTCUT pause
exit /b 0

:fail
echo.
echo The install stopped at the step above. Check the internet connection and run this file again.
echo If it keeps failing, send a photo of this window.
if not defined MS_NO_SHORTCUT pause
exit /b 1
