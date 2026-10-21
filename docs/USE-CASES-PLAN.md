# New use cases (pre-op, and the next ten): plan

**Status:** proposal (21 Oct 2026). Nothing is built.

**Goal:** a new outreach scenario takes **days, not a development project**:
- A clinical lead builds it in the admin UI.
- Or a developer or an AI skill produces it as a YAML file.
- Either way it goes through the same checks, the same approval and the same tests.

**Related:**
- `docs/EDITING-QUESTIONS.md`: today's post-op editing guide.
- `docs/KNOWLEDGE-PLAN.md`: knowledge scoped per use case.
- `docs/ADMIN-PLAN.md`: where the builder lives.

## 1. Where we are

The post-op campaign file (`config/campaigns/post_op.yaml`) already drives the voice agent's wording: questions, scripts, red flags, SMS text. But the rest of the system is built around the post-op question ids.

| Area | Today | Blocks a second use case because |
|---|---|---|
| Choosing the use case | `campaign_id` defaults to `"post_op"` everywhere. Call-list entries, runs, ingest and nurse tools have no field for it, and the gateway never reads it. | Every call is post-op. |
| Answer parsing | `survey._PARSERS` is keyed by **question id** (`pain_severity`, `fever_reported`…). | `plan_problems` rejects a file whose ids differ. |
| Triage | One function, `survey.triage()`, over the post-op fields. | A pre-op call always triages as "none". |
| Report check | `reports.COMPLETED_REQUIRED_ANSWERS` requires the post-op answers. | A completed pre-op report is refused. |
| SMS questionnaire | `survey.SMS_CAMPAIGN_ID = "post_op"`, cached once for the process. Pain and swelling variants are special-cased. | Texts are always post-op. |
| Closing | The medication-list reminder and the patient-questions loop are required by code. | Some scenarios need neither. |
| Voicemail, agent name | One clip per gateway, from post_op. | The same voicemail for everything. |
| Nurse UI, nurse agent | Findings rows are pain, fever, incision, swelling, sleep and sugar; "procedure" and "POD" are on the cards. | A pre-op call shows "Not asked" on every row. |
| Analytics | `fact_calls` has columns `pain_score`, `fever`, `incision`…, and no `campaign_id`. | There is nowhere to put other answers. |
| Knowledge | The prompts say "after surgery"; the context is days *since* surgery. | Pre-op has days *until*. |
| Evals | The personas are post-op. | There is no test of a new script. |

**What already generalises:**
- the call frame (`call_facts`, identity check, consent, recording, red-flag handling, end of call);
- telephony, recall and SMS mechanics;
- the generic answer list in the drawer (`survey-answers.tsx`);
- the knowledge base's applies-to model.

## 2. The idea: a use case is a versioned spec, the code is an engine

A **use case spec** (one document; the YAML and the admin UI edit the same schema) declares *everything* that differs between scenarios. The code becomes a generic engine that reads it.

```
                      ┌──────────── use case spec (versioned, approved) ───────────┐
admin UI builder  ─┐  │ who it's for · patient fields · questions (typed) ·        │
YAML / AI skill   ─┼─▶│ rules (tiers) · scripts (voice, SMS) · nurse display ·     │
import / export   ─┘  │ knowledge scope · test personas                            │
                      └────────────────────────────┬───────────────────────────────┘
                                                   ▼
   engine (code, unchanged per use case): identity · consent · red flags/911 · no advice ·
   calling hours · allowlist · STOP/HELP · parsing by TYPE · rule evaluation · reports ·
   nurse UI and agent rendered from the display spec · analytics in long format
```

**What a spec can never turn off** (enforced in code and checked by the linter):
- identity verification before any health talk;
- the consent and recording notice;
- red-flag detection and the 911 script (a spec can *add* red flags, never remove the shared ones);
- no medical advice;
- calling hours;
- the dial allowlist / consent rules;
- SMS STOP/HELP.

## 3. The spec (schema v2)

The spec extends today's `Campaign` model. Post-op is rewritten in it and must behave identically (§6, step 1).

```yaml
id: pre_op                      # stable key; versions are tracked separately
name: Pre-op readiness call
status: draft                   # draft → in_review → approved → active → retired
channels: [voice, sms]
agent_name: Sara
purpose: >                      # one paragraph; shown to staff and used in the agent's frame
  Confirm the patient is ready for surgery: arrival time, fasting, ride home,
  medicines they were told to stop, and any questions.

patient_fields:                 # drives forms, CSV columns, ingest validation and `when_patient`
  - {id: procedure, type: text, required: true}
  - {id: surgery_date, type: date, required: true}
  - {id: arrival_time, type: time, required: true}
  - {id: location, type: text}
  - {id: is_diabetic, type: bool}

questions:
  - id: arrival_confirmed
    type: yes_no
    ask: "Your arrival time is {arrival_time} on {surgery_date}. Does that still work for you?"
    sms: "Your arrival time is {arrival_time} on {surgery_date}. Does that still work? Reply YES or NO."
  - id: fasting_understood
    type: yes_no
    ask: "Do you know when to stop eating and drinking before surgery?"
  - id: ride_home
    type: yes_no
    ask: "Do you have an adult to drive you home and stay with you?"
  - id: stop_meds_instructions
    type: yes_no_unknown
    ask: "Were you told which of your medicines to stop before surgery?"
  - id: feeling_unwell
    type: yes_no
    ask: "Have you had a fever, cough or cold in the last few days?"
  - id: unwell_description
    type: text
    when: {feeling_unwell: "yes"}
    ask: "Can you tell me a little about that?"
  - id: has_questions
    type: open_questions        # the existing questions loop, now optional per use case

rules:                          # evaluated in order; every match is a finding; the worst tier wins
  - when: "feeling_unwell == yes"
    tier: urgent_same_day
    reason: "unwell before surgery: {unwell_description}"
  - when: "stop_meds_instructions in [no, unknown]"
    tier: urgent_same_day
    reason: "unsure which medicines to stop"
  - when: "ride_home == no"
    tier: routine_next_business_day
    reason: "no ride home arranged"
  - when: "arrival_confirmed == no"
    tier: routine_next_business_day
    reason: "arrival time does not work"
  - when: "fasting_understood == no"
    tier: urgent_same_day
    reason: "fasting instructions not understood"

red_flags_extra: []             # added to the shared list, never replacing it

scripts:                        # voice; the required keys come from the engine's frame
  opener: "Hi, this is {agent_name} calling from {org_name} about your upcoming surgery…"
  closing_follow_up: "…"
  closing_no_follow_up: "…"
  closing_extra: ""             # replaces today's hard-wired medication-list reminder
  voicemail: "…"
sms_scripts: {…}

display:                        # what nurses see (web drawer, Gemini Enterprise card, lists)
  title: Pre-op readiness
  facts: [procedure, surgery_date, arrival_time]
  findings:
    - {label: Arrival, from: arrival_confirmed}
    - {label: Fasting, from: fasting_understood}
    - {label: Ride home, from: ride_home}
    - {label: Medicines to stop, from: stop_meds_instructions}
    - {label: Feeling unwell, from: [feeling_unwell, unwell_description]}

analytics:
  measures: [arrival_confirmed, fasting_understood, ride_home, stop_meds_instructions, feeling_unwell]

knowledge:
  scope: pre_op                 # guidelines and examples tagged for this use case
  context: [procedure, surgery_date, care_setting]

tests:                          # scripted patients; CI runs them offline, the voice eval runs them live
  - name: all good
    answers: {arrival_confirmed: yes, fasting_understood: yes, ride_home: yes,
              stop_meds_instructions: yes, feeling_unwell: no, has_questions: no}
    expect: {tier: none}
  - name: has a cold
    answers: {feeling_unwell: yes, unwell_description: "bad cough since Sunday", …}
    expect: {tier: urgent_same_day, reasons_include: ["unwell before surgery"]}
```

**Design choices in the spec:**

- **Parsing by type, not by id.**
  - The types are `yes_no`, `yes_no_unknown`, `scale` (min and max), `choice`, `number` (unit and range), `date`, `time`, `text` and `open_questions`.
  - Any question of a type gets the same tested parser on voice and SMS.
- **Rules: a small, safe expression language, never `eval`.**
  - Comparisons, `and`/`or`/`not`, `in [...]`, `answered(x)`, `mentions(x, word_list)`, and patient fields.
  - The UI builds rules as rows (when … then tier … because …), and the YAML shows the same text.
  - **Escape hatch:** named, reviewed Python checks (`check: swelling_is_urgent`) for clinical logic too subtle for the expression language. This is how post-op's free-text heuristics carry over unchanged.
  - Adding a named check is a code change with clinical review. Using one in a spec is not.
- **What "complete" means comes from the spec:** every applicable question that is not optional must have an answer. This replaces the post-op lists in `reports.py`.
- **Display and analytics are declared:**
  - The nurse UI, the nurse agent and analytics render from `display` and `measures`.
  - Analytics moves to a long table `fact_answers(call_id, campaign_id, spec_version, question_id, value_bool, value_num, value_choice)`. Free text is never exported.
  - Post-op keeps its current panels through the same mechanism.
- **Every call stores `campaign_id` and `spec_version`.** An old call always displays and triages with the version it was made with.

## 4. Lifecycle and storage

**One schema, two homes:**
- **Git:** `config/campaigns/*.yaml` holds the seed and source, where developers and the skill work.
- **Firestore:** `campaigns/{id}/versions/{n}` holds what the admin UI edits.
- **Movement between them:** import and export are lossless. `scripts/campaign.py push` uploads a YAML file as a draft; `pull` exports a version.

**Status flow:** draft → in review → approved → active → retired.
- **Approval:** a named clinical approver, required for anything that changes questions, rules, red flags or patient-facing wording. The approver sees a diff against the active version.
- **Activation** is a separate step: the version becomes the one new calls use. Calls in flight keep their version.
- **Same validator everywhere** (`campaign_problems()`), in the UI, the API, the CLI, CI and the skill:
  - the schema;
  - required scripts for the chosen channels;
  - placeholders that resolve against `patient_fields`;
  - GSM-7 and length limits for SMS;
  - rules that reference known ids;
  - every question reachable;
  - the tests pass;
  - the guardrails in §2 untouched.

**Assignment** (which patients get which use case):
- a `campaign_id` on call-list entries, runs, ingest tranches (`"campaign": "pre_op"`), the CSV import and the nurse forms;
- a default per ingest source;
- lists, the nurse agent and analytics can filter by use case.

## 5. Two ways to build one

### a. Admin UI → Use cases (for a clinical lead)

1. **Start:** from a template (post-op, pre-op, blank) or by duplicating an existing use case.
2. **Basics:** name, purpose, channels, the agent's name.
3. **Patient fields:** pick from common ones (procedure, surgery date, diabetic…) or add one with a type. The CSV and ingest columns appear automatically.
4. **Questions:** an ordered list you can drag.
   - Each question has a type picker, the wording for voice and for text, and an "only ask if…" condition.
   - A live preview shows how it sounds and how the text reads, with a GSM-7 counter.
5. **Rules:** when … then tier … because …, written as rows.
   - The test grid alongside shows scripted answers and the resulting tier, updated as you edit.
6. **Scripts:** opener, closings, voicemail and the SMS texts, with placeholder chips and checks.
7. **Nurse view:** which facts and findings show, with a live mock of the drawer and the Gemini Enterprise card.
8. **Check:** the validator's results in plain words, each with a link to the field to fix.
9. **Try it:**
   - a browser test call (softphone) and "Test by text" against the **draft** version;
   - a text-only run with scripted answers.
10. **Submit for approval**, then **activate**.
    - The diff view shows what changed since the active version.
    - YAML import and export are on every use case.

**"Draft with AI"** (in the UI, later): paste a protocol or discharge or prep sheet. Gemini produces a draft spec against the same JSON Schema, and it lands as a draft for a human to edit and approve. It never activates anything.

### b. Automation (for developers, and for batches of new use cases)

- **CLI** (`scripts/campaign.py`):
  - `new --from pre_op`
  - `lint`
  - `render`: the voice instruction and every SMS text, as the patient would see or hear them
  - `simulate`: the scripted tests, offline
  - `diff`
  - `push --draft`
  - `pull`
- **JSON Schema** (generated from the pydantic model): editor autocompletion, and structured output for any LLM that drafts a spec.
- **Claude Code skill `new-use-case`** (in `.claude/skills/new-use-case/`), working from a short brief or a pasted protocol:
  1. Asks the few questions it can't infer: channels, patient fields available from the EHR, which answers are urgent.
  2. Writes `config/campaigns/<id>.yaml` from the nearest template.
  3. Writes test personas, both scripted answers and voice personas.
  4. Runs `lint`, `simulate` and the offline tests, and fixes its own errors.
  5. Optionally runs the live voice eval for that use case.
  6. Opens a branch or PR, or pushes the spec as a **draft** to the admin UI.
  7. **Never approves or activates.** A named clinician does that in the UI.
- **CI** runs every spec's tests on every change, and the live persona eval per use case on demand: `make eval-voice CAMPAIGN=pre_op`.

## 6. Order of work

| Step | Scope | Outcome |
|---|---|---|
| 1. Engine, post-op parity | • `campaign_id` and `spec_version` flow from entry, run and ingest through the dialer and gateway to the call doc, report and SMS session.<br>• Parsers by type.<br>• Rule engine plus named checks.<br>• Completeness from the spec.<br>• `closing_extra` and an optional questions loop.<br>• Voicemail per use case. | Post-op rewritten as a spec gives **identical** results: every existing triage, report and SMS golden test passes unchanged, plus an equivalence test over recorded call answers. No visible change. |
| 2. Display and analytics from the spec | • Nurse UI drawer, lists, the nurse-agent cards and the system prompt render from `display`.<br>• `fact_answers` in long format, with `campaign_id` everywhere.<br>• Use-case filter. | Post-op looks the same, and any spec renders without code. |
| 3. Lifecycle, CLI, skill, first new use case | • Firestore versions, validator, approval and activation API, import and export.<br>• `scripts/campaign.py` and the JSON Schema.<br>• The `new-use-case` skill.<br>• **Pre-op built with the skill** as the proof, live personas included. | A developer or the skill adds a use case in about a day. |
| 4. Admin builder | • Use cases section: wizard, previews, rule test grid, try it on the draft, approval with diff.<br>• Assignment in forms, CSV and ingest. | A clinical lead adds one without a developer. |
| 5. Later | • "Draft with AI" in the UI.<br>• Template gallery.<br>• Knowledge scoped per use case.<br>• Inbound calls (a different frame; the same spec). | |

Step 1 is the only risky one, because it touches the call path. Equivalence tests keep it honest: it ships only when post-op behaves byte for byte the same.

## 7. Things outside the software

- **SMS registration (A2P 10DLC).** US carriers register *what a number's texts are for*. A new use case with different message content (pre-op reminders, versus post-op follow-up) may need a new or amended Twilio campaign registration before its texts are allowed. Check this per use case. Voice has no equivalent.
- **Consent wording.** Patients consent to texts for a purpose, so the consent text may need to cover the new use.
- **Clinical sign-off** on every rule set, and an owner for each use case.
- **Caller ID and number per use case**, if patients should recognise the purpose of the call.

## 8. Questions to settle

1. **Who approves a new use case:** one named clinician, or a committee? Do wording-only changes need the same approval as rule changes?
2. **Must every use case support text,** or can some be voice only?
3. **Which use cases come after pre-op** (for example care transitions, medication adherence, appointment reminders)? They decide which question types and named checks to build first.
4. **May an EHR feed choose the use case per patient,** or only per source?
5. **Should the agent's name and voice be per use case?**
