# voice-outreach-custom-adk

Outbound voice and SMS outreach for post-operative follow-up, built on
**Google ADK** and **Gemini Live**, deployed on **Cloud Run** with **Terraform**.
A from-scratch replacement for a CX-Agent-Studio + Twilio proxy system.

> Status: **phase 0** (bootstrap + spikes). See [`docs/PLAN.md`](docs/PLAN.md).

## Layout

| Path | What |
|---|---|
| `packages/voa_core` | config loader, structured logging + PII masking, domain logic |
| `packages/voa_providers` | telephony interfaces + adapters (`twilio`, `browser`, `fake`), audio transcoding |
| `packages/voa_agents` | ADK agents (voice first), tools, prompts |
| `spikes/voice` | phase-0 voice spike: bridge, harness, spike server, softphone |
| `infra/` | Terraform modules and per-environment roots |
| `config/` | layered YAML config; identifying values only in gitignored files |
| `scripts/` | PII scanner, bootstrap and secret helpers |

## Quick start

```bash
make setup                       # uv sync + git hooks (PII scan, gitleaks, ruff)
cp config/site.example.yaml config/site.yaml   # set gcp.project_id
make test
```

Talk to the agent without a phone line:

```bash
gcloud auth application-default login
make spike-serve                 # open http://localhost:8080/softphone
```

Run the simulated-patient scenarios (real Gemini Live calls, ~2 min each):

```bash
make spike-harness
```

## Deploying infrastructure

```bash
cp infra/envs/demo/terraform.tfvars.example infra/envs/demo/terraform.tfvars   # edit
make tf-bootstrap tf-init tf-plan
make tf-apply
scripts/set-secrets.sh <project-id>   # Twilio credentials -> Secret Manager
```

## Keeping the repository free of PII

Identifying values (org names, project ids, phone numbers, emails) live only in
gitignored files; each has a committed `*.example*` twin with fictional values
(phone numbers in the reserved 555-0100…0199 block). `scripts/pii_scan.py` runs on
every commit and in CI against a private denylist (`config/pii-denylist.txt`
locally, the `PII_DENYLIST` repository secret in CI). See `docs/PLAN.md` §14.
