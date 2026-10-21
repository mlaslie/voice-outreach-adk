# Knowledge base and grounded answers: plan (v2, hybrid)

**Status:**
- **Built (step A), 20 Oct 2026, not yet deployed:** the `voa_knowledge` package, guidelines API, settings v2 with modes, "Try it", shadow task and nurse rating, live voice tool, care setting, IAM for check-grounding (`roles/discoveryengine.viewer`), evaluation set. API contract as built: `docs/KNOWLEDGE-API.md`.
- **20 Oct 2026:** v2 replaces the step-1 design. Built and deployed earlier the same day, step 1 answered only by reading out an approved Q&A entry word for word, and its gate was the triage keyword rules.
- **Why v2:** clinicians should be able to put in knowledge easily: example Q&A *and* guideline text ("outpatient knee arthroscopy: lift nothing over 10 lb for two weeks"). An LLM should then answer from that material when a question is safe to answer.

**Related plans:**
- `docs/ADMIN-PLAN.md`: managed in the admin UI.
- `docs/ANALYTICS-PLAN.md`: answered vs deferred.

## Design in one picture

```
patient question (voice now; text later)
   │
   ▼
voa_knowledge.KnowledgeAgent.answer(question, context)   ← one dedicated component
   1. classify   LLM (category, red flag)  +  keyword rules (veto only)
   2. gate       red flag / medication / symptom / not allowed  → DEFER
   3. assemble   approved examples + approved guidelines that apply to this
                 patient's procedure and care setting
   4. draft      LLM answers ONLY from that material, or says "not covered"
   5. verify     Vertex check-grounding: every claim supported (score ≥ threshold)
                 + output veto (no medication or dose talk in the answer)
   └─▶ {outcome answered | deferred, answer, sources, scores, reason}
```

**One component, isolated:** the `packages/voa_knowledge` package, and only this:
- It has its own instruction files, its own versioned prompts and its own evaluation set.
- The voice agent knows one tool contract: question in, `answered` + text or `deferred` out. Changing how questions are answered cannot change how the call itself behaves.
- It is an ADK agent with structured output, called in-process: a separate service would add a network hop to a turn that already holds a patient on the line.
- It can move behind A2A later without touching the voice agent.

## 1. Classification: the LLM decides, the keyword rules can only veto

**What the LLM returns** (`gemini-3.8-flash`, structured output):
- `category`: one of the existing categories;
- `red_flag`: bool;
- `confidence`.

It understands paraphrase ("glass of wine" → diet).

**Keyword veto:** the existing deterministic rules (`patient_questions.classify` and `intent.is_red_flag`) still run. If *either* side says red flag, medication or symptom, the question defers. The rules can make a question stricter, never looser.

**When the LLM is unsure or fails:** low confidence (< 0.6), a timeout or an error defers.

**Deferred questions keep today's triage:** the priority is the more urgent of the two classifications, and an unclear question is immediate.

**Locked, in code:** medication and symptom are never answered, in any setting. The other categories can each be allowed or not (default: appointment, activity, diet, paperwork/billing, supplies, recovery).

## 2. Knowledge: examples and guidelines

| Kind | What a clinician writes | How it is used |
|---|---|---|
| **Example Q&A** (exists) | A typical question, other wordings, and the clinic's answer. | Shown to the model as examples, with their meaning matched rather than the exact words; the answer is the preferred wording. |
| **Guideline** (new) | A titled block of text, pasted or uploaded as .txt/.md. Optional "applies to": procedures (list) and care setting (inpatient / outpatient). | Included only when it applies to this patient. A guideline with no "applies to" applies to everyone. |

**Shared rules for both:**
- **Lifecycle:** draft → approved (named approver) → retired. Editing approved content makes a new draft version. Only approved content is ever used.
- **PHI check on save:** a phone number, a date of birth, an email address or an MRN-like number is refused.
- **No RAG at clinic scale:** the whole approved and applicable set goes into the prompt (limits: 100 guidelines × 20,000 characters, 500 examples, 200,000 characters approved in total).
  - This is simpler, more predictable and easier to audit than retrieval.
  - The component has a `Retriever` seam, so Vertex AI RAG Engine can replace "send everything" when a clinic outgrows the budget.

**Patient context given to the agent:**
- procedure;
- days since surgery;
- care setting (a new optional field on call-list entries, CSV import and the ingest API);
- diabetic or not.

Never the name, DOB, phone or MRN.

## 3. Drafting and verification

**The draft** (`gemini-3.8-flash`) returns:
- `covered`: bool;
- `answer`: ≤ 3 short spoken sentences;
- `source_ids`: the material actually used.

The instruction:
- answer only from the material given;
- if it doesn't clearly cover this patient's situation (procedure, setting, timing), set `covered = false`;
- no medication, doses or diagnosis;
- plain words, no "you should be fine".

**Verification, every step deterministic or Google-checked:**
1. `covered` is false, or there are no sources → defer.
2. **Grounding check:** Vertex AI's check-grounding API (Discovery Engine `groundingConfigs/default_grounding_config:check`), with the cited sources as facts. The support score must reach `min_support` (default 0.85, admin-tunable 0.5–1.0). A claim with no citation fails.
3. **Output veto:** the answer is run through the keyword rules too. Any medication, dose or symptom wording defers.
4. **Time budget:** 4 s in total on a live call; a timeout defers. The agent may say one holding line ("Let me check that for you").

## 4. Three modes: off → shadow → live

| Mode | What the patient hears | What happens |
|---|---|---|
| **off** (default) | Today's behaviour: every question goes to a nurse. | Nothing. |
| **shadow** | Today's behaviour: every question goes to a nurse. | After the report is filed, a background task runs the agent on each question and stores what it *would* have said (answer, sources, scores, reason) on the question. Nurses see it in the drawer, under "Agent's draft answer (not said to the patient)", and rate it *Good* or *Not right*. Calls are never slowed. |
| **live** | An answer, when one passes every check; otherwise a nurse follows up. | The voice tool calls the agent during the call. Answered questions are recorded with sources and scores. "Did that answer your question?" — "no" turns it back into a nurse follow-up. |

Shadow mode is the evidence for going live: the nurse ratings plus the evaluation set (§6) show how often the agent would have been right, and how often it would have been confidently wrong.

**Analytics** (later): the shadow "good" rate, the deferral reasons, and the questions most often not covered. That last list is the to-do list for new knowledge content.

## 5. API contract

All `/api/admin/knowledge/*` routes are ADMIN only and audited. Reads are not audited, and neither is `/try`.

**Settings** (`settings/knowledge`) replaces `enabled` / `min_match`:
```json
{"mode": "off|shadow|live", "channels": {"voice": true, "sms": false},
 "allowed_categories": ["appointment","activity","diet","paperwork_billing","supplies","recovery"],
 "min_support": 0.85, "ask_satisfied": true,
 "updated_at": null, "updated_by": null,
 "locked_categories": ["medication","symptom"],
 "available_categories": [{"key": "appointment", "label": "Appointment"}]}
```
- `GET` and `PUT /api/admin/knowledge/settings`; `PUT` is a partial update.
- An old document with `enabled: true` reads as `mode: "live"`; `enabled: false` reads as `"off"`.

**Example Q&A:** the existing `/items` routes and Item shape are unchanged.
- `category` stays (used for filtering and display) but no longer has to match the classifier's topic.
- A question whose own wording reads as medication or symptom is still refused.

**Guidelines (new)** use the same lifecycle routes as items:
- `GET /api/admin/knowledge/documents` → `{documents: [Doc]}`
- `POST /api/admin/knowledge/documents` `{title, body, procedures?, care_settings?}` → Doc
- `PATCH /api/admin/knowledge/documents/{doc_id}` (the same fields)
- `POST …/documents/{doc_id}/approve`, `POST …/documents/{doc_id}/retire`
- `DELETE …/documents/{doc_id}` (only a draft or retired one; an approved one gets 409)

Doc:
```json
{"doc_id": "…", "title": "Lifting after outpatient knee surgery", "body": "…",
 "procedures": ["knee arthroscopy"], "care_settings": ["outpatient"],
 "status": "draft|approved|retired", "version": 1, "chars": 1234,
 "created_at": "…", "created_by": "…", "updated_at": "…", "updated_by": "…",
 "approved_at": null, "approved_by": null}
```
- **Limits:** title ≤ 120 characters, body ≤ 20,000 characters, ≤ 100 documents, ≤ 200,000 approved characters in total (approving beyond that is a 409).
- **`procedures`:** ≤ 20 free-text names. A guideline applies when one of them matches the patient's procedure, case-insensitively, as a substring either way.
- **`care_settings`:** a subset of `["inpatient","outpatient"]`; empty applies to all.

**Try it:** `POST /api/admin/knowledge/try` `{question, context?: {procedure?, care_setting?, days_since_surgery?, is_diabetic?}}` returns:
```json
{"outcome": "answered|deferred", "reason": "…",
 "classification": {"category": "diet", "category_label": "Diet",
                    "llm_category": "diet", "keyword_category": "other",
                    "red_flag": false, "vetoed_by": null},
 "answer": "…", "sources": [{"kind": "example|guideline", "id": "…", "title": "…", "version": 1}],
 "support_score": 0.93, "latency_ms": 1840, "note": "test only: mode is shadow"}
```
- **`reason` is one of:** answered, empty_question, red_flag, locked_category, unclassified, category_not_allowed, low_confidence, no_knowledge, not_covered, low_support, output_veto, timeout, error.
- **`vetoed_by`** is `"keywords"`, `"llm"` or null.

**Patient-question record** (on the call, optional fields, and old entries stay valid):
- **Live answers:** `outcome`, `answer`, `sources`, `support_score`, `satisfied`.
- **Shadow:** `shadow`: `{outcome, reason, answer, sources, support_score, at}`.
- **Nurse rating:** `shadow_review`: `{verdict: "good|not_right", note, by, at}`.

**Nurse rating route:** `POST /api/calls/{call_id}/questions/{index}/shadow-review` with `{verdict, note?}`.
- Callers: the nurse UI and the nurse agent (NURSE).
- A note is ≤ 300 characters and passes the PHI check.

**Care setting:** an optional `care_setting` (`inpatient|outpatient`) on call-list entries (`POST /api/call-list`, bulk, CSV import), on ingest calls, and in the call's `patient_context`.

## 6. Testing

**Unit:**
- the classifier veto table (the keyword rules can only block);
- locked categories, whatever the settings say;
- guideline applicability matching;
- every path that defers;
- the output veto;
- the mode switch: off and shadow leave the voice instruction and tools byte-for-byte as today.

**Evaluation set** (`config/knowledge/eval_questions.yaml`, growing to about 100):
- Each question has the patient context and a label: answer or defer.
- Paraphrases are included, e.g. "glass of wine", "hop in the shower".
- **CI (offline, fake models):** the gates must never answer a question labelled defer.
- **`make eval-knowledge` (live models):** reports deferral precision (must be 1.0), coverage, and the grounding scores.

**Shadow review data** becomes evaluation data: a "not right" answer is a new labelled case.

## 7. Order of work

| Step | Scope |
|---|---|
| A | `voa_knowledge` package: classifier and veto, assembly, draft, grounding check, output veto, modes; guidelines API; care setting; shadow task and the review route; voice tool in live mode; IAM for check-grounding; tests and evaluation set. |
| B | Admin UI: mode selector, guidelines editor (paste or upload .txt/.md, applies-to), "Try it" with the classification, sources and scores. Nurse UI: the shadow draft and its rating in the drawer. Care setting in patient forms and CSV import. |
| C, later | Text-message path; PDF/DOCX import; RAG Engine retriever beyond the budget; analytics panel; nurse-agent card shows shadow drafts. |

## Questions to settle (clinical)

1. **Who approves knowledge content,** and does each guideline need a review date?
2. **Should recovery-timeline questions be answerable?** "Is it normal that…" can edge into symptoms.
3. **What shadow result is good enough to go live?** Suggested: ≥ 200 rated drafts, ≥ 95% "good", no harmful answer.
4. **Which content loads first?** Discharge instructions per procedure, lifting and driving rules, parking and appointments.
