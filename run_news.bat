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

echo [INFO] Checking Docker status...
docker info > nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo [INFO] Waiting for Docker to be ready...
    :wait_docker
    timeout /t 5 /nobreak > nul
    docker info > nul 2>&1
    if %errorlevel% neq 0 goto wait_docker
    echo [INFO] Docker is ready.
) else (
    echo [INFO] Docker is already running.
)

echo [INFO] Starting Docker container...
docker compose up --build

echo [INFO] Cleaning up Docker container...
docker compose down

echo [INFO] Done. Check your WordPress drafts.
pause
