@echo off
if not exist ".venv" (
    echo Creating virtual environment .venv...
    python -m venv .venv
)
call .venv\Scripts\activate.bat
python install.py
