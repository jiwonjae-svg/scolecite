#!/usr/bin/env bash
# =============================================================================
# Project Scolecite — Cloud Run Deployment Script
# Usage: bash deploy/deploy.sh
# Prerequisites: gcloud CLI authenticated, project set
# =============================================================================
set -euo pipefail

# ---- Configuration (edit these) ----
PROJECT_ID="${GCP_PROJECT_ID:-your-gcp-project-id}"
REGION="asia-northeast3"
SERVICE_NAME="scolecite-bot"
REPO_NAME="scolecite"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}"
CLOUD_SQL_INSTANCE="${PROJECT_ID}:${REGION}:scolecite-db"
VPC_CONNECTOR="scolecite-vpc"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  Project Scolecite — Cloud Run Deployment           ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Project : ${PROJECT_ID}"
echo "║  Region  : ${REGION}"
echo "║  Service : ${SERVICE_NAME}"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ---- Step 0: Set project ----
echo "▸ Setting GCP project..."
gcloud config set project "${PROJECT_ID}"

# ---- Step 1: Enable required APIs ----
echo "▸ Enabling APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    vpcaccess.googleapis.com \
    compute.googleapis.com

# ---- Step 2: Create Artifact Registry repo ----
echo "▸ Creating Artifact Registry repository..."
gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Project Scolecite container images" \
    2>/dev/null || echo "  (already exists)"

# ---- Step 3: Create Cloud SQL PostgreSQL instance ----
echo "▸ Creating Cloud SQL instance (this may take 5-10 min)..."
gcloud sql instances describe scolecite-db --project="${PROJECT_ID}" &>/dev/null || \
gcloud sql instances create scolecite-db \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="${REGION}" \
    --storage-size=10GB \
    --storage-auto-increase \
    --availability-type=zonal \
    --backup-start-time=04:00 \
    --maintenance-window-day=SUN \
    --maintenance-window-hour=3

# Create database & user
echo "▸ Creating database and user..."
gcloud sql databases create scolecite --instance=scolecite-db 2>/dev/null || echo "  (database exists)"

DB_PASSWORD=$(openssl rand -base64 24)
gcloud sql users create scolecite \
    --instance=scolecite-db \
    --password="${DB_PASSWORD}" \
    2>/dev/null || echo "  (user exists — password NOT changed)"

# ---- Step 4: Create Secrets in Secret Manager ----
echo "▸ Setting up Secret Manager..."
declare -a SECRETS=(
    "ANTHROPIC_API_KEY"
    "XAI_GROK_API_KEY"
    "POLYGON_API_KEY"
    "APCA_API_KEY_ID"
    "APCA_API_SECRET_KEY"
    "DATABASE_URL"
)

for SECRET in "${SECRETS[@]}"; do
    gcloud secrets describe "${SECRET}" --project="${PROJECT_ID}" &>/dev/null || \
    gcloud secrets create "${SECRET}" --replication-policy=automatic
done

# Store DATABASE_URL (unix socket for Cloud Run)
DB_URL="postgresql+asyncpg://scolecite:${DB_PASSWORD}@/scolecite?host=/cloudsql/${CLOUD_SQL_INSTANCE}"
echo -n "${DB_URL}" | gcloud secrets versions add DATABASE_URL --data-file=-

echo ""
echo "⚠  Set your API keys manually (one-time):"
echo "   echo -n 'sk-...' | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-"
echo "   echo -n 'xai-...' | gcloud secrets versions add XAI_GROK_API_KEY --data-file=-"
echo "   echo -n 'pk-...' | gcloud secrets versions add POLYGON_API_KEY --data-file=-"
echo "   echo -n 'PK...'  | gcloud secrets versions add APCA_API_KEY_ID --data-file=-"
echo "   echo -n 'SK...'  | gcloud secrets versions add APCA_API_SECRET_KEY --data-file=-"
echo ""

# ---- Step 5: Create VPC Connector (for Cloud SQL private IP) ----
echo "▸ Creating Serverless VPC Access connector..."
gcloud compute networks vpc-access connectors describe "${VPC_CONNECTOR}" \
    --region="${REGION}" &>/dev/null || \
gcloud compute networks vpc-access connectors create "${VPC_CONNECTOR}" \
    --region="${REGION}" \
    --network=default \
    --range=10.8.0.0/28 \
    --min-instances=2 \
    --max-instances=3 \
    --machine-type=e2-micro

# ---- Step 6: Grant Cloud Run SA access to secrets ----
echo "▸ Granting IAM permissions..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for SECRET in "${SECRETS[@]}"; do
    gcloud secrets add-iam-policy-binding "${SECRET}" \
        --member="serviceAccount:${SA}" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet
done

# Cloud SQL Client role
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA}" \
    --role="roles/cloudsql.client" \
    --quiet

# ---- Step 7: Build & Push Docker image ----
echo "▸ Building and pushing Docker image..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

docker build -t "${IMAGE}:latest" .
docker push "${IMAGE}:latest"

# ---- Step 8: Deploy to Cloud Run ----
echo "▸ Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image="${IMAGE}:latest" \
    --region="${REGION}" \
    --platform=managed \
    --allow-unauthenticated \
    --port=8000 \
    --concurrency=80 \
    --cpu=2 \
    --memory=2Gi \
    --min-instances=0 \
    --max-instances=3 \
    --timeout=300 \
    --set-env-vars="TRADING_MODE=paper,ENABLE_PROMPT_CACHING=true" \
    --set-secrets="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,XAI_GROK_API_KEY=XAI_GROK_API_KEY:latest,POLYGON_API_KEY=POLYGON_API_KEY:latest,APCA_API_KEY_ID=APCA_API_KEY_ID:latest,APCA_API_SECRET_KEY=APCA_API_SECRET_KEY:latest,DATABASE_URL=DATABASE_URL:latest" \
    --add-cloudsql-instances="${CLOUD_SQL_INSTANCE}" \
    --vpc-connector="${VPC_CONNECTOR}" \
    --vpc-egress=all-traffic \
    --startup-cpu-boost \
    --execution-environment=gen2

# ---- Done ----
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format='value(status.url)')
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ Deployment Complete!                             ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  URL    : ${SERVICE_URL}"
echo "║  Health : ${SERVICE_URL}/health"
echo "║  API    : ${SERVICE_URL}/api/status"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "▸ Post-deployment checklist:"
echo "  1. curl ${SERVICE_URL}/health"
echo "  2. Check Cloud Run logs: gcloud run services logs read ${SERVICE_NAME} --region=${REGION}"
echo "  3. Verify DB migration: curl ${SERVICE_URL}/api/status"
echo "  4. Set API keys in Secret Manager (see above)"
echo "  5. Connect desktop client to ${SERVICE_URL}"
