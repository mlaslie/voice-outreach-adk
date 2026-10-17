#!/usr/bin/env bash
# Build images for the given services at HEAD and roll them out with Terraform.
#   scripts/deploy.sh demo gateway api ui
# Refuses a dirty tree (via build.sh); only touches the *_image lines in tfvars.
set -euo pipefail
ENV_NAME="${1:?usage: deploy.sh <env> <service>...}"; shift
[[ $# -gt 0 ]] || { echo "name at least one service: gateway api ui" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF="$ROOT/infra/envs/$ENV_NAME"
TAG="$("$ROOT/scripts/build.sh" "$ENV_NAME" "$@" | tail -1)"
[[ "$TAG" =~ ^[0-9a-f]{7,40}$ ]] || { echo "build did not return a commit tag: $TAG" >&2; exit 1; }
REPO="$(terraform -chdir="$TF" output -raw image_repository)"
for s in "$@"; do
  line="${s}_image = \"$REPO/voa-$s:$TAG\""
  if grep -q "^${s}_image *=" "$TF/terraform.tfvars"; then
    sed -i.bak "s#^${s}_image *=.*#${line}#" "$TF/terraform.tfvars" && rm -f "$TF/terraform.tfvars.bak"
  else
    echo "$line" >> "$TF/terraform.tfvars"
  fi
done
terraform -chdir="$TF" plan -input=false -out=tfplan
terraform -chdir="$TF" apply -input=false tfplan
echo "deployed $* at $TAG"
