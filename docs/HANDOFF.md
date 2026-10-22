# Handoff

The state of the system as of 19 Oct 2026, and what a new owner needs to know.

## What exists

| Piece | State | Where |
|---|---|---|
| Voice agent | **Built.** ADK live agent on `gemini-3.8-live`. Its instruction is generated from `config/campaigns/post_op.yaml`. Tools verify the DOB server-side, normalize answers with the shared survey parsers, triage deterministically, and validate the report before telling the model "ok". | `packages/voa_agents/voice` |
| Gateway | **Deployed** (public). Signed Twilio webhooks, media streams, AMD hold and voicemail drop, barge-in, closing drain, stereo recording, the two guards (exit gate and auto-hangup), and the browser softphone with HMAC tokens. | `services/gateway` |
| API | **Deployed** (private). Dashboard REST/SSE, Cloud Tasks dialing (call-slot guard), scheduled runs, auto-recall, allowlist, budgets, follow-up overrides, recordings, test calls, the SMS follow-up and the expiry cron. | `services/api` |
| Dashboard | **Deployed** behind Cloud Run IAP. Pure BFF: the browser only talks to its own origin; the server calls the API with an ID token. Matches the GE-CX console as of 19 Oct (palette, Overdue chip, clinic-time pickers, run dialog, report sign-off; Follow-ups tab removed). The logo and org name come from gitignored `ui/public/brand/` and `NEXT_PUBLIC_LOGO_URL` / `org_name`. | `ui/` |
| SMS follow-up | **Built and deployed in dry-run.** It logs instead of sending until `sms_dry_run = false`. | `services/api/src/voa_api/sms_service.py` |
| Nurse assistant | **Deployed** (private, A2A + A2UI v0.9, Cloud Run). ADK agent with 25 tools over the API; sessions and proposals in Firestore; confirm-by-id only. Not yet registered in Gemini Enterprise. The API now has report sign-off endpoints; the nurse agent has no sign-off tools yet. | `services/nurse` |
| Infrastructure | **All Terraform.** One root (`infra/envs/demo`), with modules for project services, IAM, Firestore, storage, secrets, Artifact Registry, Cloud Run, queues, scheduler and observability. | `infra/` |
| Monitoring | Log metrics and alerts per failure class, plus a gateway uptime check. | `infra/modules/observability` |

## How it was verified

| Check | Result |
|---|---|
| Unit tests | 880+ (`make test`); no cloud needed |
| Live persona evals (`make eval-voice`, real Gemini Live, simulated patient over 8 kHz mu-law, realistic AMD timing) | 21/21 (7 personas x 3): happy, DOB retry, barge-in, red flag, negated red flag, wrong person, voicemail. First agent audio ~1 s after connect (speak-first AMD). See `docs/evals/` |
| Real PSTN calls | 2 calls to a test handset: identity gate refused a wrong DOB correctly; full questionnaire completed and triaged urgent (incision drainage) |
| Deployed smoke (`make smoke`) | 9 REST checks against the private API, made as the dashboard SA, then a spoken conversation through an API-minted softphone token |
| Store contract | Same tests pass against Firestore and the in-memory store |
| CI | Python, UI, Terraform, the PII scan (tree and full history) and gitleaks |

## What is waiting on a person

| Item | Why it blocks | How |
|---|---|---|
| Twilio secrets | Real calls and webhook signature checks. Until then the system fails closed. | `scripts/set-secrets.sh <project>` |
| The 2 registered test numbers | They must be on the allowlist before anything can be dialled | Dashboard → Allowlist, or `config/seed/allowlist.yaml` |
| Clinical sign-off | Triage rules; the blood-sugar thresholds (<70 low, >180 high) were added during the port and are not from the source | `packages/voa_core/src/voa_core/survey.py` |
| Clinical sign-off on the 19 Oct questionnaire criteria | Patient-question priorities, swelling and sleep rules were agreed with the product owner, not yet a clinician | `voa_core/patient_questions.py` (categories and patterns), `voa_core/survey.py` (`triage`, `_SWELLING_URGENT`) |
| Nurse time format | Defaulted to 24-hour, the usual convention for hospital clinical documentation. Not yet confirmed against the client's nursing documentation policy | Ask their nurse informatics / clinical documentation team: "Does nursing documentation use 24-hour (military) time or 12-hour AM/PM?" Then set `site.nurse_time_format`, or flip it in dashboard Settings → Time display |
| Nurse assistant registration in Gemini Enterprise | The service is deployed and verified over A2A; registration needs the GE app id and, for end-user identity, its OAuth client (`nurse_oauth_client_id`) | `services/nurse/README.md` |

## Things that bit us (and the fix in place)

1. **The model ignores ordering rules.** It says goodbye without calling `end_call`, or asks a question and takes an exit in the same breath.
   - The gateway auto-hangs up after the report.
   - `submit_report` refuses non-emergency exits while a question is unanswered.
2. **`extra={"name": ...}` crashes Python logging** at INFO. It only happened in the cloud.
   - `voa_core.logging.safe_extra` fixes it.
   - Tests now log at INFO.
3. **`app.yaml` said `env: dev`**, which overrode `VOA_ENV` and relaxed auth checks in the cloud. `VOA_ENV` is now authoritative.
4. **YAML-parsing env vars turned phone numbers into ints.** Env overrides now stay strings.
5. **The org policy forbids `allUsers`.** The gateway is public via `invoker_iam_disabled`.
6. **Cloud Run reserves `/healthz`.** Every service uses `/health`.
7. **Composite Firestore queries fail at runtime.** The store sorts client-side when the filter and sort fields differ.
8. **Strict config plus a stale image means a failed revision.** Adding a config section and then changing a shared env var while one service ran an older image left that service on its previous revision.
   - It failed closed with no downtime.
   - Deploy every service that reads a new config section together.
9. **A `tfplan` file was committed.** It is a binary zip of variable values, so the text scanner skipped it.
   - It is now ignored and blocked by the hook.
   - The scanner opens archives and fails on other binaries.
   - CI scans the full history.
   - History rewrite: see the note below.

## Where to start reading

1. `docs/architecture.html` (open it in a browser): where each part runs, how a call and a text flow, and where to change questions, wording and triage.
2. `docs/PLAN.md`: why the system is shaped this way.
3. `docs/spikes/voice.md`: the measured voice behaviour.
4. `services/gateway/src/voa_gateway/session.py`: one call, end to end.
5. `services/api/src/voa_api/dialer.py` then `routes_tasks.py`: how dialing works.
6. `docs/RUNBOOK.md` and `docs/DEPLOY.md`.

## Open follow-ups

- The API has no rate limit per dashboard user. It relies on IAP plus budgets.
- Register the nurse assistant in Gemini Enterprise and live-test it there (heartbeats, card rendering, identity).
- Add sign-off tools to the nurse agent (the API endpoints exist since 19 Oct).

## 19 Oct: UI parity with the GE-CX console

- **Why:** the GE-CX console was redesigned; this dashboard has to look and behave the same so demos are comparable.
- **Backend it needed:** report sign-off (`voa_core/review.py`, sign-off routes in `routes_calls.py`), `follow-ups/open`, any patient can be put in a run, and a patient who books by text is dropped from auto-recall.
- **Shared code changed:** `voa_core` lifecycle and compliance, so gateway, api, ui and nurse were deployed together (see trap 8).
- **Not the same:** the sidebar logo and org name, because brand assets are not in git.
- **Same screens, different home page.** Both apps served the classic table at `/` and the console at `/nurse`, so opening the two root URLs looked like two different products. Here `/` now redirects to `/nurse`; the classic table moved to `/classic` and has a "Nurse console" link back.
- **Browser test calls stuck "in flight".** `POST /api/test-call` writes the call doc before anyone opens the 5-minute link; a link never opened (e.g. from a smoke run) left a doc with no status forever. The every-minute `dispatch-due` cron now marks such docs `canceled` after 15 minutes (`close_unused_test_calls`). They never held a call slot (the slot count ignores test calls), so only the display was wrong.
- **Recordings never played (502).** The API streamed the WAV with `blob.download_as_bytes(start, end=stop + 1)`, but GCS `end` is the *last* byte, inclusive: each 256 KB chunk carried one extra byte, the body overran `Content-Length`, and uvicorn aborted every response. Unit tests had no recording test, so nothing caught it; there is one now with a fake blob that keeps GCS's inclusive semantics. The recordings themselves were always saved correctly.

## 19 Oct: time format by audience

- **Patients: always 12-hour** ("Tue Oct 20, 2:30 PM"). SMS readbacks go through `timeparse.humanize` → `timefmt.patient_clock`; the voice instruction tells the agent to say times the 12-hour way. No setting changes this.
- **Nurses: the clinic's `time_format`** (`12h` | `24h`). Default `site.nurse_time_format` (24h); overridden at runtime by `call_settings/display`, which the dashboard's Settings → Time display writes (`PUT /api/call-settings/display`). It applies to the console, the classic table, calling-hours messages from the API, and the nurse agent's cards and replies (the agent re-reads the setting at most once a minute).
- **Pickers:** native `type="time"` / `datetime-local` inputs follow the OS locale and cannot be forced to 12h/24h, so the console uses its own date + time-dropdown picker whose labels follow the setting.

## 19 Oct: new questionnaire (clinic request)

**Order:** how are you feeling → pain (yes/no; if yes: 0–10, describe it, better/same/worse) → fever → incision (+ describe) → legs/feet swollen (if yes: one leg or both, getting worse?) → sleeping well → blood sugar range (diabetic only; the patient's words are kept as `blood_sugar_reading`) → "Do you have any questions for the care team?"

**Kept although the clinic list dropped them:** fever and incision (infection screening drives most same-day follow-ups) and the 0–10 pain score (triage needs a number). Decided with the product owner.

**Patient questions.**
- The agent repeats back a short summary, asks "any other questions?" (up to 5), and never answers them.
- Each question is classified deterministically by `voa_core/patient_questions.py`:
  - **Immediate (same day):** medication (which, when, how much, missed dose, stop/start, side effects, refills), and symptoms or complications.
  - **Next business day:** appointments, activity, diet, paperwork or billing, supplies, recovery timeline.
  - **Fits nothing:** immediate.
  - Any immediate match wins over a routine one.
- A "yes" with no question captured is next business day.
- Questions are not scanned by the "wants a human" prose rule ("when will I see my doctor?" is a question, not a request to be called).

**Other new rules.**
- Legs/feet swollen: next business day. One leg, getting worse, or painful/red/warm: same day. Calf swelling with pain stays an emergency red flag.
- Not sleeping well alone: informational.

**Closing.**
- Every completed call says the medication-list reminder ("bring a list of your current medications to your post-op follow-up clinic visit").
- Then: "the care team will follow up to get your questions answered" if they asked any; otherwise the follow-up or all-clear goodbye.

**Old calls** lack the new keys and render as "not asked". Completed reports now require `legs_swollen`, `sleeping_well` and `has_questions`.

## 19 Oct: multiple follow-up reasons; SMS questionnaire on (dry-run)

**Every finding is kept.**
- `survey.triage` returns `findings`: every reason with its own tier, most urgent first.
- Reports and call docs store them as `follow_up_reasons: [{tier, text, source, fields}]`.
- `follow_up` is still the most urgent tier.
- `follow_up_reason` names that tier's findings, then "Also noted: …" for the lower ones. Example from a real call: "question asked (medication)" (same day) plus "mild pain 3/10" and "not sleeping well" (noted).
- Before this, only the top tier's findings were recorded, so "not sleeping well" was lost whenever anything more urgent was found.
- `scripts/backfill_follow_up_reasons.py` fills the list for older calls. It only runs where the recomputed tier equals the stored one, and never on a nurse-changed call.

**Nurses can set several reasons.**
- `POST /api/calls/{id}/follow-up` takes `reasons: [..]` (1–10) alongside the old single `reason` note.
- The nurse's list replaces `follow_up_reasons` (source `nurse`). The agent's originals are kept in `agent_follow_up_reasons` / `agent_follow_up_reason`, alongside the existing `agent_follow_up`.
- The console's Change follow-up dialog and the nurse agent's change action both send `reasons`.

**Medication vocabulary.** "refilled" and "beta blocker" alone were labelled "other" (still same day). The patterns now cover refill forms and common drug classes.

**SMS.**
- The by-text questionnaire is on: Terraform `sms_survey_enabled` → `VOA__SMS__SURVEY_ENABLED`.
- Sending was still in dry-run at this point. It went live later on 19 Oct; since 21 Oct this system owns the number outright (see "21 Oct: ADK owns the number").
- "Test by text" in the dashboard runs the whole text flow against the dry-run provider. Allowlisted numbers only, a fictional patient, refused unless dry-run.

## 19 Oct: SMS sending on, and texting fixes

The shared-number switch that was built here was removed on 21 Oct (see "21 Oct: ADK owns the number").

- **SMS sending turned on (19 Oct, owner's go-ahead).**
  - `sms_dry_run = false`, and `VOA__TELEPHONY__SMS_PROVIDER = "twilio"`. With the old default (`fake` from app.yaml), turning dry-run off would have sent nothing, silently.
  - Real texts are still limited to allowlisted numbers with SMS consent.
  - The "Test by text" simulator refuses to run while dry-run is off.
- **"No text after a voicemail" (19 Oct).**
  - The follow-up texts were refused for lack of SMS consent: the calls went to a number with no consent recorded. The sending path worked as designed, but nothing on screen said why.
  - Calls now carry `sms_follow_up: {status: sent | not_sent | deferred, reason, at}`.
  - The console shows "Text not sent" (with the reason) on Call status rows, and a "Follow-up text" line in the call details.
  - Consent is recorded in the classic dashboard's Allowlist dialog.
- **Follow-ups use what the patient already said (tester feedback, 20 Oct).**
  - **By text:** `survey.known_pain_site`, `swelling_known`, `followup_value` and `followup_already_answered`.
    - Pain already placed ("around my incision") → "You mentioned pain at your incision. Is there pain anywhere else?" The stored value joins both: "incision; also: …".
    - Swelling: if the reply already says which side and whether it's changing, the follow-up is skipped. Otherwise it asks only the missing part.
    - "Left/right foot" now counts as one-sided swelling (same day).
  - **By voice:** an instruction rule plus notes on the two follow-ups in `config/campaigns/post_op.yaml`.

## 19 Oct: questionnaire wording editable in one file (not deployed)

**What changed.** The SMS questionnaire wording moved from `voa_core/copy.py` into `config/campaigns/post_op.yaml`, beside the voice wording. A non-developer can now reword both channels in one file. The guide is `docs/EDITING-QUESTIONS.md`.
- Each question has `sms`. `pain_location` also has `sms_if_known` (`{site}`), and `swelling_description` has `sms_if_side_known` / `sms_if_trend_known`.
- A new `sms_scripts` section holds the preamble, the numbering, the re-ask line, the questions loop, the closings, the medication reminder, `cut_short` and `file_failed`.
- The SMS plan order and the follow-ups now come from the campaign's question order, `when` and `when_patient` (`survey.build_plan` / `followups_for`). The ids, parsers, `STRICT_FIELDS` / `UNKNOWNABLE` and triage stay in `survey.py`.

**What stayed in code, and why.** The invite, the menu, the scheduling replies, the DOB gate, STOP/HELP and `survey_stopped` all stay in `copy.py`. They are sent before identity is verified (`survey_stopped` is also sent at the DOB gate), or they are carrier-registered compliance copy, so a change there is a privacy and compliance review rather than a rewording. Triage, patient-question priorities and the swelling rules stay code: they are clinical decisions and deliberately deterministic.

**Guards.**
- `test_sms_golden.py` holds every outbound message of five SMS walks plus the closings, captured before the move. The texts are byte-identical, so a deliberate rewording must update it.
- `test_campaign_editable.py` and the runtime checks (`survey.plan_problems`, `copy.sms_copy_problems`) reject: a missing `sms`, an unknown or removed question id, a `when` on an unknown, later or follow-up question, an unknown `when_patient` flag, unknown or dropped placeholders, an unknown `sms_scripts` key, a variant on the wrong question, and non-GSM-7 characters.
- The checks run on the first text after a deploy. A bad file fails loudly there instead of failing halfway through a patient's questionnaire.

**One difference kept on purpose.** `when_patient: {is_diabetic: true}` is read with `coerce_bool` for SMS ("1"/"on" count as true, as before) but with `norm_answer` for voice (unchanged). Unifying the two would change one channel's behaviour.

**Next.** Load the campaign from GCS or Firestore so that edits apply without a redeploy. Today the file is baked into the images: deploy `gateway` and `api` together.
- **Bulk delete in Call status (20 Oct).**
  - Turn on Select to tick rows, or use Select all on the rows currently shown, then Delete.
  - In-progress calls and cancelled queue entries are skipped. Deletes go in chunks of 100 through `POST /api/calls/batch-delete`, which removes the call, its report, transcript, timeline and recording.
  - Each request logs `calls.batch_deleted` with who did it and the counts, never ids.
  - The architecture overview with a "where to change it" guide is in `docs/architecture.html`.
- **"A deleted call still shows in Gemini Enterprise" (20 Oct).**
  - The call wasn't deleted. Call status was on Today, so Select all only covered today's calls. The 17 Oct calls stayed, and the nurse agent's "Needs you now" lists open follow-ups of any age. Selection mode now says so, with a "Show everything" link.
  - **A real bug found on the way:** the gateway's end-of-call save recreated an empty call record (only `recording_uri`) when the call had been deleted while it ended (the smoke test's cleanup). `finish` now skips its writes and the upload when the call is gone.
- **Nurse agent list cards (20 Oct).**
  - The follow-up reason is now the second line of each patient row: body size, bold (Markdown `**…**`), capitalised. It used to be a small italic caption.
  - A2UI v0.9 can't change the font: Gemini Enterprise uses its own. The Text component only offers `h1`–`h5`, `body` and `caption`, plus simple Markdown, and the surface theme only offers `primaryColor`.
  - "Change tier" was removed from the list cards (Needs you now, Cleared). It is now only on the call's detail card, so a nurse changes a tier after reading the call.
- **Detail card alignment (20 Oct).** Marker rows (reasons, symptom review, patient questions) use `cards.marked`.
  - The marker is body size and the row is top-aligned, so it sits beside the first line. Centred, heading-size markers had floated between lines.
  - Unsure answers use ❓; the pale ❔ was nearly invisible on Gemini Enterprise's grey card.
  - Reasons are capitalised.
- **List rows got the same alignment fix.** Needs you now, Called today, Cleared, follow-up queue rows and patient rows now put the dot at name-line size (h5), top-aligned beside the name. It used to be heading-size and centred on the three-line block.
- **Recordings play inside the nurse agent's card (20 Oct).**
  - The private API mints a 15-minute link for one call: `POST /api/calls/{id}/recording-link`.
    - Signed with the softphone key, but over `rec.<call>.<exp>`, so a test-call token can't open a recording.
    - Logged as `recording.link_minted` with who asked.
  - The public gateway serves `GET /recording/{call}?exp&sig` from GCS. It supports seeking, logs `recording.served`, and refuses expired, tampered or other-call links. Shared code: `voa_core/links.py`.
  - The gateway now also has `storage.objectViewer` on the recordings bucket.
  - The nurse agent's detail card has "Play recording", which renders an A2UI `AudioPlayer`. The model never sees the link.
  - Trade-off (review A-9): anyone holding a link can play that one recording until it expires. The earlier design sent nurses to the dashboard instead.
- **Detail card buttons in two rows (20 Oct).** Six buttons in one row ran past the card's edge in Gemini Enterprise.
  - Row 1, for reviewing the call: History · Transcript (Texts for SMS calls) · Play recording.
  - Row 2, for acting on it: Call back · Change tier · Open in dashboard · Main menu.
- **Dashboard button on every nurse-agent card (20 Oct).**
  - The automatic footer is now "Main menu · Dashboard", unless the card already links to the dashboard.
  - The call detail card always has "Dashboard" in its second row. Before, it appeared only when the call had a recording.
  - Nothing is added when `VOA_NURSE_DASHBOARD_URL` is unset.
- **Warm instances (20 Oct, for a demo).**
  - New Terraform variables `api_min_instances`, `ui_min_instances` and `nurse_min_instances`, default 0, alongside `gateway_min_instances` (default 1).
  - The demo tfvars sets all four to 1, so nothing waits on a cold start.
  - Set them back to 0 after the demo to save cost.
- **The home card (main menu) also has Dashboard** next to Settings. It is the one card without the automatic footer, so the earlier change missed it.
- **"No text after a voicemail", part 2 (20 Oct).**
  - The texts were skipped by the 24-hour invite cooldown: one missed-call text per number per day, and the test number had been texted the evening before.
  - Skips now stamp `sms_follow_up` with a plain reason: cooldown, survey in progress, callback already booked, or opted out.
  - The cooldown switch, which was only in the classic Follow-ups tab, is now also in the console under Settings → Missed-call texts.

## 20 Oct: admin plan step 1 (nurse and admin consoles)

**API roles.**
- `auth.ROUTE_POLICY` lists every `/api/*` route with who may call it: the nurse UI, the admin UI, or the nurse agent.
- Configuration (patients, runs, calling hours, display, authorized numbers, consent, text limit, SMS simulator) is admin-only.
- Clinical work (follow-up changes, sign-off, the call queue, place call, recordings) is nurse-only. Reads are open to all three.
- The nurse agent keeps the few configuration actions it already had: bulk-add patients, delete a patient, authorized numbers, the text limit. Whether they move is decided in step 2.
- A route missing from the table is refused. `tests/test_route_policy.py` fails if a route has no policy, or a policy has no route.

**Two consoles from one codebase.**
- `APP_MODE=nurse` builds `voa-ui`; `APP_MODE=admin` builds `voa-admin` (Cloud Run, IAP with `iap_admin_members`, its own service account `voa-admin`).
- Middleware serves only the build's own pages, and the BFF refuses the other build's API routes.
- The admin console has Schedules, Patients, Authorized numbers (with consent), Messaging and Display.
- The nurse console lost its Settings page. It shows the calling hours read-only, and links to the admin console via `ADMIN_CONSOLE_URL`; the admin console links back via `NURSE_CONSOLE_URL`.
- The classic dashboard hides its configuration controls ("moved to the admin console") until step 2 removes it.

**Deploy.**
- `scripts/deploy.sh demo admin` builds the admin image (`cloudbuild.yaml` builds `ui` and `admin` with their mode).
- A new shared setting (`VOA__SERVICES__ADMIN_UI_SA`) means gateway, api and nurse must deploy together (trap 8).
- **Company name in the admin console (20 Oct).**
  - Admin → Display → Company name sets `call_settings/organization`. It falls back to `site.org_name`.
  - `voa_core.organization` reads it through a 60 s cache, and it drives:
    - the voice script (the gateway puts it in session state at call start);
    - the voicemail clip (re-rendered and cached per name);
    - every SMS (refreshed at each SMS entry point).
  - `PUT /api/call-settings/organization` is admin-only. Validation: 2–60 GSM-7 characters, no braces. An empty name resets to the default.
  - The UI warns that the name is part of the SMS consent wording and the A2P 10DLC brand registration.
  - The staff UI's own logo and name are still build-time branding (admin plan step 4).

## 20 Oct: admin plan step 2 (classic dashboard retired)

**Admin console additions.**
- **Messaging:** text conversations (open the thread) and booked callbacks (cancel).
- **Testing:** test in browser, test by text.
- **Call diagnostics:** the gateway's millisecond timeline per call, with first audio, tool calls, interruptions and errors. Names and statuses only, never answers.
- **Audit log.**
- **Patients:** CSV import with a preview and per-row errors. It sends at most 200 rows per batch, and rejects a patient ID already on the list.

**Nurse console.** "New call" (ad hoc place call) in the Call queue header.

**Removed.** `/classic` and every component only it used. `/classic` now redirects to the build's own console.

**API.**
- `GET /api/calls/{id}/events` (admin) and `GET /api/audit` (admin). `POST /api/test-call` is now admin-only.
- **Audit:** `routes_calls.api_of` writes `audit_log` after any successful write to an admin route (plus batch delete): who, when, route, path parameters with phones masked. Request bodies are never stored.

**Not ported:** the classic live HUD (the active-calls strip while a call is in progress). Nurses see "Calling now" in the queue; engineers see the timeline after the call.

## 20 Oct: admin steps 3–4 (ingest API, branding) and analytics phase 1

**EHR scheduling API (admin step 3).**
- `PUT /api/v1/call-batches/{source}/{batch_id}` creates call runs and call-list entries from a batch. It is idempotent (a replay changes nothing), returns a result per item, and upserts on `(source, external_id)`.
- Only the new `voa-ingest` service account may call it. The contract is in `docs/INGEST-API.md`.
- `scripts/sample_ehr_feed.py` sends fictional patients as that account; operators may impersonate it.
- Every ingest write lands in the audit log.

**Branding (admin step 4).**
- Admin → Branding sets the title, the staff-facing organisation name, logos, favicon and 8 colour tokens. It has a live preview, a WCAG contrast check (warn only), and Reset to defaults.
- Storage: values in Firestore `settings/branding`; images in the `branding` bucket, served through the BFF.
- Images are checked by magic bytes, and SVG is refused.
- The root layout reads branding per request (cached 60 s; any failure falls back to the built-in look). The nurse agent's cards pick up the brand colour.
- The patient-facing company name is a separate setting (Display) and is unchanged.
- Known: the default theme's urgent badge text is 3.96:1 against its tint, so the editor shows one contrast warning even at the defaults.

**Analytics phase 1 (`docs/ANALYTICS-PLAN.md`).**
- The new package `services/analytics` holds two pieces:
  - A Cloud Run Job `voa-analytics-export`, every 15 min, does a full refresh: Firestore → de-identified rows → BigQuery dataset `voa_analytics`, tables `fact_calls`, `fact_reasons`, `fact_questions` and `export_meta`.
  - A private service `voa-analytics` serves `/analytics/summary`. Only the nurse UI's identity may call it.
- **De-identification:**
  - Rows are built from an allow-list of fields.
  - The patient key is an HMAC with the `analytics-key` secret.
  - No names, numbers, DOB, free text or question text.
  - Triage reason phrases are kept; nurse and agent reasons are not.
  - A test plants PHI in a call and checks that none reaches any row.
- **Nurse UI → Analytics** has three sections:
  - Outreach: the reach and voice funnel, text completions, unreached patients.
  - Clinical picture: tiers by day, top reasons, symptoms, pain score, questions, emergencies.
  - Nurse workload: open and overdue items by tier, time to sign-off, tier changes.
  - Names are resolved in the browser from data the console already holds.
- **One-time setup after the first apply:** `openssl rand -hex 32 | gcloud secrets versions add analytics-key --data-file=-`. Until then the export job exits with code 2 and the tab shows "unavailable".
- **Decisions to revisit:**
  - Test calls (softphone and SMS simulator) are counted; `is_test` is in the table if they should be excluded.
  - The service and the job share one identity, so the service could write its dataset. Split them for production.
  - The full refresh should become incremental at scale.
- **Bit us on the first live run:** the "data as of" query aliased a column `AS at`. `AT` is reserved in GoogleSQL, so the query failed, and the error was swallowed as "no export yet". The UI then never showed a refresh time, while the fake-client tests passed. It is now aliased `exported`, and the error is logged with its traceback. Lesson: run each new BigQuery statement once with `bq query` before shipping.

## 20 Oct: knowledge base steps 1–2 (approved Q&A answers, off by default)

**What it does:**
- When an admin switches it on (Admin → Knowledge base), the voice agent can answer a patient's question with the clinic's approved Q&A text, word for word. It then asks "Did that answer your question?".
- Everything else defers to a nurse exactly as before. With it off, the instruction and tools are byte-for-byte unchanged (tested).

**Gates, in order (`voa_core.knowledge.answer`):**
1. A red flag defers.
2. Medication and symptom always defer (locked, even if settings are edited directly).
3. "Other" defers, and so does a category the clinic hasn't allowed.
4. Only approved items in the question's category are candidates.
5. Gemini flash picks one or none, seeing only the candidates' questions and never the answers or patient details. Answered only at confidence ≥ min_match (0.8). A timeout (3 s) or any error defers.

**Recording:**
- Each question records `outcome`, `item_id`, `item_version`, `answer` and `satisfied`.
- Answered and satisfied questions add no follow-up reason. "No, that didn't answer it" turns the question into a normal nurse follow-up.
- Nurses see answered questions in the drawer and on the Gemini Enterprise card.

**Admin UI:** Q&A editor (draft → approved → retired; editing an approved item makes a new draft version), settings, "Try it", and the audit log.

**Deliberate limits:**
- No document search (RAG Engine) and no generated answers; those are step 3.
- The text-message path is not built.

**Bit us in review:**
- Matching across categories was tried, so that a question classified "appointment" could match an "activity" item. The eval set (`config/knowledge/eval_questions.yaml`) caught "Can I fly…?" being answered with the follow-up appointment text, so matching stays within one category.
- That exposed a silent failure: an item whose own question classifies under another topic can never be used. "Where do I park?" classifies as *other*.
- The API now refuses such an item with a message naming the right category or asking for a rewording (`check_topic`). Every sample item passes; a test checks it.

**Try it:** `uv run python scripts/seed_knowledge.py demo` loads 12 fictional Q&A drafts. Approve them in the admin console, use "Try it", then switch on to hear it on a call.
- **Bit us on the first live run:** a cold Gemini client took longer than the 3 s budget to sign in, so the first question on a fresh instance always deferred. That is safe, but wrong. Warm calls matched paraphrases correctly ("Is it okay to take a shower yet?", "Where should I leave my car…"), and medication and wound questions were locked out. The matcher now warms in the background when a call starts with the knowledge base on (and when an admin opens the editor). Two sample items, shower and parking, were approved for this test; answering is still **off**.

## 20 Oct: demo over, minimum instances back to 0
- **Change:** every service is back to `min_instances = 0` (tfvars). The gateway's own default is 1, and is now overridden to 0.
- **Trade-off:**
  - The first call after an idle period pays a gateway cold start. A few seconds of that fall before the agent's first audio, while the patient hears silence after answering.
  - The first nurse-console load is slower, and so is the first knowledge-base match on a new instance (warmed at call start; see above).
  - For another demo, set the five `*_min_instances` back to 1 and apply.
- **Still to restore:** the 24-hour text limit is still off (Admin → Messaging).

## 20 Oct: knowledge base v2 (hybrid; replaces the step-1 matcher)

**Design:** `docs/KNOWLEDGE-PLAN.md`. Contract: `docs/KNOWLEDGE-API.md`. Code: `packages/voa_knowledge`, a dedicated agent with its own prompts and evaluation set.

**Request:** the user asked for three things:
- an LLM classifier, with locked topics acknowledged as today;
- answers grounded in example Q&A *plus* free-text guidelines that can be limited to a procedure or care setting;
- a separate sub-agent, so changing it can't destabilise the call.

**How the answer path is guarded:**
- The keyword rules run first and can only veto. The LLM classifier only decides among the allowed topics.
- Guidelines are filtered to the patient's procedure and care setting. The whole approved set goes into the prompt; there is no RAG at clinic scale.
- The drafter must cite its sources or say "not covered".
- Vertex check-grounding must reach `min_support` (0.85).
- The output veto blocks any medication, dose or symptom wording in the answer.
- There is a 4 s budget. Every failure defers.

**Modes:**
- **off:** the default.
- **shadow:** after the report, a Cloud Task drafts an answer per question. Nurses rate the drafts in the drawer; patients hear nothing different.
- **live:** voice only.
- Off and shadow leave the voice instruction and tools byte-for-byte unchanged (golden tests).

**New field:** `care_setting` (inpatient/outpatient) on call-list entries, CSV import and the ingest API.

**IAM:** check-grounding needs `discoveryengine.groundingConfigs.check`. That permission is in `roles/discoveryengine.viewer`, not `.user`.

**Known weakness: the output veto over-matches.**
- It reuses the triage keyword stems: "number" matches *numb*, "discharge" is a symptom word, and "can I take" reads as medication.
- Clinic wording that uses these words will defer, even when correct. Guideline text about a "dressing" or "incision" can never be answered.
- This is safe, but it will cost coverage. Shadow-mode data will show how much, and a narrower output vocabulary is the fix if it matters.
- **Bit us on the first live test: speed.** The knowledge agent first ran on `gemini-3.8-flash` (the SMS parser's model), and every answerable question hit the 4 s budget: 2–15 s per call.
  - **Measured on global:** the classifier on `gemini-2.5-flash-lite` takes about 0.9 s; the drafter on `gemini-2.5-flash` with thinking off takes 1.1–1.4 s; check-grounding takes about 0.4 s. A full answer now takes 2.3–3.5 s.
  - **Don't put the drafter on flash-lite.** It answered "when can I drive again?" for a hip replacement with the general "two weeks" guideline instead of the hip one (six weeks). Grounding may well pass such an answer, because the cited text does say it, only for other patients.
  - The settings are in `models.knowledge_*` in `config/app.yaml`. The grounding scores on correct answers were 0.97–0.99.
- **Live demo settings:** allowed topics are activity, diet and recovery, saved earlier in the admin console. So parking and appointment questions defer as "category not allowed", which is not a bug. All sample content (fictional) is approved, and the mode is **shadow**.

## 21 Oct: use-case engine, step 1 (not deployed)

**What changed.** A use case is now a versioned spec (`config/campaigns/<id>.yaml`, schema v2) and the code is a generic engine (`docs/USE-CASES-PLAN.md` step 1). Post-op is rewritten as a spec and behaves identically; `pre_op.yaml` is a draft example reachable only through the API's `campaign_id`.
- Parsing by question **type** (`voa_core/parsers.py`), with named parsers `trend` and `blood_sugar`; voice "re-ask / unknown / keep" and SMS strict / unknown rules come from the type, not from id lists.
- Triage is the spec's `rules` (`voa_core/rules.py`: a hand-written expression parser, no eval) plus named checks (`voa_core/checks.py`: free-text scans, swelling, patient questions). `survey.triage(answers)` keeps its signature.
- Report completeness, voice closing (`scripts.closing_extra`), the questions loop and the SMS closings' required placeholders all come from the spec. One validator: `voa_core/campaign_lint.campaign_problems` (absorbs `plan_problems` and `sms_copy_problems`; also runs each spec's `tests`).
- `campaign_id` (and `spec_version`) flow: entry / run / ingest tranche or call / place-call / test-call -> dialer -> `pending_calls` and the call doc -> gateway (`CallSession._resolve_campaign`; voicemail per use case; every report stamped) and SMS (session, snapshot, report, closings, recall dial).

**How it was proved.** `packages/voa_core/tests/parity/` captured the pre-engine outputs BEFORE any engine code was written: triage over ~4,000 synthetic answer sets plus every input the existing tests fed it, report validation, the voice instruction, tool declarations, every SMS text, 34 scripted SMS conversations and ~400 replayed `decide()` calls, `applicable()`/`missing()` sequences, and scripted voice-tool sessions. The goldens were never regenerated after the refactor; they pass unchanged.

**What bit us.**
- Stamping `campaign_id` into the report the voice tool or `_report_payload` builds would have changed the parity goldens (and let the model set it). The filing layer stamps it instead: `CallSession._save_report` and the SMS snapshot (only when the session carries a use case, so in-flight sessions file exactly as before).
- One golden case (a report payload that already carried `campaign_id`) encoded the old "unknown keys are dropped" behaviour that step 1 deliberately changes. It was removed from the generator before the refactor started, not regenerated after.
- The ingest batch fingerprint hashes the whole body; a new optional field would have changed it for every re-sent batch. Absent `campaign` keys are left out of the hash.

**Not byte-identical, on purpose.** A voice tool context with no `campaign_id` (tests and spikes only: the gateway always sets it) now uses the post-op spec's `missing()` order instead of the old hard-coded list. Call docs gain `spec_version`; entries gain `campaign_id: null`; SMS sessions gain `campaign_id` / `spec_version`; reports from new calls carry both.

**Next (step 2).** The nurse UI, nurse agent and analytics still read post-op's field names; a pre-op call shows its answers only in the drawer's generic list. Before pre-op is used for real: clinical sign-off on its rules, an A2P check of its SMS copy, live persona evals.
- **Verification of step 1 (added by the reviewing session):**
  - **Goldens:** regenerated independently from the pre-refactor commit in a separate worktree. All 13 files were byte-identical, so they really capture the old behaviour, and the new engine passes them unchanged.
  - **Live persona eval:** see `docs/evals/voice-personas-20261021.md`. dob_retry 6/7 and patient_questions 11/13 on the new engine, all other personas 3/3. An A/B of patient_questions gave 6/6 on both new and old code, so the misses are model variation.
  - **Added fix:** an entry whose use case no longer exists is now *blocked*, with the reason, instead of a 500 that Cloud Tasks retried with the row stuck in "calling" (`routes_tasks.task_dial`, tested).
  - **Deploy order:** deploy gateway and API together, so both know the same specs.

## 21 Oct: Analytics moved to the admin console, with call outcome totals

**Why:** the user asked for Analytics in the admin console, and for totals per period of no answer, voicemail plus text, completed by text, completed by voice, and not completed.

**Moving it:**
- The view is now `ui/src/components/admin/analytics-panel.tsx`, the first admin section; the nurse console no longer has the tab.
- `/api/analytics/summary` answers only in the admin build.
- `voa-analytics` accepts only `VOA__SERVICES__ADMIN_UI_SA`, and Terraform makes the admin service account its only invoker. `ANALYTICS_URL` moved from voa-ui to voa-admin.

**Call outcomes block** (`outcomes` in the summary; `metrics._outcomes`):
- Each call lands in exactly one bucket, so the buckets sum to `total`: completed by voice, completed by text, voicemail left, rang with no answer (`call_status` rna), busy, could not dial (failed or canceled), and not completed (the rest, broken down by outcome, or `hung_up`).
- Voicemail, no-answer and busy each carry `text_sent` (the missed-call text went out).
- A missed call that the patient later completed by text counts as **completed by text**, not as voicemail, because the text report is filed onto the original call.
- An emergency call counts under not completed, labelled "emergency (sent to 911)".
- The service now also reads `call_status`.
- **Bit us on the first live read:** two "wrong person" calls on demo carry `call_status: left_vmail`, so the first version counted them as voicemails. The agent's outcome now wins, and the line status is used only when a call has no outcome (no answer, busy, failed). Tested with that exact shape. Why a wrong-person call ends with `left_vmail` is a separate question for the call lifecycle (`lifecycle.classify_completed`); it is not changed here.

## 21 Oct: ADK owns the number (switch removed; deployed 21 Oct)

**Why:** the user chose to go all in on this (ADK) system. The Twilio number is no longer shared with GE-CX, so the take/give switch had nothing left to do.

**Twilio now** (checked read-only on 21 Oct): `SmsUrl` → `<gateway_url>/twilio/sms` (POST), no `VoiceUrl` (outbound calls set their own TwiML; ADK has no inbound voice), no messaging service on the number.

**Removed:** `voa_api/sms_control.py`, `GET/POST /api/sms/control` and their route policies, the send gate in `send_sms` (every other check is unchanged, same order), `NumberControl` / `TwilioNumberControl` / `FakeNumberControl` in `voa_providers/sms.py`, the six `sms.*` switch settings, the four Terraform variables and `VOA__SMS__*` env lines (also removed from the real `terraform.tfvars`), the UI indicator, hook and Messaging card, and `docs/sms-number-switch.md`. The Twilio 8021 error test moved to `tests/test_sms.py`. Old `sms_control_log` rows stay in Firestore; nothing reads them.

**Pending on the GE-CX side** (its own repo; not edited from here): remove its switch and its SMS sending. GE-CX was asked. Until that lands, don't trigger GE-CX texts: a reply would come here.

**Watch out:** ADK no longer detects a change to the number's `SmsUrl`. If anyone repoints it (for example the GE-CX switch, before it is removed), patients' replies silently go elsewhere and ADK keeps sending. Check it with RUNBOOK → "Check the number's webhooks" (`scripts/check_twilio_number.py demo`).

**Deploying it:** use the normal deploy (images and env change in one `terraform apply`). Config is strict, so a new image started with the old `VOA__SMS__SWITCH_ENABLED` / `PEER_*` / `SELF_*` env vars would fail its revision (trap 8); the old images run fine without them.
- **GE-CX side, confirmed by the GE-CX session on 21 Oct:**
  - Its number switch is removed and deployed: `/api/sms-number` returns 404, and there is no pill or card. It no longer writes SmsUrl or VoiceUrl.
  - Its texting is off: the `sms-followup-svc-tick` scheduler is paused, and the service runs `DRY_RUN=true` and `ENABLE_SMS_SURVEY=false`.
  - **Still pending:** GE-CX's outbound calls show the 5695 caller ID until the user gives GE-CX its own Twilio number, so patients called by GE-CX who text back reach ADK.
  - Optional, not done: a GE-CX-only Twilio API key without permission to change incoming numbers.
- **New docs for outside engineers:** `docs/ARCHITECTURE.md`, and `docs/INTEGRATION-GUIDE.md` (other telephony providers, feeding the queue, results back to an EHR). The guide records the known gaps:
  - the `ehr_outbox` has no reader;
  - `voa-ingest` can't read results;
  - Workload Identity Federation is not in Terraform;
  - `language` is not stored;
  - the CSV import has no use-case column.

## 21 Oct: patient language (step 1; deployed 21 Oct)

**What changed** (`docs/LANGUAGES-PLAN.md` step 1). Each patient has `language`, one of the 24 live-model languages (default `en-US`), and `call_in_english` (an admin's override). Calls and texts are unchanged: still English only.
- `voa_core/languages.py`: the 24 codes with English and native names, `normalize` ("es", "Spanish" → `es-US`; "zh" → None), `available(campaign_id)` (English plus approved packs, read from `config/campaigns/i18n/<id>.<lang>.yaml` with `status: approved`; none exist), `hold_reason`.
- API: the models validate it (422 lists the 24). Ingest rejects an unsupported language per item and keeps it in the feed fields, not `call_in_english`. The entry PATCH changes either only when sent. New `GET /api/languages` (READ).
- **The hold:** `task_dial` never dials an entry whose language is not available, named by a nurse or not. It is re-checked inside the claim transaction, and `dialer.dial` checks again (`LanguageHeld`, 409). The run records the member under `held`. The entry stays `pending`, so a later run dials it once the language is available or the override is on. The per-minute due pass leaves held entries out. `POST /api/place-call` gives 409 with "…Turn on 'Call in English'…". The SMS recall carries the original call's language and override.
- The call doc (top level and `patient_context`) and `pending_calls` carry `language` and `call_in_english`. The gateway strips both before `state["patient"]`, so the instruction, tools and voice are untouched.
- UI (both builds): the language select ("not available yet" marks) with a "Call in English" checkbox, CSV `language` / `call_in_english` columns, a language chip (hidden for en-US) on the queue, Call status, follow-ups, the drawer and the admin patients list, "Held: waiting for … support" on the queue, and a clear 409 in New call. Sample data has a held Spanish patient and a Vietnamese one called in English.
- Nurse agent: place-call, the form, redial and add take `language` / `call_in_english`; cards show "Language: Español (Spanish US)"; a 409 is a "Not called" card.
- Analytics: `fact_calls.language` (default en-US). The loader adds a new NULLABLE column to an existing table with an explicit schema update before the load.

**What bit us / watch out.**
- A full-replace PATCH from an older client would have reset a Spanish patient to English, which makes them callable in English. The PATCH now keeps `language` and `call_in_english` unless they are sent.
- A held patient with a planned time stays "due" forever, because it is never claimed. Without the filter, the due pass would start a run every minute.
- A recall reached through the SMS form would have dropped the language, because the nurse form card had no language field. Both fields were added to the form.
- BigQuery refuses `schema_update_options` on a WRITE_TRUNCATE of a whole table, and the empty-table path never loads at all. So the new column is added with `update_table` instead.
- The UI's sample mode has no backend, so it uses `ui/src/lib/languages-sample.json`, a copy of the API's answer. `test_ui_sample_languages_match_the_endpoint` fails if it drifts.

**Parity:** the goldens and tool declarations are unchanged; the suite passes as is.
- **Verified live after deploy:**
  - `GET /api/languages` returns 24 languages, with en-US and en-IN available.
  - "Call now" for a Spanish patient returns 409 with the "Call in English" hint; `zh` returns 422.
  - The analytics export added `fact_calls.language` to the existing table, and all 10 existing calls show en-US.
  - Smoke passes.
  - The first smoke rerun hung locally with no output, and the cause was not found. gcloud auth was fine, and a rerun with visible output passed in under 4 minutes.

## 22 Oct: bilingual transcripts (step 2; deployed 22 Oct)

**Why now:** Gemini Live follows a patient who switches language mid-call, so a Spanish speaker on an English call already produces Spanish turns. Before this, a turn wholly in a non-Latin script was emptied by the gateway, `caller_turns` stayed 0, and `submit_report` refused to file.

**What changed** (`docs/LANGUAGES-PLAN.md` step 2).
- **Gateway cleaning** (`session.clean_transcript`). On an English call, a turn whose letters are mostly Latin is cleaned exactly as before: every non-Latin letter and mark goes, so English speech gives a byte-identical transcript (a test compares against the old function). A turn mostly or wholly in another script is kept and counts as the caller speaking. The original garbage case (`"date of birth?িতার"`, a53146a) is still cleaned to `"date of birth?"`. Known limit: a few words of another script inside a mostly-English turn are still dropped from the text (the recording keeps them).
- `call_facts.unanswered_question` accepts `？` and `؟`.
- **Translator** (`voa_core/translate.py`): the `Translator` protocol (`translate_turns(turns, target="en") -> [TurnTranslation(lang, text_en)]`), `FakeTranslator`, and `looks_english`, a heuristic that only skips obviously all-English batches. It is **not a safety gate.** The Gemini implementation is in `voa_agents/translate.py`: `models.translate` (gemini-2.5-flash, `global`, thinking off). It makes one structured call per transcript (chunked over 80 turns or 12k characters), requires exactly one entry per turn in order, retries 3 times, then raises. It never returns a partial result.
- **The task** `/tasks/transcript-translate {call_id, phone?}` (`voa_api/translation.py`) is queued by the gateway's `finish` (name `xlate-<call_id>`) and by the by-text filing (with `phone`). It writes `turns[i].lang` / `text_en` and `translation: {status: done|failed|skipped, model, at, languages, error?}`, and sets `language_used` (majority of the patient's turns, en-US for English or none) and `languages_spoken` on the call doc. It is idempotent (done or skipped is never redone; failed is retried on the next delivery) and never touches `follow_up`, triage or answers. With `phone`, it adds `lang` / `body_en` to that call's `sms_messages`.
- **Step 3 seam:** `translation.after_translation(api, call_id, turns, translation)`, called right after a successful translation is stored. The raise-only re-triage goes there.
- **API:** the transcript turns carry `text_en` and `lang`; the top level adds `translation`, `language`, `language_used` and `title` (the use case's `display.title`). `GET /api/sms/messages` adds `body_en` / `lang` when present.
- **Console:** when any turn is translated: a "Machine-translated from Spanish (US)" label and an English / Original / Side by side toggle (English by default). Pending shows "Translating…" with the original; failed shows the original with a note. All-English calls look as before. The download follows the view, notes the language on the title line, and uses the use case's title (no longer "Post-Op Wellness Check"). The filename keeps accented and non-Latin names. The Texts tab has an English / Original toggle. A "Spoke Spanish (US)" chip shows in the drawer and on Call status when `language_used` differs from the language asked for. Sample data: Lucia Fernández (Spanish, translated) and Theo Nakamura (Japanese, pending).
- **Nurse agent:** the transcript card shows English with "Translated from Spanish (US)", plus a "Show original" button (the `show_transcript` event with `original: true`; `get_transcript(original=…)` too). The model gets both `text` and `text_en`. The texts card shows English with the original under it.
- **Analytics:** `fact_calls.language_used`, added to the existing table by `_add_new_columns`.
- **Config:** `models.translate`, `models.translate_location`, `models.translate_thinking_budget`. Strict config: deploy the API and the gateway images together with the config (trap 8).
- **IAM:** none needed. The gateway and API SAs already have `roles/aiplatform.user`, and both already enqueue on the events queue.

**What bit us / watch out.**
- The old filter's job was garbage after English, not other languages. A letter-majority rule keeps that job and makes English parity provable. A per-word rule would have let `"Yes িতার"` (a space before the garbage) through.
- A punctuation-only remnant ("，？") of a dropped Chinese turn is not empty, so the old code even recorded junk turns. The test checks letters, not emptiness.
- A short reply from the model (one entry missing) must not be "translated except one". `check` rejects a count or index mismatch, and the task stores `failed` with the original intact.
- `looks_english` was a cost saver here: "no tengo fiebre" has no accents, so the word list catches it, but ASCII-only text with no listed words was skipped. **Superseded by step 3 (22 Oct):** every transcript with a patient turn is translated; `looks_english` is now a hint only, used where "not English" is the safe reading.
- There is no `ROUTE_POLICY` entry for the task. Like every `/tasks/*` route, it is on the tasks router and accepts only the tasks-invoker SA. `ROUTE_POLICY` covers `/api/*` only, and its test fails on a stale entry.
- **Live smoke** (22 Oct, the demo project, fictional Spanish, Vietnamese, Russian and English turns): first call 2.6 s (cold client), then 1.4 s. The output was literal, with "No sé" → "I don't know", numbers kept and English detected (`text_en` null).

**Parity:** the goldens and tool declarations are unchanged.

## 22 Oct: language safety layer (step 3; deployed 22 Oct)

**Why now:** Gemini Live follows a patient who switches language mid-call, so Spanish answers already arrive on English calls. A survey proved seven ways today's English-only code read them less safely (`docs/LANGUAGES-PLAN.md` §2). **The rule:** a patient's language never makes a call or text less safe; what can't be read goes to a nurse; re-checks only raise.

**The seven known failures, before → after** (each has a test that failed before: `packages/voa_core/tests/test_language_safety.py`, `packages/voa_knowledge/tests/test_knowledge_language.py`):
1. `intent.is_red_flag("tengo dolor en el pecho y no puedo respirar")` False → True (Spanish list, negation aware: "no tengo dolor en el pecho" stays False).
2. `parse_yes_no("no sé")` "no" → None; `parse_trend("sí")` "same" → None; `_norm` keeps letters of every script. The typed parsers return None for any reply that is not plainly English (`parsers.not_english`).
3. `swelling_is_urgent("solo la pierna izquierda, está peor")` False → True; `severe_language` / `asks_for_a_person` / `expressed_worry` (new public functions, and the named checks) fail safe on non-English words with their own finding text ("answer in another language (may express worry)").
4. `classify("¿Qué dieta… fiebre y sangrado?")` diet/next day → other/immediate; a non-Latin SMS question is recorded, not dropped (`_read_questions_reply`, `_words`).
5. Knowledge: the keyword gate defers a non-English question (reason `language`), the output veto refuses a non-English draft, and examples/guidelines must be English to save or approve ("Content must be in English until language packs exist.").
6. DOB: `parse_dob(..., day_first=True)` for a non-English patient or reply; a spoken date in another language is translated (3 s, one try) and number words are read ("March fifth, nineteen fifty"); **the re-asks are bounded** (below).
7. STOP/HELP: "ALTO", "PARAR"… opt out; 24 languages' lists in `config/languages/keywords.yaml`.

**What changed.**
- **A. Always translate:** `/tasks/transcript-translate` translates every transcript with a patient turn (only an agent-only, English-looking one is skipped). `looks_english` is a hint, never a gate.
- **B. Post-call re-check** (`voa_core/language_safety.py`, called from `translation.after_translation`). Only for a call where the patient spoke another language (a translated patient turn, or a non-English `language_used`/`language`); all-English calls are untouched. Runs `red_flag_match` (plus translator phrasings, below) and the four free-text checks (negation aware) on each patient turn's English and the per-language red flags on the original; translates the free-text answers and `_raw` replies and each patient question in one more call; re-classifies questions on the English (more urgent priority kept; `text_en`, `text_original` stored). A more urgent finding raises `follow_up`, prepends `follow_up_reasons` entries with `source: "translation_check"`, prefixes `follow_up_reason`, appends `follow_up_history` (`by: translation_check`), clears a lower nurse override, logs `translation_check.raised` and writes an `audit_log` row. It stores `translation_check: {status, at, findings, from, raised_to, reopened, held}`; `done`/`partial` are never re-run. A failed transcript translation of a call that looks non-English raises to same day ("couldn't read the transcript in …", status `failed`, re-run on a later delivery). `review.merge_late_report` holds a translation-raised tier like a nurse's, so a milder late report never lowers it. **Emergency:** nothing can be said to the patient after the call; the raise sets `follow_up = emergency` with the review open, so `/api/follow-ups/open` and the console's emergency banner pick it up (tested).
- **Signed-off reviews:** reopened exactly the way a nurse's tier change reopens them (`status: reopened`, `reopened_by: translation_check`, `export_withdrawn`, the `ehr_outbox` record set to `withdrawn`), plus `translation_check.reopened: true`. Chosen over a separate "mark for nurse" because the reopen path already exists and puts the call back in every open list.
- **C. Voice tools** (declarations unchanged): `record_answer` refuses a non-English value for yes_no / yes_no_unknown / scale / choice / trend with "Record the answer in English, e.g. yes / no / unknown / a number". `record_patient_question` keeps a non-English question with `text_original`, `lang`, other, immediate. `answer_from_knowledge` defers it (`reason: language`). `verify_identity` reads day first when `state["patient_language"]` is set (the gateway sets it only for a non-English patient) or the reply is not English, asks once for the month name when an ambiguous numeric date (both parts ≤ 12) did not match, and translates an unreadable date (gateway: `GeminiTranslator(attempts=1, timeout_s=3)`).
- **The DOB retry rule (voice):** `MAX_DOB_ATTEMPTS = 2` failed attempts end the check (`mismatch_final`). Unparseable replies 1 and 2 are free; the 3rd and every later unparseable reply counts as a failed attempt (`unparseable_counted`). So four unreadable replies in a row end it; any mix ends after at most 2 real attempts. The ambiguous-date month question is free and asked once. By text, unchanged: 3 unreadable replies close the conversation, 2 mismatches file wrong person.
- **D. Texts** (`sms_service.read_inbound`): for a non-English patient or a text that does not look English, STOP/HELP and red flags are checked on the original first (all languages' lists), then the text is translated and the state machine reads the English (`decide(…, original=, original_lang=)`; its red-flag check also reads translator phrasings). The message gets `lang` and `body_en` (not for the DOB reply), answers keep `_original[field]`, questions keep `text_original` / `lang` and are classified on the English. A digits-only reply is not translated. A DOB the patient types is parsed day first first (no model call). **If translation fails:** nothing in the conversation moves, no reply, and the call is raised to same day: "couldn't read a text reply in Spanish (US): a nurse must check" (`text_translation_check`). An English patient's English text takes exactly today's path.
- **E. Keywords** `config/languages/keywords.yaml`: STOP/HELP for all 24 languages, red-flag phrases for es-US (chest pain, breathing, heavy bleeding, fainting, stroke, clot, self-harm) with negators and clause breaks. Loaded additively by `intent`; folded for accents; a test fails on any collision with an English reply word. `intent` also has translator phrasings ("lost consciousness", "severe bleeding", "taking my own life"…) used only on machine-translated text, so an English patient's texts read exactly as before.
- **G. Evaluation:** `config/languages/eval/safety.yaml` (82 fictional rows: es, vi, ru, ar, hi plus STOP words in 10 languages; red flags incl. negated, "don't know", yes/no, pain in words, day-first and spoken dates, mixed questions, one-sided swelling). `voa_core/language_eval.py` runs it through the live code; offline CI uses the corpus English (`test_language_eval.py`); `make eval-languages ENV=dev` uses Gemini.
- **Deploy:** `deploy/service.Dockerfile` and `.dockerignore` now ship `config/languages` (without it the extra lists do not load and only English applies; logged as `intent.keywords_missing`). `reports.REASON_SOURCES` and the nurse agent know `translation_check`.

**Live eval** (22 Oct, the demo project, gemini-2.5-flash, 82 rows, ~15 s): first run red-flag recall **0.952** ("Сильное кровотечение" → "Severe bleeding…" matched no English pattern) and DOB **0.857** ("cinco de marzo de mil novecientos cincuenta" → "March fifth, nineteen fifty"). Fixed with the translator phrasings and the number-word reader. Next two runs: every metric **1.0** (red-flag recall 1.0, negated not flagged 1.0, "don't know" read as yes/no 0, yes/no 1.0, pain in words 1.0, DOB 1.0, mixed questions immediate 1.0, swelling 1.0, STOP 1.0, HELP 1.0).

**What bit us / watch out.**
- **The keyword lists are drafts** (`status: draft`, "needs qualified translator review") and are used anyway: an extra opt-out or 911 reply is the safe direction. Get them reviewed before any non-English text goes out; "para", "fin", "baja" as whole messages now opt out.
- `looks_english` misses unaccented Spanish with no listed word ("quiero hablar con alguien" did, until the word list grew). Everything safety-relevant therefore either always translates (transcripts with a patient turn) or treats "not English" as the unsafe reading. The list is still a heuristic.
- Translators paraphrase: "perdí el conocimiento" became "I lost consciousness", which today's English list does not catch. The translator-phrasing list closes the known gaps; the eval is how new ones are found.
- Echoes in transcripts ("¿dolor de pecho? no" → "Chest pain? No.") will raise false emergencies on non-English calls. Accepted: raise only, a nurse reviews; English calls are not re-checked, so they get no new noise.
- Non-English free text recorded on a voice call makes the call same day at once (fail safe) even when it is benign; the re-check cannot lower it.
- The English parsers now return None (re-ask) for an English-looking reply that contains a listed foreign word or a non-ASCII letter (a name like "José" in a yes/no answer). Safe direction.
- The unparseable-DOB bound applies to English calls too (it was an unbounded loop in every language).

**Parity:** the goldens and the tool declarations are unchanged; the full suite passes.

- **Live persona eval after steps 2–3: 23/24** (`docs/evals/voice-personas-20261022.md`).
  - **The one failure (dob_retry) was not the new DOB rule.** The model *invented* a date of birth ("January first, nineteen eighty") in the same turn as its question, before the patient spoke, and checked it. That used one of the two attempts, and the persona's deliberately wrong first answer then ended the check.
  - **Proposed hardening:** `verify_identity` refuses to check unless the caller has spoken since the DOB question (`call_facts` already counts caller turns). That would stop a model-invented date from costing an attempt.
- **Live `make eval-languages` rerun before deploy:** every metric 1.0, all gates passed.
- **Deployed 22 Oct:** all six services healthy, smoke passes, no errors in the logs. **Not yet seen live:** the transcript-translate task on a real call (the smoke softphone call doesn't save a transcript), and the `config/languages` lists loading in the images. Place one test call with a Spanish reply, then check the transcript toggle and `translation.status`.

## 22 Oct: wrong-person status and DOB guard (not deployed)

**The failure.** Two demo calls on 20 Oct (…2083, …bb8d) had `outcome: unable_to_reach_wrong_person` but `call_status: left_vmail`. …bb8d was texted (`sms_follow_up: sent`); …2083 was only stopped by the 24-hour invite cooldown. The suspicion was that an AMD machine verdict had labelled a human-answered call as voicemail, so the missed-call text went to a stranger.

**The real sequence** (call docs, `call_events`, `reports`; statuses and timings only):
- …2083: answered 17:45:39.4. The agent opened at once (speak-first). At 7.5 s, `submit_report` was refused (AMD still pending). AMD `machine_end_beep` arrived at 17:45:46.8 (7.6 s); the gateway dropped the clip and filed **`voicemail_left` at 17:45:47.17** (`call_status: left_vmail`). The agent called `submit_report` again at 8.1 s, now accepted, and **`unable_to_reach_wrong_person` was saved at 17:45:47.71**. It then called `end_call` at 11.3 s. `completed` arrived at 17:45:58.5 (19 s); the status was already terminal, so it kept `left_vmail`. The missed-call text was refused by the cooldown.
- …bb8d: answered 20:30:20.1. AMD `machine_end_beep` came at 11.4 s; the drop's `voicemail_left` report was filed at 20:30:31.65, and the agent's `unable_to_reach_wrong_person` at 20:30:32.70. `completed` arrived at 20:30:33.7 and kept `left_vmail`. **The text was sent at 20:31:35.**
- Both "patient" transcript turns contain voicemail-greeting wording (checked as a yes/no only). **A machine answered both calls.** `left_vmail` was right; the wrong part was the outcome.

**Root cause.** After a voicemail drop the agent is muted (audio is sent only in `live`/`closing`), but the model keeps running. In both calls it filed its own report about the greeting it had heard, and `_save_report` saved it in any phase, overwriting the drop's `voicemail_left`. The model's `end_call` also moved the phase from `voicemail` to `closing`, which unmutes the agent over the clip.

**The latent bug from the brief is real too, though it was not what happened here.** `classify_completed` let non-reaching outcomes fall through to the machine inference. So a person answers, AMD says `machine_start` (released, not dropped) and the agent files wrong person: the call became `left_vmail` and was texted. When `completed` beat the report, nothing corrected it.

**The fix.**
- **Gateway (`session.py`).** After a drop, an agent report is not saved (`report.ignored`, `reason: voicemail_dropped`), and `end_call` no longer leaves the `voicemail` phase.
- **Lifecycle (`lifecycle.py`).**
  - `classify_completed`: a non-reaching outcome (wrong person, identity failed, consent declined) is `interrupted`, ahead of any machine verdict.
  - `report_demotes_voicemail`: in the reverse order (`completed` first), a **voice** non-reaching report changes an inferred `left_vmail` to `interrupted`.
  - **Why `interrupted`, not `success`:** `success` clears the call-list entry and closes the real patient's follow-up (the first-real-call bug). `interrupted` is the lifecycle's existing "answered, not reached" status: recallable, never texted. No new status was added.
  - The demotion is skipped in two cases. If the doc's outcome is `voicemail_left`, the drop really played. An SMS report is the other case: a by-text questionnaire that fails the DOB files wrong person onto the call it followed up, and that voicemail was real.
  - `reschedule_requested`, `questionnaire_completed` and `emergency_ended` were already `success` before the machine check.
- **The text, gated three times on the outcome** (`lifecycle.missed_call_text_block`: any filed outcome except `voicemail_left` blocks it, with a reason):
  1. `/tasks/call-terminal` does not queue it, and stamps `sms_follow_up: {status: not_sent, reason: "wrong person answered"}` (or "identity not verified", and so on).
  2. `handle_missed_call` re-reads the call and refuses with the same stamp.
  3. `send_invite` re-checks after its delay, because the report can land while the invite waits.
- **DOB guard (`voice/tools.py`, declaration unchanged).** With live `call_facts`, `verify_identity` returns `{status: "wait", message: "The caller hasn't answered yet. …"}` in three cases: the caller hasn't spoken, the agent's last words include an unanswered question (the DOB prompt), or the caller hasn't spoken since the last attempt. `state["dob_attempt_caller_turns"]` records the turn count at each attempt. A wait costs no attempt and records nothing. The step-3 rule is unchanged: unparseable replies 1–2 are free, the 3rd counts, 2 failed attempts end the check. With no `call_facts` (the parity voice sessions), nothing changes.
- **Not done: matching the DOB against the caller's words.** `call_facts` keeps no caller text. The model normalises speech ("three fifteen fifty" → "March 15th 1950"), and dates arrive translated or with day first. A text match would refuse real answers. The turn guard alone stops an invented date.

**Existing data.** `scripts/fix_wrong_person_left_vmail.py <project> [--apply]` (dry run by default; not applied). Dry run on the demo project: **2 `drop_overwritten`** (…2083, …bb8d: outcome back to `voicemail_left` from the drop's report, with `report_doc_id` and the follow-up fields; status stays `left_vmail`), **0 `inferred_voicemail`** (status → `interrupted`), and 3 left_vmail calls already correct. It adds a `correction` note and never touches `sms_follow_up` or report docs.

**Watch out.**
- `interrupted` is recallable, so a wrong-number call is auto-recalled, just as a wrong-person call with no AMD verdict always was. Blocking recalls after a wrong person is a separate decision.
- In the reverse order, the call-list entry keeps `last_call_status: left_vmail`, because `call-terminal` is not re-run (re-running it would double-count the run). The SMS gates still hold.
- The guard relies on `?` in the agent's output transcription to spot the DOB question. A prompt with no question mark is caught only from the second attempt on.

**Tests:**
- `test_lifecycle.py` covers the real sequence, report-then-completed, completed-then-report, the late AMD verdict, the SMS channel and the other outcomes.
- `test_sms.py` covers call-terminal, the handler and the invite after a late report: no text for wrong person, identity failed or consent declined, even at `left_vmail`; a real voicemail is still texted.
- `test_voicemail_drop_report.py` (gateway) shows the agent's report is ignored after the drop.
- `test_voice_tools.py`: the invented DOB, one attempt per caller turn, and the unparseable rule with the guard.
- The correction script has its own tests.

**Parity:** the goldens and tool declarations are unchanged.
- **Verified before deploy:** full suite green, goldens unchanged, live persona eval **24/24** (all three dob_retry runs pass with the new guard).
- **Data correction not applied.** On the demo project, the dry run of `scripts/fix_wrong_person_left_vmail.py` would restore 2 outcomes to `voicemail_left` and change 0 statuses. It writes to the live database, so run it with `--apply` only on the user's say-so.

## 22 Oct: Spanish voice pilot (step 4; deployed 22 Oct, Spanish held)

**What changed** (`docs/LANGUAGES-PLAN.md` step 4). Calls can now be spoken in a patient's language once that language has an **approved** pack. The Spanish post-op pack is a **machine draft**, so nothing changes for patients yet: Spanish patients stay held.
- **Pack format, loader, lint** (`voa_core/language_packs.py`): `config/campaigns/i18n/<campaign>.<lang>.yaml` with `language`, `campaign`, `status` (draft | in_review | approved | retired), `translated_by`, `reviewed_by`, `approved_by`, `approved_at`, `notes`, `scripts` (every key of the spec's scripts), `questions: {id: {ask, note?}}`, optional `custom_vocabulary`, and optional `sms_scripts` / question `sms*` (step 5). Wording only: ids, types, rules and triage are the English spec's (`localize` copies the spec and swaps only the wording). `load_pack`, `pack_problems` (the lint: every voice line and question, identical placeholders, 911 plus "hang up and call" in the emergency line, no extra keys, approvers required when approved), `voice_pack_for_call` (the gate), `summary` (the console rows). Format and how to edit: `docs/EDITING-QUESTIONS.md`, "Language packs".
- **Availability by channel:** `languages.available(campaign_id, channel="voice")` counts approved, lint-clean, complete packs. `channel="sms"` stays English-only (`SMS_PACKS_ENABLED = False`), because the shared SMS copy (invite, menu, DOB prompt, STOP/HELP in `copy.py`) is not in the pack format yet.
- **No English text to a non-English patient:** `lifecycle.missed_call_text_block` now also returns `languages.text_block(...)`: a patient whose language is not English and who is not on "Call in English" gets no missed-call text (so no 1/2/3 recall menu either), stamped `sms_follow_up: not_sent, "no approved Spanish (US) text wording"`. All three existing gates apply it (call-terminal, `handle_missed_call`, `send_invite` after its delay). English patients and calls with no language are unchanged.
- **Packs:** `post_op.es-US.yaml` (draft, `translated_by: "machine draft (Claude)"`, notes for the reviewer: the fever threshold said as "cien punto cuatro grados Fahrenheit", "incisión", "equipo de cuidados"; Section 1557 review required). `pre_op.es-US.yaml` too (draft, for a draft use case).
- **Voice wiring:**
  - Gateway `CallSession._choose_language`: the spoken language is the patient's when `voice_pack_for_call` returns a pack (approved, or a draft with the eval switch) and `call_in_english` is off; otherwise English (timeline `language.english`). It sets `state["call_language"]`, turns the knowledge base off for that call, adds the pack's vocabulary, and passes the language to `run_config`.
  - `run_config(cfg, vocab, language)`: for a non-English call, `speech_config` (same voice, `language_code=es-US`) and input-transcription `language_codes=[es-US, en-US]`. For English the RunConfig is exactly as before (tested).
  - **What bit us:** ADK's `Gemini.connect` overwrites the live request's speech config with the model's own (set once at agent build from `cfg.voice`), so `RunConfig.speech_config` alone is silently ignored. `voice/agent.py` now builds a `PerCallSpeechGemini` that keeps a per-call speech config when one is set, and connects exactly as before when none is (English).
  - Instruction: for `call_language`, the localized spec plus a language block after the first paragraph ("Speak only Spanish (US)… RESPOND IN SPANISH. YOU MUST RESPOND UNMISTAKABLY IN SPANISH", scripted lines as written, tool arguments in English, DOB passed as an English date, switch to English on request, interpreter path) and a short reminder at the end. English instruction byte-identical (parity goldens unchanged).
  - Tools: the closing `submit_report` returns comes from the pack. On a Spanish call a free-text answer that is not English is refused ("Call record_answer again with a short English summary… Do not ask the caller again"); see the eval below.
  - **`language_assistance_needed` was not added.** A new outcome touches the `submit_report` docstring (the tool-declaration golden), `reports.OUTCOMES`, the lifecycle, the nurse agent, analytics and the UI. Instead, on a non-English call only, the frame tells the model to record "Needs an interpreter: speaks <language>" and file `reschedule_requested`; `submit_report` then files it `routine_next_business_day` with "language assistance needed: call back with an interpreter" (a plain reschedule stays `none`). English reschedules are unchanged.
  - Voicemail: `Gateway.voicemail_clip(campaign, language=…)` uses the pack's voicemail and `PromptCache.ensure(text, language_code=…)`, cached per language; English key and render are unchanged (tested). Approved languages' clips are warmed at startup. **TTS:** `es-US` checked live on `gemini-2.5-flash-tts` (22 Oct): accepted (5.1 s clip; `es-ES` also accepted). es-US is used.
- **The eval switch:** `VOA_ALLOW_DRAFT_PACKS=1` is honoured only with `VOA_ENV` explicitly `dev` or `test` (logged and refused otherwise), only by the gateway's choice of spoken language, and never by `languages.available` (it cannot make anyone callable). Deployed services run `VOA_ENV=demo`.
- **Console:** Admin → Display → **Languages** card (read-only): each pack, its status, and "Voice: draft, needs translator review" / "Texts: not translated yet". `GET /api/languages` adds `packs`; the UI sample JSON was regenerated from the endpoint. The patient language select still says "(not available yet)" for Spanish. Not checked in a browser (types, unit tests and both builds pass). The nurse agent needed nothing: its hold and 409 paths already read `languages`.

**Evals** (real Gemini Live, local ADC, dev config, 3 runs, concurrency 3):
- **Spanish** (`docs/evals/voice-personas-20261022-es.md`): **20/21**. happy_es, red_flag_es (emergency), negated_red_flag_es (no emergency), patient_questions_es (Tylenol question → medication, same day; both questions recorded in English), switch_to_english (Spanish share of agent turns 1.0 before the request, ≤0.1 after) and voicemail_es (Spanish clip): all 3/3. dob_retry_es 2/3.
  - **The failure:** the model recorded `overall_feeling: "feeling جيد"` (an Arabic word in an English summary of a Spanish answer). Triage failed safe (the non-English free-text check made it a follow-up), but the answer was not English. Fixed by the refusal above (Spanish calls only).
  - **Recheck after the fix** (`…-es-recheck.md`): happy_es and dob_retry_es **6/6**.
  - The Spanish DOB ("quince de marzo de mil novecientos cincuenta") verified in every run; the model passed it as an English date.
- **English** (`docs/evals/voice-personas-20261022-step4-en.md`): **24/24**, unchanged from the baseline.

**How to approve a pack** (who, and what to change):
1. A **qualified medical translator** reviews the file line by line against `post_op.yaml` (and listens to a few eval calls: `evals/out/*.json` keep the Spanish transcripts). Wording fixes go into the pack.
2. The named **approver** (clinic's language-access or compliance owner) edits the pack file in a commit:
   ```yaml
   status: approved
   reviewed_by: "<translator's name and credential>"
   approved_by: "<approver's name>"
   approved_at: "YYYY-MM-DD"
   ```
   Commit history is the audit trail. Run `uv run pytest -q -p no:warnings packages/voa_core` (the lint runs on every pack).
3. Also get the Spanish red-flag and STOP/HELP lists in `config/languages/keywords.yaml` reviewed (still draft from step 3).
4. **Redeploy the gateway and the API together** (`make deploy SERVICES="gateway api"`): the API decides who is held, the gateway decides the spoken language, and the pack ships inside both images. Only after the deploy are Spanish patients dialled in Spanish; held ones are picked up by the next run.
5. Place one test call to a test handset as a Spanish patient; check the timeline `language` event, the transcript toggle and the voicemail.
- To withdraw: set `status: retired` (or `draft`) and redeploy. Patients go back to held.
- Texts stay English-only and are skipped for Spanish patients until step 5.

**Watch out.**
- Once Spanish is approved, a Spanish patient's missed call gets **no text** until step 5 (today such patients are never called, so nothing changes yet). Patients on "Call in English" still get the English text, as before.
- The pack is used as soon as it is approved; an approved pack with a wording error is lint-clean if placeholders and 911 are intact. The review is the control.
- The frame's own guidance phrases ("Do you have any other questions?") are English; the model says them in Spanish. The eval transcripts show it does, but they are not pack-reviewed wording.
- Switching to English mid-call: the model translates the remaining Spanish lines back to English itself (not reviewed wording).
- Spanish calls average ~145 s against ~98 s in English.

**Parity:** the goldens, the tool declarations and the voice-tool session goldens are unchanged; the full suite passes (2115).
- **Flaky test seen once (22 Oct):** `services/api/tests/test_ingest.py::test_a_batch_creates_patients_and_a_run_without_echoing_patient_data` failed in one full-suite run, then passed alone, in its file, in the API suite, and in two more full runs. It is not investigated yet. If it recurs, suspect test-order shared state (stores or caches) before timing. **Cause found (22 Oct):** neither order nor timing. The test checked for PHI with `"1950" not in r.text`, but the response carries two `entry_id`s, which are random uuid4 hex (`new_id()`). A 32-hex id contains "1950" about 1 time in 2,600, so with two ids the test fails about once in 1,300 runs, with no PHI in the response. Reproduced deterministically by pinning `routes_ingest.new_id` to an id containing 1950: the old assertion fails on the id. The ingest code was right; the check was wrong. **Fix (test only):** a `without_ids(r)` helper drops `entry_id` and `runs` before the "1950" check (the "Test Patient" check still scans the raw text), and a regression test, `test_the_phi_check_ignores_an_entry_id_that_happens_to_contain_the_birth_year`, pins the id. Clocks and caches were checked and ruled out: ingest uses `api.now()` for "in the past" and calling hours, and every test builds a fresh `MemoryStore`. (`budget.consume` still reads the wall clock when no `now` is passed; that is harmless here, because each test has its own store, but it is worth passing `api.now()` someday.)
- **Deployed 22 Oct and verified:** `/api/languages` lists the two draft es-US packs, with only English available; a Spanish "call now" still returns 409; all services healthy; smoke passes. **To go live in Spanish:** a qualified medical translator reviews `config/campaigns/i18n/post_op.es-US.yaml`, the approver fills `status: approved`, `reviewed_by`, `approved_by` and `approved_at`, then gateway and API redeploy together. Texts stay English-gated (Spanish patients get no text) until step 5.

## 22 Oct: Spanish texts (step 5; built, not deployed)

**What changed** (`docs/LANGUAGES-PLAN.md` step 5). Texts can now go out in a patient's language once that language's pack has **approved, complete, lint-clean SMS wording**. The Spanish wording is a **machine draft**, so nothing changes for real patients yet: a Spanish patient (not on "Call in English") still gets no missed-call text, stamped "no approved Spanish (US) text wording". The deployed demo can try it through the simulator (below).
- **The shared copy in the pack format.** `voa_core/sms_copy.py`: `ENGLISH` is `copy.py`'s English cut into 38 keys (`you`, `invite_intro`, `reply_header`, `menu_call_now`, `menu_pick_time`, `menu_by_text`, `calls_come_from`, `opt_out_line`, `unclear_choice`, `gave_up_choice`, `calling_now`, `calling_now_from`, `cannot_call_back`, `ask_for_time`, `unclear_time`, `gave_up_time`, `booked`, `booked_adjusted`, `booked_calls_from`, `booked_cancel`, `already_booked`, `your_callback`, `booking_canceled`, `nothing_to_cancel`, `emergency`, `opted_out`, `help`, `session_closed`, `ask_dob`, `dob_unreadable`, `dob_failed`, `survey_stopped`, `confirm_time`, `confirm_time_adjusted`, `confirm_time_reply`, `weekdays`, `months`, `when_format`). A pack's `sms_copy:` has the same keys and placeholders (`docs/EDITING-QUESTIONS.md` has the table). `EnglishCopy` calls `copy.py` (English is still sent from there, unchanged); `PackCopy` puts a pack's texts together the same way, and a test proves `PackCopy(ENGLISH)` rebuilds every `copy.py` text and `timeparse.humanize` byte for byte. The time interpreter's read-back (`confirm_time_prompt`) moved from `voa_agents/sms/agent.py` into `copy.py` (same text, re-exported). `question_noted` was already campaign wording (`sms_scripts`).
- **The conversation's language** is fixed when it opens: `send_invite` stamps `sms_language` on the session from `languages.text_language(language, campaign, call_in_english)` (the patient's language when it is not English, not on "Call in English", and available for texts). `conversation.session_copy(session)` picks the shared texts; `conversation.campaign_of(session)` returns the spec with the pack's SMS wording (`language_packs.localize_sms`: `sms_scripts` and question `sms*` only; ids, rules and triage stay English). The by-text closing reads the language from the filing snapshot (`sms_language`). Booked times are written in the language (`when_format`, "mié 4 mar, 3:00 PM"), also in `scheduled_for_text`. English sessions have no `sms_language` and take exactly today's path (the parity goldens, including `sms_texts.json` and both `sms_conversations_*`, pass unchanged).
- **Inbound** is step 3's path, unchanged: STOP/HELP and red flags on the original, then translation to English, then the state machine. One addition: "C" is never sent to the translator (like digits), so it works in every language. A recorded patient question is now echoed back in the patient's own words, not our English translation (this also applies to a non-English question on an English session).
- **Availability:** `SMS_PACKS_ENABLED` is gone. `languages.available(campaign, channel="sms")` counts approved packs that pass `pack_problems` **and** `sms_problems`. The missed-call text, the menu and the questionnaire go out in Spanish only when Spanish is available for texts and the patient is not on "Call in English" (then English, as before).
- **Real texts never use a draft** (`sms_service.send_sms`, new step 4b `wording_block`): every send to a session with `sms_language` re-checks that the pack is approved and SMS-complete. A draft passes only for the simulator's own session, while `sms.dry_run` is on and the provider is `DryRunSms`. A pack withdrawn mid-conversation therefore stops the texts ("No approved Spanish (US) text wording."); nothing goes out in English instead. Tests: `test_a_draft_pack_is_never_used_for_a_real_text`, `test_a_draft_flag_on_a_real_session_does_not_unlock_drafts`, `test_the_draft_wording_never_leaves_through_a_real_provider`.
- **Lint** (`language_packs.sms_problems`, `sms_warnings`, `sms_segment_report`): every `sms_copy` key, `sms_scripts` key and question `sms*` present; the English placeholders; **911**, **tel:911**, **STOP** and the reply letter **C** kept wherever the English has them; the menu lines start with 1/2/3; `ask_dob` and `dob_unreadable` ask for DD/MM (non-English dates are read day first); 7 weekdays and 12 months; then every text is rendered as sent with long fictional values (org "Northgate Regional Health", first name "Maximiliano", a caller ID, the longest booked-time shape, a 60-character question echo) and its encoding and segments counted (`sms_copy.segments`: GSM-7 160/153, extension characters count two; UCS-2 70/67, astral characters count two). **Thresholds: warn above 2 segments, fail above 3** (201 UCS-2 characters). Three is the most a single text may cost: the closings carry the clinic's medication reminder and cannot be shorter than three Spanish segments, and anything longer is billed per segment and arrives less reliably. The English rule is unchanged (plain GSM-7; English texts are not segment-limited). The SMS lint never affects voice: a pack with a bad text stays available for calls.
- **Budget:** `rate_limits/sms_sends` counted messages. It now counts **segments for UCS-2 texts** (`budget.consume(..., units=)`, `sms_service.budget_units`); a GSM-7 text still costs one unit, so English texting is counted exactly as before (a long GSM-7 English text was always one unit; changing that would change English behaviour, and the English texts are fixed and short). Released units match reserved ones.
- **Simulator** (Admin → Testing → "Test by text"): a **Patient language** picker with the 24 languages, English by default. A language whose pack has complete text wording is selectable (a draft is marked "(draft wording)"); the others say "(no text wording yet)" and are disabled (the API answers 409). With a draft the dialog says **"Draft wording, not approved"** before and during the test. The API: `POST /api/sms/test/start {phone, diabetic, language}` stamps `sms_language`, `sms_draft_wording` and the patient's language on the simulator's session and call, and returns `wording: {language, name, status, approved, label}`. Replies go through `handle_inbound`, so Spanish replies are translated by Gemini (deployed) and the safety checks run as for a real text. STOP words in any language are refused by the simulator (they would opt the number out for real). The DOB hint shows 15/03/1950 for a non-English test. **Not checked in a browser** (types, unit tests and both builds pass).
- **Wording:** `post_op.es-US.yaml` now has `sms_copy`, `sms_scripts` and every question's `sms*` (draft, formal "usted", digits, "Responda SÍ o NO", DOB "DD/MM/AAAA", examples "mañana 3pm"; the invite ends "Responda STOP para cancelar."). `notes` lists what the reviewer should check. `pre_op.es-US.yaml` has no text wording (optional; pre_op stays voice-only in Spanish).

**Encoding and segments per Spanish text** (rendered with the long sample values; numbered questions include "(n/9) "):

| Text | Enc. | Units | Seg. | | Text | Enc. | Units | Seg. |
|---|---|---|---|---|---|---|---|---|
| invite | GSM-7 | 220 | 2 | | preamble | UCS-2 | 134 | 2 |
| unclear_choice | UCS-2 | 96 | 2 | | overall_feeling | UCS-2 | 56 | 1 |
| gave_up_choice / _time | UCS-2 | 117 | 2 | | pain_present | UCS-2 | 49 | 1 |
| calling_now | GSM-7 | 56 | 1 | | pain_severity | UCS-2 | 78 | 2 |
| cannot_call_back | UCS-2 | 111 | 2 | | pain_location | UCS-2 | 52 | 1 |
| ask_for_time | UCS-2 | 89 | 2 | | pain_location (site known) | UCS-2 | 118 | 2 |
| unclear_time | UCS-2 | 74 | 2 | | pain_trend | UCS-2 | 56 | 1 |
| booked | UCS-2 | 123 | 2 | | fever_reported | UCS-2 | 106 | 2 |
| already_booked | UCS-2 | 110 | 2 | | incision_concerns | UCS-2 | 142 | **3** |
| booking_canceled | GSM-7 | 95 | 1 | | incision_description | GSM-7 | 27 | 1 |
| nothing_to_cancel | GSM-7 | 48 | 1 | | legs_swollen | UCS-2 | 64 | 1 |
| emergency | UCS-2 | 160 | **3** | | swelling_description (all variants) | UCS-2/GSM-7 | 38–50 | 1 |
| opted_out | UCS-2 | 68 | 1 | | sleeping_well | UCS-2 | 45 | 1 |
| help | GSM-7 | 193 | 2 | | blood_sugar_status | UCS-2 | 137 | **3** |
| session_closed | UCS-2 | 153 | **3** | | has_questions | GSM-7 | 92 | 1 |
| ask_dob | GSM-7 | 117 | 1 | | unclear_answer (longest question) | UCS-2 | 158 | **3** |
| dob_unreadable | GSM-7 | 92 | 1 | | ask_question / another_question | UCS-2 | 51 / 59 | 1 |
| dob_failed | UCS-2 | 126 | 2 | | question_noted | UCS-2 | 132 | 2 |
| survey_stopped | UCS-2 | 112 | 2 | | question_noted_last | UCS-2 | 160 | **3** |
| confirm_time (both) | UCS-2 | 112 | 2 | | cut_short / file_failed | UCS-2 | 136 / 135 | **3** |
| | | | | | closing_routine / closing_urgent | UCS-2 | 198 / 194 | **3** |

48 texts: 38 UCS-2; 19 one segment, 19 two, 10 three (warnings), none above three. The invite and the DOB prompts are GSM-7 only because they avoid á/í/ó/ú: **a patient or org name with an accent makes the invite UCS-2** ("María" with the long org name and a caller ID: 214 units, **4 segments**). The lint does not fail on that (the name is data, not wording); the budget counts it.

**Live run** (22 Oct, local ADC, `VOA_ENV=dev`, the demo project, the real `GeminiTranslator`, `sms_simulator.start(language="es-US")` with `DryRunSms` and a MemoryStore, fictional patient Jordan Rivera, ~14 s): invite → "3" → DOB gate (DD/MM/AAAA) → "15/03/1950" verified (not translated) → seven questions answered in Spanish ("bastante bien, gracias", "sí", "ocho", "en la rodilla derecha, como un pinchazo", "peor", "no", "no", "sí", "solo la pierna izquierda y cada día está más hinchada", "no sé" twice, "¿Cuándo puedo volver a manejar?", "no"). Every outbound text was the pack's Spanish; Gemini translated each reply ("ocho" → "eight", "no sé" → "I don't know", the question → "When can I drive again?"). Recorded English values: pain 8, trend worse, swelling description in English, sleeping_well **unknown** (asked once more in Spanish, as an English "I don't know" is), the question as activity/next business day with the original kept. Triage **urgent_same_day**: "pain 8/10, pain worsening, leg or foot swelling (one side, worsening, or painful)". The closing was the Spanish urgent closing with the questions line and the medication reminder. Script: kept out of the repo (scratchpad).

**What bit us.**
- **Accents decide the cost.** GSM-7 has é, ñ, ¿, ¡ but not á, í, ó, ú, so most Spanish texts are UCS-2 at 70/67 characters a segment. The first Spanish urgent closing was 5 segments; the closings and the questionnaire lines were rewritten short (and "su equipo de cuidados" became "su equipo" in the urgent closing) to fit three.
- The first version echoed a patient's question back in our **English translation** ("Anotado: "When can I drive again?"") to a Spanish speaker. Found in the live run; the echo now uses their own words.
- "C" for a non-English patient used to go to the translator (it has a letter). It is now passed through untranslated.
- "no sé" by text is re-asked once before it is recorded as unknown. That is today's English behaviour for "I don't know" on a yes/no question, kept for parity; it is never recorded as "no".
- `ruff format .` reformats the whole repo; it is part of the checks, but note it also touches files another agent may be editing.

**Carrier registration (before any Spanish text goes live).** The A2P 10DLC campaign must cover **Spanish message content**: add Spanish sample messages (the invite with "Responda STOP para cancelar.", the HELP reply, a question and a closing) and confirm the campaign's opt-out and help language covers Spanish. STOP and HELP keep working in English; the Spanish words (ALTO, PARAR, AYUDA…) are handled by our code. If Twilio Advanced Opt-Out is on (it answers keywords instead of us, `reply_to_compliance_keywords` off), check its keyword list and reply language: unless configured, its replies are the English ones and it may not treat ALTO as an opt-out on its side. Also confirm the consent the clinic collects covers texts in Spanish.

**How to approve the Spanish texts** (the same pack approval as voice; one approval covers both channels):
1. The qualified medical translator reviews `sms_copy`, `sms_scripts` and every question's `sms*` in `config/campaigns/i18n/post_op.es-US.yaml` against `voa_core/sms_copy.py` (`ENGLISH`) and `post_op.yaml`, ideally by playing a conversation in the simulator. Keep the texts short: run `uv run pytest -q -p no:warnings packages/voa_core` (the lint fails a text above 3 segments and lists those at 3).
2. Get the Spanish STOP/HELP and red-flag lists (`config/languages/keywords.yaml`) reviewed too, and the carrier registration above.
3. The approver sets `status: approved`, `reviewed_by`, `approved_by`, `approved_at` in a commit. A pack with complete, lint-clean texts is then available for both voice and texts; to approve voice only, remove `sms_copy` (texts then stay off for that language).
4. Redeploy the API with the gateway (`make deploy SERVICES="gateway api"`). The next missed call of a Spanish patient gets the Spanish invite. Conversations already open keep the language they opened in.
5. To withdraw: `status: retired` and redeploy; open Spanish conversations stop receiving texts (never English).

**Parity:** the goldens and tool declarations are unchanged; full suite **2174 passed, 12 skipped**; UI tsc, vitest (647), both builds pass. Not deployed.
