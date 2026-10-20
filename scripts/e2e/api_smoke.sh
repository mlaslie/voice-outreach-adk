#!/usr/bin/env bash
# Deployed API smoke test, calling the private API exactly as the consoles do:
# configuration as the admin console, clinical work as the nurse dashboard (ID tokens
# minted by impersonating each SA; needs `operators` in tfvars). It also checks the
# route policy: the nurse dashboard must be refused a configuration change.
#   scripts/e2e/api_smoke.sh demo
set -euo pipefail
ENV_NAME="${1:-demo}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF="$ROOT/infra/envs/$ENV_NAME"
API="$(terraform -chdir="$TF" output -raw api_url)"
UI_SA="$(terraform -chdir="$TF" output -json service_accounts | python3 -c 'import json,sys;print(json.load(sys.stdin)["voa-ui"])')"
ADMIN_SA="$(terraform -chdir="$TF" output -json service_accounts | python3 -c 'import json,sys;print(json.load(sys.stdin)["voa-admin"])')"
mint() { gcloud auth print-identity-token --impersonate-service-account="$1" --audiences="$API" --include-email 2>/dev/null; }
TOKEN="$(mint "$UI_SA")" || { echo "FAIL could not mint a token as $UI_SA (operators need tokenCreator)"; exit 1; }
ADMIN_TOKEN="$(mint "$ADMIN_SA")" || { echo "FAIL could not mint a token as $ADMIN_SA (operators need tokenCreator; new IAM grants take a few minutes)"; exit 1; }
H=(-H "Authorization: Bearer $TOKEN" -H "X-Acting-User: smoke-test" -H "Content-Type: application/json")
HA=(-H "Authorization: Bearer $ADMIN_TOKEN" -H "X-Acting-User: smoke-test" -H "Content-Type: application/json")
code() { curl -s -o /tmp/voa_smoke_body -w '%{http_code}' "${H[@]}" "$@"; }    # as the nurse dashboard
acode() { curl -s -o /tmp/voa_smoke_body -w '%{http_code}' "${HA[@]}" "$@"; } # as the admin console
fail=0
check() { local want=$1 got=$2 what=$3; if [[ "$got" == "$want" ]]; then echo "ok   $what ($got)"; else echo "FAIL $what: want $want got $got"; cat /tmp/voa_smoke_body; echo; fail=1; fi; }

check 200 "$(code "$API/api/calls?limit=5")" "GET /api/calls"
check 200 "$(code "$API/api/call-settings")" "GET /api/call-settings"
check 403 "$(code -X POST "$API/api/allowlist" -d '{"phone":"+12025550177","label":"smoke"}')" "nurse dashboard refused a configuration change"
check 201 "$(acode -X POST "$API/api/allowlist" -d '{"phone":"+12025550177","label":"smoke"}')" "POST /api/allowlist (admin)"
check 201 "$(acode -X POST "$API/api/call-list" -d '{"phone":"+12025550177","patient_name":"Smoke Test","patient_id":"SMOKE-1","patient_dob":"1950-03-15","is_diabetic":false}')" "POST /api/call-list"
ENTRY="$(python3 -c 'import json;print(json.load(open("/tmp/voa_smoke_body"))["entry_id"])')"
check 422 "$(acode -X POST "$API/api/call-list" -d '{"phone":"+12025550177","patient_name":"X","patient_id":"S","patient_dob":"1950-13-45"}')" "invalid DOB rejected"
# Never dials: this number is not on the allowlist (it is removed again below before
# any place-call), so the dialer must refuse before reaching the provider. 403 =
# blocked by allowlist; 503 = telephony not configured. Both are fail-closed.
acode -X DELETE "$API/api/allowlist/%2B12025550177" >/dev/null
got="$(code -X POST "$API/api/place-call" -d '{"phone":"+12025550177","patient_name":"Smoke Test","patient_id":"SMOKE-1","patient_dob":"1950-03-15"}')"
if [[ "$got" == 403 || "$got" == 503 ]]; then echo "ok   place-call to a non-allowlisted number refused ($got)"; else echo "FAIL place-call not refused: $got"; fail=1; fi
check 200 "$(code -X POST "$API/api/test-call" -d "{\"entry_id\":\"$ENTRY\"}")" "POST /api/test-call"
SOFTPHONE="$(python3 -c 'import json;print(json.load(open("/tmp/voa_smoke_body"))["softphone_url"])')"
check 200 "$(acode -X DELETE "$API/api/call-list/$ENTRY")" "DELETE /api/call-list/{id} (admin)"
check 201 "$(acode -X POST "$API/api/allowlist" -d '{"phone":"+12025550177","label":"smoke"}')" "POST /api/allowlist (again)"
check 200 "$(acode -X DELETE "$API/api/allowlist/%2B12025550177")" "DELETE /api/allowlist/{phone} (admin)"
echo "softphone conversation via API-minted token:"
(cd "$ROOT" && uv run python scripts/e2e/softphone_smoke.py "$SOFTPHONE" 2>&1 | grep -E "^(caller|first|SMOKE)") || fail=1
if [[ $fail == 0 ]]; then echo "API SMOKE: ALL CHECKS PASSED"; else echo "API SMOKE: FAILED (see FAIL lines above)"; fi
exit $fail
