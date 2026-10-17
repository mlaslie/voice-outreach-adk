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
- `dial.error` is network or provider downtime. Cloud Tasks retries.

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

## Stopping things

| Need | Do |
|---|---|
| Stop scheduled dialling | `gcloud scheduler jobs pause voa-dispatch-due --location <region>` |
| Stop all dialling now | Set `VOA__DIALING__MAX_DIALS_PER_HOUR=0` is NOT a stop (0 = unlimited). Instead remove numbers from the allowlist, or pause the `voa-dial` queue: `gcloud tasks queues pause voa-dial --location <region>` |
| Stop SMS sends | `gcloud tasks queues pause voa-sms --location <region>`, or set `VOA__SMS__DRY_RUN=true` and redeploy the API |
| Cancel a manual run | Dashboard → Cancel run (sets `cancel_requested`; queued dial tasks then skip) |

## Known behaviours and traps

| Symptom | Cause | Status |
|---|---|---|
| `allUsers` binding refused | The org policy has domain-restricted sharing | Public services use `invoker_iam_disabled` instead |
| First call after a deploy has a slow first agent audio (about 20 s once) | A cold Live connection on a freshly replaced instance | Typical latency is about 1 s |
| Query fails with "requires an index" | A Firestore composite index is missing | The store sorts client-side when filter and sort fields differ; don't add composite queries without declaring the index in Terraform |
| A phone number became an int | YAML-parsing of env values | Env overrides now stay strings |
| A service behaved as `dev` | A file overrode `VOA_ENV` | `VOA_ENV` is now authoritative |
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
