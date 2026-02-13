#!/bin/bash
# =============================================================================
# Deploy Breakout Room Calibrator to Google Cloud Run
# =============================================================================

set -e

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-your-project-id}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="breakout-calibrator"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "=========================================="
echo "Deploying Breakout Room Calibrator"
echo "=========================================="
echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo "Service: ${SERVICE_NAME}"
echo "=========================================="

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI not found. Install from https://cloud.google.com/sdk"
    exit 1
fi

# Ensure logged in
gcloud auth print-access-token > /dev/null 2>&1 || {
    echo "Please login to gcloud:"
    gcloud auth login
}

# Set project
gcloud config set project ${PROJECT_ID}

# Enable required APIs
echo "Enabling Cloud Run and Container Registry APIs..."
gcloud services enable run.googleapis.com containerregistry.googleapis.com

# Build container
echo "Building container..."
gcloud builds submit --tag ${IMAGE_NAME}

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_NAME} \
    --platform managed \
    --region ${REGION} \
    --allow-unauthenticated \
    --set-env-vars "NODE_ENV=production" \
    --set-env-vars "MAIN_BACKEND_URL=${MAIN_BACKEND_URL:-}" \
    --set-env-vars "ZOOM_CLIENT_ID=${ZOOM_CLIENT_ID:-}" \
    --set-env-vars "ZOOM_CLIENT_SECRET=${ZOOM_CLIENT_SECRET:-}" \
    --set-env-vars "REDIRECT_URL=${REDIRECT_URL:-}" \
    --memory 512Mi \
    --timeout 300

# Get service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format 'value(status.url)')

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo "Service URL: ${SERVICE_URL}"
echo ""
echo "IMPORTANT: Update your Zoom App settings with these URLs:"
echo "  Home URL: ${SERVICE_URL}"
echo "  Redirect URL: ${SERVICE_URL}/auth/callback"
echo "=========================================="
