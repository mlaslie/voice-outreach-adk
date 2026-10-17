# Implementation plan: phases 1–6

This is the working plan behind `PLAN.md` §10. Each phase ends the same way: tests green, the PII scan clean, applied to the demo project, and committed.

## Runtime topology (v1)

```
Twilio ──(signed webhooks + media WS)──► voa-gateway  (Cloud Run, public)
                                           ├─ /twilio/answer, /twilio/status (+ async AMD), /twilio/sms
                                           ├─ /twilio/media  → CallSession (bridge + AMD hold/drop + drain
                                           │                    + recording + transcript)
                                           ├─ writes calls/{id} lifecycle, events, transcript (Firestore)
                                           └─ enqueues Cloud Tasks → voa-api (/tasks/*) for anything slow
Browser ─IAP─► voa-ui (Next.js BFF) ──ID token──► voa-api (Cloud Run, private)
                                           ├─ REST + SSE contract used by the UI (same paths as today)
                                           ├─ /tasks/dial          (Cloud Tasks, one named task per attempt)
                                           ├─ /tasks/call-terminal (SMS invite / auto-recall decisions)
                                           ├─ /tasks/sms-inbound   (SMS conversation agent)
                                           ├─ /tasks/sms-action    (send invite, callback, submit survey)
                                           └─ /cron/dispatch-due, /cron/sms-expire (Scheduler, OIDC)
```

Rules that hold everywhere:

- Only the gateway is public, and every route on it is signature-checked.
- Only the API dials. It does so from a Cloud Task, after the allowlist and budget checks.
- The gateway never blocks a webhook on slow work: it enqueues a task.

## Code layout

| Module | Contents | Source |
|---|---|---|
| `voa_core.models` | Pydantic models for the API contract (CallDoc, CallListEntry, CallRun, RecallSettings, Report, SmsSession, …) | UI `types.ts`, proxy |
| `voa_core.campaign` | `campaigns/post_op.yaml` loader: questions, answer vocab, triage thresholds, scripts | new |
| `voa_core.lifecycle` | Pure call-status rules: terminal protection, report promotion, completed classification, AMD verdict actions | proxy `main.py` |
| `voa_core.compliance` | Allowlist parse/match, calling window, recall slots, quiet hours, `is_due` | proxy + SMS |
| `voa_core.survey`, `intent`, `dob`, `timeparse`, `copy` | Triage, red flags with negation, DOB, time parsing, patient-facing copy | SMS service |
| `voa_core.sms.conversation` | Pure SMS state machine `decide()` | SMS `conversation.py` |
| `voa_core.store` | Repository protocols plus Firestore and in-memory implementations | new (pattern from proxy) |
| `voa_core.budget` | Transactional window counter | proxy/SMS |
| `voa_providers.twilio` | Voice + SMS adapters, AMD normalization | proxy + SMS |
| `voa_agents.voice` | Live agent, tools, prompts, CallFacts | spike |
| `voa_agents.sms` | ADK `BaseAgent` wrapping `decide()` + LLM free-text fallback | new |
| `voa_agents.plugins` | Structured logging, transcript, tool audit | new |
| `services/gateway` | FastAPI app + `CallSession` (bridge promoted from the spike) | spike + proxy F6/F8 |
| `services/api` | FastAPI app: REST/SSE, tasks, cron | proxy + SMS |
| `ui/` | Next.js app, BFF only | `twilio-proxy-ui` |

## Phases

| Phase | Build | Test | Apply |
|---|---|---|---|
| 1 Foundation | Domain ports (above), models, campaign YAML, store layer, Twilio SMS adapter | Ported unit tests, store contract tests (memory + Firestore `live` marker) | Firestore indexes + TTL via Terraform |
| 2 Voice path | Gateway service: CallSession with AMD HOLD / voicemail drop / drain / barge-in, recording to GCS, transcript + events, lifecycle writes | Fake-provider tests through the real WebSocket endpoint; spike harness against the gateway | Cloud Run `voa-gateway`, Cloud Build images |
| 3 Outreach API | REST/SSE contract, Cloud Tasks dial, runs, recall, allowlist, budget, signed recording URLs | API tests on the memory store; contract snapshot vs UI types | Cloud Run `voa-api` (private), queues, Scheduler (OIDC) |
| 4 UI | Copy the app, BFF for every backend call (ID token), SMS routes, site config | vitest, `next build`, BFF route tests | Cloud Run `voa-ui` behind IAP |
| 5 SMS | Inbound → task → SMS agent; invites, callbacks, survey by text; consent history | Ported F7/F9 scenario tests | Same services; webhook script for a second number (manual, see PLAN §11.9) |
| 6 Hardening | Alerts, uptime checks, log metrics, runbook, `DEPLOY.md`, `make bootstrap` | Deploy into a scratch project from docs | Monitoring in Terraform |

## What needs the user

| Item | Why | Blocks |
|---|---|---|
| Twilio credentials in Secret Manager (`scripts/set-secrets.sh`) | Real calls and webhook signature checks | Real-call tests (phases 2 and 5) |
| The 2 registered test numbers in `config/seed/allowlist.yaml` | Allowlist; only these may be dialled | Real-call tests |
| SMS webhook choice (second number vs cut-over) | One inbound webhook per number | Phase 5 live SMS |
| IAP user list in `terraform.tfvars` (`iap_members`) | Who can open the dashboard | Phase 4 access |

Until these are in place, everything is tested with the `fake` and `browser` providers, and SMS runs with `dry_run: true`.
