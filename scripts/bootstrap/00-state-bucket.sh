#!/usr/bin/env bash
# One-time: create the GCS bucket that holds Terraform state for an environment,
# and write infra/envs/<env>/backend.hcl. Terraform cannot create its own state bucket.
#
#   scripts/bootstrap/00-state-bucket.sh demo
set -euo pipefail

ENV_NAME="${1:-demo}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_DIR="$ROOT/infra/envs/$ENV_NAME"
TFVARS="$ENV_DIR/terraform.tfvars"

[[ -f "$TFVARS" ]] || { echo "missing $TFVARS (copy terraform.tfvars.example)" >&2; exit 1; }
PROJECT_ID="$(sed -nE 's/^project_id *= *"([^"]+)".*/\1/p' "$TFVARS")"
REGION="$(sed -nE 's/^region *= *"([^"]+)".*/\1/p' "$TFVARS")"
REGION="${REGION:-us-central1}"
[[ -n "$PROJECT_ID" ]] || { echo "project_id not set in $TFVARS" >&2; exit 1; }

BUCKET="${PROJECT_ID}-tfstate"
# APIs Terraform itself needs before it can manage anything else.
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com storage.googleapis.com --project "$PROJECT_ID"

if gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "state bucket gs://$BUCKET already exists"
else
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT_ID" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
  gcloud storage buckets update "gs://$BUCKET" --versioning
fi

cat > "$ENV_DIR/backend.hcl" <<HCL
bucket = "$BUCKET"
prefix = "voice-outreach/$ENV_NAME"
HCL
echo "wrote $ENV_DIR/backend.hcl; next: (cd $ENV_DIR && terraform init -backend-config=backend.hcl)"
