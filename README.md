# Voice Outreach

Automated patient follow-up calls and texts, built on **Google ADK** and **Gemini Live**. It runs on **Cloud Run** with all infrastructure in **Terraform**.

**What it does:**
- **Calls patients** after a procedure, talks with them in natural speech, asks a short clinical questionnaire, and triages what it hears.
- **Puts every finding in front of a nurse,** ranked by urgency.
- **Texts patients it couldn't reach,** offering a call back or the same questions by text.

Out of the box it ships a **post-operative check-in**. Pre-operative readiness and other scenarios are added as configuration, not code.

> **Status: reference implementation.** It is built to be downloaded, adapted to your environment and tested. It is **not a medical device** and makes no clinical decisions on its own. Triage rules, wording and any automated answers must be reviewed and signed off by your clinical team before patients are contacted. Read [Safety and privacy](#safety-and-privacy) before going live.

---

## Contents
- [What it does](#what-it-does)
- [Screenshots](#screenshots)
- [What it connects to](#what-it-connects-to)
- [Google Cloud services and what each is for](#google-cloud-services-and-what-each-is-for)
- [Components](#components)
- [The life of a call](#the-life-of-a-call)
- [Integrating with your environment](#integrating-with-your-environment)
- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Documentation map](#documentation-map)
- [Safety and privacy](#safety-and-privacy)
- [License](#license)

---

## What it does

### For patients
- A phone call at a reasonable hour, in their preferred language
  - a language is used only once its wording is approved ([how](docs/LANGUAGES-PLAN.md#language-availability-and-approval)); a demo-only switch auto-approves all 24 with unreviewed, machine-translated wording
- A voice agent that says up front that it is automated
- An identity check before any health questions:
  - asks for the patient by name
  - verifies the date of birth
- A short check-in:
  - how they're feeling
  - pain: a score from 0 to 10, where it is, better or worse
  - fever
  - the incision
  - swelling in the legs or feet
  - sleep
  - blood sugar (diabetic patients only)
  - any questions for the care team
- Emergency warning signs are watched for on every turn:
  - the patient is told to call 911
  - the call is flagged for nurses
- A text if the call is missed:
  - 1 = call me now
  - 2 = pick a time
  - 3 = answer the same questions by text

### For nurses (nurse console)
- A work queue ranked by urgency:
  - emergency
  - same day
  - next business day
- The reason for each follow-up, e.g. "pain 8/10, worsening; one-sided leg swelling"
- Everything about the call in one place:
  - the patient's answers
  - the transcript, in English or the patient's language
  - the call recording
  - the text conversation
- Actions:
  - change the follow-up tier
  - sign off
  - place a new call
- The same work from Gemini Enterprise, through an assistant with interactive cards

### For administrators (admin console)
- Analytics for a chosen period:
  - call outcomes: completed by voice or text, voicemail and texted, no answer, not completed
  - how many patients were reached
  - what patients reported
  - nurse workload and sign-off times
- Scheduling:
  - call runs and calling hours
  - patients, one at a time or by CSV import
  - authorized numbers and SMS consent
- Content and settings:
  - text messaging
  - the optional knowledge base (off by default)
  - languages
  - company name and branding
  - EHR export mapping: map app answers to the EHR's FHIR fields, with a side-by-side preview (the mapping is built; sending to an EHR is not yet)
- Testing and oversight:
  - a test call from the browser
  - a simulated text conversation (nothing is sent)
  - call diagnostics
  - an audit log of every configuration change

### For your systems
- An EHR scheduling API to load the day's call lists automatically
- A FHIR-shaped record of every nurse-signed result, ready for your exporter
- A provider interface, so calls and texts can use a provider other than Twilio

---

## Screenshots

These are taken from the nurse console's sample mode (`?data=demo`). Every patient, phone number and answer is fictional, and the recording is synthesized speech reading a fictional call.

**Call queue: calls scheduled for today.** Who is due a call and when, the call in progress, and the day's totals. Calls can be placed now, one at a time or all together.

![Nurse console call queue: four summary tiles (calls due, reached, immediate follow-up, overdue), an emergency banner, one call in progress and calls scheduled for 11:30.](docs/images/screenshots/call-queue.jpg)

**Call status: completed calls.** What happened on each call and what it needs from a nurse, with each call's follow-up tier, outcome and language.

![Nurse console call status: filters by follow-up tier and outcome, and a list of completed calls with urgent, emergency and no-follow-up badges.](docs/images/screenshots/call-status.jpg)

**Transcript.** The full conversation, time-stamped by turn. It can be downloaded, and translated calls show the original and English side by side.

![A completed call's transcript: the agent confirms the patient's identity, then asks about pain, fever, the incision and swelling.](docs/images/screenshots/transcript.jpg)

**Call recording.** The stereo recording of the call, played in the browser, with the patient on the left channel and the agent on the right.

![The call recording player in the patient drawer.](docs/images/screenshots/recording.jpg)

---

## What it connects to

![System context: the app connects patients' phones through a telephony and SMS provider, receives call lists from the EHR and sends results back, serves the nurse and admin consoles and the Gemini Enterprise assistant, and uses Gemini models on Gemini Enterprise Agent Platform.](docs/images/context.png)

| External system | Direction | How |
|---|---|---|
| **Telephony and SMS provider** (Twilio today) | both ways | Outbound calls through the provider's REST API, with call progress via signed webhooks. Live audio over a bidirectional media WebSocket. Texts through the provider's API, with inbound texts via webhook. Other providers plug in through an adapter; see [Telephony and SMS providers](docs/TELEPHONY-PROVIDERS.md). |
| **EHR or integration engine** | in | Daily call lists via `PUT /api/v1/call-batches/{source}/{batch_id}`, authenticated as a Google service account. No API keys. |
| **EHR** | out | One FHIR-shaped record per nurse-signed call (a QuestionnaireResponse, plus a Task when resolved), in an outbox your exporter reads. |
| **Gemini Enterprise** | in | The nurse assistant, served over A2A, with A2UI cards. |
| **Staff browsers** | in | The nurse and admin consoles, behind Google Identity-Aware Proxy (two separate access lists). |

---

## Google Cloud services and what each is for

![Google Cloud services map: Cloud Run services; Firestore, Cloud Storage and BigQuery for data; Cloud Tasks and Cloud Scheduler for orchestration; Gemini Live, Gemini Flash, Gemini TTS and Check Grounding for AI; IAP, Secret Manager, Cloud Logging and Terraform for security and operations.](docs/images/services.png)

| Service | Used for |
|---|---|
| **Cloud Run** | Six services and one job (see [Components](#components)). Scale to zero is supported; set a minimum instance count for low first-call latency. |
| **Agent Platform: Gemini Live API** (`gemini-3.8-live`) | The real-time voice conversation: speech in and out, turn-taking, interruptions, tool calls. 24 languages. |
| **Agent Platform: Gemini Flash** (`gemini-3.8-flash`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`) | Reading free-text SMS replies; translating transcripts and texts; the nurse assistant; the optional knowledge-base classifier and drafter. |
| **Agent Platform: Gemini TTS** (`gemini-2.5-flash-tts`) | The pre-recorded voicemail message, per language. |
| **Agent Search: check grounding API** | One stateless API call that checks a knowledge-base answer against the approved text it cites, before the answer can be spoken. No Agent Search data store, app or index is created: approved knowledge lives in Firestore and is passed with each request. Used only when the knowledge base is on. |
| **Firestore** (Native mode) | The operational database: calls, reports, transcripts, the call queue and schedules, text conversations, settings, the audit log and the EHR outbox. |
| **Cloud Storage** | Call recordings (stereo WAV; retention configurable, 90 days by default), voicemail clips, branding images. |
| **BigQuery** | De-identified analytics tables (`fact_calls`, `fact_reasons`, `fact_questions`), refreshed every 15 minutes. No names, phone numbers, dates of birth, answers in the patient's words or transcripts. Patients are identified only by a keyed hash (HMAC-SHA256 of the patient id, with a secret key held in Secret Manager), so repeat calls to one patient can be counted without storing who they are. |
| **Cloud Tasks** | A queue of small jobs, each delivered later as a signed HTTP request to the API, with retries. Three queues: **dial** (one task per patient; a task with no free call slot re-queues itself a few seconds later, and `dialing.max_concurrent_calls` caps simultaneous calls), **sms** (the missed-call text, inbound replies, menu actions) and **events** (call-completion handling, translation, post-call checks). |
| **Cloud Scheduler** | Starting scheduled runs (every minute), expiring stale text conversations, refreshing analytics. |
| **Identity-Aware Proxy** | Sign-in for the two consoles, each with its own access list. |
| **Secret Manager** | Provider credentials and signing keys. Never in code or Terraform state. |
| **IAM service accounts** | One identity per service. The private API checks the caller's identity route by route. |
| **Cloud Logging / Monitoring** | Structured logs with no answers or PHI, log-based metrics, alerts and an uptime check. |
| **Cloud Build / Artifact Registry** | Building and storing the container images. |

---

## Components

| Component | What it does | Inputs | Outputs | Exposure / who calls it |
|---|---|---|---|---|
| **voa-gateway** (`services/gateway`) | The telephony edge and the voice agent. Answers the provider's webhooks, holds the live media stream, runs the Gemini Live conversation (ADK) with its tools, records the call, plays the voicemail. | Provider webhooks (answer, status, answering-machine result, inbound SMS); the media WebSocket; the patient record for that call. | Reports, call updates, transcripts, timelines and recordings (Firestore, Cloud Storage); follow-up tasks. | **Public**, every route authenticated (provider signature or HMAC token). |
| **voa-api** (`services/api`) | The private REST API and business logic: call queue and schedules, dialling, the SMS conversation, nurse actions and sign-off, admin settings, the EHR scheduling API, task and cron handlers. | Requests from the consoles, the nurse agent, the EHR feed, Cloud Tasks and Scheduler. | Firestore records; outbound calls and texts through the provider; tasks; the EHR outbox. | **Private.** Cloud Run IAM, then a per-route role table (nurse UI, admin UI, nurse agent, ingest, tasks). |
| **voa-ui** (`ui/`, nurse build) | The nurse console. The browser only talks to its own server, which calls the API with its service identity. | Staff browsers. | API calls on the nurse's behalf. | **IAP**, the nurses list. |
| **voa-admin** (`ui/`, admin build) | The admin console: the same codebase built in admin mode, with analytics, scheduling, patients, authorized numbers, messaging, knowledge base, languages, branding, EHR export mapping, testing, diagnostics and audit. | Staff browsers. | API calls (configuration routes only this identity may use); analytics reads. | **IAP**, the administrators list. |
| **voa-nurse** (`services/nurse`) | The Gemini Enterprise assistant: an ADK agent with A2UI cards (work lists, call details, transcripts, recordings, sign-off). | A2A requests from Gemini Enterprise. | API calls; A2UI cards. | **Private**, Gemini Enterprise only. |
| **voa-analytics** + export job (`services/analytics`) | The job refreshes de-identified BigQuery tables from Firestore every 15 minutes. The service answers the admin console's analytics queries. | Firestore (job); the admin console (service). | BigQuery tables; the analytics summary JSON. | **Private**, admin console only. |
| **Shared packages** (`packages/`) | `voa_core`: domain logic (lifecycle, triage rules, report validation, SMS state machine, languages, store). `voa_providers`: provider interfaces and adapters. `voa_agents`: the ADK voice agent. `voa_knowledge`: the knowledge-base agent. | n/a | n/a | Libraries. |

**Use cases and languages are configuration.**
- Questions, wording, triage rules and what nurses see come from a use-case file: `config/campaigns/post_op.yaml`, with `pre_op.yaml` as an example.
- Translations come from language packs in `config/campaigns/i18n/`.
- See [Editing questions and languages](docs/EDITING-QUESTIONS.md).

---

## The life of a call

![The life of one call: 1 Queue, 2 Dial, 3 Answer, 4 Verify, 5 Talk, 6 Save, 7 Follow up, 8 Review, then the EHR outbox. At any time during the conversation an emergency leads to telling the patient to call 911.](docs/images/lifecycle.png)

1. **Queue.** Patients join the call list from three places:
   - the EHR scheduling API, which also schedules the run;
   - a CSV import in the admin console (Patients → Import). The file is read in the browser and sent to `POST /api/call-list/bulk`; no file is stored.
   - a nurse's "New call".

   An admin then starts a run now, or schedules one (Schedules).
2. **Dial.** One Cloud Task per patient checks, in order:
   - calling hours;
   - the patient's language;
   - the authorized-numbers list;
   - the **hourly dial budget**: a safety cap on calls per hour (`dialing.max_dials_per_hour`, default 30; texts have their own, default 100). It is counted in Firestore, and a run stops when it is reached.

   Then it asks the provider to place the call.
3. **Answer.** The provider connects the call's audio to the gateway. Answering-machine detection decides between a person and a voicemail.
4. **Verify.** The agent confirms it has the right person and checks the date of birth server-side (two attempts).
5. **Talk.** Gemini Live asks the questions. Deterministic parsers type each answer; rules from the use-case file decide the follow-up tier. Emergency warning signs are checked on every turn.
6. **Save.** The report, transcript, recording and a millisecond timeline are stored. Non-English calls are translated, and re-checked for safety on the translation.
7. **Follow up.** No answer, busy or voicemail sends a text: call me now, pick a time, or answer by text. Automatic retries are configurable.
8. **Review.** A nurse reviews, adjusts the tier if needed, and signs off. Sign-off writes the FHIR-shaped record to the EHR outbox.

More detail: [Architecture](docs/ARCHITECTURE.md).

---

## Integrating with your environment

![Integration points: calls in through the scheduling API from your EHR feed; results out from the EHR outbox through your exporter to the EHR (FHIR or HL7); telephony and SMS through a provider adapter to your PBX, SBC or carrier.](docs/images/integration.png)

There are three integration points, each documented with examples.

| You want to… | Guide |
|---|---|
| **Load call lists from your EHR** and check what happened to each patient (Python examples) | [Integrating with your environment §1–2](docs/INTEGRATION-GUIDE.md#1-calls-in-loading-call-lists) |
| **Send results back to your EHR** (outbox exporter, FHIR or HL7) | [Integrating with your environment §3](docs/INTEGRATION-GUIDE.md#3-results-out-sending-results-to-your-ehr), and the configurable mapping in [EHR export](docs/EHR-EXPORT-PLAN.md). The mapping and preview are built; the service that sends to an EHR is not. |
| **Use your own telephony or SMS provider** (Cisco, Avaya, Genesys or another PBX, SBC, CCaaS or SMS gateway) | [Telephony and SMS providers](docs/TELEPHONY-PROVIDERS.md) |

**Example:** load tomorrow's morning list from Python, running as the ingest service account:

```python
import google.auth.transport.requests, google.oauth2.id_token, requests

API = "https://voa-api-<hash>-<region>.a.run.app"  # your private API URL
token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), API)

batch = {
    "tranches": [
        {
            "label": "Morning",
            "scheduled_for": "2026-11-03T10:00:00-05:00",
            "campaign": "post_op",
            "calls": [
                {
                    "external_id": "enc-1001",
                    "mrn": "A1234567",
                    "name": "Jordan Rivera",
                    "dob": "1950-03-15",
                    "phone": "+12025550123",
                    "language": "es-US",
                    "procedure": "Total knee replacement",
                    "procedure_date": "2026-11-01",
                }
            ],
        }
    ]
}

r = requests.put(
    f"{API}/api/v1/call-batches/epic-prod/2026-11-03",
    json=batch,
    headers={"Authorization": f"Bearer {token}"},
    timeout=60,
)
r.raise_for_status()
for item in r.json()["items"]:
    print(item["external_id"], item["status"], item.get("reason"))
```

---

## Quick start

**You need:** a Google Cloud project with billing, `gcloud`, `terraform ≥ 1.9`, `uv`, Node 22+, and Python 3.13 (installed by `uv`).

```bash
git clone <this repo> && cd voice-outreach
make setup          # dependencies and git hooks (PII scan, secret scan, ruff, terraform fmt)
make test           # ~2,000 unit tests, no cloud needed
```

**Run it in your project.** Full steps are in [Deploy](docs/DEPLOY.md):

```bash
cp config/site.example.yaml config/site.yaml                       # project id, org name, timezone
cp infra/envs/demo/terraform.tfvars.example infra/envs/demo/terraform.tfvars
make bootstrap ENV=demo                                            # state bucket, APIs, identities, data stores
make deploy ENV=demo SERVICES="gateway api ui admin nurse analytics"
make smoke ENV=demo                                                # end-to-end checks, incl. a spoken test call
```

**You can evaluate it without any telephony account.** The admin console's **Testing** section places a test call from your browser through the real voice agent, and simulates a text conversation that never sends a real text.

**Evaluate the agent:**
- `make eval-voice` runs simulated patients through the real Gemini Live agent, in English and Spanish.
- `make eval-languages` runs the non-English safety set.
- `make eval-knowledge` runs the knowledge-base set.

---

## Repository layout

| Path | What |
|---|---|
| `services/gateway` | Telephony edge and the live voice session |
| `services/api` | Private API, task and cron handlers, SMS, EHR scheduling API |
| `services/nurse` | Gemini Enterprise nurse assistant (A2A + A2UI) |
| `services/analytics` | Analytics export job and summary service |
| `ui/` | Next.js nurse and admin consoles (one codebase, two builds) |
| `packages/voa_core` | Domain logic, with no vendor SDKs |
| `packages/voa_providers` | Telephony and SMS interfaces and adapters (the only place vendor SDKs live) |
| `packages/voa_agents` | The ADK voice agent and its tools |
| `packages/voa_knowledge` | The knowledge-base agent |
| `config/` | Use cases (`campaigns/`), language packs (`campaigns/i18n/`), language keyword lists, app defaults. Identifying values live only in gitignored files. |
| `infra/` | Terraform modules and per-environment roots |
| `evals/` | Simulated-patient voice evaluations |
| `scripts/` | Build, deploy, secrets, PII scanner, sample EHR feed, end-to-end checks |
| `docs/` | Documentation (see below) |

---

## Documentation map

| Read this | When |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | You want the full picture: services, data, one call end to end, security model |
| [Integrating with your environment](docs/INTEGRATION-GUIDE.md) | You're connecting an EHR: loading call lists, checking results, exporting results |
| [Telephony and SMS providers](docs/TELEPHONY-PROVIDERS.md) | You need a provider other than Twilio |
| [EHR scheduling API reference](docs/INGEST-API.md) | The exact contract of `/api/v1/call-batches` |
| [Deploy](docs/DEPLOY.md) | Standing up a new environment |
| [Runbook](docs/RUNBOOK.md) | Operating it: logs, alerts, troubleshooting |
| [Configuration reference](docs/CONFIG.md) | Every configuration key and environment variable |
| [Editing questions and languages](docs/EDITING-QUESTIONS.md) | Changing wording, questions, triage rules or translations |
| [Design notes](docs/README.md#design-notes) | Why things are the way they are: use cases, languages, knowledge base, analytics, admin |

---

## Safety and privacy

**Clinical safety**
- **Deterministic triage.** Follow-up tiers come from rules in the use-case file and from reviewed code, not from the model's judgement. Every rule needs clinical sign-off.
- **Emergencies.** Emergency warning signs are checked on every turn, by the model and, for texts and translated transcripts, by keyword rules too. The patient is told to call 911, and the call is flagged for nurses. The system does not call emergency services itself.
- **No medical advice.** The agent never gives medical advice. The optional knowledge base answers only routine, approved questions; medication and symptom questions always go to a nurse.
- **Other languages.** Answers in other languages are normalised to English with safeguards that can only raise urgency. Translations of patient-facing wording must be reviewed by a qualified medical translator before use. See [Language availability and approval](docs/LANGUAGES-PLAN.md#language-availability-and-approval). The demo-only `languages_auto_approve` switch makes every language available with unreviewed (draft or machine-translated) wording; the services refuse it outside dev/test/demo, and it must never be on where real patients are called.

**Privacy**
- **PHI minimisation:**
  - the date of birth travels only with the call that needs it;
  - logs carry identifiers and field names, never answers;
  - analytics is de-identified.
- **Your responsibilities:** HIPAA, a BAA with Google Cloud and with your telephony provider, SMS consent and carrier registration (A2P 10DLC in the US), calling-hours rules, and language-access requirements are the deploying organisation's responsibility.

**This repository** contains no patient data and no real configuration. A PII scanner runs on every commit and in CI. Report security issues as described in [SECURITY.md](SECURITY.md).

---

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
