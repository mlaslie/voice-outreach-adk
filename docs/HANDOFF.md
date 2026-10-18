# Handoff

The state of the system as of 17 Oct 2026, and what a new owner needs to know.

## What exists

| Piece | State | Where |
|---|---|---|
| Voice agent | **Built.** ADK live agent on `gemini-3.8-live`. Its instruction is generated from `config/campaigns/post_op.yaml`. Tools verify the DOB server-side, normalize answers with the shared survey parsers, triage deterministically, and validate the report before telling the model "ok". | `packages/voa_agents/voice` |
| Gateway | **Deployed** (public). Signed Twilio webhooks, media streams, AMD hold and voicemail drop, barge-in, closing drain, stereo recording, the two guards (exit gate and auto-hangup), and the browser softphone with HMAC tokens. | `services/gateway` |
| API | **Deployed** (private). Dashboard REST/SSE, Cloud Tasks dialing (call-slot guard), scheduled runs, auto-recall, allowlist, budgets, follow-up overrides, recordings, test calls, the SMS follow-up and the expiry cron. | `services/api` |
| Dashboard | **Deployed** behind Cloud Run IAP. Pure BFF: the browser only talks to its own origin; the server calls the API with an ID token. | `ui/` |
| SMS follow-up | **Built and deployed in dry-run.** It logs instead of sending until `sms_dry_run = false`. | `services/api/src/voa_api/sms_service.py` |
| Nurse assistant | **Deployed** (private, A2A + A2UI v0.9, Cloud Run). ADK agent with 25 tools over the API; sessions and proposals in Firestore; confirm-by-id only. Not yet registered in Gemini Enterprise. Report sign-off from the old agent is not ported (the API has no sign-off endpoint). | `services/nurse` |
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
- Decide whether to add a report sign-off endpoint to the API (the old agent had one).
