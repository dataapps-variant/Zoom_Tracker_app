@echo off
REM =============================================================================
REM Deploy Breakout Room Calibrator to Google Cloud Run (Windows)
REM =============================================================================

setlocal enabledelayedexpansion

REM Configuration
if "%GCP_PROJECT_ID%"=="" set GCP_PROJECT_ID=your-project-id
if "%GCP_REGION%"=="" set GCP_REGION=us-central1
set SERVICE_NAME=breakout-calibrator
set IMAGE_NAME=gcr.io/%GCP_PROJECT_ID%/%SERVICE_NAME%

echo ==========================================
echo Deploying Breakout Room Calibrator
echo ==========================================
echo Project: %GCP_PROJECT_ID%
echo Region: %GCP_REGION%
echo Service: %SERVICE_NAME%
echo ==========================================

REM Check if gcloud is installed
where gcloud >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Error: gcloud CLI not found.
    echo Install from https://cloud.google.com/sdk
    exit /b 1
)

REM Set project
gcloud config set project %GCP_PROJECT_ID%

REM Enable required APIs
echo Enabling Cloud Run and Container Registry APIs...
gcloud services enable run.googleapis.com containerregistry.googleapis.com

REM Build container
echo Building container...
gcloud builds submit --tag %IMAGE_NAME%

REM Deploy to Cloud Run
echo Deploying to Cloud Run...
gcloud run deploy %SERVICE_NAME% ^
    --image %IMAGE_NAME% ^
    --platform managed ^
    --region %GCP_REGION% ^
    --allow-unauthenticated ^
    --set-env-vars "NODE_ENV=production" ^
    --set-env-vars "MAIN_BACKEND_URL=%MAIN_BACKEND_URL%" ^
    --set-env-vars "ZOOM_CLIENT_ID=%ZOOM_CLIENT_ID%" ^
    --set-env-vars "ZOOM_CLIENT_SECRET=%ZOOM_CLIENT_SECRET%" ^
    --set-env-vars "REDIRECT_URL=%REDIRECT_URL%" ^
    --memory 512Mi ^
    --timeout 300

REM Get service URL
for /f "tokens=*" %%i in ('gcloud run services describe %SERVICE_NAME% --region %GCP_REGION% --format "value(status.url)"') do set SERVICE_URL=%%i

echo.
echo ==========================================
echo Deployment Complete!
echo ==========================================
echo Service URL: %SERVICE_URL%
echo.
echo IMPORTANT: Update your Zoom App settings:
echo   Home URL: %SERVICE_URL%
echo   Redirect URL: %SERVICE_URL%/auth/callback
echo ==========================================

endlocal
