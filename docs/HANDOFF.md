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

1. `docs/PLAN.md`: why the system is shaped this way.
2. `docs/spikes/voice.md`: the measured voice behaviour.
3. `services/gateway/src/voa_gateway/session.py`: one call, end to end.
4. `services/api/src/voa_api/dialer.py` then `routes_tasks.py`: how dialing works.
5. `docs/RUNBOOK.md` and `docs/DEPLOY.md`.

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
