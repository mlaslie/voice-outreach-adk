# Knowledge base and grounded answers: plan

**Status:** step 1 built, backend only (20 Oct 2026); not deployed. Off by default.

- **Built:** the shared function `voa_core.knowledge.answer` (red-flag check, then the category gate, then the Q&A matcher); Firestore `knowledge_items` and `settings/knowledge`; the admin API (`docs/KNOWLEDGE-API.md`); the voice tools `answer_from_knowledge` and `record_answer_feedback` and the instruction block, both present only when the knowledge base is on for voice; recording of `outcome`, `item_id`, `item_version`, `answer` and `satisfied` on each patient question; triage and the nurse card; the question test set (`config/knowledge/`) and `scripts/seed_knowledge.py`.
- **Q&A entries only.** The matcher asks `gemini-3.8-flash` to *select* one approved item in the question's category (or none), with a confidence; it sees only the candidates' wordings and the patient's question, never the answers or the patient. Answered only at confidence ≥ `min_match` (default 0.8); a 3 s timeout or any error defers. The patient hears the item's exact text.
- **Deferred to step 3:** documents, the Vertex AI RAG Engine corpus (no Terraform yet), generated answers and the check grounding API; the text-message path (SMS questions keep today's behaviour; `answer()` has no voice-only assumptions).
- **Not built here:** the admin UI (built separately against `docs/KNOWLEDGE-API.md`).

**Related:** `docs/ADMIN-PLAN.md` (the knowledge base is managed in the admin UI) and `docs/ANALYTICS-PLAN.md` (answered vs deferred questions).

## What changes for the patient

Today the agent never answers a question: it repeats a short version back and says the care team will follow up. With this feature, at the "Do you have any questions?" step:

1. The patient asks a question.
2. If the clinic's **approved** material answers it, and the question is in a category the clinic allows the agent to answer, the agent gives that answer. It then asks "Did that answer your question?"
3. Otherwise the agent says a nurse will answer, records the question and triages it as today: medication or symptom questions same day, the others next business day.
4. Each question is decided on its own: one may be answered on the spot, the next deferred to a nurse.

**Example:**
- "When can I shower?" → answered from the discharge instructions.
- "Can I take ibuprofen with my blood thinner?" → deferred to a nurse, same day.
- "Where do I park for my follow-up visit?" → answered.

## Safety rules

These are deterministic, in code, and off by default.

1. **Grounding is off unless an admin turns it on** (Admin → Knowledge base). With it off, behaviour is exactly as today.
2. **Approved content only.**
   - Every file or Q&A entry has a status: draft, then approved (by a named clinician), then retired.
   - Only approved content is searchable. Changing a document makes it a draft again until it is re-approved.
3. **The model never answers from memory.** The answer step is a tool, `answer_from_knowledge(question)`. It returns either an approved answer with its source, or "defer". The instruction requires the agent to say only what the tool returns, or the deferral line.
4. **Categories gate the answer before retrieval runs.**
   - The same deterministic classifier as today (`patient_questions.py`) runs first.
   - **Medication and symptom questions are never answered,** in any configuration.
   - The other categories are allowed by default and each can be switched off: appointments, activity, diet, paperwork, supplies, recovery timeline.
   - "Other" (unclassified) defers.
5. **Two answer types, safest first:**
   - **Q&A entries:** a matched question returns the clinic's exact written answer. Nothing is generated.
   - **Documents** (discharge instructions, FAQs): a short answer is generated from the retrieved passages. It is then checked with Google's **check grounding API**, and spoken only if every claim is supported (support score ≥ the admin's threshold, default 0.9). It always names the source ("Your discharge instructions say…").
6. **Retrieval has a minimum relevance score.** Below it, the agent defers. Defer is the default for every failure: timeout, error, low score, or a grounding check that doesn't pass.
7. **The emergency check still runs on every turn first.** A red flag inside a question goes to the 911 script, as today.
8. **"Did that answer your question?"**
   - "No" or "not really" turns the question into a nurse follow-up, triaged as if it had been deferred.
   - A clarification is asked at most once.
9. **Everything is recorded.** Each question keeps:
   - `outcome: answered | deferred`;
   - for an answered question: the answer as spoken, the source document and version, the scores, and whether the patient was satisfied.

   Nurses see answered questions in the call drawer and the Gemini Enterprise card ("Answered by the agent from: Discharge instructions v3"), so they can correct anything. Answered questions don't raise the follow-up tier.
10. **No PHI goes in.** The knowledge base holds clinic material only. The upload check rejects files with obvious PHI patterns. Questions sent to retrieval go without the patient's name or identifiers.

## Components

```
Admin UI → Knowledge base
   upload (PDF, DOCX, TXT, MD; CSV of Q&A) · edit Q&A · approve / retire
   toggles: grounding on/off · voice / text · allowed categories · thresholds
   "Try it": ask a question, see answer or defer, with source and scores
        │
        ▼
voa-api  /api/admin/knowledge/*   (admin UI identity only; audit logged)
   GCS bucket "knowledge" (files, versioned)  +  Firestore knowledge_items (metadata, status)
        │  on approve / retire
        ▼
Vertex AI RAG Engine corpus  (approved items only; re-indexed on change)
        ▲
        │  retrieve (relevance threshold)          check grounding API
voa-gateway: voice tool answer_from_knowledge ──────────────┘
voa-api:     the SMS questions loop uses the same function (shared in voa_core)
```

- **Retrieval: Vertex AI RAG Engine.**
  - Managed parsing, embedding and storage, in the same project and region, created by Terraform.
  - Vertex AI Search is a fine alternative: it is search-quality ranking with Cloud Storage sync, so worth evaluating with real clinic documents in step 1.
  - Retrieval runs inside our own tool, so it works with the Gemini Live agent the same way `verify_identity` does today.
- **One shared function** (`voa_core.knowledge.answer(question, category, policy)`) is used by both the voice tool and the text questionnaire. Both channels answer the same question the same way.
- **Speed on calls.** Retrieval plus a grounding check adds about 1–2 seconds.
  - The agent covers it with a short holding line ("Let me check that for you"), allowed only on this tool.
  - Q&A matches skip generation and the grounding check, so they are the fast path.
  - If an answer takes more than 4 seconds, the agent defers.
- **Text messages.** Same rules. The answer is sent as the clinic's text, with a reply option ("Reply NO if that didn't answer it").

## Configuration (admin UI, stored in Firestore `settings/knowledge`)

| Setting | Default |
|---|---|
| Answer from knowledge base | off |
| Channels | voice and text |
| Allowed categories | appointment, activity, diet, paperwork, supplies, recovery. Medication and symptom can never be enabled. |
| Minimum retrieval relevance | to be tuned in step 1 against a test set |
| Minimum grounding support (generated answers) | 0.9 |
| Generated answers from documents | off (Q&A entries only) until clinical sign-off |
| Ask "Did that answer your question?" | on |

## Testing and evaluation

- **Unit tests:**
  - the category gate (medication and symptom are always deferred, whatever the settings);
  - thresholds;
  - the "defer on any failure" paths;
  - recording of answered and deferred questions.
- **A question test set** maintained with the clinic: about 100 questions, each labelled "answer" or "defer", plus the expected answer. CI runs it whenever content or settings change and reports:
  - deferral precision (it must never answer a question labelled defer);
  - answer coverage.
- **Live persona evals:** new simulated patients ask an answerable question and then a medication question in the same call. The checks: the first is answered with the source named, the second is deferred and triaged same day, and the call still ends with the medication-list reminder.
- **Analytics** (see the analytics plan): answered vs deferred by category, "didn't answer it" rate, and top unanswered questions. That last list is the to-do list for new knowledge base content.

## Order of work

| Step | Scope | Size |
|---|---|---|
| 1 | **Built (backend).** Shared answer function with the category gate and the Q&A-entry path; admin storage, approval and settings API; voice tool and instruction changes behind the off-by-default toggle; recording of outcomes; unit tests and the question test set. The RAG Engine corpus moved to step 3. | M |
| 2 | Admin UI: Knowledge base section (upload, Q&A editor, approval, "Try it", settings). Nurse UI and Gemini Enterprise card show answered questions with their source. | M |
| 3 | RAG Engine corpus (Terraform); generated answers from documents with the grounding check; the text-message path; persona evals; analytics panel. | M |

Step 1 can begin once the admin UI split (admin plan, step 1) has given admin routes their own identity.

## Questions to settle

1. **Who approves knowledge content,** and does each document need a review date?
2. **Should "recovery timeline" questions be answerable?** They can edge into clinical territory ("is it normal that…").
3. **Generated answers from documents:** are they wanted at all, or Q&A entries only? Q&A only is far easier to sign off.
4. **Starting content:** which material should load first (discharge instructions per procedure, parking and appointment FAQs)?
5. **Per-procedure knowledge:** should answers depend on the patient's procedure? (Knee replacement vs heart valve: different showering guidance.) Retrieval would then filter on the procedure in the call context.
