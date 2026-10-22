# voice-outreach-custom-adk: build plan

Status: **draft v2**. Decisions from the first review are applied (see §12). Nothing has been built yet.
Target GCP project: a new, dedicated demo project (its id lives only in the gitignored `config/site.yaml` and `infra/envs/demo/terraform.tfvars`; see §13).
Source system analysed: the original CX Agent Studio + Twilio proxy repository (16 Oct 2026),
including `docs/HANDOFF.md`, `docs/F1`–`F9` designs, `docs/KNOWN_ISSUES.md`,
`docs/agent_instructions_v4.md`, and `docs/ARCHITECTURE_REVIEW_2026-10-16.md`.

---

## 1. What the current system does

### 1.1 Use case

A hospital (brand name from config) calls patients after surgery.

1. A nurse loads patients into a **call list** (by hand or CSV). Calls go out now, at a scheduled time, or in **scheduled call runs**.
2. An outbound phone call connects the patient to a **voice agent ("Sara")**. Sara:
   - asks for the patient by name, and says nothing clinical first;
   - verifies the **date of birth**;
   - asks for **consent** and gives the recording notice;
   - runs a **4–5 question post-op questionnaire**: overall feeling; pain (with severity, location and trend); fever; incision; blood sugar for diabetics;
   - watches **every turn for red flags**, and on one tells the patient to call 911;
   - files a structured report with a **triage tier**: `emergency | urgent_same_day | routine_next_business_day | informational | none`. The report also carries an `outcome`: `questionnaire_completed | reschedule_requested | voicemail_left | unable_to_reach_wrong_person | consent_declined | identity_verification_failed | emergency_ended`.
3. **Answering-machine detection (AMD)** holds the agent back until there is a verdict. On a machine it plays a canned voicemail clip and hangs up.
4. If the call fails (`rna`, `busy` or `left_vmail`), an **SMS follow-up** texts the patient three options:
   1. call me now;
   2. pick a time (deterministic time parsing, clamped to quiet hours);
   3. answer the questionnaire by text (a DOB gate, then the same questions with deterministic triage).
5. **Auto-recall** retries unanswered patients inside a clinic calling window, up to `max_recalls`.
6. Nurses work from a **Next.js dashboard and nurse console**. These show live call status over SSE, verdicts, transcripts, recordings, follow-up overrides, the worklist, the allowlist, SMS consent, call runs and recall settings.
7. A **Gemini Enterprise assistant** (A2A + A2UI cards) lets nurses do the same in chat. Every write action follows a propose → confirm pattern.
8. **Abuse controls** sit at one choke point:
   - an outbound allowlist (env ∪ Firestore) that fails closed;
   - transactional dial, SMS and list-write budgets;
   - SMS consent kept separate from the allowlist;
   - STOP/HELP compliance.

### 1.2 How it is built today

| Component | Tech | Role |
|---|---|---|
| `twilio-proxy-v2` | FastAPI on Cloud Run, 4.8k-line `main.py` | Twilio ↔ CES audio bridge, AMD hold/drop, closing drain, call lifecycle, call list and runner, REST + SSE for the UI, survey webhook, allowlist and budget |
| CX Agent Studio agent | Managed (console-configured) | Conversation, STT, TTS, recording, BigQuery transcripts |
| `sms-followup-svc` | FastAPI on Cloud Run + Scheduler tick | SMS state machine, survey by text, action queue |
| `twilio-proxy-ui` | Next.js 15 / React 19 / Tailwind 4 behind IAP | Dashboard and nurse console |
| previous nurse-assistant agent | raw `a2a-sdk` + `google-genai` (not ADK) | Nurse assistant in Gemini Enterprise |
| State | Firestore, GCS (recordings), BigQuery (CES transcripts) | |

### 1.3 What the rewrite changes

- The managed CES agent is replaced by an **ADK live agent on Gemini Live**. Telephony audio goes straight into ADK `run_live()`.
- STT and TTS come from Gemini:
  - Live API native audio, with input and output transcription;
  - Gemini-TTS for the prompts we pre-render;
  - optional Gemini transcribe-live for a second transcript track.
- Twilio is hidden behind **provider interfaces**, so another VoIP or SMS vendor is an adapter plus config.
- Two services take over the proxy and SMS sidecar logic: one **voice gateway** and one **outreach API**. Both share a domain library.
- The nurse assistant is rebuilt on **ADK** and keeps today's pattern: **Cloud Run + A2A + A2UI cards**, registered in Gemini Enterprise. It is **not in v1**; the previous nurse-assistant agent is still being refined. v1 only keeps the API it needs stable.
- Cross-cutting behaviour moves into **ADK plugins**: logging, transcript capture, safety, budget and audit.
- Domain knowledge moves into **ADK skills**: survey, DOB, red flags and SMS scheduling.
- Everything deployment-specific comes from **config files**.

---

## 2. Target architecture

```
                         ┌──────────────────────── Gemini Enterprise ─────────────────────────┐
                         │   Nurse assistant (ADK, A2A, A2UI cards) ── Cloud Run   [post-v1]  │
                         └───────────────────────────────┬────────────────────────────────────┘
                                                         │ authenticated REST (ID token)
┌───────────────┐  IAP   ┌──────────────┐  ID token  ┌──▼────────────────────────────┐
│ Nurse browser ├───────►│  ui (Next.js)├───────────►│  outreach-api   (Cloud Run)    │
└───────────────┘        │  BFF routes  │   (BFF)    │  REST + SSE for UI/agent       │
                         └──────────────┘            │  provider webhooks (status,    │
                                                     │    AMD, inbound SMS)           │
   ┌──────────┐ status/AMD/SMS webhooks (signed)     │  Cloud Tasks targets (dial,    │
   │ VoIP/SMS ├─────────────────────────────────────►│    sms actions, recall)        │
   │ provider │                                      │  SMS agent (ADK, run_async)    │
   │ (Twilio) │◄──── place call / send SMS ──────────┤                                │
   └────┬─────┘                                      └──────┬──────────────┬──────────┘
        │ media WS (μ-law 8k)                               │              │
   ┌────▼──────────────────────────────┐            Firestore (state)   Cloud Tasks / Scheduler
   │ voice-gateway  (Cloud Run)        │                    ▲
   │  provider media adapter           │────────────────────┘ call doc, events, transcript
   │  AMD hold / VM drop / drain       │
   │  recorder (stereo WAV → GCS)      │──► GCS (recordings, rendered prompts)
   │  ADK Runner.run_live()            │──► BigQuery (analytics export, agent analytics)
   │    voice agent + plugins + skills │
   └────┬──────────────────────────────┘
        │ Live API WS (PCM16 16k in / 24k out)
   ┌────▼──────────────────────┐   ┌─────────────────────┐   ┌───────────────────────┐
   │ Gemini Live (gemini-3.8-  │   │ Gemini-TTS          │   │ Gemini text models     │
   │ live, Vertex us-central1) │   │ (voicemail/prompts) │   │ (SMS NLU, summaries,   │
   └───────────────────────────┘   └─────────────────────┘   │  nurse assistant)      │
                                                             └───────────────────────┘
```

### 2.1 Deployables

| Unit | Hosting | Why there |
|---|---|---|
| **voice-gateway** | Cloud Run: `--no-cpu-throttling --session-affinity --timeout=3600 --concurrency=<N>`, min-instances 1 | Must expose a public WebSocket to the telephony provider. It runs `run_live()` in-process, which avoids a second streaming hop. Agent Runtime bidi streaming is still **Preview, EXPERIMENTAL server mode** only. Google's own Gemini Live telephony sample uses this Cloud Run shape. |
| **outreach-api** | Cloud Run, private (`--no-allow-unauthenticated`). Only the signed provider webhooks are reachable from outside, through a separate public ingress path or service. | REST and SSE for the UI and the assistant; webhooks; Cloud Tasks handlers. Merges the proxy REST surface with the SMS sidecar. |
| **nurse-assistant** (post-v1) | Cloud Run, ADK agent exposed over A2A (`to_a2a` / A2A executor) with A2UI cards, registered in Gemini Enterprise. Same pattern as the previous nurse-assistant agent. | Keeps the working A2UI button/card pattern. Session state and pending proposals move to a persistent ADK session service (Firestore- or Vertex-backed), so `max-instances=1` goes away. |
| **ui** | Cloud Run behind IAP (reused Next.js app) | BFF pattern: the browser never calls the backend directly. |
| **sms agent** | In-process inside outreach-api (ADK `Runner.run_async`). | Short text turns, triggered by webhooks or tasks; a hop would add nothing. |

**Where Agent Runtime fits.** With Cloud Run allowed, nothing *needs* to be hosted on Agent Runtime in v1. Its bidi streaming is Preview / EXPERIMENTAL, so voice stays on Cloud Run regardless. The pieces worth using are managed, not hosting:

- The SMS agent keeps its durable state in the `sms_sessions` doc (as today) and runs each inbound message in a short-lived ADK session, so it needs no session service.
- The nurse assistant (post-v1) needs sessions that survive instances. Agent Runtime's managed **Sessions** (`VertexAiSessionService`) does that without us writing a store. Behind a config switch: `adk.session_service: memory | vertex`.

If the "use Agent Runtime" requirement needs a hosted agent anyway, the SMS agent is the easiest one to host there. It is text-only and request/response, and outreach-api would call it over HTTPS. That costs one extra hop per SMS.

One Python monorepo and one shared domain package. The gateway and the API build as **two images from the same code**, so the voice path scales and fails independently of the CRUD path.

### 2.2 Models (all names in config, none in code)

| Purpose | Default | Notes |
|---|---|---|
| Voice conversation | `gemini-3.8-live` (GA, Vertex `us-central1`) | Do **not** build on `gemini-live-2.5-flash-native-audio`. Its Vertex retirement date is **2026-12-13**. |
| Live STT (captions and transcript) | Live API `input_audio_transcription` with `custom_vocabulary` (procedure names, drug names, org name) | Comes free with the session. |
| Second-pass STT (optional) | `gemini-3.5-transcribe-live-preview`, or batch over the recording | For transcript QA and for analytics. Behind a feature flag. |
| TTS | Gemini-TTS via Cloud TTS (`gemini-2.5-flash-tts`, regional `us`) | Pre-renders the voicemail clip, the "technical difficulty" apology, and hold prompts. Clips are cached in GCS and keyed by text hash plus voice. |
| Text agents | `gemini-3.8-flash` (fallback `gemini-3.5-flash`, which has longer support) | Nurse assistant, SMS free-text understanding, post-call summary. |

---

## 3. Modular provider layer

Interfaces live in `voa_core/providers/base.py` as Python `Protocol`s. Adapters register through entry points. Config picks the active one (`providers.voice: twilio`).

```python
class VoiceProvider(Protocol):
    name: str
    call_id_pattern: re.Pattern
    async def place_call(self, req: PlaceCallRequest) -> PlacedCall          # to, from, answer_url, status_url, amd: AmdOptions|None
    async def hangup(self, call_id: str) -> None                              # REST fallback when the WS is gone
    def answer_response(self, media_ws_url: str, params: dict) -> HttpReply   # TwiML / NCCO / TeXML ...
    def verify_webhook(self, req: InboundHttp) -> bool                        # also used on the WS upgrade
    def parse_status(self, req: InboundHttp) -> CallEvent                     # normalized: ringing|answered|completed|busy|no_answer|failed|canceled|amd
    def normalize_amd(self, raw: str) -> AmdVerdict                           # human|machine_beep|machine_ambiguous|fax|unknown
    def media_codec(self) -> AudioFormat                                      # e.g. MULAW_8K

class MediaSession(Protocol):                                                 # one per call, wraps the provider WS
    async def frames(self) -> AsyncIterator[MediaFrame]                       # Start|Audio|Dtmf|MarkAck|Stop
    async def send_audio(self, pcm: bytes) -> None                            # adapter transcodes to native codec, chunks 20 ms
    async def mark(self, name: str) -> None
    async def clear(self) -> None                                             # barge-in + VM drop
    async def close(self) -> None                                             # hang up

class SmsProvider(Protocol):
    async def send(self, to: str, body: str) -> SentMessage                   # raises SendRejected(code, permanent, opted_out)
    def verify_webhook(self, req: InboundHttp) -> bool
    def parse_inbound(self, req: InboundHttp) -> InboundSms                   # from, body, message_id
    handles_compliance_keywords: bool                                         # e.g. Twilio Advanced Opt-Out
```

Adapters in the first release:

- `twilio` (voice and SMS). It ports `twilio_utils.py`, the AMD parameters, and the media frame handling.
- **`browser`** (voice only): a WebSocket softphone page served by the gateway, sending PCM16. This allows **end-to-end tests and demos without a phone number or Twilio cost**, and gives a fast inner dev loop.
- **`fake`** (voice and SMS): a scripted provider for CI. It drives the real gateway endpoint, which closes review finding C-9's test gap.

Telnyx, Vonage, SIP or LiveKit can be added later as adapters. LiveKit has an official ADK integration, so it is a natural second voice provider. Nothing outside `providers/` imports a vendor SDK.

---

## 4. Agents, skills and plugins

### 4.1 Voice agent (ADK `LlmAgent`, live)

The **prompt comes from a template file** (`agents/voice/prompts/post_op.md.j2`), ported from `agent_instructions_v4.md`. Patient fields are rendered from session state.

Several safety rules move **out of the prompt and into code**, because prompt-only rules were the main source of bugs (traps 1–4, 16):

| Today (prompt-enforced on CES) | Rewrite |
|---|---|
| Model sees the DOB and compares it itself | **The model never sees the DOB.** Tool `verify_identity(spoken_dob)` runs the ported `dobcheck.py` server-side and returns `match \| mismatch \| needs_readback \| unparseable`. It keeps a retry counter in state. |
| Model echoes `session_uuid` and `patient_id` into the report (it fabricated them) | Tools read `call_id` and `patient_id` from `tool_context.state`. The model never handles identifiers. |
| Model decides the triage tier | The model records answers with `record_answer(question_id, value, note)`. **Deterministic `triage()` (ported from `survey.py`) computes the tier.** The model's suggested tier and reason are stored next to it for audit. |
| A 400 backstop on an incomplete report | `submit_report()` validates completeness against the campaign YAML. Errors come back to the model as a tool result naming the missing question. |
| Red-flag detection by prompt only | The prompt still covers it. In addition, `SafetyGuardPlugin` runs the negation-aware `intent.is_red_flag` on each **final input transcription**. On a hit it injects a system turn ("SAFETY: patient reported X, follow the emergency procedure") and flags the call. |
| `end_session` with `LIVE_AGENT_HANDOFF` params | Tool `end_call(reason)` sets `phase=closing`. The gateway waits for the model's turn to finish, sends a provider `mark`, waits for the echo, then hangs up. That is the F6 drain, with a per-drain event (trap 17). |
| Outbound opener triggered by CES `session_start` | After AMD release the gateway sends one `send_content` "[call connected]" turn to trigger the opener. **Spike item:** `gemini-3.8-live` has proactive audio permanently on, so verify it still opens reliably. |

Tool declarations that must finish before the model speaks use `behavior=BLOCKING`: `submit_report`, `verify_identity` and `end_call`. On `gemini-3.8-live` **NON_BLOCKING is the default**, and it would bring back the "spoke before submitting" bug.

RunConfig, all values from config:

- `session_resumption` (the Live connection resets about every 10 minutes);
- `context_window_compression`;
- voice name and language;
- `realtime_input_config` (VAD sensitivities tuned for telephony);
- `input_audio_transcription` with custom vocabulary.

### 4.2 Skills (`skills/<name>/SKILL.md` + `resources/`)

| Skill | Used by | Content |
|---|---|---|
| `post-op-survey` | voice, SMS, nurse | Questions, branching, answer vocab, triage rules, patient-facing copy. Machine-readable source is `campaign.yaml`; SKILL.md is the narrative. |
| `identity-verification` | voice, SMS | DOB rules: readback, day/month swap, two-digit years, retry policy. |
| `safety-red-flags` | voice, SMS | Red-flag list, emergency script, negation rules. |
| `voicemail-etiquette` | voice | No PHI on a voicemail; single-turn voicemail exit. |
| `sms-scheduling` | SMS | Quiet hours, time phrasing, STOP/HELP. |
| `call-review` / `worklist-ops` | nurse assistant | How to read verdicts, propose/confirm rules, CSV import. |

**Loading strategy:**

- Text agents (nurse, SMS) use ADK `SkillToolset`, which loads skills on demand.
- The **voice agent compiles the relevant skills into its system instruction at session start**. On-demand skill loading means an extra tool round trip in the middle of a phone call (latency). It is also unverified on Live models, so it is a spike item.

**Campaigns are skills plus config.** "Post-op survey" becomes the first campaign. An appointment reminder or a medication check would be a new `campaigns/<id>.yaml` plus a skill folder, with no code change. This matches the generic repo name.

### 4.3 Plugins (registered on the ADK `App`, so they apply to every agent)

| Plugin | Purpose |
|---|---|
| `StructuredLogPlugin` | One JSON log line per agent, model, tool and live event. Carries `call_id`, `session_id`, `campaign`, `provider`, trace id, and **PHI masking**: names, DOB and phone are masked, and tool args are logged by key only unless `debug.log_tool_args=true` in a non-PHI environment. |
| `TranscriptPlugin` | Writes input and output transcription fragments to `calls/{id}/transcript` (Firestore) as turns finalize, and exports to BigQuery. This replaces the dependency on the CES BigQuery transcript. |
| `SafetyGuardPlugin` | Deterministic red-flag check (see 4.1). Also screens model output, for example "never speak the DOB". |
| `SessionBudgetPlugin` | Wall-clock cap per call, turn cap, and a reconnect-storm breaker. `max_llm_calls` does **not** apply to `run_live()`, so this is the only cost ceiling. |
| `ToolAuditPlugin` | Tool name, latency, outcome and error onto the per-call event timeline. |
| `CallEventsPlugin` | Emits normalized lifecycle events to `calls/{id}/events`, which the UI timeline reads. |
| BigQuery Agent Analytics + OpenTelemetry → Cloud Trace | Uses the built-in ADK and Agent Platform observability. Exact plugin APIs to be confirmed in phase 0. |
| `DebugLoggingPlugin` (ADK built-in) | Local and dev only, behind `debug.enabled`. |

### 4.4 SMS agent

Deterministic code stays in charge. The ported `conversation.decide()` is wrapped as a custom ADK `BaseAgent` that owns the state machine. That agent can call an `LlmAgent` sub-agent **only** to interpret free text the parsers could not handle, for example "sometime after lunch Thursday". Rules for that sub-agent:

- It returns a structured candidate.
- Deterministic validators (clamp, quiet hours, confirm with the patient) accept or reject the candidate.
- It never books, triages or files on its own.

This keeps the reviewed clinical behaviour and still meets the ADK requirement.

### 4.5 Nurse assistant (post-v1)

Out of v1 scope. The previous nurse-assistant agent keeps evolving in the old repo. v1 only commits to two things:

1. outreach-api exposes everything the assistant's `backend.py` calls today, plus call-runs and settings;
2. the API accepts a service-to-service caller that forwards the signed-in nurse's email.

When it is ported, it keeps the **Cloud Run + A2A + A2UI** pattern:

- Ported from the previous nurse-assistant agent to ADK. About 70% of the code carries over:
  - `triage.py`, `cards.py` and `backend.py` almost unchanged;
  - `actions.py` refactored;
  - the 21 tool schemas become `FunctionTool`s;
  - the hand-written loop is deleted.
- Proposals live in ADK session state (Vertex session service). Confirmation only through the button or an explicit "confirm <id>". That is fix C-5: "ok" is no longer a confirmation.
- Pre-routing (button clicks, CSV, greeting) moves into a `before_agent_callback`.
- It calls outreach-api with the signed-in user's identity forwarded, so PHI reads are attributable.

---

## 5. Outreach API and data

### 5.1 API contract

The UI's current contract is kept so the UI ports with config-only changes. The details are in the source repo's `twilio-proxy-ui/src/lib/api.ts` and `types.ts`. Changes:

- Paths stay. Twilio-shaped `CA…` IDs become provider-neutral `call_id`s, validated by the active provider's `call_id_pattern` plus our own `local_` prefix. The UI's BFF regex check moves into config.
- The SMS sidecar endpoints (`/api/followups/*` today) move under the same API: `/api/sms/*`.
- Auth: a private service. The UI BFF and the assistant call it with Google ID tokens. Writes record the IAP or GE user email (`acted_by`). The single shared `DEMO_API_KEY` goes away.
- OpenAPI is generated from Pydantic models. A TS client could be generated for the UI later, but that is optional.
- `/health` and `/readyz` on both services.

### 5.2 Background work

| Today | Rewrite |
|---|---|
| Detached in-memory asyncio runner, two gunicorn workers (K1, K2, C-8) | **Cloud Tasks**: one named task per dial (`dial-{entry_id}-{attempt}`, deduped for about 24 h). A run is a Firestore doc that counts tasks. Cancel = flag + delete queued tasks. Queue `max_concurrent_dispatches` = concurrency. |
| Scheduler tick every minute scanning `calls` for SMS | Call-terminal handler **enqueues** the SMS invite task directly; quiet hours are re-checked at execution (S-4). Scheduler keeps only `dispatch-due` and session expiry. |
| 400 ms Firestore poll for AMD verdict | Firestore `on_snapshot` on `calls/{id}` in the gateway (sync client on a thread → `call_soon_threadsafe`); poll fallback. |
| SSE via 2.5 s poll | Same listener pattern, one shared watcher per instance fanning out to SSE clients. |

### 5.3 Firestore model (kept close to today, with fixes)

- `calls/{call_id}`: the same lifecycle fields. **No DOB** (C-2). Adds `provider`, `campaign_id`, `recording_uri`, `transcript_ready`.
- Subcollections on each call: `calls/{id}/events` (timeline), `calls/{id}/transcript`.
- `reports/{id}`, `call_list`, `call_runs`, `settings/*`, `allowlist`, `rate_limits/*`, `sms_sessions`, `sms_messages`, `sms_consents` (append-only with `withdrawn_at`, fix S-6), `sms_inbound_seen` (TTL), `pending_calls` (read-once PHI staging; kept).
- **Ported unchanged:** the terminal-state rules (`_update_call_doc`, `_classify_completed`, report promotion, the withheld-promotion outcomes). They are pure functions in `voa_core/lifecycle.py`, tested with the existing cases.
- **Recordings are made by the gateway**, as stereo WAV with patient and agent channels, written to GCS. This is provider-independent and needs no vendor recording API. The UI reaches them through a signed-URL endpoint (fix A-9 / C-3).

---

## 6. Configuration

Layered and validated with `pydantic-settings`. The service **refuses to start** on an invalid config (fail closed).

```
config/
  app.yaml               # models, regions, timeouts, feature flags, debug
  providers/twilio.yaml  # numbers, AMD mode/thresholds, webhook paths
  campaigns/post_op.yaml # questions, answer vocab, triage thresholds, scripts, voicemail text
  compliance.yaml        # calling window, quiet hours, recall policy, caps/budgets, allowlist mode
  site.yaml              # org name, support email, privacy/terms URLs, logos, timezone
  environments/{dev,demo,prod}.yaml   # overrides
  *.example              # committed; real files gitignored where they hold identifying values
```

- Precedence, lowest to highest: base → environment file → env vars (`VOA__COMPLIANCE__QUIET_START_HOUR=8`) → Secret Manager references (`secret://twilio-auth-token`), which are resolved at boot.
- The UI keeps `NEXT_PUBLIC_*` build args. A small script generates them from `site.yaml`, so there is one source of truth.
- Infrastructure as code: **Terraform** in `infra/`. It is written to be **shared and redeployed by others** into a fresh project; see §13.
  - One root module (`infra/envs/<name>/`) per environment.
  - Reusable modules under `infra/modules/`:
    - `project_services`
    - `iam`
    - `firestore`
    - `storage`
    - `secrets`
    - `cloud_run_service`
    - `tasks_scheduler`
    - `iap`
    - `observability`
  - Every org-specific value is a variable in `terraform.tfvars`. Only `terraform.tfvars.example` is committed.
  - Secret *values* (Twilio auth token, webhook secrets) are never in Terraform state. Terraform creates the empty secret, and `scripts/set-secrets.sh` adds the version.
  - Container images build with Cloud Build (`cloudbuild.yaml` per service, tagged with the git SHA, dirty builds refused; fixes I-2). Terraform deploys the image digest.
  - The few things Terraform can't do well are scripted and idempotent:
    - first-time IAP brand / OAuth consent screen;
    - the Gemini Enterprise registration (post-v1);
    - pointing Twilio numbers at webhooks.

    These live in `scripts/bootstrap/*.sh` and read the same tfvars.
- **Packaging for others:** `make bootstrap ENV=demo` runs Terraform apply, then secrets, then the build, then the deploy, then smoke tests. `docs/DEPLOY.md` lists the prerequisites:
  - a GCP project with billing;
  - a Twilio account with a number and an A2P campaign, or the `browser` provider only, which needs no telephony;
  - the `gcloud`, `terraform` and `uv` versions.

  A **"no-telephony" profile** (`browser` voice provider, SMS `DRY_RUN`) lets someone evaluate the whole system with just a GCP project.

---

## 7. Logging, troubleshooting, debugging

1. **One log schema** for every service. Fields:
   - `severity`, `message`, `event` (dotted taxonomy: `dial.requested`, `media.start`, `amd.verdict`, `live.connected`, `live.goaway`, `live.resumed`, `agent.interrupted`, `tool.submit_report`, `drain.mark_sent`, `drain.mark_ack`, `call.ended`, …);
   - `call_id`, `session_id`, `campaign`, `provider`;
   - `logging.googleapis.com/trace` and `spanId`, so Cloud Logging groups a call's lines under one trace;
   - a `labels` dict.

   A masking filter covers phone numbers, names and DOB.
2. **Per-call event timeline** (`calls/{id}/events`). It gives millisecond timestamps for dial → ring → answer → AMD verdict → live connect → first agent audio → each tool → drain → hangup. It shows in the existing `call-timeline.tsx`. Latency regressions become visible without opening logs.
3. **OpenTelemetry traces** from ADK (model and tool spans) and FastAPI, exported to Cloud Trace. A single trace links the webhook, the task, the gateway session and the tools.
4. **Debug switches** (config, per environment, some per call):
   - `debug.save_audio`, sampled: raw inbound/outbound PCM to GCS with a 7-day retention rule;
   - `debug.log_tool_args`: non-PHI environments only;
   - `DebugLoggingPlugin` in dev;
   - `debug.trace_frames`: logs media frame counts and timing every N seconds.
5. **Local loop:**
   - `adk web` runs the voice agent in text or mic mode;
   - the browser softphone provider runs the full gateway path locally;
   - the `fake` provider plus scripted patient personas run in CI.
6. **Evals.** ADK eval sets with simulated patients: wrong person, voicemail, DOB mismatch, red flag with negation, pain-range answers, a "not sure" answer, refusals. They are scored for report correctness and script adherence before each prompt or model change (`agents-cli eval`).
7. **Ops:**
   - log-based metrics: calls by status, live connect failures, GoAway/resume count, drain timeouts, tool errors;
   - alert policies and uptime checks;
   - a runbook in `docs/RUNBOOK.md` with saved Logs Explorer queries per symptom (the HANDOFF "traps" carry over there).

---

## 8. Reuse map

| Source | Destination | Reuse |
|---|---|---|
| `sms-followup-svc/survey.py`, `intent.py`, `dobcheck.py`, `timeparse.py`, `messages.py`, `conversation.py` | `voa_core/{survey,intent,dob,timeparse,copy}.py`, `agents/sms/state_machine.py` | Port with config injected; keep and extend their tests. Apply review fixes C-6 and C-7. |
| proxy `_update_call_doc`, `_classify_completed`, `_verdict_action`, recall-window helpers, allowlist parsing, budget transaction | `voa_core/lifecycle.py`, `compliance.py`, `budget.py` | Extract as pure functions plus a thin repository layer; port the tests. |
| proxy `audio.py` | `providers/twilio/codec.py` | Reuse. Add a 24k→8k path; consider `soxr` for quality. |
| proxy hold, drain and VM-drop logic | `gateway/session.py` | Re-implemented against `MediaSession` (provider-neutral). Fixes C-9. |
| `twilio_utils.py`, `logging_setup.py` | `providers/twilio/`, `voa_core/logging.py` | Reuse and extend. |
| `agent_instructions_v4.md` | `prompts/post_op.md.j2` + skills | Port. Remove the parts now enforced in code. |
| `twilio-proxy-ui` (whole app, including the untracked nurse console) | `ui/` | Copy. Change config and the `api.ts` base; replace direct browser reads with BFF routes; add SMS route paths. The pure libs (`triage.ts`, `status.ts`, `nurse/model.ts`, …) are kept. |
| previous nurse-assistant agent | `agents/nurse/` | About 70%; see 4.5. |
| Next.js `instrumentation.ts` localStorage fix, deploy-ui IAP audit | `ui/`, `infra/` | Reuse. |

---

## 9. Repository layout

```
voice-outreach-custom-adk/
├── pyproject.toml              # uv workspace; ruff, mypy, pytest config
├── config/                     # §6
├── packages/
│   ├── voa_core/               # domain: models, lifecycle, survey/triage, dob, intent, timeparse,
│   │                           #   compliance, budget, config loader, logging, firestore repos
│   ├── voa_providers/          # base protocols + twilio/, browser/, fake/
│   └── voa_agents/
│       ├── voice/              # live agent, tools, prompts/
│       ├── sms/                # BaseAgent state machine + NLU sub-agent
│       ├── nurse/              # A2UI assistant over A2A (post-v1; placeholder)
│       ├── skills/             # SKILL.md folders
│       └── plugins/
├── services/
│   ├── gateway/                # FastAPI: provider answer webhook, media WS, softphone page
│   └── api/                    # FastAPI: REST, SSE, webhooks, task handlers
├── ui/                         # Next.js (copied)
├── infra/                      # Terraform
├── evals/                      # ADK eval sets + personas
├── scripts/                    # deploy, seed, place_test_call, render_prompts (TTS)
└── docs/                       # PLAN, ARCHITECTURE, RUNBOOK, CONFIG reference, HANDOFF
```

---

## 10. Phased delivery

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0. Bootstrap + spikes** (go/no-go) | Terraform base for the new demo project: APIs, service accounts, Firestore, bucket, secrets (§13). Private GitHub repo with the PII guard (§14). Spikes: (a) Twilio μ-law → ADK `run_live` with `gemini-3.8-live`: latency, outbound opener with proactive audio, barge-in `clear`, BLOCKING tools, transcription quality at 8 kHz, session resumption. (b) `SkillToolset` with a live model vs static compile. (c) Gemini-TTS render to μ-law. (d) Twilio SMS webhook coexistence with the old system (§11.9). | A written result for each spike. `terraform apply` from an empty project succeeds. |
| **1. Foundation** | Config loader, logging, `voa_core` ports with tests, provider protocols, Twilio and fake adapters | Ported tests green; config validation fails closed; PII scan clean in CI. |
| **2. Voice path** | Gateway, browser softphone, voice agent + tools + plugins, AMD hold / VM drop, drain, recording, transcript | A full questionnaire on a real phone and in the browser; persona evals pass. |
| **3. Outreach API** | Calls, call list, runs through Cloud Tasks, allowlist, budget, recall, SSE, signed recording URLs | The UI contract test suite passes against it. |
| **4. UI** | Copied app on the new API through the BFF, behind IAP | Dashboard and nurse console work end to end. |
| **5. SMS** | Inbound webhook, SMS agent, survey by text, consent and opt-out | The F7/F9 scenario tests pass, with C-6, C-7 and S-1…S-6 fixed. |
| **6. Hardening + handoff** | Alerts, budget, runbook, config reference, eval CI, `DEPLOY.md`, `HANDOFF.md`, the `make bootstrap` path tested in a second scratch project | A new engineer deploys from the docs alone, into a project they created. |
| *post-v1* | Nurse assistant ported to ADK (Cloud Run + A2A + A2UI) once the current one stabilizes | The 37 ported tests pass; confirm-only-by-button. |

Phases 3/4 and 5 can run in parallel once phase 2's domain models settle.

**Phase 0 status (16 Oct 2026).** The voice spike passed 6 of 6 simulated-patient scenarios on `gemini-3.8-live` once two code-side guards were added: auto-hangup after the report, and an exit gate while a question is unanswered. Skills work under `run_live` at about +0.4 s per load, and Gemini-TTS renders mu-law clips. Results and open items are in [`spikes/voice.md`](spikes/voice.md). The Terraform plan is clean (72 resources) and not yet applied. A real PSTN call is still to do.

---

## 11. Risks, concerns and open questions

### Risks and concerns

1. **We take on what CES gave us for free:**
   - turn-taking tuning
   - recording
   - transcript export
   - console-editable flows
   - guardrails

   The plan rebuilds each one (§4, §5). The voice gateway becomes the most complex component.
2. **Live-model behaviour on telephony is the biggest unknown.** Phase 0 exists for this. Specific concerns with `gemini-3.8-live`:
   - proactive audio is always on and "responds only when addressed", which may affect the outbound opener and voicemail;
   - non-blocking tools are the default;
   - 8 kHz input quality;
   - "polite" barge-in.
3. **Agent Runtime.** Everything runs on Cloud Run in v1 (agreed). Agent Runtime is used only where it saves work: managed Sessions for the nurse assistant, post-v1 (§2.1).
4. **Model lifecycle.** Pin model IDs in config; record in the runbook which models retire when.
5. **PHI and compliance.** A demo, but the review's gaps should be designed out from day one rather than retrofitted:
   - BAAs
   - recording disclosure
   - calling windows
   - consent history
   - data-access audit logs
   - no PHI in logs, URLs or agent metadata
6. **UI language.** The UI is TypeScript (Next.js). "Use Python" is read as applying to the backend and agents, because the reuse requirement points at the existing UI.
7. **The SMS campaign is registered (A2P 10DLC) with exact message copy.** If the same campaign and number are reused, the SMS copy in `campaigns/post_op.yaml` must stay byte-identical to the registered copy, or it must be re-registered.
8. **Cost.** Live sessions bill per audio token. `SessionBudgetPlugin`, AMD-before-attach, and the HOLD phase are all needed so mailboxes don't run up model time.
9. **Sharing one Twilio account with the old system.** Outbound voice is safe: each call carries its own answer and status URLs, so the old and new systems can both dial from the same number. **Inbound SMS is not.** A number (or Messaging Service) has exactly one inbound webhook, so patient replies go to whichever system it points at. Options, to settle in phase 0:
   - **(a)** cut the SMS webhook over to the new system when phase 5 starts, which stops SMS follow-up in the old one;
   - **(b)** buy a second number for the new system and add it to the existing A2P campaign (a new number on an approved campaign normally needs no re-vetting, but confirm in the Twilio console);
   - **(c)** a small router that forwards by the phone's last outbound system.

   **Recommendation: (b).** Each system owns its number.

   **Outcome (21 Oct):** for a while both systems shared the number through a take/give switch on its SMS webhook. On 21 Oct the switch was removed and this system owns the number outright (HANDOFF, "21 Oct: ADK owns the number").
10. **Twilio credentials live in two projects.** Use a separate Twilio **API key** (SK…) for the new project rather than copying the primary auth token, so either side can be revoked alone. Webhook signature checks still need the account's auth token; it goes in the new project's Secret Manager.

---

## 12. Decisions (review 1, 16 Oct 2026)

| # | Question | Decision | Effect on the plan |
|---|---|---|---|
| 1 | GCP project | **A new dedicated demo project** (id kept in gitignored config) | All IAM, APIs and data are created from scratch by Terraform (§13). Nothing shared with the old project. |
| 2 | Reuse Twilio account, numbers, A2P | **Yes** | SMS copy must stay identical to the registered copy (risk 7). SMS webhook coexistence (risk 9). |
| 3 | Nurse assistant in v1 | **No.** Still being refined. When ported, it stays on Cloud Run + A2A + A2UI. | Moved to post-v1. outreach-api keeps the endpoints it needs. |
| 4 | Infra tooling | **Terraform wherever possible**, packaged so others can deploy into new environments | §6 packaging, `make bootstrap`, no-telephony profile, second-project deploy test in phase 6. |
| 5 | UI | **Keep Next.js** | Copy `twilio-proxy-ui` (§8). |
| 6 | Campaigns | See below | Build the seam; ship post-op only. |
| 7 | Data migration | **Only the registered test numbers (2)** | Seeded into the allowlist and SMS consent by `scripts/seed.py` from a gitignored `config/seed/allowlist.yaml` (`.example` committed). No call or report history migrates. |
| 8 | GitHub | **Private repo; strip anything PII-like before pushing** | §14 guardrails, from the first commit. |

**About #6 ("campaigns").** Today the system is hard-wired to one kind of call: the post-op survey. The questions, triage rules, scripts and SMS copy are spread through the prompt and the code. A **campaign** is one kind of outreach call described in a single config file plus a skill folder:

- the questions;
- how answers map to a follow-up tier;
- the opener, voicemail and closing scripts;
- the SMS copy.

Adding, say, "pre-op instructions reminder" would then mean writing `campaigns/pre_op_reminder.yaml` rather than changing code.

**Recommendation:** build the seam now and ship only `post_op`.

- The seam: the voice agent, SMS survey and triage read from `campaigns/post_op.yaml` instead of constants.
- Not built in v1: no campaign picker in the UI, no second campaign.

It costs little extra while porting, because those modules are being rewritten anyway. It also makes the repo more useful to others who deploy it.

---

## 13. Bootstrapping the demo project

Everything below is Terraform (`infra/modules/*`) unless marked *script*.

**APIs to enable** (`google_project_service`):

- `run`, `cloudbuild`, `artifactregistry`
- `firestore`, `storage`, `bigquery`
- `cloudtasks`, `cloudscheduler`
- `secretmanager`
- `aiplatform` (Gemini Live, text models, Agent Runtime sessions)
- `texttospeech` (Gemini-TTS)
- `iap`
- `logging`, `monitoring`, `cloudtrace`
- `iam`, `iamcredentials`
- `serviceusage`, `cloudresourcemanager`
- `billingbudgets`

Post-v1: `discoveryengine` for the Gemini Enterprise registration.

**Service accounts.** One per workload, with the fewest roles. The default compute SA is never used (fixes C-1).

| SA | Roles (scoped to the resource where possible) |
|---|---|
| `voa-gateway` | `aiplatform.user`; `datastore.user`; `storage.objectCreator` on the recordings bucket; `secretAccessor` on the Twilio auth token only; `cloudtrace.agent`; `logging.logWriter` |
| `voa-api` | `datastore.user`; `cloudtasks.enqueuer` on its queues; `iam.serviceAccountUser` on `voa-tasks-invoker` (needed to mint OIDC tokens); `storage.objectViewer` on recordings (plus the signing ability for signed URLs); `secretAccessor` on the Twilio token, Twilio API key and webhook secret; `aiplatform.user` (SMS agent); `bigquery.dataEditor` on the analytics dataset |
| `voa-ui` | `run.invoker` on `voa-api` only |
| `voa-tasks-invoker` | `run.invoker` on `voa-api` (Cloud Tasks and Scheduler OIDC identity) |
| `voa-build` | Cloud Build deploy roles, limited to these services |
| `voa-deployer` (humans / CI) | Used by Terraform. Docs say to impersonate it, not to use Owner. |

**Data:**

- Firestore (Native, `nam5` or the chosen region) with PITR, a daily backup schedule, delete protection, and the TTL policy on `sms_inbound_seen`. Composite indexes are declared in Terraform, which avoids trap 12.
- GCS `…-recordings` (uniform access, lifecycle rule, optional retention lock).
- GCS `…-prompts` (TTS clips).
- BigQuery dataset `voa_analytics`.

**Network and edge:**

- `voa-gateway`: public, because Twilio connects to it; every request is signature-checked.
- `voa-api`: private.
- Provider webhooks (status, AMD, inbound SMS) need a public path. They go to a **separate small public route on the gateway service**, which validates the signature and writes to Firestore or enqueues a task, so `voa-api` stays fully private.
- `voa-ui`: behind IAP with an explicit user list (from tfvars).

**Other resources:** Secret Manager secrets (empty, values set by *script*), Cloud Tasks queues (`dial`, `sms`), Scheduler jobs (OIDC), log-based metrics, alert policies, uptime checks, and a budget with email alerts.

**Audit:** Data Access audit logs for Firestore, GCS and Secret Manager (`google_project_iam_audit_config`).

**Terraform state:** a GCS bucket created by a one-time *script* (`scripts/bootstrap/00-state-bucket.sh`), because Terraform can't hold its own state bucket before it exists.

**Twilio:** *script* `scripts/bootstrap/twilio.sh` uses the Twilio API to point the chosen number's SMS webhook at the new gateway. It runs only when explicitly invoked (see risk 9).

---

## 14. Keeping the private repo PII-free

Policy: identifying values are **data, not code**. They live only in gitignored files, and CI fails if one is committed.

1. **Gitignored by default:**
   - `config/site.yaml`, `config/environments/*.yaml`, `config/seed/*` (only `*.example` committed);
   - `infra/envs/*/terraform.tfvars`;
   - `*.tfstate*`;
   - `.env*`;
   - `ui/public/brand/`;
   - rendered audio clips with an org name.
2. **Examples use reserved, fictional values:**
   - phone numbers in the reserved 555-0100…0199 block (for example `+1 202 555 0100`);
   - `example.org` emails;
   - "Acme Health" as the org;
   - fictional patients.

   Test fixtures follow the same rule. The old repo's tests contained real numbers (review I-5); they are rewritten, not copied.
3. **Pre-commit and CI scanning:**
   - `gitleaks` for secrets;
   - a custom `scripts/pii_scan.py` with a denylist kept in a gitignored local file. The denylist holds real org names, project IDs and numbers, the real test phone numbers, and home paths. Real values never appear in the public patterns.
   - generic patterns: E.164 numbers outside 555-01xx, DOB-shaped dates next to name fields, Twilio `AC…`/`SK…` SIDs, `/Users/`, GCP project numbers.
4. **When porting from the old repo,** copy into a scratch stage, scan the stage, then move into the new repo. Never copy unscanned files directly. Pip-compile headers (home paths), logos and `deploy-f4-f5.sh` are known offenders.
5. **No PHI in logs** is enforced in code by the masking filter (§7). Tests assert that masked output contains no raw numbers or DOBs.
6. Commits use only your git identity; no co-author trailers.
