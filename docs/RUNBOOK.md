# Runbook

## Finding one call

Every log line from the gateway and the API is structured JSON. Each carries the `event`, `call_id`, `session_id` and `campaign` labels plus a trace id.

```text
resource.type="cloud_run_revision"
labels.call_id="<CA... or browser_...>"
```

| Where to look | What's there |
|---|---|
| `call_events/{call_id}` | The call's millisecond timeline: `media.start`, `agent.release`, `agent.audio_start`, `tool.call`/`tool.response`, `agent.interrupted`, `drain.mark_sent`/`media.mark_ack`, `call.ended`. This is the first stop for anything latency-related. |
| `transcripts/{call_id}` | The turns: agent and patient |
| `calls/{call_id}` | The lifecycle doc: status, timestamps, AMD verdict, report fields |
| `gs://<project>-voa-recordings/recordings/<call_id>.wav` | The stereo recording: left = patient, right = agent |

## Alerts

Each alert below is a log-based metric defined in `infra/modules/observability`.

### `live_errors`: the run_live loop failed during a call

- **First step:** read `jsonPayload.exception` on the `live.error` entry.
- **Past cause: logging key collision.** A `KeyError: Attempt to overwrite 'name' in LogRecord` came from a log `extra=` key colliding with a LogRecord attribute. It is fixed by `voa_core.logging.safe_extra`; use that for any new `extra` dict built from arbitrary keys.
- **Gemini Live errors:**
  - Check that the model and region pair is valid (`models.live`, `models.live_location`; Live models are regional, never `global`).
  - Check quota (1,000 concurrent sessions per project on pay-as-you-go).

### `drain_timeouts`: goodbye playout not confirmed before hang-up

- **Cause:** the provider never echoed the closing `mark` within 30 s.
- **Impact:** the caller may have heard the goodbye cut off.
- **What to check:** the call's timeline for `drain.mark_sent` with no `media.mark_ack`. A mark that echoes instantly while the caller heard nothing means the problem is upstream (the model produced no closing audio).

### `dial_failures`: the provider refused the call or could not be reached

- `dial.rejected` with a 21211-style code is a bad or unroutable number. The entry is parked as `blocked`, not retried.
- `dial.error` is network or provider downtime (any other exception from the provider call). It is NOT retried by Cloud Tasks: the dialer gives the hourly dial-budget token back and raises a 502 `DialError`, and `/tasks/dial` catches it and answers 200. The entry goes back to `pending` with `last_call_status: dispatch_failed` and `last_error` "Could not reach the telephony provider."; the run counts it as failed and lists it in its `errors`. The patient is dialled again only by a later run (a scheduled or due run, or a nurse's "Call again"). Cloud Tasks retries a dial task only when the handler itself fails (an unhandled exception, a 5xx).

### `invalid_reports`: a call report failed validation

The report errors are kept on `calls/{id}.report_errors`; the tool validates before it tells the model "ok", so this should be rare. Usually it means a schema change in `voa_core.reports` without a matching change in the voice tools.

### `http_errors`: unhandled exception in a request

Read the stack trace. Responses never leak `str(exc)`.

### `no_twilio_token`: Twilio webhooks are being refused

A `secret://` reference has no version. Run `scripts/set-secrets.sh <project>`. No redeploy is needed: empty secrets are not cached.

### Gateway health check failing

1. Run `gcloud run services describe voa-gateway`.
2. Check the latest revision's startup probe (`/health`).

**Note:** Cloud Run's front end reserves `/healthz`, so it never reaches the container; use `/health`.

## Answering-machine strategy

`amd.strategy` (`VOA__AMD__STRATEGY`):

- `speak_first` (default): the agent opens about 1 s after pick-up while Twilio's detection keeps running. A machine verdict (DetectMessageEnd fires at the beep) clears the agent and drops the clip. Nothing said before the beep is recorded.
- `hold`: the agent waits for the verdict, which adds about 4 s of silence.

While the verdict is pending, the agent cannot take any exit except the emergency one. A verified identity overrides a late machine verdict.

## Stopping things

| Need | Do |
|---|---|
| Stop scheduled dialling | `gcloud scheduler jobs pause voa-dispatch-due --location <region>` |
| Stop all dialling now | Set `VOA__DIALING__MAX_DIALS_PER_HOUR=0` is NOT a stop (0 = unlimited). Instead remove numbers from the allowlist, or pause the `voa-dial` queue: `gcloud tasks queues pause voa-dial --location <region>` |
| Stop SMS sends | `gcloud tasks queues pause voa-sms --location <region>`, or set `VOA__SMS__DRY_RUN=true` and redeploy the API |
| "Test by text" while texts are live | Nothing to do: the simulator always runs simulated (dry-run provider, no queue) in its own `simulator__*` collections, whatever `VOA__SMS__DRY_RUN` says. It stops only with `VOA__SMS__ENABLED=false`. Its collections hold fictional test data only and can be deleted at any time; each document expires `sms.simulator_ttl_days` (7) after its last write (Firestore TTL on `expire_at`; deletion can lag by up to a day) |
| Cancel a manual run | Dashboard → Cancel run (sets `cancel_requested`; queued dial tasks then skip) |

## Check the number's webhooks

This system owns the Twilio number outright (since 21 Oct) and no longer watches its settings, so a repointed webhook goes unnoticed: patients' replies land elsewhere while texts keep going out. Check it after anyone touches the number in the Twilio Console, and whenever replies seem to stop arriving.

```
uv run python scripts/check_twilio_number.py demo
```

Read-only. It uses the Twilio secrets in Secret Manager and prints only the URLs and the number's last 4 digits. Expected:

- `SmsUrl` is `<gateway_url>/twilio/sms`, method POST.
- `VoiceUrl` is empty: there is no inbound voice (outbound calls carry their own TwiML).
- No SMS application on the number (it would override `SmsUrl`).

If it fails, fix the number in the Twilio Console (Phone Numbers → the number → Messaging: "A message comes in" → Webhook, `<gateway_url>/twilio/sms`, HTTP POST), then rerun the check.

## Scaling calls

Simultaneous calls are capped by `dialing.max_concurrent_calls`: Terraform `max_concurrent_calls`, default 3 in the demo, at most 20. A dial task that finds every slot taken re-queues itself `dialing.slot_wait_s` later (15 s). It gives up after `dialing.max_slot_wait_s` (1 h), and the patient is recorded as deferred and stays pending. Before raising the cap, check each of these limits:

| Limit | Where | What it means for N calls |
|---|---|---|
| Gemini Live concurrent sessions | Vertex AI quota for the Live model in `models.live_location` | One session per call. Keep N plus browser test calls under the quota (see `live_errors` above). |
| Twilio calls per second | Twilio account (outbound CPS, 1 by default) | The dial queue sends at most 1 dial per second, one at a time (`max_dispatches_per_second = 1`, `dial_concurrency = 1`). Raise the queue rate only after Twilio raises the CPS. |
| Hourly dial budget | `dialing.max_dials_per_hour` (30) | At 3 calls of about 4 minutes, a run can place about 45 dials an hour, so the budget, not the slots, stops a large run ("budget_exhausted"). Raise it with N. |
| Patients per run | `dialing.max_dials_per_run` (50) | A run larger than this is refused (a nurse's run) or cut (a scheduled run). |
| Gateway | `module "gateway"` in `services.tf`: `concurrency = 8`, `max_instances = 10`, 2 vCPU and 2 GiB, CPU always on, session affinity | Each call holds one media WebSocket. One instance carries 3 calls with room to spare: audio transcoding is C (`audioop`), and the 8 kHz recording is about 1 MB per call-minute in memory. 80 streams in total is far above any dial cap. Set `gateway_min_instances = 1` when calls start cold. |
| Waiting load on the dial queue | 1 dispatch per second, shared by dials and slot re-checks | With W patients waiting, each re-checks about every max(15 s, W s). A freed slot can sit idle that long; it is not lost. |

`dial_concurrency` is NOT the call cap. It is how many `/tasks/dial` requests run at once. Keep it at 1. One at a time makes the free-slot check and the dial that follows it race-free, and a dial takes about a second, so it already matches Twilio's 1 call per second. Terraform refuses `dial_concurrency > max_concurrent_calls`.

**A run that never finishes.** A dial task that Cloud Tasks gave up on (5 attempts within 15 minutes, after real errors only) would leave its patient uncounted. `/cron/dispatch-due` closes such a run once it has made no progress for about 1 h 40 min (the full slot wait, the error retries and one call) and none of its calls is still live. It logs `run.reconciled`; still-pending patients are deferred (a scheduled run carries them to the next opening). To end a stuck run sooner, cancel it from the dashboard.

**A patient stuck on "calling".** A row stays `calling` when its call never gets a final status on the row: no terminal callback from Twilio, a gateway crash, a lost `/tasks/call-terminal` task, or a dial task that died after it claimed the row. Every tick, `/cron/dispatch-due` checks the `calling` rows (at most 50) against the call doc of their current attempt (`recover_stranded_entries`). A call with a terminal status that ended 5 minutes ago or more gives the row what that status implies (`completed` for success, otherwise `pending`). A call with no terminal status, or no call doc at all, older than the in-flight TTL (`max(dialing.call_timeout_s, voice.max_call_seconds + 120 s)`, 17 min by default) puts the row back to `pending` with `last_error` "call never finished: re-queued". A live call inside the TTL is never touched. Each fix logs `entry.stranded_recovered` (entry id, attempt, reason, call id, run id; no phone or name) and is recorded on the current run if that run claimed the row. Auto-recall and the missed-call text are not triggered by a recovery; the re-queued patient is dialled by the next run. Nothing to do by hand; to find them, filter the logs on `jsonPayload.event="entry.stranded_recovered"`.

## Known behaviours and traps

| Symptom | Cause | Status |
|---|---|---|
| `allUsers` binding refused | The org policy has domain-restricted sharing | Public services use `invoker_iam_disabled` instead |
| A follow-up text shows "Text not sent: ... (code 8021)" | The Twilio API key the system uses lacks Messaging permissions (calls still work: the key has voice permissions) | Twilio Console → API keys → the key → add Messaging (send) permissions; no redeploy needed |
| The smoke test's browser voice step fails on the first run after a deploy, then passes | The new gateway's first Live session is cold (about 3 s to first audio); the scripted caller answers after 1.2 s of silence and talks over the opener | Rerun the smoke test; only a repeat failure is real |
| A smoke run right after a deploy shows a 500 with 0 ms latency | The request hit the previous revision while Cloud Run drained it (the log line names the old revision) | Transient; rerun the smoke test a minute later |
| First call after a deploy has a slow first agent audio (about 20 s once) | A cold Live connection on a freshly replaced instance | Typical latency is about 1 s |
| Query fails with "requires an index" | A Firestore composite index is missing | The store sorts client-side when filter and sort fields differ; don't add composite queries without declaring the index in Terraform |
| A phone number became an int | YAML-parsing of env values | Env overrides now stay strings |
| A service behaved as `dev` | A file overrode `VOA_ENV` | `VOA_ENV` is now authoritative |
| A service stays on its old revision after a deploy ("Extra inputs are not permitted") | A shared `VOA__*` env var was added for a config section the running image doesn't know (strict config fails closed) | Deploy every service that reads the new section together: `scripts/deploy.sh <env> gateway api` |
| The agent says goodbye but the call stays up | The model never calls `end_call` | The gateway hangs up 5 s after the report and turn complete (`hangup.auto_after_report`) |
| The agent takes an exit right after asking a question | Live-model behaviour | `submit_report` refuses non-emergency exits while a question is unanswered |

## Secrets rotation

| Secret | Rotation |
|---|---|
| Twilio | Add a new secret version (`scripts/set-secrets.sh`), then roll the revision: `scripts/deploy.sh <env> gateway api` |
| Softphone key | Add a new version. Outstanding test-call links (5 minutes) stop working. |

## Costs to watch

| Cost | Where it comes from |
|---|---|
| Gemini Live | Audio tokens, about 25 per second each way. AMD hold plus the voicemail drop keeps mailboxes off the model. |
| Gateway | `min_instances = 1` with CPU always on. Set `gateway_min_instances = 0` for an idle demo; the first answered call then waits on a cold start. |
