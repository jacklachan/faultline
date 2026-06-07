#!/usr/bin/env bash
# Build + deploy the Faultline server (agent + FastAPI + web console) to
# Cloud Run.
#
# Required env (or .env):
#   GOOGLE_CLOUD_PROJECT
#   GOOGLE_CLOUD_REGION
#   GITLAB_URL                (defaults to https://gitlab.com)
#   GITLAB_PROJECT_PATH
#   GITLAB_TOKEN
#   VERTEX_AI_MODEL           (defaults to gemini-2.5-flash)
#
# Runs from Git Bash / WSL on Windows, or any *nix shell with gcloud.

set -euo pipefail

# Load .env if present so the user does not have to re-export every var.
if [[ -f .env ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

: "${GOOGLE_CLOUD_PROJECT:?GOOGLE_CLOUD_PROJECT must be set (in .env or env)}"
: "${GOOGLE_CLOUD_REGION:?GOOGLE_CLOUD_REGION must be set}"
: "${GITLAB_PROJECT_PATH:?GITLAB_PROJECT_PATH must be set}"
: "${GITLAB_TOKEN:?GITLAB_TOKEN must be set}"

GITLAB_URL="${GITLAB_URL:-https://gitlab.com}"
VERTEX_AI_MODEL="${VERTEX_AI_MODEL:-gemini-2.5-flash}"
SERVICE_NAME="faultline"
REPO="faultline"
AR_HOST="${GOOGLE_CLOUD_REGION}-docker.pkg.dev"

if git rev-parse --short HEAD >/dev/null 2>&1; then
  IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"
else
  IMAGE_TAG="${IMAGE_TAG:-manual}"
fi

IMAGE="${AR_HOST}/${GOOGLE_CLOUD_PROJECT}/${REPO}/server:${IMAGE_TAG}"

if ! gcloud artifacts repositories describe "${REPO}" \
      --location="${GOOGLE_CLOUD_REGION}" >/dev/null 2>&1; then
  echo ">>> creating Artifact Registry repo ${REPO} in ${GOOGLE_CLOUD_REGION}"
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${GOOGLE_CLOUD_REGION}" \
    --description="Faultline images"
fi

echo ">>> building ${IMAGE}"
gcloud builds submit . --config=cloudbuild.yaml --substitutions="_IMAGE=${IMAGE}"

echo ">>> deploying ${SERVICE_NAME}"
gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE}" \
  --region="${GOOGLE_CLOUD_REGION}" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --memory=512Mi \
  --cpu=1 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GOOGLE_CLOUD_REGION=${GOOGLE_CLOUD_REGION},GOOGLE_GENAI_USE_VERTEXAI=true,VERTEX_AI_MODEL=${VERTEX_AI_MODEL},GITLAB_URL=${GITLAB_URL},GITLAB_PROJECT_PATH=${GITLAB_PROJECT_PATH},GITLAB_TOKEN=${GITLAB_TOKEN},FAULTLINE_FAKE_TELEMETRY=0"

URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region="${GOOGLE_CLOUD_REGION}" --format='value(status.url)')

echo
echo "Faultline server deployed: ${URL}"
echo "Open it in a browser to start an investigation."
