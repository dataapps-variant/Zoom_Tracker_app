# ==============================================================================
# DOCKERFILE FOR CLOUD RUN
# ==============================================================================
#
# WHAT THIS DOES:
# 1. Uses Python 3.11 as base image (lightweight "slim" version)
# 2. Copies your code into the container
# 3. Installs dependencies (flask, bigquery, etc.)
# 4. Runs the webhook server with gunicorn
#
# WHY DOCKER:
# Cloud Run runs containers. Docker packages your code + dependencies
# into a single unit that runs the same everywhere.
#
# WHY GUNICORN:
# Production-grade web server. Flask's built-in server is for development only.
# Gunicorn handles multiple requests efficiently.

# Base image - Python 3.11 slim (smaller size, faster deployment)
FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Copy requirements first (for Docker caching)
# WHY: If requirements don't change, Docker reuses cached layer = faster builds
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code
COPY . .

# Cloud Run sets PORT environment variable
# WHY: Cloud Run assigns dynamic ports, we must use $PORT
ENV PORT=8080

# Run with gunicorn
# --bind :$PORT  = Listen on the port Cloud Run assigns
# --workers 1    = Single worker (Cloud Run scales by adding containers, not workers)
# --threads 8    = Handle 8 concurrent requests per container
# --timeout 0    = No timeout (webhooks can be slow)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 zoom_webhook_bigquery:app
