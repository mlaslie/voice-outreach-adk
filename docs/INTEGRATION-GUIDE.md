# Integrating with your environment

This guide is for engineers connecting Voice Outreach to an EHR or integration engine. It covers three things:

1. **Calls in:** loading call lists programmatically.
2. **Auditing the call list:** what happened to each patient you sent.
3. **Results out:** sending results back to your EHR.

Telephony and SMS providers have their own guide: [TELEPHONY-PROVIDERS.md](TELEPHONY-PROVIDERS.md). For the overall picture, read [ARCHITECTURE.md](ARCHITECTURE.md) first.

![Integration points: calls in through the scheduling API; results out through the EHR outbox and your exporter; telephony and SMS through a provider adapter.](images/integration.png)

---

## Authentication: service accounts, no API keys

The API (`voa-api`) is private. Every call needs a Google-signed **ID token** whose audience is the API's URL, issued to a service account the API recognises. Two checks apply:
1. Cloud Run IAM verifies the token.
2. The API's route table (`services/api/src/voa_api/auth.py`, `ROUTE_POLICY`) allows each identity only its own routes.

| Identity | Terraform name | May call |
|---|---|---|
| **EHR feed** | `voa-ingest` | `PUT`, `GET` and `DELETE /api/v1/call-batches/{source}/{batch_id}`, and nothing else |
| Admin console | `voa-admin` | Configuration, `GET /api/v1/call-batches` (list), `GET /api/audit` |
| Nurse console, nurse agent | `voa-ui`, `voa-nurse` | Calls, call list, sign-off; no configuration |

**Getting a token:**
- **Inside Google Cloud** (Cloud Run, GKE, Composer, a VM): run your integration *as* `voa-ingest`. The client libraries fetch the token from the metadata server.
- **Outside Google Cloud** (an on-premises integration engine, another cloud): use [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation) to exchange your platform's identity (OIDC, SAML, AWS, Azure or X.509) for short-lived credentials that impersonate `voa-ingest`.
  - The federation pool and provider are **not** in this repository's Terraform yet; add them in `infra/envs/<env>`.
- **On a developer laptop:** impersonate it with `roles/iam.serviceAccountTokenCreator` on `voa-ingest`. Terraform grants that to the `operators` list.

**Python helper used by every example below:**

```python
# pip install google-auth requests
import google.auth
import google.auth.transport.requests
import google.oauth2.id_token
import requests
from google.auth import impersonated_credentials

API = "https://voa-api-<hash>-<region>.a.run.app"  # terraform -chdir=infra/envs/<env> output -raw api_url
INGEST_SA = "voa-ingest@<project>.iam.gserviceaccount.com"


def id_token(audience: str = API, impersonate: str | None = None) -> str:
    """An ID token for the API.

    Running as the service account (Cloud Run, GKE, Workload Identity Federation):
        id_token()
    From a laptop with gcloud application-default credentials:
        id_token(impersonate=INGEST_SA)
    """
    req = google.auth.transport.requests.Request()
    if impersonate is None:
        return google.oauth2.id_token.fetch_id_token(req, audience)
    source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    target = impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=impersonate,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds = impersonated_credentials.IDTokenCredentials(
        target, target_audience=audience, include_email=True
    )
    creds.refresh(req)
    return creds.token


def api(method: str, path: str, **kw) -> requests.Response:
    r = requests.request(
        method,
        f"{API}{path}",
        timeout=60,
        headers={
            "Authorization": f"Bearer {id_token(impersonate=INGEST_SA)}",
            "X-Acting-User": "ehr-feed:epic-prod",
        },  # the name shown in the audit log
        **kw,
    )
    r.raise_for_status()
    return r
```

`scripts/sample_ehr_feed.py` is a complete, runnable feed that uses the same pattern with fictional patients.

---

## 1. Calls in: loading call lists

### The model
- **A batch** is one list from one source, e.g. tomorrow's discharges from `epic-prod`, identified by `(source, batch_id)`.
- **A batch has tranches.** Each tranche becomes one **call run** that starts dialling at `scheduled_for`, within the clinic's calling hours, with automatic retries and missed-call texts as configured.
- **Each patient** is matched on `(source, external_id)`. Use the encounter id, so a re-send updates the patient instead of duplicating them.
- **Re-sending is safe.**
  - The identical batch is a no-op (`"replay": true`).
  - A patient missing from a re-sent batch is cancelled, if not called yet.
  - Changed details update the patient.

### Create or replace a batch

```python
batch = {
    "tranches": [
        {
            "label": "Morning",
            "scheduled_for": "2026-11-03T10:00:00-05:00",  # timezone required
            "campaign": "post_op",  # use case; default post_op
            "calls": [
                {
                    "external_id": "enc-1001",  # your stable id for this encounter
                    "mrn": "A1234567",
                    "name": "Jordan Rivera",
                    "dob": "1950-03-15",
                    "phone": "+12025550123",  # E.164
                    "procedure": "Total knee replacement",
                    "procedure_date": "2026-11-01",
                    "is_diabetic": False,
                    "care_setting": "outpatient",  # inpatient | outpatient (optional)
                    "language": "es-US",  # one of 24 codes, or "es", "Spanish"; default en-US
                },
            ],
        },
        {"label": "Afternoon", "scheduled_for": "2026-11-03T14:00:00-05:00", "calls": []},
    ]
}

r = api("PUT", "/api/v1/call-batches/epic-prod/2026-11-03", json=batch)
out = r.json()
print(out["status"], out["counts"], "replay" if out["replay"] else "")
for item in out["items"]:
    # created | updated | unchanged | cancelled | rejected (+ reason)
    print(
        f"{item['external_id']:>10}  {item['tranche']:<9} {item['status']:<9} {item.get('reason') or ''}"
    )
```

**Response:**

```json
{
  "source": "epic-prod", "batch_id": "2026-11-03", "status": "active", "replay": false,
  "counts": {"created": 1},
  "runs": ["<run id>"],
  "items": [{"external_id": "enc-1001", "tranche": "Morning", "entry_id": "<id>",
             "status": "created", "reason": null}]
}
```

**Rules worth knowing:**
- **One bad record never fails the batch.** It comes back `rejected` with a reason: invalid phone or date of birth, number not authorized, tranche in the past or outside calling hours, unsupported language, unknown use case.
- **No patient details are echoed back.**
- **Limits:** 20 tranches, 500 calls per batch.
- **Language.** A patient whose language isn't available yet (no approved language pack) is **held**: accepted, but not dialled until the language is approved or an admin chooses "Call in English" for them.
- **Demo environments only dial authorized numbers.** Production deployments replace that allowlist with a policy; see [ADMIN-PLAN.md §3](ADMIN-PLAN.md).

### Without code: importing a CSV in the admin console

For a pilot, or when no feed exists yet, an administrator can load patients from a spreadsheet:
1. Open **Admin console → Patients**.
2. Click **Example CSV** to download a template with the right header and four fictional rows.
3. Click **Import CSV** to load your file.

**How the import works:**
- The file is read **in the browser**. You see every row with its problems before anything is sent.
- The valid rows go to the API as JSON, in chunks of 200 (`POST /api/call-list/bulk`). The file itself is never stored.
- The import adds patients to the call list; **it doesn't start calling.** Start or schedule a run under **Schedules**.

| Column | Required | Format | Example |
|---|---|---|---|
| `phone` | yes | E.164 | `+12025550123` |
| `patient_name` | yes | text (quote it if it contains a comma) | `"Ruiz, Maria"` |
| `patient_id` | yes | letters, digits, `_`, `.`, `-`; unique; your MRN or a stable id | `P-1001` |
| `patient_dob` | yes | `YYYY-MM-DD`, a real date, not in the future | `1950-03-15` |
| `operation_name` | no | text | `Total knee replacement` |
| `operation_date` | no | `YYYY-MM-DD` | `2026-11-01` |
| `is_diabetic` | no | `true`, `yes`, `y` or `1` means diabetic; anything else, including blank, means not | `false` |
| `care_setting` | no | `inpatient`, `outpatient`, `in`, `out` or blank | `outpatient` |
| `language` | no | one of the 24 supported languages, as a code or English name; blank is English (US) | `es-US` or `Spanish` |
| `call_in_english` | no | `yes`, `no` or blank: call in English while the patient's language isn't available yet | `yes` |

**Rules:**
- The header row must contain the four required columns. Extra columns are ignored.
- A row is refused, with the reason shown, if:
  - a required value is missing;
  - the format is wrong;
  - the phone number or `patient_id` appears twice in the file;
  - the `patient_id` is already on the list.

  The other rows still import.
- **Use case:** the CSV has no column for it, so every row uses the post-op check-in. Use the scheduling API (`campaign`) for other use cases.

### Withdraw a batch

```python
api("DELETE", "/api/v1/call-batches/epic-prod/2026-11-03")
# Cancels every patient in the batch who hasn't been called yet, and removes its pending runs.
```

The full contract is in [INGEST-API.md](INGEST-API.md).

---

## 2. Auditing the call list

### Per patient: what happened to the people you sent

```python
r = api("GET", "/api/v1/call-batches/epic-prod/2026-11-03")
for item in r.json()["items"]:
    print(
        item["external_id"],
        item["status"],  # what the batch did with it
        item["list_status"],
        item["attempts"],
    )  # where it is in the queue now
```

| `list_status` | Meaning |
|---|---|
| `pending` | Waiting for its run, for a retry, or held for its language |
| `calling` | A call is in progress |
| `completed` | Reached: the questionnaire was completed, or the call ended with a final outcome |
| `blocked` | Can't be dialled, e.g. the number isn't authorized or the provider refused it permanently |
| `canceled` | Cancelled: withdrawn, removed from a re-sent batch, or cancelled by a nurse |

`attempts` counts dial attempts, including automatic retries.

### Across batches and changes (admin identity)
These two routes belong to the **admin console's identity**, not the feed's. Call them from an admin tool, or read them in the admin console (Patients and Audit log).

```python
ADMIN_SA = "voa-admin@<project>.iam.gserviceaccount.com"


def admin(method, path, **kw):
    r = requests.request(
        method,
        f"{API}{path}",
        timeout=60,
        headers={"Authorization": f"Bearer {id_token(impersonate=ADMIN_SA)}"},
        **kw,
    )
    r.raise_for_status()
    return r.json()


for b in admin("GET", "/api/v1/call-batches?limit=20")["batches"]:  # recent batches, with counts
    print(b["source"], b["batch_id"], b["status"], b["counts"], b["updated_at"])

for e in admin("GET", "/api/audit?limit=50")["entries"]:  # every write by the feed and admins
    print(e["at"], e["by"], e["action"], e.get("target"))
```

Every write by the feed is in the audit log, with the `X-Acting-User` you sent, the route and the target (phone numbers masked; request bodies never stored).

> **Not built yet: per-patient results for the feed.** Today the feed's identity sees queue status, not outcomes. The natural extension is to add each item's latest call id, outcome, follow-up tier and sign-off status to `GET /api/v1/call-batches/{source}/{batch_id}`, or to add a `GET /api/v1/results?since=` cursor. Either is a small change in `routes_ingest.py`, with an entry in `ROUTE_POLICY`.

---

## 3. Results out: sending results to your EHR

### What exists for every call

| Data | Where | Contents |
|---|---|---|
| Call record | Firestore `calls/{call_id}` | Times; line status; outcome (completed, voicemail, wrong person, identity not verified, declined, call back later, emergency); use case; language asked for and spoken; how it was placed. **No date of birth.** |
| Answers | `calls.survey_responses` | Typed answers (e.g. `pain_severity: 7`), the patient's own words for free-text questions, and patient questions with category and priority. |
| Triage | `calls.follow_up`, `follow_up_reasons[]` | The tier, plus every finding with its own tier and source. |
| Nurse review | `calls.review`, `follow_up_override` | Sign-off (who, when, note) and any tier change. |
| Transcript | Firestore `transcripts/{call_id}` | Turns with `text` (original), `text_en` (translation) and `lang`. |
| Recording | `gs://<project>-voa-recordings/recordings/{call_id}.wav` | Stereo: caller left, agent right. |
| **EHR outbox** | Firestore `ehr_outbox/{call_id}` | **One FHIR-shaped record per signed-off call.** |

### The EHR outbox record

Written when a nurse signs off. Code: `packages/voa_core/src/voa_core/review.py` `ehr_payload`.

```json
{
  "call_sid": "CA…",
  "patient":   {"mrn": "A1234567", "name": "Jordan Rivera"},
  "encounter": {"date": "2026-11-03T15:05:12Z", "channel": "phone", "identity_check": "verified"},
  "questionnaire_response": {
    "resourceType": "QuestionnaireResponse", "status": "completed",
    "item": [{"linkId": "pain_severity", "answer": 7}, {"linkId": "fever_reported", "answer": "no"}]
  },
  "disposition": {"follow_up": "urgent_same_day", "reason": "Patient reports pain 7/10 …",
                  "agent_follow_up": "urgent_same_day", "changed_by_nurse": null},
  "review": {"status": "signed_off", "by": "nurse@example.org", "at": "…", "note": "…", "resolved": true},
  "task": {"resourceType": "Task", "status": "completed", "description": "Post-op RN follow-up", "…": "…"},
  "status": "pending_export"
}
```

- `status` becomes `withdrawn` if the sign-off is reopened.
- **The record never contains the date of birth.**

### Build an exporter

Nothing reads the outbox yet; that's the piece you build. Give it **its own service account** with Firestore read/write and nothing else, and your EHR credentials through Secret Manager. Run it on a Firestore trigger (Eventarc) or every few minutes from Cloud Scheduler.

```python
# pip install google-cloud-firestore requests
from datetime import UTC, datetime
from google.cloud import firestore

db = firestore.Client(project="<project>")
FHIR = "https://fhir.example.org/R4"  # your EHR's FHIR endpoint


def to_fhir(rec: dict, patient_ref: str) -> dict:
    qr = dict(rec["questionnaire_response"])
    qr["subject"] = {"reference": patient_ref}
    qr["identifier"] = {
        "system": "urn:voice-outreach:call",
        "value": rec["call_sid"],
    }  # idempotency key
    qr["questionnaire"] = "Questionnaire/post-op-check"  # your canonical form
    qr["authored"] = rec["encounter"]["date"]
    return qr


def export_pending(fhir_session, limit: int = 50) -> None:
    q = db.collection("ehr_outbox").where("status", "==", "pending_export").limit(limit)
    for snap in q.stream():
        rec = snap.to_dict()
        try:
            patient_ref = resolve_patient_by_mrn(fhir_session, rec["patient"]["mrn"])  # your lookup
            r = fhir_session.put(  # conditional update on the identifier = idempotent
                f"{FHIR}/QuestionnaireResponse?identifier=urn:voice-outreach:call|{rec['call_sid']}",
                json=to_fhir(rec, patient_ref),
                timeout=30,
            )
            r.raise_for_status()
            snap.reference.update(
                {
                    "status": "exported",
                    "exported_at": datetime.now(UTC).isoformat(),
                    "ehr_ref": r.headers.get("Location"),
                }
            )
        except Exception as exc:  # retried on the next run
            snap.reference.update(
                {"export_attempts": firestore.Increment(1), "last_error": str(exc)[:300]}
            )
```

**Production checklist for the exporter:**
- **Idempotency:** use `call_sid` as the FHIR `identifier` (or the HL7 message control id), so a retry never duplicates.
- **Withdrawals:** a `withdrawn` record means the earlier export no longer stands. Mark it `entered-in-error`, or send a correction.
- **Mapping:** map each `linkId` to your questionnaire's item ids. The ids come from the use-case file (`config/campaigns/<id>.yaml`).
- **HL7 v2:** send an ORU^R01 (observations) or MDM^T02 (a summary document) through your integration engine instead.
- **Transcripts and recordings contain PHI.** Decide with your privacy office whether they go to the EHR. If they do:
  - send them as `DocumentReference`s;
  - copy the recording into the EHR's storage, because links from this system expire after 15 minutes and recordings are deleted after the retention period.
- **Emergencies:** the agent sends the patient to 911 during the call, and nurses see the call at the top of the console. **Don't rely on an EHR export for urgent escalation.**

### Other patterns

| Pattern | When | What to add |
|---|---|---|
| **Pull API** | The EHR side prefers to poll | `GET /api/v1/results?since=<cursor>` for the feed identity, returning outbox records in order |
| **Events** | Several consumers, near real-time | Publish to Pub/Sub on "report filed" (`services/gateway/.../persist.py`, `sms_service.py`), "signed off" (`routes_calls.py`) and "call finished" (`/tasks/call-terminal`) |
| **Raw answers before review** | The EHR should show patient-reported data at once | Export on "report filed", marked as unreviewed and patient-reported |
