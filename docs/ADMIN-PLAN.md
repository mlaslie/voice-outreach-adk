# Nurse UI and Admin UI, EHR scheduling API, branding: plan

**Status (23 Oct 2026):**
- **Steps 1–6 are built and deployed:**
  1. the nurse and admin console split;
  2. admin sections, with the classic dashboard retired;
  3. the EHR scheduling API (`docs/INGEST-API.md`);
  4. branding;
  5. Analytics, in the admin console;
  6. the knowledge base.
- **Not built:** Pub/Sub decoupling for the feed, a real EHR connector job, multiple brands, Workload Identity Federation in Terraform.

**Companion plans:**
- `docs/ANALYTICS-PLAN.md`: its Analytics section is in the admin UI (moved from the nurse UI on 21 Oct 2026, at the user's request).
- `docs/KNOWLEDGE-PLAN.md`: a knowledge base managed in the admin UI, and an off-by-default switch that lets the agent answer some patient questions from approved content.

**Goals:**
1. Retire the classic dashboard (`/classic`) without losing anything it does.
2. Split the staff UI in two: a **nurse UI** for the daily clinical work, and an **admin UI** for configuration.
3. Let an EHR-facing service create the call schedule through an API, with no clicking.
4. Let an admin restyle the staff UI (logo, favicon, title, colours) at runtime, with a reset to the defaults.

## 1. What the classic dashboard still does that the console doesn't

| Classic feature | Destination | Notes |
|---|---|---|
| Authorized numbers (dial allowlist) with SMS consent | **Admin → Authorized numbers** | The only way to add or remove a number, or record consent, today. |
| Follow-ups tab: text conversations, booked callbacks, cancel a callback, consent, 24-hour text limit | **Admin → Messaging** (monitor, cancel, limit); **nurse drawer → Texts** (per patient, exists) | The text limit switch already moved to the console. |
| "Test in browser" (softphone) | **Admin → Testing** | Next to "Test by text". Both are off in production. |
| Place a one-off call | **Nurse → New follow-up call** (exists as the call queue's "Call now") | Check for parity: the classic dialog takes ad hoc patient details. |
| Live call HUD with the millisecond timeline | **Admin → Call diagnostics** | Engineering view; nurses keep the transcript and recording. |
| Calls table with bulk delete | **Nurse → Call status** (done) | Retention and cleanup tools move to **Admin → Data**. |
| Patient list (call list) | **Admin → Patients** | It sits in the console's Settings today. |

**Retirement test:** each row above has a home and an automated test. Then `/classic` and its components are deleted, together with the UI paths only it used.

## 2. Two UIs, two services

```
IAP group "nurses"  →  voa-nurse-ui  (Cloud Run)  ─┐
IAP group "admins"  →  voa-admin-ui  (Cloud Run)  ─┼→ voa-api (private) ─→ Firestore, Twilio, …
EHR sync / Pub/Sub  →  (service accounts)         ─┘
Gemini Enterprise   →  voa-nurse (A2A agent)      ─┘
```

- **One codebase, two builds.**
  - The existing Next.js app gains an `APP_MODE=nurse|admin` build argument.
  - Shared components, theme and the BFF stay in one place. Each build mounts only its own routes, and its BFF allowlist only lists its own API paths.
  - Two Cloud Run services, two service accounts, two IAP access lists.
  - This keeps one design system while giving a hard boundary: a nurse cannot even reach an admin page.
- **The API enforces roles, not the UI.**
  - API routes are grouped as nurse, admin, ingest and tasks. Each group accepts only its callers' service accounts: nurse UI and nurse agent; admin UI; the ingest identity; Cloud Tasks.
  - `X-Acting-User` stays the audit name. IAP group membership decides who can open which UI.
- **What moves where:**
  - **Admin UI sections:**
    - Patients: records, CSV import, history.
    - Schedules: call tranches and runs, calling hours, auto-recall.
    - Authorized numbers and consent.
    - Messaging: SMS number switch, text limit, conversation monitor, callbacks.
    - Display: time format.
    - Branding.
    - Integrations: API clients, ingest log.
    - Knowledge base: approved Q&A and documents, the answer-grounding switch (see `docs/KNOWLEDGE-PLAN.md`).
    - Testing: browser call, test by text.
    - Call diagnostics.
    - Audit log.
  - **Nurse UI:** the call queue, Call status, the patient drawer (answers, reasons, sign-off, follow-up changes, texts, transcript, recording) and a read-only note of the calling hours.

## 3. Scheduling API for an EHR feed

**Expected shape in production:**
- A service polls or subscribes to the EHR. It could be a FHIR R4 query on discharges and procedures, or HL7 ADT (A03 discharge) through an integration engine.
- It builds the day's list and pushes it into this system, which owns the calling: hours, recall, dialing.

**API (versioned, documented with OpenAPI):**

`PUT /api/v1/call-batches/{source}/{batch_id}`

```json
{
  "timezone": "America/New_York",
  "tranches": [
    {"label": "Morning", "scheduled_for": "2026-10-21T09:00:00-04:00",
     "calls": [
       {"external_id": "enc-123", "mrn": "…", "name": "…", "dob": "1950-03-15",
        "phone": "+1…", "procedure": "Total knee replacement",
        "procedure_date": "2026-10-19", "is_diabetic": false, "language": "en"}
     ]}
  ]
}
```

- **Idempotent.**
  - The key is `(source, batch_id)`, and each patient upserts on `(source, external_id)`.
  - Re-sending the same batch changes nothing. Sending it with an item missing cancels that item, but only if it hasn't been called yet.
- **The response reports every item:** created, updated, unchanged, cancelled, or rejected with a reason (bad phone, missing DOB, outside calling hours, not authorized). One bad record never fails the batch.
- **Companion endpoints:**
  - `GET /api/v1/call-batches/{source}/{batch_id}`: status and results per call, for the EHR side to reconcile.
  - `DELETE` the same path: cancel what hasn't been dialled.
- **Built on what exists:** tranches become the existing call runs, and patients become call-list entries tagged with `source`, `external_id` and `batch_id`. The existing `/call-list/bulk` stays for the UI.

**Authentication, with no API keys:**
- A dedicated `voa-ingest` service account may call only the `/api/v1/*` ingest routes.
- An EHR integration running in Google Cloud uses that account directly. One running elsewhere uses Workload Identity Federation to impersonate it.

**Optional decoupling:**
- A Pub/Sub topic `call-batches` with an authenticated push subscription to the same endpoint, plus a dead-letter topic.
- It suits a feed that arrives in bursts, or sits outside our uptime.

**A connector, built only when the EHR is known:**
- A Cloud Run Job, `voa-ehr-sync`, with one adapter per source (FHIR sandbox, CSV drop in Cloud Storage, HL7 through an integration engine).
- It calls the API above. For the demo, a "sample EHR" adapter loads fictional patients so the flow can be shown end to end.

**Production considerations:**
- **Patient numbers.** The dial allowlist protects the demo. Production replaces it with a policy: any patient number from an authorized source, with SMS consent required before texting.
- **Records.** The batch and the source are kept on every call for audit.
- **Rate limits** apply per source.

## 4. Branding and appearance (admin)

**What an admin can change:**
- the product title and favicon;
- the organisation name shown to staff;
- the square logo and the wide logo;
- brand colours: primary, deep, accent, and the alert/urgent/routine hues;
- the console background.

**Where it applies:** the nurse UI, the admin UI, and the Gemini Enterprise cards' `primaryColor`. Everything starts from today's design (`theme.css` tokens and the current logo).

**How it works:**
- **Storage:** `settings/branding` in Firestore holds the values and a version. Images go to a `branding` Cloud Storage bucket.
  - PNG, JPEG, WebP and ICO only; size limits.
  - SVG is refused or sanitised, because it can carry scripts.
- **Loading:** the UI's server layout reads branding on each request (cached about a minute).
  - The colours become CSS variable overrides on top of `theme.css`.
  - The title and favicon go into `<head>`.
  - The images are served through the BFF at `/brand/<asset>?v=<version>`, so they stay behind IAP and cache correctly.
- **Editor:** a live preview of the console as you edit; a contrast check that warns when text on a chosen colour fails WCAG AA; Save; **Reset to defaults**, which deletes the document so the built-in look returns. Every change is in the audit log.
- **Build-time settings** (`NEXT_PUBLIC_LOGO_URL` and similar) become the fallback defaults, so a fresh deploy looks right before anyone opens the editor.
- **Not included: patient-facing wording.** The organisation name the voice agent and the texts say stays in `site.yaml` and the campaign file. It is part of the SMS consent wording and the A2P registration, so it changes through review, not from a colour picker. The admin page shows this name read-only, with that explanation.

## 5. Production shape after these plans

| Service | Exposure | Who calls it |
|---|---|---|
| voa-gateway | public | Twilio, the softphone (demo only), recording links |
| voa-api | private | nurse UI, admin UI, nurse agent, ingest, Cloud Tasks |
| voa-nurse-ui | IAP, group "nurses" | people |
| voa-admin-ui | IAP, group "admins" | people |
| voa-nurse (agent) | private, A2A | Gemini Enterprise |
| voa-analytics (+ export job) | private | admin UI |
| voa-ehr-sync (job, later) | none | Scheduler, calls the API as `voa-ingest` |

Each one is a Terraform module instance with its own identity and its own minimal permissions. Production is a second `infra/envs/prod` using the same modules (see the analytics plan, section 6).

## 6. Order of work

| Step | Scope | Size |
|---|---|---|
| 1 | Group the API routes by role, with a test that every route belongs to exactly one group. `APP_MODE` build and the `voa-admin-ui` service with its own IAP group (Terraform). Move the console's Settings content (runs, calling hours, patients, time format, SMS number, text limit) into the admin UI. | M |
| 2 | Admin: authorized numbers and consent; messaging monitor and callbacks; testing tools; call diagnostics; audit log. Check every classic feature against section 1, then delete `/classic`. | M |
| 3 | Ingest API v1 (idempotent batches, per-item results, OpenAPI), the `voa-ingest` identity, optional Pub/Sub, and the sample-EHR job for demos. | M |
| 4 | Branding editor with live preview, contrast check and reset; Gemini Enterprise card colour. | S–M |
| 5 | Analytics (separate plan), in the admin UI. | M |
| 6 | Knowledge base and grounded answers (separate plan), after step 1. | M ×3 |

Steps 3 and 4 depend only on step 1 and can run in parallel after it.

## 7. Questions to settle

1. Who are the admins (named people, a group, clinical leads)? Do nurses need read-only access to any admin page?
2. Should a nurse still be able to add one ad hoc patient and call them from the nurse UI, or is that admin-only?
3. Which EHR, and which feed: FHIR, HL7 ADT through an integration engine, or a scheduled extract? It decides the first adapter.
4. Is a batch the full list for a day (missing items cancel) or additions only?
5. Branding: should the admin UI be able to hold more than one brand, for switching between demo customers quickly?
