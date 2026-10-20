# Deploying into a new environment

This takes an empty GCP project to a working system. Everything is Terraform, except for a few one-time scripts that Terraform can't do well.

## 0. Prerequisites

- **Google Cloud:** a GCP project with billing enabled, and Owner (or equivalent) on it for the person running Terraform.
- **Tools:** `gcloud`, `terraform >= 1.9`, `uv`, `node 22+` (UI only), `git`.
- **Logged in:** run `gcloud auth login` and `gcloud auth application-default login`.
- **Telephony:**
  - *Real phone calls* (optional): a Twilio account with a voice number. SMS also needs an A2P 10DLC campaign that registers the copy in `packages/voa_core/src/voa_core/copy.py`.
  - *Without Twilio*: skip every Twilio step. The **"Test in browser"** button and `scripts/e2e/*` exercise the full voice path through the gateway's softphone, so you can evaluate the system with only a GCP project.

## 1. Local config (gitignored)

```bash
cp config/site.example.yaml config/site.yaml            # gcp.project_id, org name, timezone
cp infra/envs/demo/terraform.tfvars.example infra/envs/demo/terraform.tfvars
```

Set these in `terraform.tfvars`:

| Variable | Required | What it is |
|---|---|---|
| `project_id` | yes | The target project |
| `iap_members` | yes | Who may open the nurse dashboard, e.g. `["user:nurse@example.org"]` |
| `iap_admin_members` | for the admin console | Who may open the admin console (configuration) |
| `operators` | no | Who may impersonate the dashboard SA for e2e tests and local UI development |
| `alert_emails` | no | Who gets alert e-mails |
| `org_name`, `support_email`, `clinic_timezone` | no | Branding and clinic timezone |
| `twilio_from_number` | no | Caller ID (E.164) |
| `static_allowlist` | no | Numbers that are always dialable. Normally left empty; manage numbers in the dashboard. |
| `billing_account_id` | no | Adds a monthly budget alert |
| `protect_data = false` | no | Throwaway projects only: lets `terraform destroy` remove data |

## 2. Base infrastructure

```bash
make tf-bootstrap ENV=demo     # state bucket + backend.hcl; enables the APIs Terraform itself needs
make tf-init tf-plan ENV=demo
make tf-apply ENV=demo         # APIs, SAs, Firestore (nam5, PITR, backups), buckets, secrets, queues, monitoring
```

Firestore's location is permanent. Choose `firestore_location` before the first apply.

## 3. Secrets (never in git, never in Terraform state)

```bash
# Softphone HMAC key: random, generated once
python3 -c "import secrets;print(secrets.token_hex(32),end='')" | \
  gcloud secrets versions add softphone-token-key --project <project> --data-file=-
# Twilio (skip for a browser-only evaluation)
scripts/set-secrets.sh <project> --from-env <path/to/.env> \
    --auth-token-from projects/<other-project>/secrets/<name> \
    --tfvars infra/envs/demo/terraform.tfvars   # shape-checked; --dry-run to preview; prompts for anything missing
```

Until the Twilio secrets exist, the system fails closed:

- Twilio webhooks return 403.
- `POST /api/place-call` returns 503.
- The `no_twilio_token` alert fires if Twilio calls the gateway.

## 4. Build and deploy the services

```bash
git status                           # must be clean: images are tagged with the commit
scripts/deploy.sh demo gateway api ui admin nurse
```

`deploy.sh` does four things in order:

1. builds the images in Cloud Build as the `voa-build` SA;
2. writes `*_image` into `terraform.tfvars`;
3. plans;
4. applies.

## 5. Verify

```bash
scripts/e2e/api_smoke.sh demo         # private API as the dashboard calls it + a spoken softphone call
uv run python scripts/e2e/softphone_smoke.py "$(terraform -chdir=infra/envs/demo output -raw gateway_url)"
```

Then open `terraform -chdir=infra/envs/demo output -raw ui_url` and sign in as one of the `iap_members`.

## 6. Twilio wiring (real calls)

| Item | Setting |
|---|---|
| Outbound calls | Nothing to configure on the number. Every call carries its own answer and status URLs, pointing at the gateway. |
| Inbound SMS | Set the number's (or Messaging Service's) incoming-message webhook to `POST <gateway_url>/twilio/sms`. A number has exactly one SMS webhook, so use a number that no other system needs (see PLAN.md §11.9). |
| Allowlist | Add the test numbers in the dashboard (Allowlist dialog), or seed them with `config/seed/allowlist.yaml`. |
| SMS sending | Keep `VOA__SMS__DRY_RUN=true` until the A2P campaign and copy are confirmed. Then set it to `false` (Terraform env) and redeploy the API. |

## 7. Go-live checklist (beyond the demo)

These are listed in PLAN.md §11; none of them are done by this repo:

- [ ] BAAs with Google and Twilio
- [ ] Recording disclosure confirmed by counsel
- [ ] Calling windows and recall policy reviewed
- [ ] Clinical sign-off on the triage rules and blood-sugar thresholds (`voa_core/survey.py`)
- [ ] Data Access audit logs reviewed (enabled by Terraform)
- [ ] Retention policy agreed for the recordings bucket (`recordings_retention_days`)
