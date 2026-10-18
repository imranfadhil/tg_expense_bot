@echo off
setlocal enabledelayedexpansion

REM ===================================================================
REM  backup.bat - Zero-downtime hot backup of remote PostgreSQL database
REM  Uses pg_dump inside the running tg_expense_db container to take a
REM  consistent MVCC snapshot without locking tables or stopping the bot.
REM
REM  Usage:
REM    backup.bat                 Download timestamped SQL dump to ..\backups\
REM    backup.bat --to-local-db   Also restore dump into local tg_expense_db container
REM ===================================================================

cd /d "%~dp0"
set DEPLOY_DIR=%CD%

if not exist "config.bat" (
  echo [backup] config.bat not found. Copy config.example.bat to config.bat first.
  exit /b 1
)
call config.bat

if "%POSTGRES_USER%"=="" set POSTGRES_USER=expense_user
if "%POSTGRES_DB%"=="" set POSTGRES_DB=family_expenses

echo.
echo ===============================================================
echo    TELEGRAM EXPENSE BOT - REMOTE POSTGRESQL BACKUP
echo ===============================================================
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

if not exist "%KEY_FILE%" (
    echo [ERROR] SSH key not found: %KEY_FILE%
    exit /b 1
)

set "SSH_OPTS=-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -i "%KEY_FILE%""

REM -- Generate local timestamp (YYYYMMDD_HHMMSS) ---------------------
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"') do set TIMESTAMP=%%t
set "BACKUP_DIR=%DEPLOY_DIR%\..\backups"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

set "BACKUP_FILE=%BACKUP_DIR%\expense_%TIMESTAMP%.sql"
set "LATEST_FILE=%BACKUP_DIR%\expense_latest.sql"

echo [1/3] Creating hot PostgreSQL snapshot on %DROPLET_NAME% (%PUBLIC_IP%)...
ssh %SSH_OPTS% root@%PUBLIC_IP% "if docker ps --format '{{.Names}}' | grep -q '^tg_expense_db$'; then docker exec tg_expense_db pg_dump -U '%POSTGRES_USER%' -d '%POSTGRES_DB%' --clean --if-exists --no-owner --no-privileges > '%REMOTE_DIR%/expense.backup.tmp.sql' && [ -s '%REMOTE_DIR%/expense.backup.tmp.sql' ]; else exit 2; fi"
if %ERRORLEVEL% equ 2 (
    echo [ERROR] Remote container tg_expense_db is not running on droplet.
    exit /b 1
)
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to create remote PostgreSQL dump.
    ssh %SSH_OPTS% root@%PUBLIC_IP% "rm -f '%REMOTE_DIR%/expense.backup.tmp.sql'" >nul 2>&1
    exit /b 1
)
echo [OK] Snapshot created.

echo [2/3] Downloading snapshot to local backups folder...
scp %SSH_OPTS% "root@%PUBLIC_IP%:%REMOTE_DIR%/expense.backup.tmp.sql" "%BACKUP_FILE%"
set SCP_RC=%ERRORLEVEL%

REM Clean up temporary snapshot on droplet
ssh %SSH_OPTS% root@%PUBLIC_IP% "rm -f '%REMOTE_DIR%/expense.backup.tmp.sql'" >nul 2>&1

if %SCP_RC% neq 0 (
    echo [ERROR] Failed to download database backup via scp.
    exit /b 1
)

copy /y "%BACKUP_FILE%" "%LATEST_FILE%" >nul
echo [OK] Saved timestamped backup: %BACKUP_FILE%
echo [OK] Updated latest pointer:   %LATEST_FILE%

echo [3/3] Verifying local PostgreSQL backup integrity...
for /f "delims=" %%s in ('powershell -NoProfile -Command "$f = Get-Item '%BACKUP_FILE%'; Write-Output ($f.Length.ToString() + ' bytes')"') do set FILE_SIZE=%%s
echo [OK] Backup file size: %FILE_SIZE%

if /i "%1"=="--to-local-db" goto :restore_local
if /i "%1"=="--to-local-data" goto :restore_local
goto :done

:restore_local
echo.
echo [INFO] Restoring backup into local tg_expense_db container...
docker ps --format "{{.Names}}" | findstr /i /x "tg_expense_db" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [WARN] Local container tg_expense_db is not running. Start it with: docker compose up -d db
    goto :done
)
type "%BACKUP_FILE%" | docker exec -i tg_expense_db psql -U "%POSTGRES_USER%" -d "%POSTGRES_DB%" >nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to restore backup into local tg_expense_db container.
    exit /b 1
)
echo [OK] Restored backup into local tg_expense_db container (%POSTGRES_DB%).

:done
echo.
echo ===============================================================
echo    BACKUP COMPLETE
echo    File:   %BACKUP_FILE%
echo    Latest: %LATEST_FILE%
echo ===============================================================

endlocal

