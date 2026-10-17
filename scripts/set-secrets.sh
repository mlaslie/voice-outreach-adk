#!/usr/bin/env bash
# Load Twilio credentials into Secret Manager and the caller ID into tfvars.
# See scripts/set_secrets.py for sources, shape checks and options.
#
#   scripts/set-secrets.sh <project-id> --from-env <path/to/.env> \
#       --auth-token-from projects/<old-project>/secrets/<auth-token-secret> \
#       --tfvars infra/envs/demo/terraform.tfvars
set -euo pipefail
exec python3 "$(dirname "$0")/set_secrets.py" "$@"
