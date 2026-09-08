#!/usr/bin/env bash
set -e
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment .venv..."
    python3 -m venv .venv
fi
source .venv/bin/activate
python3 install.py
