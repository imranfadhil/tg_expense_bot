@echo off
REM ===================================================================
REM  DigitalOcean deployment config for Telegram Family Expense Bot
REM  Copy this file to  config.bat  and fill in your values.
REM  config.bat is gitignored so your secrets stay local.
REM
REM  Prereq: doctl installed & authenticated  ->  doctl auth init
REM ===================================================================

REM ---- Project (Isolated from other apps on the same droplet) ----
set PROJECT_NAME=tg-expense-bot
REM Isolated directory on the droplet where this bot lives
set REMOTE_DIR=/opt/tg_expense_bot

REM ---- Shared Droplet (Existing DigitalOcean Droplet) ----
set DROPLET_NAME=pemuda-surau-server
set REGION=sgp1
set SIZE=s-1vcpu-2gb
set IMAGE=docker-20-04

REM ---- SSH Key (Existing key authorized on the droplet) ----
set KEY_NAME=pemuda-surau-do-key
set KEY_FILE=%USERPROFILE%\.ssh\%KEY_NAME%
set PUB_FILE=%USERPROFILE%\.ssh\%KEY_NAME%.pub

REM ---- Telegram Expense Bot Config ----
REM Create a NEW bot with @BotFather -> /newbot and paste its token here
REM (or leave blank here to read BOT_TOKEN from the root .env file)
set BOT_TOKEN=

REM Default currency symbol for new households (e.g. $, RM, S$)
set DEFAULT_CURRENCY=RM

REM Timezone for monthly boundaries (e.g. Asia/Kuala_Lumpur or Asia/Singapore)
set TIMEZONE=Asia/Kuala_Lumpur

REM Internal PostgreSQL database settings (isolated inside tg_expense_db container)
set POSTGRES_USER=expense_user
set POSTGRES_DB=family_expenses