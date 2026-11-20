#!/bin/bash
# Start Media Sorter on a Mac and open the dashboard. Stop it with "Quit app" in the dashboard.
cd "$(dirname "$(readlink "$0" || echo "$0")")" || exit 1
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"  # so the app can find ffmpeg
nohup .venv/bin/python sorter.py --open >/dev/null 2>&1 &
