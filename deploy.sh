#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Phase 5: Google Cloud Run Deployment Script
# Enterprise Revenue Recovery AI Agent
# ─────────────────────────────────────────────────────────────────
#
# Prerequisites:
#   1. gcloud CLI installed: https://cloud.google.com/sdk/docs/install
#   2. Authenticated: gcloud auth login
#   3. Project set: gcloud config set project YOUR_PROJECT_ID
#   4. APIs enabled: Cloud Run, Artifact Registry, Secret Manager
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# ─────────────────────────────────────────────────────────────────

set -e  # Exit on any error

# ── CONFIG — Update these before deploying ────────────────────────
SERVICE_NAME="enterprise-revenue-agent"
REGION="us-central1"
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Enterprise Revenue Recovery Agent — Cloud Run Deploy ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Project : ${PROJECT_ID}"
echo "  Service : ${SERVICE_NAME}"
echo "  Region  : ${REGION}"
echo "  Image   : ${IMAGE_NAME}"
echo ""

# ── STEP 1: Validate gcloud is configured ─────────────────────────
if [ -z "${PROJECT_ID}" ]; then
  echo "❌ ERROR: No GCP project set."
  echo "   Run: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

# ── STEP 2: Build & push container to Google Container Registry ───
echo "🔨 Building and pushing Docker image..."
gcloud builds submit \
  --tag "${IMAGE_NAME}" \
  --timeout=10m \
  .

echo "✅ Image pushed to: ${IMAGE_NAME}"

# ── STEP 3: Deploy to Cloud Run ───────────────────────────────────
echo ""
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_NAME}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 300 \
  --set-env-vars "GEMINI_MODEL=gemini-2.5-flash" \
  --set-env-vars "POSTGRES_URL=${POSTGRES_URL}" \
  --set-env-vars "REDIS_URL=${REDIS_URL}"

# ── STEP 4: Get the deployed URL ──────────────────────────────────
echo ""
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --format "value(status.url)")

echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ DEPLOYMENT COMPLETE                               ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  API URL     : ${SERVICE_URL}          "
echo "║  Health      : ${SERVICE_URL}/health   "
echo "║  API Docs    : ${SERVICE_URL}/docs     "
echo "║  Alerts API  : ${SERVICE_URL}/api/alerts"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "⚠️  Remember: Set GEMINI_API_KEY as a Secret Manager secret"
echo "   and reference it with --set-secrets flag for production!"
