# ui — outreach dashboard

The staff-facing dashboard for the voice-outreach system: a Next.js 15 app
with two front ends over the same data.

- `/` — the operations dashboard: every call with its outcome, transcript,
  recording and survey answers; the call list (stage patients, import CSV,
  start a batch); the outbound allowlist and SMS consent; SMS follow-ups.
- `/nurse` — the nurse console: today's schedule, completed calls and
  texts, and the follow-up worklist. `/nurse` shows **fictional demo data**
  (`src/lib/nurse/demo.ts`); `/nurse?data=live` shows the same screens on
  real data.

## Architecture: a pure backend-for-frontend

The browser never calls the backend. Every request goes to this app's own
same-origin route, which forwards it on the server:

```
browser ──same origin──▶ /api/backend/<path>  (src/app/api/backend/[...path]/route.ts)
                            │  1. method + path must be on the allowlist   (src/lib/bff.ts)
                            │  2. writes must be same-origin + JSON          (src/lib/bff.ts)
                            │  3. Authorization: Bearer <Google ID token, audience = API_BASE_URL>
                            │     X-Acting-User: <IAP signed-in user email>
                            ▼
                        ${API_BASE_URL}/api/<path>
```

- **Allowlist.** Only the endpoints the UI uses are forwarded
  (`ALLOWED_ROUTES` in `src/lib/bff.ts`); anything else is a 404 before the
  backend is contacted. Path segments are decoded and checked individually,
  so `%2F` / `..` cannot reach another endpoint.
- **Cross-site writes are refused.** POST/PATCH/PUT/DELETE need
  `Sec-Fetch-Site: same-origin` (or, if the browser does not send it, an
  `Origin` naming this host), and any body must be `application/json`.
- **Identity.** The route mints a Google-signed ID token for the audience
  `API_BASE_URL` with the service's own identity (`google-auth-library`),
  and passes the IAP user (`x-goog-authenticated-user-email`, without the
  `accounts.google.com:` prefix) as `X-Acting-User`. The backend authorises
  each call from the token (is this the dashboard?) and the acting user
  (who is doing it, and may they?). There is no API key anywhere.
- **Streaming.** Responses are streamed through unchanged: status codes,
  JSON `{"detail": ...}` errors, ranged audio for recordings, and the live
  call feed `GET /api/backend/calls/stream` (`text/event-stream`).

Public client functions (`api.*` in `src/lib/api.ts`, the SMS functions in
`src/lib/followups.ts`) and the types in `src/lib/types.ts` are the contract
the components use; only their transport changed.

## Environment variables

| Variable | When | Purpose |
| --- | --- | --- |
| `API_BASE_URL` | runtime, server only | The backend's base URL (its Cloud Run URL in a deployment). Also the ID-token audience. A `http://localhost…` URL gets no token. Unset ⇒ every `/api/backend/*` call returns 503. |
| `NEXT_PUBLIC_ORG_NAME` | build | Organisation name; appears in the SMS consent statement, so it must match the backend's configured name. |
| `NEXT_PUBLIC_SUPPORT_EMAIL` | build | Help / withdrawal address under the consent checkbox. |
| `NEXT_PUBLIC_PRIVACY_URL`, `NEXT_PUBLIC_TERMS_URL` | build | Policy links beside the consent checkbox. |
| `NEXT_PUBLIC_LOGO_URL`, `NEXT_PUBLIC_LOGO_WIDE_URL` | build, optional | Nurse-console logos (a path under `public/` or a full URL). Unset shows a placeholder mark. `public/brand/` is gitignored: never commit a real organisation's logo. |
| `APP_MODE` → `NEXT_PUBLIC_APP_MODE` | build | `nurse` (default; service `voa-ui`, the nurse console at `/nurse`, plus `/classic`) or `admin` (service `voa-admin`, the admin console at `/admin`). The Docker build refuses any other value. `src/middleware.ts` serves only that build's pages, and the BFF refuses the other build's API routes (`src/lib/bff.ts`). Locally: `NEXT_PUBLIC_APP_MODE=admin npm run dev`. |
| `ADMIN_CONSOLE_URL` | runtime, nurse service, optional | The admin console's URL. Set ⇒ the nurse sidebar shows an "Admin console" link and the SMS indicator links there. |
| `NURSE_CONSOLE_URL` | runtime, admin service, optional | The nurse console's URL. Set ⇒ the admin sidebar shows "Open nurse console". |

`NEXT_PUBLIC_*` values are compiled into the client bundle, so they are
build args (see `Dockerfile`, `cloudbuild.yaml`) and a change needs a
rebuild. They are branding only — never a secret or a backend URL. All are
read in `src/lib/site.ts`. Colours and fonts are in `src/app/theme.css` and
`src/app/layout.tsx`; the theme's token names are what components use, so a
re-skin changes values only.

Nurse sign-off (`POST /api/calls/{sid}/sign-off`, `POST /api/calls/sign-off`)
is a backend endpoint for the nurse assistant; the dashboard only shows the
resulting `review` on a call, so it is deliberately not on the BFF allowlist.

## Local development

Needs Node 22 or newer.

```bash
cd ui
cp .env.local.example .env.local      # fictional values; API_BASE_URL=http://localhost:8081
npm ci
npm run dev                            # http://localhost:3000
```

Run the backend API locally on port 8081. With `API_BASE_URL=http://localhost:8081`
the BFF sends no ID token and, with no IAP in front, no `X-Acting-User`
either, so the local backend must be in its development auth mode. To
exercise a deployed backend from your machine instead, set `API_BASE_URL` to
its URL and use Application Default Credentials that can mint an ID token
for that audience (e.g. a service account via
`gcloud auth application-default login --impersonate-service-account=...`);
plain user credentials cannot.

Do not delete `src/instrumentation.ts`: Node 22+ exposes a broken global
`localStorage` that turns every server render into a 500, and that file
removes it.

## Tests, types, build

```bash
npx vitest run          # unit tests, including src/lib/bff.test.ts (allowlist + cross-site writes)
npx tsc --noEmit
NEXT_PUBLIC_ORG_NAME="Acme Health" NEXT_PUBLIC_SUPPORT_EMAIL=support@example.org \
NEXT_PUBLIC_PRIVACY_URL=https://example.org/privacy NEXT_PUBLIC_TERMS_URL=https://example.org/terms \
  npm run build
```

## Container and deployment

`Dockerfile` builds a standalone Next server on `node:22-alpine` that runs
as the non-root `node` user on port 8080. The four required
`NEXT_PUBLIC_*` build args fail the build when empty. `cloudbuild.yaml`
passes them for `gcloud builds submit`.

In a deployment the dashboard runs on Cloud Run **behind IAP**: IAP signs
the user in and adds `x-goog-authenticated-user-email`, which the BFF passes
on as `X-Acting-User`. Set `API_BASE_URL` on the service, and give the
service's runtime identity `roles/run.invoker` on the backend. The backend
should accept only that identity's ID tokens, so the IAP-protected dashboard
is the only way in for a browser.

## Backend endpoints the UI depends on

All paths are on the backend under `/api/`; the browser reaches each as
`/api/backend/<same path>`. Types are in `src/lib/types.ts` and
`src/lib/followups.ts`.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/api/calls?limit=&status=` | — | `CallsListResponse` |
| GET | `/api/calls/stream` | — | SSE (`text/event-stream`) of call updates |
| GET | `/api/calls/{sid}` | — | `CallDoc` |
| GET | `/api/calls/{sid}/transcript` | — | `TranscriptResponse` |
| GET | `/api/calls/{sid}/recording` | `Range` honoured | audio bytes |
| POST | `/api/calls/{sid}/follow-up` | `FollowUpOverrideRequest` | `CallDoc` |
| POST | `/api/calls/batch-delete` | `{call_sids: string[]}` | `BatchDeleteResponse` |
| GET | `/api/follow-ups/open?limit=` | — | `CallsListResponse`: every open follow-up (tier needs a callback, review not closed), most severe first, uncapped |
| POST | `/api/place-call` | `PlaceCallRequest` | `PlaceCallResponse` |
| GET | `/api/call-list` | — | `CallListResponse` |
| POST | `/api/call-list` | `CallListEntryInput` | `CallListEntry` |
| POST | `/api/call-list/bulk` | `CallListEntryInput[]` | `CallListResponse` |
| PATCH | `/api/call-list/{entry_id}` | `CallListEntryInput` | `CallListEntry` |
| DELETE | `/api/call-list/{entry_id}` | — | `{deleted}` |
| POST | `/api/call-list/reset` | — | `{reset: number}` |
| POST | `/api/call-list/cancel` | `{entry_ids, reason}` | `{results: {entry_id, status}[]}` |
| POST | `/api/call-list/{entry_id}/reschedule` | `RescheduleTarget` | `{run_id, scheduled_for}` |
| GET | `/api/call-list/run` | — | `CallListRunResponse` |
| POST | `/api/call-list/run` | `{entry_ids: string[] \| null}` | `CallListRunResponse` |
| POST | `/api/call-list/run/cancel` | — | `CallListRunResponse` |
| GET | `/api/call-runs` | — | `{runs: CallRun[], count}` |
| POST | `/api/call-runs` | `CallRunInput` | `CallRun` |
| PATCH | `/api/call-runs/{run_id}` | `Partial<CallRunInput>` | `CallRun \| {deleted}` |
| DELETE | `/api/call-runs/{run_id}` | — | `{deleted}` |
| GET | `/api/call-settings` | — | `CallSettingsResponse` |
| PUT | `/api/call-settings/recall` | `RecallSettings` | `CallSettingsResponse` |
| GET | `/api/allowlist` | — | `AllowlistResponse` |
| POST | `/api/allowlist` | `{phone, label}` | `AllowlistEntry` |
| DELETE | `/api/allowlist/{phone}` (E.164, URL-encoded) | — | `{deleted}` |
| GET | `/api/sms/sessions` | — | `{sessions: FollowUpSession[]}` |
| GET | `/api/sms/callbacks` | — | `{callbacks: ScheduledCallback[]}` |
| DELETE | `/api/sms/callbacks/{callback_id}` | — | `{status}` |
| GET | `/api/sms/messages?phone=&call_sid=` | — | `{phone, messages: SmsMessage[]}` |
| GET | `/api/sms/consent` | — | (allowlisted; not called by the UI today) |
| POST | `/api/sms/consent` | `{phone, granted, statement_version, granted_by}` | `{phone, granted}` |
| GET | `/api/sms/settings` | — | `{settings: FollowUpSettings}` |
| POST | `/api/sms/settings` | `{name, value}` | `{settings: FollowUpSettings}` |

Errors are `{"detail": string}` (or FastAPI's validation list) with the
backend's status code; the client turns them into `ApiError` /
`FollowUpError`. Who performed a write (`set_by`, `canceled_by`, ...) is the
backend's job, from `X-Acting-User`.
