#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# 1. Search for Python in local virtual environments or system PATH
PYTHON_BIN=""

if [ -f ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
elif [ -f "venv/bin/python" ]; then
    PYTHON_BIN="venv/bin/python"
elif [ -f "ENV/bin/python" ]; then
    PYTHON_BIN="ENV/bin/python"
elif [ -f "env/bin/python" ]; then
    PYTHON_BIN="env/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "[ERROR] Python 3 was not found in PATH or a local virtual environment (.venv/venv)."
    echo "Please install Python 3 and ensure the required packages from requirements.txt are installed."
    exit 1
fi

# 2. Check for background launch flag
if [ "$1" = "--background" ] || [ "$1" = "-b" ]; then
    echo "Starting mGBA Emulator in background..."
    nohup "$PYTHON_BIN" main.py "$@" > startup.log 2>&1 &
    echo "Logs are being redirected to startup.log"
else
    echo "Starting mGBA Python Emulator using: $PYTHON_BIN"
    exec "$PYTHON_BIN" main.py "$@"
fi