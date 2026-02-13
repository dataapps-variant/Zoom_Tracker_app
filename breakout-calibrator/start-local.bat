@echo off
REM =============================================================================
REM Local Development Startup Script (Windows)
REM =============================================================================
REM
REM This script starts:
REM   1. ngrok tunnel for HTTPS
REM   2. React app with HTTPS
REM   3. Backend server
REM
REM Prerequisites:
REM   - Node.js 16+ installed
REM   - ngrok installed and authenticated
REM   - npm install completed in both root and server folders

echo ==========================================
echo Breakout Room Calibrator - Local Dev
echo ==========================================

REM Check if .env exists
if not exist ".env" (
    echo.
    echo ERROR: .env file not found!
    echo Please copy .env.example to .env and fill in your credentials.
    echo.
    pause
    exit /b 1
)

REM Install dependencies if needed
if not exist "node_modules" (
    echo Installing React app dependencies...
    call npm install
)

if not exist "server\node_modules" (
    echo Installing server dependencies...
    cd server
    call npm install
    cd ..
)

echo.
echo Starting services...
echo.
echo [1] Starting ngrok (check ngrok window for URL)
echo [2] Starting React app at https://localhost:3000
echo [3] Starting backend server at http://localhost:3001
echo.
echo IMPORTANT: After ngrok starts, copy the HTTPS URL and update:
echo   1. Your .env file (REACT_APP_REDIRECT_URL)
echo   2. Zoom Marketplace app settings (Home URL, Redirect URL)
echo.

REM Start ngrok in new window
start "ngrok" cmd /k "ngrok http 3000"

REM Wait a moment for ngrok to start
timeout /t 3 /nobreak > nul

REM Start backend server in new window
start "Backend Server" cmd /k "cd server && npm start"

REM Start React app with HTTPS
echo Starting React app...
set HTTPS=true
npm start
