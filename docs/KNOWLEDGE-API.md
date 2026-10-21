# Knowledge agent API (v2)

The admin routes are `ADMIN` only in `services/api/src/voa_api/auth.py` (`ROUTE_POLICY`).
Writes are audited automatically (who, when, route, ids; never the body). `POST /try`
changes nothing and is not audited. The UI forwards the signed-in user as
`X-Acting-User`; without it, `created_by`, `updated_by` and `approved_by` are `"admin"`.

- **Plan and safety rules:** `docs/KNOWLEDGE-PLAN.md`.
- **Code:**
  - `packages/voa_knowledge/` (the agent);
  - `packages/voa_core/src/voa_core/knowledge.py` (storage, lifecycle, settings);
  - `services/api/src/voa_api/routes_knowledge.py` (these routes and the shadow task).

## Example Q&A (`knowledge_items`)

```json
{
  "item_id": "3f1c…(32 hex)",
  "question": "When can I shower?",
  "alt_questions": ["Is it okay to shower yet?"],
  "answer": "You can usually shower two days after surgery…",
  "category": "activity",
  "category_label": "Activity",
  "status": "draft | approved | retired",
  "version": 1,
  "created_at": "2026-10-20T14:00:00.000+00:00",
  "created_by": "dr.lee@example.org",
  "updated_at": "…",
  "updated_by": "…",
  "approved_at": null,
  "approved_by": null
}
```

**Limits:** `question` ≤ 300 characters, `answer` ≤ 600, `alt_questions` ≤ 10 × 300, 500 items.

**Rules:**
- **`category`:** one of `appointment`, `activity`, `diet`, `paperwork_billing`, `supplies`, `recovery`.
  - Medication, symptom and other are refused (422).
  - It is for filtering and display: it no longer has to match how the question is classified (v1's topic check is gone).
- **Wordings:** a `question` or `alt_questions` wording that itself reads as a medication or symptom question is refused (422).
- **PHI check:** a question, answer or other wording containing a phone number, a date like 03/15/1950, an email address or a run of 6+ digits is refused (422).
- **Versioning:** changing `question`, `answer`, `category` or `alt_questions` of an approved (or retired) item gives `version` + 1, back to `draft`, approval cleared.
  - A PATCH that changes nothing changes nothing.
  - Editing a draft keeps its version.

| Route | Body | Returns |
|---|---|---|
| `GET /api/admin/knowledge/items` | | `{items: [Item]}`, newest first |
| `POST /api/admin/knowledge/items` | `{question, answer, category, alt_questions?}` | Item (draft, version 1); 409 at 500 items |
| `PATCH /api/admin/knowledge/items/{item_id}` | `{question?, answer?, category?, alt_questions?}` | Item |
| `POST /api/admin/knowledge/items/{item_id}/approve` | | Item (`approved_by` = acting user); already approved: unchanged |
| `POST /api/admin/knowledge/items/{item_id}/retire` | | Item; already retired: unchanged |
| `DELETE /api/admin/knowledge/items/{item_id}` | | draft or retired: `{deleted: true}`; approved: 409 `"Retire it first."` |

Unknown body fields, or a field sent as `null`: 422. Unknown `item_id`: 404.

## Guidelines (`knowledge_documents`)

```json
{
  "doc_id": "9a0b…(32 hex)",
  "title": "Lifting after outpatient knee surgery",
  "body": "…",
  "procedures": ["knee arthroscopy"],
  "care_settings": ["outpatient"],
  "status": "draft | approved | retired",
  "version": 1,
  "chars": 1234,
  "created_at": "…", "created_by": "…", "updated_at": "…", "updated_by": "…",
  "approved_at": null, "approved_by": null
}
```

| Route | Body | Returns |
|---|---|---|
| `GET /api/admin/knowledge/documents` | | `{documents: [Doc]}`, newest first |
| `POST /api/admin/knowledge/documents` | `{title, body, procedures?, care_settings?}` | Doc (draft, version 1); 409 at 100 guidelines |
| `PATCH /api/admin/knowledge/documents/{doc_id}` | any of the same fields | Doc |
| `POST /api/admin/knowledge/documents/{doc_id}/approve` | | Doc; 409 when approved guidelines would exceed 200,000 characters |
| `POST /api/admin/knowledge/documents/{doc_id}/retire` | | Doc |
| `DELETE /api/admin/knowledge/documents/{doc_id}` | | draft or retired: `{deleted: true}`; approved: 409 `"Retire it first."` |

**Limits:**
- `title` 1–120 characters;
- `body` 1–20,000 characters (whitespace tidied per line, line breaks kept);
- ≤ 100 guidelines;
- ≤ 200,000 characters (`chars`, the body length) across approved guidelines.

**Fields:**
- **`procedures`:** ≤ 20 free-text names, ≤ 120 characters each, de-duplicated ignoring case.
- **`care_settings`:** a subset of `["inpatient", "outpatient"]`.
- **PHI check:** title, body and procedures, as for examples (422).
- **Versioning, unknown fields, `null`, 404:** as for examples.

**Applies to a patient when both hold:**
- `procedures` is empty, or one of them matches the patient's procedure, case-insensitively, as a substring either way. An unknown procedure matches only a guideline with no procedures.
- `care_settings` is empty, or holds the patient's care setting. An unknown setting matches only a guideline with none.

## Settings (`settings/knowledge`)

```json
{
  "mode": "off",
  "channels": {"voice": true, "sms": false},
  "allowed_categories": ["appointment", "activity", "diet", "paperwork_billing", "supplies", "recovery"],
  "min_support": 0.85,
  "ask_satisfied": true,
  "updated_at": null,
  "updated_by": null,
  "locked_categories": ["medication", "symptom"],
  "available_categories": [
    {"key": "appointment", "label": "Appointment"},
    {"key": "activity", "label": "Activity"},
    {"key": "diet", "label": "Diet"},
    {"key": "paperwork_billing", "label": "Paperwork or billing"},
    {"key": "supplies", "label": "Supplies"},
    {"key": "recovery", "label": "Recovery timeline"}
  ]
}
```

| Route | Body | Returns |
|---|---|---|
| `GET /api/admin/knowledge/settings` | | Settings |
| `PUT /api/admin/knowledge/settings` | any subset of `{mode, channels: {voice?, sms?}, allowed_categories, min_support, ask_satisfied}` | Settings |

**Rules:**
- **Partial update.** These are 422:
  - unknown fields, including v1's `enabled` and `min_match`;
  - `null` values;
  - a `mode` other than `off`, `shadow` or `live`;
  - `medication`, `symptom`, `other` or an unknown key in `allowed_categories`;
  - `min_support` outside 0.5–1.0.
- **Migration on read:** a v1 document with `enabled: true` reads as `mode: "live"`, `enabled: false` as `"off"`. `min_match` is dropped. The next PUT stores the v2 shape.
- **Read-only:** `locked_categories`, `available_categories`, `updated_at`, `updated_by`.
- **Effective mode per channel:** `mode` when `channels.<channel>` is true, else off.
- **Freshness:** the gateway reads the settings at the start of each call, through a 60 s cache.

| Mode | What happens |
|---|---|
| `off` | Nothing. The voice agent's instruction and tools are exactly as before. |
| `shadow` | Calls are exactly as before. After a report is filed (voice, or SMS with `channels.sms`), the task `/tasks/knowledge-shadow` stores what the agent would have said on each question (below). |
| `live` | Voice only. The agent gets `answer_from_knowledge` and `record_answer_feedback`, and answers when every check passes. SMS answering is not built yet. |

## Try it

`POST /api/admin/knowledge/try` takes:

```json
{"question": "Can I lift my grandkid?",
 "context": {"procedure": "Knee arthroscopy", "care_setting": "outpatient",
             "days_since_surgery": 3, "is_diabetic": false}}
```

- `question`: 1–300 characters.
- `context`: optional; every field is optional; `procedure` ≤ 120 characters, `days_since_surgery` 0–3650. Other fields are 422.

It runs the real agent in any mode, against the approved content:

```json
{
  "outcome": "answered",
  "reason": "answered",
  "classification": {"category": "activity", "category_label": "Activity",
                     "llm_category": "activity", "keyword_category": "activity",
                     "red_flag": false, "vetoed_by": null},
  "answer": "For the first two weeks, lift nothing heavier than ten pounds…",
  "sources": [{"kind": "guideline", "id": "9a0b…", "title": "Lifting after outpatient knee surgery", "version": 1}],
  "support_score": 0.93,
  "latency_ms": 1840,
  "note": "test only: mode is shadow"
}
```

**Every key is always present:**
- `answer` and `support_score` are `null` when they don't apply.
- `sources` is `[]` when nothing was cited. A deferral after the draft (`low_support`, `output_veto`) keeps its sources and score.
- `note` is `null` in live mode.

**`classification`:**
- `llm_category` is `null` when the keyword rules deferred the question first (the LLM is then not called).
- `category` is the one that counted.
- `vetoed_by` is `"keywords"` or `"llm"` when that side's red flag or locked category deferred the question, else `null`.

**`reason`** is one of:

| Reason | Meaning |
|---|---|
| `answered` | Every check passed. |
| `empty_question` | The question was blank. |
| `red_flag` | Either side saw a possible emergency. |
| `locked_category` | Medication or symptom, from either side. |
| `unclassified` | The LLM said other. |
| `category_not_allowed` | The clinic has not allowed this category. |
| `low_confidence` | The LLM's confidence was below 0.6. |
| `no_knowledge` | No approved example, and no approved guideline that applies. |
| `not_covered` | The draft said not covered, or cited nothing valid. |
| `low_support` | The grounding score was below `min_support`, or a claim had no citation. |
| `output_veto` | The answer held medication, dose or symptom wording, a red flag, or more than 600 characters. |
| `timeout` | The 4 s budget ran out. |
| `error` | A model or API call failed. |

## Patient-question record

Questions live in `calls/{call_id}.survey_responses.patient_questions[]`. Every field below is optional, and old entries stay valid.

| Field | When |
|---|---|
| `outcome` | `answered` or `deferred`; live mode. |
| `answer`, `sources` (as in Try it), `support_score`, `satisfied` | Live, answered questions. `satisfied` is `true`, `false` or `null`. A "no" to "Did that answer your question?" makes the question `deferred` with `satisfied: false`. |
| `item_id`, `item_version` | v1 entries only. |
| `shadow` | `{outcome, reason, answer, sources, support_score, at}`. Shadow mode; it never changes `follow_up` or the triage. |
| `shadow_review` | `{verdict: "good" \| "not_right", note, by, at}`. A nurse's rating. |

In live mode, `category` and `priority` take the stricter of the keyword and LLM classifications; an unclear question is immediate.

## Shadow task and the nurse rating

**`POST /tasks/knowledge-shadow {call_id}`:**
- Who calls it: Cloud Tasks on the events queue (OIDC, tasks-invoker only).
- When: queued by the gateway (voice) or the SMS filing, after a report with questions is filed in shadow mode. It is named `kshadow-<call>-<report>`, so it is queued once per report.
- What it does: runs the agent on each question that has no `shadow` yet, with the call's patient context, then stores the drafts in one transaction. It is idempotent.

**`POST /api/calls/{call_id}/questions/{index}/shadow-review`:**
- Body: `{verdict: "good" | "not_right", note?}`.
- Callers: the nurse UI and the nurse agent (`NURSE`). It is audited.
- Returns: the updated question.
- Errors:
  - 404 when there is no such call, no such question, or no `shadow` on it;
  - 422 for a bad verdict, a note over 300 characters, a note holding PHI, or unknown fields.
- Rating again replaces the earlier rating.

## Care setting

An optional `care_setting` (`inpatient` | `outpatient`; case-insensitive; `""` means none):
- on `POST /api/call-list`, `POST /api/call-list/bulk`, `PATCH /api/call-list/{entry_id}` and `POST /api/place-call`;
- on ingest calls (`docs/INGEST-API.md`);
- copied into the call's `patient_context`.

The agent's patient context is procedure, days since surgery, care setting and diabetic. It never holds the name, DOB, phone or MRN.

## Seeding and evaluation

**Seeding:** `uv run python scripts/seed_knowledge.py <env>` loads the fictional `config/knowledge/sample_items.yaml` and `sample_guidelines.yaml` as drafts, signed in as the `voa-admin` service account. A clinician approves them in the admin console.

**Evaluation:**
- In CI, `packages/voa_knowledge/tests/test_knowledge_eval.py` runs `config/knowledge/eval_questions.yaml` with fake models: the gates and the output veto must never answer a question labelled defer.
- `make eval-knowledge` runs the same set on the live models and prints deferral precision, coverage and mean support.
