@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".env" (
    echo [ERROR] .env file not found. Please create it from .env.example.
    pause
    exit /b 1
)

echo [INFO] Checking Ollama status...
curl -s http://localhost:11434 > nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Starting Ollama...
    start "" "ollama" serve
    echo [INFO] Waiting for Ollama to be ready...
    timeout /t 5 /nobreak > nul
) else (
    echo [INFO] Ollama is already running.
)

echo [INFO] Pulling Gemma model if not already downloaded...
ollama pull gemma2:9b

echo [INFO] Starting Docker container...
docker compose up --build

echo [INFO] Cleaning up Docker container...
docker compose down

echo [INFO] Done. Check your WordPress drafts.
pause
