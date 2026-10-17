@echo off
setlocal enabledelayedexpansion

REM ===================================================================
REM  deploy.bat - Deploy Telegram Family Expense Bot to DigitalOcean
REM  Reuses existing droplet (%DROPLET_NAME%) if present, and deploys
REM  the bot into an isolated directory (%REMOTE_DIR%) and isolated
REM  Docker Compose project (tg_expense_bot).
REM ===================================================================

cd /d "%~dp0"
set DEPLOY_DIR=%CD%

if not exist "config.bat" (
  echo [deploy] config.bat not found - copying from config.example.bat
  copy /y "%DEPLOY_DIR%\config.example.bat" "%DEPLOY_DIR%\config.bat" >nul
  echo [deploy] Please set BOT_TOKEN in deploy\config.bat ^(or root .env^), then re-run deploy.bat.
  exit /b 1
)
call config.bat

echo.
echo ===============================================================
echo    TELEGRAM FAMILY EXPENSE BOT - DIGITALOCEAN DEPLOY
echo ===============================================================
echo.

REM -- Prerequisites --------------------------------------------------
where doctl >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] doctl not found. Install: https://docs.digitalocean.com/reference/doctl/how-to/install/
    exit /b 1
)
doctl account get >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] doctl not authenticated. Run: doctl auth init
    exit /b 1
)
echo [OK] doctl authenticated.

REM -- Step 1: Ensure local SSH key + register with DO ----------------
echo.
echo [1/4] Checking SSH key (%KEY_FILE%)...
if not exist "%KEY_FILE%" (
    mkdir "%USERPROFILE%\.ssh" 2>nul
    ssh-keygen -t ed25519 -N "" -f "%KEY_FILE%" -C "%KEY_NAME%"
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] ssh-keygen failed.
        exit /b 1
    )
    echo [OK] Key generated: %KEY_FILE%
) else (
    echo [OK] Local key exists: %KEY_FILE%
)

set KEY_FP=
for /f "tokens=1,2" %%a in ('doctl compute ssh-key list --format Name^,FingerPrint --no-header') do (
    if /i "%%a"=="%KEY_NAME%" set KEY_FP=%%b
)
if not "%KEY_FP%"=="" (
    echo [OK] SSH key registered with DO ^(by name^).
) else (
    for /f "tokens=2" %%f in ('ssh-keygen -lf "%PUB_FILE%" -E md5') do set LOCAL_FP=%%f
    set LOCAL_FP=!LOCAL_FP:MD5:=!
    for /f "tokens=1,2" %%a in ('doctl compute ssh-key list --format Name^,FingerPrint --no-header') do (
        if /i "%%b"=="!LOCAL_FP!" set KEY_FP=%%b
    )
    if not "!KEY_FP!"=="" (
        echo [OK] SSH key registered with DO ^(matched by fingerprint^).
    ) else (
        doctl compute ssh-key import %KEY_NAME% --public-key-file "%PUB_FILE%" >nul
        if !ERRORLEVEL! neq 0 (
            echo [ERROR] Failed to import SSH key to DO.
            exit /b 1
        )
        for /f "tokens=1,2" %%a in ('doctl compute ssh-key list --format Name^,FingerPrint --no-header') do (
            if /i "%%a"=="%KEY_NAME%" set KEY_FP=%%b
        )
    )
)

REM -- Step 2: Check existing droplet (or create if new) --------------
echo.
echo [2/4] Checking target droplet (%DROPLET_NAME%)...

set EXISTING_DROPLET=0
doctl compute droplet get %DROPLET_NAME% >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set EXISTING_DROPLET=1
    echo [OK] Reusing existing droplet: %DROPLET_NAME% ^(existing containers will NOT be touched^)
    goto :get_ip
)

echo [INFO] Droplet %DROPLET_NAME% not found - creating new droplet...
doctl compute droplet create %DROPLET_NAME% ^
    --region %REGION% ^
    --size %SIZE% ^
    --image %IMAGE% ^
    --ssh-keys %KEY_FP% ^
    --wait
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Droplet creation failed.
    exit /b 1
)
echo [OK] Droplet created: %DROPLET_NAME%

:get_ip
echo.
echo [3/4] Resolving public IP...
for /f %%i in ('doctl compute droplet get %DROPLET_NAME% --format PublicIPv4 --no-header') do set PUBLIC_IP=%%i
if "%PUBLIC_IP%"=="" (
    echo [ERROR] Could not resolve droplet public IP.
    exit /b 1
)
echo [OK] Public IP: %PUBLIC_IP%
echo %PUBLIC_IP% > "%TEMP%\%PROJECT_NAME%-do-ip.txt"

if %EXISTING_DROPLET% equ 0 (
    echo.
    echo [INFO] Waiting 45s for newly created droplet to finish booting...
    timeout /t 45 /nobreak >nul
)

REM -- Step 4: Push code and start isolated stack ---------------------
echo.
echo [4/4] Deploying Telegram Expense Bot to %REMOTE_DIR%...
call "%DEPLOY_DIR%\sync.bat" --from-deploy
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Deployment sync failed.
    exit /b 1
)

echo.
echo ===============================================================
echo    TELEGRAM EXPENSE BOT DEPLOY COMPLETE
echo ===============================================================
echo    Droplet:     %DROPLET_NAME% (%PUBLIC_IP%)
echo    Remote Dir:  %REMOTE_DIR%
echo    Containers:  tg_expense_bot, tg_expense_db
echo    SSH:         ssh -i "%KEY_FILE%" root@%PUBLIC_IP%
echo    Logs:        ssh -i "%KEY_FILE%" root@%PUBLIC_IP% "cd %REMOTE_DIR% && docker compose logs -f bot"
echo ===============================================================

endlocal