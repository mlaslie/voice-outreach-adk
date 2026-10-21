# voice-outreach-custom-adk

Outbound voice and SMS outreach for post-operative follow-up. It is built on **Google ADK** and **Gemini Live**, runs on **Cloud Run**, and its infrastructure is in **Terraform**. It replaces, from scratch, a system built on CX Agent Studio and a Twilio proxy.

The system calls a patient and:

- asks for them by name and verifies their date of birth;
- asks for consent;
- runs a short questionnaire, watching every turn for red flags;
- files a structured report with a deterministic triage tier.

A patient who isn't reached gets an SMS follow-up with three options: call me now, pick a time, or answer by text. Nurses work from a dashboard protected by IAP.

## Architecture

```
Twilio ── signed webhooks + media WS ──► voa-gateway (Cloud Run, public, every route authenticated)
                                           CallSession: AMD hold → Gemini Live (ADK run_live) → drain/hangup
                                           writes calls / reports / transcripts / events; queues Cloud Tasks
Nurse/Admin ── IAP ──► voa-ui / voa-admin (Next.js BFF) ── ID token ──► voa-api (Cloud Run, private)
EHR feed ── voa-ingest identity ──► voa-api /api/v1/call-batches
                                           REST + SSE, dialing via Cloud Tasks, runs, recall, allowlist,
                                           budgets, SMS follow-up, Scheduler cron
State: Firestore (nam5, PITR) · recordings: GCS · models: Gemini Live, Gemini-TTS, Gemini text
```

Start here:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the services, the data, one call end to end, and the security model
- [`docs/INTEGRATION-GUIDE.md`](docs/INTEGRATION-GUIDE.md): plugging in another telephony provider (Cisco or another PBX/CCaaS), feeding the call queue from an EHR, and sending results back to an EHR

History:

- [`docs/PLAN.md`](docs/PLAN.md): plan, decisions and risks
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md): build phases
- [`docs/spikes/voice.md`](docs/spikes/voice.md): phase-0 results

## Layout

| Path | What |
|---|---|
| `packages/voa_core` | config, logging and PII masking, store (Firestore and memory), tasks, budget, campaign; domain logic for lifecycle, compliance, reports, survey/triage, intent, DOB, time parsing and the SMS state machine |
| `packages/voa_providers` | provider interfaces; Twilio, browser-softphone and fake adapters; audio transcoding |
| `packages/voa_agents` | ADK voice agent (instruction built from the campaign, plus tools), SMS agent, skills |
| `services/gateway` | public telephony gateway: webhooks, media streams, `CallSession` |
| `services/api` | private API: dashboard REST/SSE, Cloud Tasks and cron handlers, SMS |
| `ui/` | Next.js dashboard and nurse console (pure BFF) |
| `config/` | layered YAML config and campaigns; identifying values live only in gitignored files |
| `infra/` | Terraform modules and per-environment roots |
| `scripts/` | build, deploy, secrets, PII scanner, end-to-end smoke tests |
| `spikes/voice` | phase-0 voice spike and simulated-patient harness |

## Common tasks

```bash
make setup                 # deps + git hooks (PII scan, gitleaks, ruff, terraform fmt)
make test                  # unit tests, no cloud
VOA_RUN_LIVE=1 uv run pytest services/gateway/tests/test_session_live.py   # live persona evals (Gemini)
make deploy ENV=demo SERVICES="gateway api ui"
make smoke ENV=demo        # deployed end-to-end checks
make docs-config           # regenerate docs/CONFIG.md
```

Further docs:

- [`docs/DEPLOY.md`](docs/DEPLOY.md): setting up a new environment
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md): operations
- [`docs/CONFIG.md`](docs/CONFIG.md): every configuration key
- [`docs/HANDOFF.md`](docs/HANDOFF.md): handoff notes

## Keeping the repository free of PII

Identifying values are org names, project ids, phone numbers and emails. They live only in gitignored files. Each of those files has a committed `*.example*` twin with fictional values; phone numbers come from the reserved 555-0100…0199 block.

Two checks enforce this:

- `scripts/pii_scan.py` runs on every commit, together with gitleaks. It checks against a private denylist in `config/pii-denylist.txt`.
- CI runs the same scan, using the `PII_DENYLIST` repository secret.
