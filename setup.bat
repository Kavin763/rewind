@echo off
rem One-time setup for Rewind on Windows. Double-click to run. Output is also saved to setup.log.
cd /d "%~dp0"
call :main > setup.log 2>&1
type setup.log
echo.
pause
exit /b

:main
echo == Rewind setup ==
python --version
if errorlevel 1 (
  echo ERROR: Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  exit /b 1
)

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet

echo Installing Rewind dependencies...
pip install -r requirements-dev.txt
if errorlevel 1 (
  echo ERROR: installing dependencies failed.
  exit /b 1
)

rem Hindsight runs in Docker when Docker is available, otherwise natively from Python.
docker info >nul 2>nul
if errorlevel 1 (
  echo Docker is not running or not installed: installing the Hindsight server into the venv instead. This download is large.
  pip install hindsight-api
  if errorlevel 1 (
    echo ERROR: installing hindsight-api failed.
    exit /b 1
  )
  if exist .hindsight-docker del .hindsight-docker
) else (
  echo Docker found: Hindsight will run with docker compose.
  echo docker> .hindsight-docker
)

rem Hindsight prints Unicode to the console; make the venv's Python use UTF-8 output.
> .venv\Lib\site-packages\sitecustomize.py echo import sys
>> .venv\Lib\site-packages\sitecustomize.py echo for s in (sys.stdout, sys.stderr):
>> .venv\Lib\site-packages\sitecustomize.py echo     try: s.reconfigure(encoding="utf-8", errors="replace")
>> .venv\Lib\site-packages\sitecustomize.py echo     except Exception: pass

if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example
)

echo Running tests...
python -m pytest -q

echo.
echo SETUP COMPLETE
echo Next: open .env, put your Groq key after GROQ_API_KEY=, save, then double-click start.bat
exit /b 0
