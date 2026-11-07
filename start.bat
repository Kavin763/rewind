@echo off
rem Starts Hindsight (memory), seeds it once, then starts Rewind on http://localhost:8000
cd /d "%~dp0"
setlocal

if not exist .venv\Scripts\activate.bat (
  echo Run setup.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Load KEY=VALUE pairs from .env (lines starting with # are skipped)
if not exist .env copy .env.example .env >nul
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"

if "%GROQ_API_KEY%"=="" goto nokey
if "%GROQ_API_KEY%"=="gsk_your_key_here" goto nokey

if "%HINDSIGHT_LLM_MODEL%"=="" set "HINDSIGHT_LLM_MODEL=openai/gpt-oss-20b"

if exist .hindsight-docker (
  echo Starting Hindsight with Docker...
  docker compose up -d
) else (
  curl -s -f -o nul http://localhost:8888/docs && (
    echo Hindsight is already running.
  ) || (
    echo Starting Hindsight server in its own window ^(log: hindsight.log^)...
    start "Hindsight memory server" /min cmd /c start_hindsight.bat
  )
)

echo Waiting for Hindsight on http://localhost:8888 ^(the first start downloads models and can take several minutes^)...
set /a tries=0
:wait
curl -s -f -o nul http://localhost:8888/docs && goto ready
set /a tries+=1
if %tries% geq 180 (
  echo ERROR: Hindsight did not start. See hindsight.log
  pause
  exit /b 1
)
timeout /t 5 /nobreak >nul
goto wait

:ready
echo Hindsight is up.
if not exist data\.seeded (
  echo Loading past incidents into memory ^(first run only^)...
  python scripts\seed_memory.py
  if errorlevel 1 (
    echo ERROR: seeding failed.
    pause
    exit /b 1
  )
  echo seeded> data\.seeded
)

echo Starting Rewind on http://localhost:8000 ...
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8000"
python -m uvicorn app.main:app --port 8000
pause
exit /b

:nokey
echo Your Groq API key is missing. Opening .env: paste your key after GROQ_API_KEY=, save, then run start.bat again.
start notepad .env
pause
exit /b 1
