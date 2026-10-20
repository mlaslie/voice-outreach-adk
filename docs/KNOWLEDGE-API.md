# Knowledge base API (step 1)

Admin console only: every route is `ADMIN` in `services/api/src/voa_api/auth.py`
(`ROUTE_POLICY`). Writes are audited automatically (who, when, route, `item_id`;
never the body). `POST /try` changes nothing and is not audited. The UI forwards the
signed-in admin as `X-Acting-User`; without it, `created_by`, `updated_by` and
`approved_by` are `"admin"`.

Plan and safety rules: `docs/KNOWLEDGE-PLAN.md`. Code: `services/api/src/voa_api/routes_knowledge.py`,
`packages/voa_core/src/voa_core/knowledge.py`.

## Item

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

- Limits: `question` ≤ 300 characters, `answer` ≤ 600, `alt_questions` ≤ 10 × 300, 500 items.
- `category`: one of `appointment`, `activity`, `diet`, `paperwork_billing`, `supplies`, `recovery`.
  Medication, symptom and other are refused (422).
- The agent only considers items in the category the classifier gives the patient's
  question (`voa_core.patient_questions.classify`), so pick the category that the
  item's own question classifies as ("Try it" shows it).
- PHI check: a question, answer or other wording containing a phone number, a date
  like 03/15/1950, an email address or a run of 6+ digits is refused (422).
- Changing `question`, `answer`, `category` or `alt_questions` of an approved (or
  retired) item: `version` + 1, back to `draft`, approval cleared. A PATCH that changes
  nothing changes nothing. Editing a draft keeps its version.

| Route | Body | Returns |
|---|---|---|
| `GET /api/admin/knowledge/items` | | `{items: [Item]}`, newest first |
| `POST /api/admin/knowledge/items` | `{question, answer, category, alt_questions?}` | Item (draft, version 1); 409 at 500 items |
| `PATCH /api/admin/knowledge/items/{item_id}` | `{question?, answer?, category?, alt_questions?}` | Item |
| `POST /api/admin/knowledge/items/{item_id}/approve` | | Item (`approved_by` = acting user); already approved: unchanged |
| `POST /api/admin/knowledge/items/{item_id}/retire` | | Item; already retired: unchanged |
| `DELETE /api/admin/knowledge/items/{item_id}` | | draft or retired: `{deleted: true}`; approved: 409 `"Retire it first."` |

Unknown body fields: 422. Unknown `item_id`: 404.

## Settings (`settings/knowledge`)

```json
{
  "enabled": false,
  "channels": {"voice": true, "sms": true},
  "allowed_categories": ["appointment", "activity", "diet", "paperwork_billing", "supplies", "recovery"],
  "min_match": 0.8,
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
| `PUT /api/admin/knowledge/settings` | any subset of `{enabled, channels: {voice?, sms?}, allowed_categories, min_match, ask_satisfied}` | Settings |

- Partial update; unknown fields, `null` values, `medication`/`symptom`/`other` (or an
  unknown key) in `allowed_categories`, and `min_match` outside 0.5–1.0 are 422.
- `locked_categories`, `available_categories`, `updated_at` and `updated_by` are read-only.
- The gateway reads these at the start of each call, through a 60 s cache: a change
  reaches calls within a minute. SMS (`channels.sms`) is stored but not used until step 3.

## Try it

`POST /api/admin/knowledge/try` `{question}` (1–300 characters) runs the real path
(red-flag check, category gate, approved items, the Gemini matcher, `min_match`) but
ignores `enabled` and `channels`.

```json
{
  "outcome": "answered",
  "category": "activity",
  "category_label": "Activity",
  "reason": "matched",
  "answer": "You can usually shower two days after surgery…",
  "item_id": "3f1c…",
  "item_version": 2,
  "confidence": 0.93,
  "note": "test only: grounding is off"
}
```

`answer`, `item_id`, `item_version` are present only when answered; `confidence` when
the matcher ran; `note` only while `enabled` is false.

`reason` is one of: `matched`, `empty_question`, `red_flag`, `locked_category`
(medication or symptom), `unclassified` (other), `category_not_allowed`,
`no_approved_items`, `no_match`, `low_confidence`, `timeout`, `error`.

## On a call

With `enabled` and `channels.voice` on, the agent gets `answer_from_knowledge` and
`record_answer_feedback`; each patient question is stored in
`survey_responses.patient_questions[]` with the new optional fields `outcome`
(`answered` | `deferred`), `item_id`, `item_version`, `answer` and `satisfied`
(`true` | `false` | `null`). A "no" to "Did that answer your question?" makes it
`deferred` with `satisfied: false`. Answered questions (unless `satisfied` is false)
add no follow-up reason. With it off, the agent's instruction and tools are exactly
as before.

## Seeding

`uv run python scripts/seed_knowledge.py <env>` loads `config/knowledge/sample_items.yaml`
(fictional) as drafts, signed in as the `voa-admin` service account. A clinician
approves them in the admin console.
