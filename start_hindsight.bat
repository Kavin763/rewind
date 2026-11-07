@echo off
rem Starts only the Hindsight memory server (start.bat runs this for you; you can also run it on its own).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
if "%HINDSIGHT_LLM_MODEL%"=="" set "HINDSIGHT_LLM_MODEL=openai/gpt-oss-20b"
rem Hindsight prints a Unicode banner; without UTF-8 the Windows console crashes it.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HINDSIGHT_API_LLM_PROVIDER=groq"
set "HINDSIGHT_API_LLM_API_KEY=%GROQ_API_KEY%"
set "HINDSIGHT_API_LLM_MODEL=%HINDSIGHT_LLM_MODEL%"
rem Free Groq accounts only allow the on_demand tier (Hindsight defaults to "auto").
set "HINDSIGHT_API_LLM_GROQ_SERVICE_TIER=on_demand"
set "HINDSIGHT_API_WORKER_ID=hindsight-rewind"
rem The first start downloads embedding and reranker models; give slow connections up to 30 minutes.
set "HINDSIGHT_API_MODEL_INIT_TIMEOUT=1800"
rem Free Groq tiers allow ~8k tokens/minute: one LLM call at a time avoids bursts of rejected requests.
set "HINDSIGHT_API_LLM_MAX_CONCURRENT=1"
echo Hindsight memory server is running. Keep this window open. Log: hindsight.log
hindsight-api > hindsight.log 2>&1
