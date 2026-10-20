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
| SMS number and webhook | A number has one inbound SMS webhook | PLAN.md §11.9; then `sms_from_number` and the webhook to `/twilio/sms` |
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
- Sending stays in dry-run, because the Twilio number's inbound SMS webhook still points at the old GE-CX `sms-followup-svc`. A number has one inbound webhook, and the owner chose not to move it yet.
- "Test by text" in the dashboard runs the whole text flow against the dry-run provider. Allowlisted numbers only, a fictional patient, refused unless dry-run.
- To go live:
  1. Pick the receiving number: move 5695's SMS webhook to `<gateway_url>/twilio/sms` (the old system stops getting replies), or use a second number added to the A2P campaign.
  2. Confirm the new copy with the A2P campaign.
  3. Set `sms_from_number`, then `sms_dry_run = false`.
  4. Deploy the API. Sends stay limited to the dial allowlist.

## 19 Oct: shared SMS number switch (ADK side)

**The design** (contract: `docs/sms-number-switch.md`).
- The Twilio number's incoming-SMS webhook is the switch. Nothing else stores state.
- `GET/POST /api/sms/control` reads the number or flips it (`take` / `give`). Only `SmsUrl` and `SmsMethod` are written. Every flip goes in `sms_control_log`.
- `send_sms` refuses real texts while the other system has the number. It fails closed if the number can't be read, and the status is cached for 30 s. Dry-run sends are not gated.

**The dashboard** has a top-right indicator and a Settings → "SMS number" card (take control / give control to GE-CX).

**Config.**
- Terraform: `sms_switch_enabled` and `sms_peer_webhook_url`, set as `VOA__SMS__SWITCH_ENABLED` / `VOA__SMS__PEER_WEBHOOK_URL`.
- The number is `sms.from_number`, falling back to the voice number.

**The GE-CX side** is built by the GE-CX session from the same contract (its own repo; this session doesn't edit it).
- Until GE-CX has its send rule, don't trigger GE-CX texts (including missed-call invites) while ADK is active.
- This system still sends in dry-run: after taking control it receives replies but doesn't answer them by text, until `sms_dry_run = false`.
- **SMS sending turned on (19 Oct, owner's go-ahead).**
  - `sms_dry_run = false`, and `VOA__TELEPHONY__SMS_PROVIDER = "twilio"`. With the old default (`fake` from app.yaml), turning dry-run off would have sent nothing, silently.
  - Real texts are still limited to allowlisted numbers with SMS consent, and only while this system has the shared number.
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
