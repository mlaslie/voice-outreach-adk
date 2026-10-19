# voa-nurse: the nurse assistant for Gemini Enterprise

An ADK agent served over **A2A** on Cloud Run and registered in a Gemini Enterprise
app. Nurses ask in their own words ("who needs me now?", "cancel Eleanor's call, she
was readmitted") or press buttons; the assistant answers with **A2UI v0.9 cards**
(plain text on clients that don't render A2UI). It reads and acts through the private
outreach API (`services/api`), always with the signed-in nurse's identity forwarded.

Ported from the previous `ge-a2ui-agent`, rebuilt on ADK, with the review findings
fixed (see "Review findings" below).

## How it fits together

```
Gemini Enterprise ──A2A message/stream (SSE)──▶ voa-nurse (Cloud Run, private)
   │  x-serverless-authorization: Discovery Engine SA  -> Cloud Run IAM
   │  authorization: user's ya29 token                 -> identity.py (tokeninfo, aud check)
   ▼
 NurseExecutor (executor.py)          one A2A task per chat turn; contextId = conversation
   ├─ pre-routing (no model): button clicks, CSV attachments, "text only",
   │  "confirm <id>", greetings/help/menu -> home card
   └─ ADK Runner ──▶ Agent (agent.py, Gemini via Vertex enterprise client)
                       └─ 25 FunctionTools (tools.py) -> actions.py / worklists.py
                             └─ Backend (backend.py) -> outreach API (ID token + X-Acting-User)
 cards.py builds every A2UI card in Python; the model never writes A2UI. A tool returns
 (card, compact data): the card is put on the turn, only the data goes to the model.
 Sessions (history, text mode) -> Firestore `nurse_sessions` (sessions.py, ADK session service)
 Pending confirmations          -> Firestore `nurse_proposals` (proposals.py)
```

Per turn the executor streams `submitted` → `working` → a `working` heartbeat every
`heartbeat_s` while it runs → **one** artifact (the A2UI DataParts, mime
`application/json+a2ui`, or one text part) → `completed`. The heartbeat stops before
the artifact; no Message is sent after the first response. Every card is validated
against the A2UI v0.9 schema before it is sent; an invalid card goes out as text.

### Confirmations

Nothing that dials, cancels, reschedules or changes a setting runs from a tool. A
`propose_*` tool stores a proposal and shows a confirmation card. It runs only on:

- a **Confirm** button click (the button carries just the proposal id), or
- a typed **`confirm <id>`** (the id is on the card; text mode tells the user to type it).

A bare "ok" / "yes" / "confirm" never runs anything; it gets a note pointing at the
button and the reference. Proposals are single-use (claimed in a transaction), expire
after 15 minutes, are bound to the conversation (an id from another conversation finds
nothing), and expired or used ones are purged at the top of every turn.

## Tools (25)

The nurse's questions: `to_be_called`, `called_today`, `needs_follow_up`,
`cleared_patients`, `patient_history`.
Calls: `list_calls`, `follow_up_queue`, `get_call`, `get_transcript`.
Admin views: `show_call_list`, `show_sms_followups`, `show_allowlist`, `show_help`.
Acting, always through a confirmation card: `propose_place_call`,
`show_place_call_form`, `propose_redial`, `propose_change_follow_up`,
`propose_cancel_call`, `reschedule_call` (form), `propose_add_to_call_list`,
`propose_remove_from_call_list`, `propose_allow_number`,
`propose_remove_allowed_number`, `propose_cancel_callback`, `propose_set_cooldown`.

Deliberately absent: deleting call records and resetting the worklist (dashboard
only), and report sign-off (the outreach API has no sign-off endpoint; a follow-up
is closed by changing its tier with a reason).

## Configuration

Shared config comes from `voa_core.config` (`config/*.yaml`, overridden by
`VOA__SECTION__KEY`):

| Key | Env var | Used for |
|---|---|---|
| `env` | `VOA_ENV` | `dev`/`test` relax nothing here; set `prod` in Cloud Run |
| `gcp.project_id` | `VOA__GCP__PROJECT_ID` | Vertex model client, Firestore |
| `models.text` | `VOA__MODELS__TEXT` | the agent's Gemini model |
| `models.text_location` | `VOA__MODELS__TEXT_LOCATION` | Vertex location (`global`) |
| `services.api_url` | `VOA__SERVICES__API_URL` | outreach API base URL and ID-token audience |
| `services.store` | `VOA__SERVICES__STORE` | `firestore` (deployed) or `memory` (local) |
| `site.timezone` | `VOA__SITE__TIMEZONE` | clinic time on every card |
| `log_level` | `VOA__LOG_LEVEL` | |

The assistant's own settings (`AppConfig` forbids unknown keys, so these use a
separate `VOA_NURSE_` prefix, read by `settings.py`):

| Env var | Default | Meaning |
|---|---|---|
| `VOA_NURSE_PUBLIC_URL` | `http://localhost:8080` | the deployed https URL; goes into the agent card. **Must** be set before registering |
| `VOA_NURSE_OAUTH_CLIENT_ID` | empty | OAuth client of the GE agent's authorization resource; enables end-user identity |
| `VOA_NURSE_ALLOWED_EMAILS` | empty | comma-separated; when set, gates every turn (reads too) |
| `VOA_NURSE_DASHBOARD_URL` | empty | recordings and live monitoring link here |
| `VOA_NURSE_PATIENT_POPULATION` | `post-operative surgical patients` | wording in the prompt and card |
| `VOA_NURSE_BRAND_COLOR` | `#22873B` | primary button colour |
| `VOA_NURSE_ENABLE_UI_CHECK` | `false` | the "ui check" debug card (fictional data only) |
| `VOA_NURSE_TURN_BUDGET_S` | `45` | cap on one turn (GE gives up after ~50-70 s of silence) |
| `VOA_NURSE_HEARTBEAT_S` | `15` | `working` keepalive period |
| `VOA_NURSE_MODEL_TIMEOUT_S` | `25` | per model call |
| `VOA_NURSE_BACKEND_TIMEOUT_S` | `12` | per API call |
| `VOA_NURSE_PROPOSAL_TTL_S` | `900` | confirmation lifetime |
| `VOA_NURSE_SESSION_TTL_H` | `24` | `expire_at` on session docs |
| `VOA_NURSE_MAX_SESSION_EVENTS` | `60` | history bound per conversation |

Firestore: create TTL policies on `nurse_sessions.expire_at` and
`nurse_proposals.expire_at` (session documents hold PHI from tool results).

## Run locally

```bash
uv sync --all-packages
# A local API (no ID token is minted for http://localhost) and in-memory stores:
VOA_ENV=dev VOA__SERVICES__STORE=memory VOA__SERVICES__API_URL=http://localhost:8081 \
VOA__GCP__PROJECT_ID=<your-project> \
  uv run uvicorn voa_nurse.app:app --port 8080
curl -s localhost:8080/.well-known/agent-card.json | jq .capabilities
```

The model call needs ADC with `aiplatform.user` on the project. Tests stub the
model and the API:

```bash
uv run pytest -q -o addopts="" services/nurse
```

Build the image with the shared recipe:
`docker build -f deploy/service.Dockerfile --build-arg SERVICE=nurse .`

## Deploy notes (not automated here)

Cloud Run: `--no-allow-unauthenticated`, `--ingress all`, **no IAP** (IAP rejects
Gemini Enterprise's bearer), `--timeout` ≥ 300, `--concurrency` 4-10. Sessions and
proposals are in Firestore, so `--max-instances` can be above 1. The A2A task store is
in memory per instance; Gemini Enterprise streams each turn and does not poll tasks,
so that is sufficient.

## Register in Gemini Enterprise

Deploy first, set `VOA_NURSE_PUBLIC_URL` to the assigned https URL, redeploy, then:

```bash
agents-cli publish gemini-enterprise \
  --registration-type a2a --deployment-target cloud_run \
  --agent-card-url https://<nurse-service-url>/.well-known/agent-card.json \
  --gemini-enterprise-app-id <GE_APP_ID> --display-name "Post-op Calling Agent (ADK)"
```

Gemini Enterprise stores the card at registration. After changing it (URL, declared
A2UI versions), edit the agent in Gemini Enterprise and paste the new card. For
end-user identity, add an authorization resource to the agent (scopes
`openid email profile`) and set `VOA_NURSE_OAUTH_CLIENT_ID` to its OAuth client id.

## IAM the deployer must add

| Principal | Role | On |
|---|---|---|
| `service-<PROJECT_NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com` (GE's Discovery Engine service agent) | `roles/run.invoker` | the **nurse** Cloud Run service (service-scoped, not project) |
| the nurse runtime SA (`services.nurse_agent_sa`) | `roles/run.invoker` | the **API** Cloud Run service |
| the nurse runtime SA | `roles/aiplatform.user` | the project (model calls) |
| the nurse runtime SA | `roles/datastore.user` | the project (Firestore `nurse_sessions`, `nurse_proposals`) |
| the nurse runtime SA | `roles/logging.logWriter` | the project |

The API admits the nurse SA on `/api/*` only when `services.nurse_agent_sa` is set in
the **API's** config (`auth.ui_callers`), so set `VOA__SERVICES__NURSE_AGENT_SA` there.

## Review findings handled

- **C-5** typed "ok" ran a stale proposal: only a button click with the id or a typed
  `confirm <id>` executes; stale proposals are purged every turn; tests cover both modes.
- **A-1** lists bounded silently: reads ask for the API's maximum page (500 calls, 1000
  worklist entries); a full page is labelled on the card ("Based on the newest 500
  calls") and flagged `may_be_incomplete` to the model.
- **A-2** `None` timestamps: every time read goes through `triage.parse_time`.
- **A-3** history validity: history is cut only at a user text event, and a failed or
  timed-out turn is rolled back out of the session.
- **A-4** identity cache keyed by a hash, bounded, short negative TTL; `ALLOWED_EMAILS`
  gates every turn.
- **A-5** per-call model and API timeouts, a turn budget, and `working` heartbeats.
- **A-6** the "ui check" card is off by default and carries no patient data or recording.
- **A-7** prefix allowlist entries (`+1202555*`) count as approved.
- **A-8** non-root pinned 3.13 image (shared Dockerfile), state in Firestore (no
  `max-instances=1`), IAM scoped as above.
- **A-9** no `AudioPlayer` on the private API URL: recordings link to the dashboard.
