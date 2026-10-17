@echo off
setlocal enabledelayedexpansion

REM ===================================================================
REM  destroy.bat - Tear down ONLY the Telegram Expense Bot stack
REM  SAFE FOR SHARED DROPLETS: This script NEVER deletes the droplet
REM  or SSH key. It only stops and removes the tg_expense_bot containers,
REM  volumes, and %REMOTE_DIR% (/opt/tg_expense_bot), leaving all other
REM  running containers on %DROPLET_NAME% completely untouched.
REM ===================================================================

cd /d "%~dp0"

if not exist "config.bat" (
  echo [destroy] config.bat not found.
  exit /b 1
)
call config.bat

echo.
echo ===============================================================
echo    REMOVE TELEGRAM EXPENSE BOT FROM %DROPLET_NAME%
echo    Target Directory: %REMOTE_DIR%
echo    NOTE: The droplet itself and other apps (/opt/pemuda_surau)
echo          will NOT be touched.
echo ===============================================================
echo.

set /p CONFIRM="Type REMOVE to stop and delete %REMOTE_DIR% (and its DB volume): "
if not "%CONFIRM%"=="REMOVE" (
    echo Aborted.
    exit /b 0
)

set PUBLIC_IP=
if exist "%TEMP%\%PROJECT_NAME%-do-ip.txt" set /p PUBLIC_IP=<"%TEMP%\%PROJECT_NAME%-do-ip.txt"
if "%PUBLIC_IP%"=="" (
    for /f %%i in ('doctl compute droplet get %DROPLET_NAME% --format PublicIPv4 --no-header 2^>nul') do set PUBLIC_IP=%%i
)
if "%PUBLIC_IP%"=="" (
    echo [ERROR] Could not resolve droplet IP for %DROPLET_NAME%.
    exit /b 1
)

set "SSH_OPTS=-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -i "%KEY_FILE%""

echo [1/2] Stopping and removing tg_expense_bot containers and volume...
ssh %SSH_OPTS% root@%PUBLIC_IP% "if [ -d '%REMOTE_DIR%' ]; then cd '%REMOTE_DIR%' && docker compose -p tg_expense_bot down -v --rmi local && cd / && rm -rf '%REMOTE_DIR%'; fi"
if %ERRORLEVEL% neq 0 (
    echo [WARN] Failed to cleanly remove remote stack. Check via SSH.
    exit /b 1
)
echo [OK] Removed %REMOTE_DIR% and tg_expense_bot containers/volumes.

echo.
echo [2/2] Remaining containers still running on %DROPLET_NAME%:
ssh %SSH_OPTS% root@%PUBLIC_IP% "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'"

del "%TEMP%\%PROJECT_NAME%-do-ip.txt" 2>nul

echo.
echo ===============================================================
echo    TEARDOWN COMPLETE (Shared droplet & other apps preserved)
echo ===============================================================

endlocal