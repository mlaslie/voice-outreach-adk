# EHR scheduling API (`/api/v1/call-batches`)

This is how a service that reads the EHR schedules post-op calls without anyone clicking. The design is in `docs/ADMIN-PLAN.md` §3; the code is `services/api/src/voa_api/routes_ingest.py`.

## Who can call it

- **The caller:** only the `voa-ingest` service account. The API refuses every other identity (`auth.ROUTE_POLICY`), and that account can call nothing else.
- **Getting a token:** inside Google Cloud, run as that account. Outside, use Workload Identity Federation to impersonate it. There are no API keys.
- **Audit:** every write is recorded in the admin console's audit log.

## Send a batch

`PUT /api/v1/call-batches/{source}/{batch_id}`

- `source`: lower-case letters, digits and dashes, e.g. `epic-prod`.
- `batch_id`: up to 64 of `A–Z a–z 0–9 _ . : -`, e.g. `2026-10-21`.

```json
{
  "tranches": [
    {
      "label": "Morning",
      "scheduled_for": "2026-10-21T10:00:00-04:00",
      "calls": [
        {
          "external_id": "enc-123",
          "mrn": "A1234567",
          "name": "First Last",
          "dob": "1950-03-15",
          "phone": "+13145550123",
          "procedure": "Total knee replacement",
          "procedure_date": "2026-10-19",
          "is_diabetic": false,
          "language": "en"
        }
      ]
    }
  ]
}
```

**Limits:** up to 20 tranches and 500 calls per batch. Tranche labels and `external_id`s must be unique within a batch.

**Response:** per-item results, with no patient details echoed back.

```json
{
  "source": "epic-prod", "batch_id": "2026-10-21", "status": "active", "replay": false,
  "counts": {"created": 41, "rejected": 2},
  "runs": ["<run id>"],
  "items": [
    {"external_id": "enc-123", "tranche": "Morning", "entry_id": "…", "status": "created", "reason": null},
    {"external_id": "enc-124", "tranche": "Morning", "entry_id": null, "status": "rejected",
     "reason": "phone: Value error, phone must be E.164, e.g. +12025550123"}
  ]
}
```

| Item status | Meaning |
|---|---|
| `created` | A new patient on the call list, in the tranche's run. |
| `updated` | The same `external_id` came back with changed details; the record was updated. A cancelled one is re-queued. |
| `unchanged` | Same as last time, or the patient is on a call right now (`reason` says which). |
| `cancelled` | In the previous version of this batch but not in this one, and not called yet. |
| `rejected` | Not accepted; `reason` says why: bad phone, DOB or MRN, number not authorized, tranche in the past or outside calling hours. One bad item never fails the batch. |

**Rules:**
- **Idempotent.** Sending the identical batch again returns the stored result with `"replay": true` and changes nothing.
- **Upsert key.** Each patient is matched on `(source, external_id)`.
- **Tranches become runs.** Each tranche is one call run, labelled `"<label> (<source>)"`. It dials at `scheduled_for` inside the clinic's calling hours, with the normal recall and text follow-up behaviour.
- **A re-sent batch is the full list for that `batch_id`.** Anything missing from it is cancelled if not yet called.

## Check, withdraw, list

| Call | Who | What |
|---|---|---|
| `GET /api/v1/call-batches/{source}/{batch_id}` | ingest, admin | The batch with each item's current `list_status` and attempts, for reconciliation. |
| `DELETE /api/v1/call-batches/{source}/{batch_id}` | ingest | Cancel everything in the batch that hasn't been called, and remove its pending runs. |
| `GET /api/v1/call-batches` | admin | Recent batches with their counts. |

## Try it with fictional patients

```bash
uv run python scripts/sample_ehr_feed.py demo --phone +1… --phone +1…
```

- **Content:** fictional patients in a morning and an afternoon tranche, tomorrow by default.
- **Numbers:** they must be authorized in the admin console.
- **Re-running:** with the same `--batch-id` it is a no-op. `--withdraw` cancels the batch.

## Not built yet

- A real EHR connector: a FHIR or HL7 adapter running as a Cloud Run Job.
- An optional Pub/Sub topic in front of this endpoint.

Both are in the admin plan §3.
