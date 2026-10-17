#!/usr/bin/env bash
# Build service images in Cloud Build, tagged with the current commit.
#   scripts/build.sh demo [gateway api ui]
# Refuses a dirty tree so every running image maps to a commit (review I-2).
set -euo pipefail
ENV_NAME="${1:-demo}"; shift || true
SERVICES="${*:-gateway api ui}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -n "$(git status --porcelain)" && "${ALLOW_DIRTY:-0}" != 1 ]]; then
  echo "working tree is dirty; commit first (or ALLOW_DIRTY=1 for a throwaway build)" >&2; exit 1
fi
TAG="$(git rev-parse --short=12 HEAD)"
TF="infra/envs/$ENV_NAME"
PROJECT_ID="$(sed -nE 's/^project_id *= *"([^"]+)".*/\1/p' "$TF/terraform.tfvars")"
REPO="$(terraform -chdir="$TF" output -raw image_repository)"
STAGING="$(terraform -chdir="$TF" output -raw build_staging_bucket)"
BUILD_SA="$(terraform -chdir="$TF" output -json service_accounts | python3 -c 'import json,sys;print(json.load(sys.stdin)["voa-build"])')"
site() { uv run --quiet python -c "from voa_core.config import load_config;c=load_config();print($1)"; }
REGION="$(sed -nE 's/^region *= *"([^"]+)".*/\1/p' "$TF/terraform.tfvars")"; REGION="${REGION:-us-central1}"
gcloud builds submit . --project "$PROJECT_ID" --region "$REGION" \
  --config cloudbuild.yaml \
  --service-account "projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
  --gcs-source-staging-dir "gs://$STAGING/source" \
  --substitutions "_REPO=$REPO,_TAG=$TAG,_SERVICES=$SERVICES,_ORG_NAME=$(site c.site.org_name),_SUPPORT_EMAIL=$(site c.site.support_email),_PRIVACY_URL=$(site c.site.privacy_url),_TERMS_URL=$(site c.site.terms_url)"
echo "$TAG"
