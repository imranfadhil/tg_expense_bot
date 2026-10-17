@echo off
setlocal enabledelayedexpansion

REM ===================================================================
REM  sync.bat - Push Telegram Expense Bot to DigitalOcean droplet
REM  Deploys into %REMOTE_DIR% (/opt/tg_expense_bot) as an isolated
REM  Docker Compose project without touching existing containers.
REM
REM  Usage:
REM    sync.bat              push code + restart bot containers
REM    sync.bat --restart    force docker rebuild
REM    sync.bat --from-deploy called by deploy.bat (first push)
REM ===================================================================

cd /d "%~dp0"
set DEPLOY_DIR=%CD%

if not exist "config.bat" (
  echo [sync] config.bat not found. Copy config.example.bat to config.bat first.
  exit /b 1
)
call config.bat

REM Fallback: if BOT_TOKEN is not set in config.bat, try reading it from ..\.env
if "%BOT_TOKEN%"=="" (
    if exist "%DEPLOY_DIR%\..\.env" (
        for /f "usebackq tokens=1,* delims==" %%a in ("%DEPLOY_DIR%\..\.env") do (
            if /i "%%a"=="BOT_TOKEN" set BOT_TOKEN=%%b
        )
    )
)

if "%BOT_TOKEN%"=="" (
    echo [ERROR] BOT_TOKEN is not set in deploy\config.bat or .env.
    echo         Paste your @BotFather token into deploy\config.bat ^(set BOT_TOKEN=...^) and re-run.
    exit /b 1
)
if "%BOT_TOKEN%"=="1234567890:AAH_your_telegram_bot_token_here" (
    echo [ERROR] BOT_TOKEN is still set to the example placeholder.
    echo         Paste your real @BotFather token into deploy\config.bat and re-run.
    exit /b 1
)

set BUILD=0
if "%1"=="--restart"     set BUILD=1
if "%1"=="--from-deploy" set BUILD=1

echo.
echo === SYNC - Telegram Family Expense Bot (%DROPLET_NAME%) ===
echo.

REM -- Resolve public IP ----------------------------------------------
set PUBLIC_IP=
if exist "%TEMP%\%PROJECT_NAME%-do-ip.txt" set /p PUBLIC_IP=<"%TEMP%\%PROJECT_NAME%-do-ip.txt"
if "%PUBLIC_IP%"=="" (
    for /f %%i in ('doctl compute droplet get %DROPLET_NAME% --format PublicIPv4 --no-header 2^>nul') do set PUBLIC_IP=%%i
)
if "%PUBLIC_IP%"=="" (
    echo [ERROR] Could not resolve IP for droplet %DROPLET_NAME%.
    echo         Ensure doctl is authenticated: doctl auth init
    exit /b 1
)
echo [INFO] Target Droplet: %DROPLET_NAME% (%PUBLIC_IP%)
echo [INFO] Remote Path:    %REMOTE_DIR% (isolated from other apps)

if not exist "%KEY_FILE%" (
    echo [ERROR] SSH key not found: %KEY_FILE%
    exit /b 1
)

set "SSH_OPTS=-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i "%KEY_FILE%""
set SSH_RETRIES=5

REM -- Package code (exclude .venv, .git, tests, deploy, .env) --------
echo [1/5] Packaging project...
cd /d "%~dp0.."
if exist "%TEMP%\%PROJECT_NAME%.tar" del "%TEMP%\%PROJECT_NAME%.tar"
tar -cf "%TEMP%\%PROJECT_NAME%.tar" ^
    --exclude=".venv" ^
    --exclude="venv" ^
    --exclude="__pycache__" ^
    --exclude=".pytest_cache" ^
    --exclude=".git" ^
    --exclude=".env" ^
    --exclude="deploy" ^
    --exclude="tests" ^
    --exclude="postgres_data" ^
    --exclude="*.pyc" ^
    --exclude="*.log" ^
    *
if %ERRORLEVEL% neq 0 (
    echo [ERROR] tar packaging failed.
    exit /b 1
)
echo [OK] Packaged.

REM -- Copy to droplet ------------------------------------------------
echo [2/5] Ensuring isolated remote directory exists (%REMOTE_DIR%)...
call :ssh_run "mkdir -p %REMOTE_DIR%"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Could not create remote directory %REMOTE_DIR%.
    exit /b 1
)
echo [OK] Remote directory ready.

echo [3/5] Uploading archive to droplet...
call :ssh_pipe "%TEMP%\%PROJECT_NAME%.tar" "cat > %REMOTE_DIR%/app.tar"
set SCP_RC=%ERRORLEVEL%
del "%TEMP%\%PROJECT_NAME%.tar" 2>nul
if not %SCP_RC% equ 0 (
    echo [ERROR] Upload failed.
    exit /b 1
)
echo [OK] Uploaded.

REM -- Extract + configure .env ---------------------------------------
echo [4/5] Extracting and configuring %REMOTE_DIR%/.env...
call :ssh_run "cd %REMOTE_DIR% && tar -xf app.tar && rm -f app.tar"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Extraction failed.
    exit /b 1
)

set "ENV_TMP=%TEMP%\%PROJECT_NAME%-configure-env.sh"
powershell -NoProfile -Command "(Get-Content -Raw '%DEPLOY_DIR%\configure-env.sh') -replace [char]13, '' | Set-Content -NoNewline '%ENV_TMP%' -Encoding ascii"
if not exist "%ENV_TMP%" (
    echo [ERROR] Could not prepare configure-env.sh.
    exit /b 1
)

call :ssh_pipe "%ENV_TMP%" "bash -s -- '%REMOTE_DIR%' '%BOT_TOKEN%' '%DEFAULT_CURRENCY%' '%TIMEZONE%' '%POSTGRES_USER%' '%POSTGRES_DB%'"
set ENV_RC=%ERRORLEVEL%
del "%ENV_TMP%" 2>nul
if not %ENV_RC% equ 0 (
    echo [ERROR] Failed to configure remote .env.
    exit /b 1
)
echo [OK] Environment configured.

REM -- Start ONLY tg_expense_bot containers ---------------------------
echo [5/5] Starting tg_expense_bot containers...
set REMOTE_CMD=cd %REMOTE_DIR% ^&^& docker compose -p tg_expense_bot up -d
if %BUILD% equ 1 set REMOTE_CMD=!REMOTE_CMD! --build

call :ssh_run "!REMOTE_CMD!"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Remote docker compose up failed. Check the droplet:
    echo         ssh -i %KEY_FILE% root@%PUBLIC_IP%
    exit /b 1
)

echo.
echo [OK] Sync complete!
if %BUILD% equ 1 echo [OK] Bot image built and started.

echo.
echo [INFO] Expense Bot stack status (%REMOTE_DIR%):
call :ssh_run "cd %REMOTE_DIR% && docker compose -p tg_expense_bot ps"

echo.
echo [INFO] All running containers on droplet %DROPLET_NAME% (verifying existing apps are untouched):
call :ssh_run "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'"

echo.
echo [INFO] Recent bot logs:
call :ssh_run "cd %REMOTE_DIR% && docker compose -p tg_expense_bot logs --tail=15 bot"

goto :sync_end

:ssh_run
set /a SSH_TRY=0
:ssh_run_loop
set /a SSH_TRY+=1
ssh %SSH_OPTS% root@%PUBLIC_IP% %* <nul
if %ERRORLEVEL% equ 0 exit /b 0
if %SSH_TRY% geq %SSH_RETRIES% exit /b 1
echo [WARN] SSH attempt %SSH_TRY%/%SSH_RETRIES% failed - retrying...
ping -n 5 127.0.0.1 >nul
goto :ssh_run_loop

:ssh_pipe
set /a SSH_TRY=0
:ssh_pipe_loop
set /a SSH_TRY+=1
type "%~1" | ssh %SSH_OPTS% root@%PUBLIC_IP% %2
if %ERRORLEVEL% equ 0 exit /b 0
if %SSH_TRY% geq %SSH_RETRIES% exit /b 1
echo [WARN] SSH attempt %SSH_TRY%/%SSH_RETRIES% failed - retrying...
ping -n 5 127.0.0.1 >nul
goto :ssh_pipe_loop

:sync_end
endlocal