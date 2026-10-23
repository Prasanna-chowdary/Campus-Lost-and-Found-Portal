@echo off
cd /d "%~dp0"
where python >nul 2>nul && (set PY=python) || (set PY=py)
%PY% -m pip install -r requirements.txt
%PY% app.py
pause
