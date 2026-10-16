#!/usr/bin/env bash
# Add secret VERSIONS (values) to the empty secrets Terraform created.
# Values are read from the environment or prompted for, never from files in the repo.
#
#   scripts/set-secrets.sh <project-id>
#   TWILIO_AUTH_TOKEN=... scripts/set-secrets.sh <project-id>   # non-interactive
set -euo pipefail

PROJECT_ID="${1:?usage: set-secrets.sh <project-id>}"
SECRETS=(twilio-account-sid twilio-api-key-sid twilio-api-key-secret twilio-auth-token)

for name in "${SECRETS[@]}"; do
  var="$(echo "$name" | tr 'a-z-' 'A-Z_')"
  value="${!var:-}"
  if [[ -z "$value" ]]; then
    read -r -s -p "$name (empty to skip): " value; echo
  fi
  [[ -z "$value" ]] && { echo "skipped $name"; continue; }
  printf '%s' "$value" | gcloud secrets versions add "$name" --project "$PROJECT_ID" --data-file=- >/dev/null
  echo "added a version to $name"
done
