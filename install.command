#!/bin/bash
# Media Sorter installer for a Mac with an Apple M-series chip. Double-click to install or repair.
# Safe to run again: it keeps your library, settings and AI models. Installs into ~/Applications/MediaSorter
# with its own Python. ffmpeg (the video tool) comes from Homebrew, which is installed if missing.
APP="$HOME/Applications/MediaSorter"
stop() { echo; echo "$1"; read -r -p "Press Enter to close."; exit 1; }
[ "$(uname -m)" = "arm64" ] || stop "This Mac has an Intel chip. Media Sorter's AI add-ons need an Apple M-series Mac (2020 or newer)."
mkdir -p "$APP/bin" && cd "$APP" || stop "Could not create $APP."

echo "[1/5] Downloading Media Sorter..."
curl -fsSL https://github.com/10xequity/media-sorter/archive/refs/heads/main.tar.gz | tar -xz --strip-components=1 || stop "Download failed. Check the internet connection."

echo "[2/5] Setting up Python (first time only)..."
[ -x bin/uv ] || curl -fsSL https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz | tar -xz --strip-components=1 -C bin || stop "Could not download Python."
bin/uv venv --quiet --python 3.12 --seed --allow-existing .venv || stop "Could not set up Python."

echo "[3/5] Installing the add-ons. This takes a few minutes the first time..."
bin/uv pip install --quiet --python .venv/bin/python -r requirements.txt || stop "Add-on install failed; see the message above."

echo "[4/5] Getting ffmpeg, the video tool..."
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
if ! command -v ffmpeg >/dev/null; then
  if [ ! -x /opt/homebrew/bin/brew ]; then
    echo "Installing Homebrew first. It asks for this Mac's login password (nothing shows as you type)."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || stop "Homebrew install failed."
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
  brew install ffmpeg || stop "Installing ffmpeg failed; see the message above."
fi

echo "[5/5] Checking everything works..."
.venv/bin/python sorter.py --selftest || stop "The built-in check failed; see the message above."
chmod +x run.command
ln -sf "$APP/run.command" "$HOME/Desktop/Media Sorter.command"

echo; echo "Done. Double-click 'Media Sorter' on the Desktop to start it. Opening it now..."
open "$APP/run.command"
