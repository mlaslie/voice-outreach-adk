# Editing the questions (use-case specs)

Each kind of outreach call (a **use case**) is one file under `config/campaigns/`:

- **`config/campaigns/post_op.yaml`**: the post-operative wellness check (live).
- **`config/campaigns/pre_op.yaml`**: a pre-op readiness example (draft; reachable only through the API's `campaign_id`).

A file holds the questions, what the voice agent and the text messages say, **and the triage rules**. You can reword a question there without touching any code. The same file drives the phone call, the text-message (SMS) questionnaire, the triage and the report check. The code is a generic engine that reads it (`docs/USE-CASES-PLAN.md`).

## What each field means

| Field | Where | What it does |
|---|---|---|
| `ask` | each question | What the voice agent asks on the phone. The agent may rephrase it slightly to sound natural. |
| `sms` | each question | The text message for that question. Every question needs one, follow-ups included. |
| `sms_if_known` | a follow-up with `reuse: pain_site` (post-op's `pain_location`) | Sent instead of `sms` when the patient already said where it hurts. `{site}` is the place they named (for example "incision"). |
| `sms_if_side_known`, `sms_if_trend_known` | a follow-up with `reuse: swelling_parts` (post-op's `swelling_description`) | Sent instead of `sms` when the patient already said which side is swollen (ask only whether it is getting worse), or already said it is getting worse (ask only which side). |
| `note` | each question | Extra guidance for the voice agent only, for example "For a range, record the higher number". The agent reads it as an instruction and never says it aloud. Texts ignore it. |
| `when` | follow-up questions | Ask this question only after a particular answer, for example `when: {pain_present: "yes"}`. By text, follow-ups are sent straight after that answer and are not numbered. |
| `when_patient` | some questions | Ask only some patients, for example `when_patient: {is_diabetic: true}` for the blood-sugar question. |
| `type`, `choices`, `min`, `max`, `unit`, `parser` | each question | What kind of answer is recorded and how it is read (see "Types" below). |
| `optional` | a question | Not needed for a report to count as complete. |
| `required_with` | a top-level question | Sub-answers a completed report must carry once this question has a given answer, and the message the voice agent reads if they are missing (see below). |
| `reuse` | a follow-up | A named helper that rewords the follow-up by text from what the patient already said (`pain_site`, `swelling_parts`). |
| `rules` | top level | The triage: what makes a patient same-day, next-day or "noted" (see "Rules" below). |
| `tests` | top level | Scripted answers and the tier they must produce. The check runs them. |
| `scripts` | top level | The voice agent's fixed lines: opener, identity check, consent, closings, voicemail, emergency line, and `closing_extra` (said before every completed call's goodbye; post-op: the medication-list reminder). |
| `closing_extra_label` | top level | How the voice instructions name `closing_extra` ("the medication-list reminder"), so the agent never skips it. |
| `question_noun` | top level | The word in report errors the voice agent reads ("Ask every applicable **wellness** question"). |
| `sms_scripts` | top level | The text questionnaire's own lines: the "I'll send N questions" preamble, the numbering, "Sorry, I didn't catch that", the questions-for-the-care-team loop, the closings, `closing_extra` and the failure messages. |
| `red_flags_extra` | top level | Symptoms, besides the shared list, that make the voice agent stop and tell the caller to dial 911. The shared red flags are in code (`voa_core/campaign.py`, `SHARED_RED_FLAGS`) and no file can remove one. |
| `patient_fields` | top level | The patient facts the use case knows (`is_diabetic`, `operation_date`...). `when_patient` and rules can test only these. |
| `version`, `status`, `purpose`, `channels` | top level | The spec's version (stored on every call and report), its lifecycle status, a one-paragraph purpose, and `voice` / `sms`. |
| `display`, `analytics`, `knowledge` | top level | What nurses see, what analytics measures, and the knowledge scope. Checked but not used yet (step 2). |

Questions are asked in the order they appear in the file, on both channels.

### Placeholders

Text in `{braces}` is filled in by the system. Use only the placeholders listed here. Never write braces for any other purpose.

- **Voice `scripts`:** `{org_name}`, `{patient_first_name}`, `{patient_last_name}`.
- **`sms_if_known`:** `{site}`.
- **`sms_scripts.preamble`:** `{total}`.
- **`sms_scripts.numbered_question`:** `{number}`, `{total}`, `{question}`. `{question}` must stay.
- **`sms_scripts.unclear_answer`:** `{question}`, which must stay.
- **`sms_scripts.question_noted`:** `{question}` (what they typed) and `{another_question}`.
- **`sms_scripts.question_noted_last`:** `{question}`.
- **`sms_scripts.closing_routine` and `closing_urgent`:** `{questions_follow_up}` (when the use case has an `open_questions` question) and `{closing_extra}` (when `sms_scripts.closing_extra` is set). Both must stay. In post-op the clinic asked for the medication reminder on every completed questionnaire.
- **`required_with.message`:** `{missing}`, the sub-answers that are missing.
- **A rule's `reason`:** `{question_id}`, the recorded answer (for example `pain {pain_severity}/10`). A reason is cut to 200 characters.

`ask`, `note` and `sms` take no placeholders.

## Types

Answers are read by the question's **type**, the same way on the phone and by text:

| Type | Recorded as | An unclear answer |
|---|---|---|
| `yes_no` | `yes` / `no` ("a little" is yes) | Phone: asked again. Text: asked once more, then `unknown`. |
| `yes_no_unknown` | `yes` / `no` / `unknown` | `unknown`. |
| `scale` (`min`, `max`) | a whole number; a range takes the higher end. `scale_0_10` is the same as `scale` 0 to 10. | Asked again. |
| `choice` (`choices`) | one of the choices | Asked again. |
| `number` (`min`, `max`, `unit`) | a number | Asked again. |
| `date`, `time` | `YYYY-MM-DD`, `HH:MM` | Asked again. |
| `text` | the patient's words (up to 500 characters) | Kept as said. |
| `open_questions` | `yes` / `no`, then a loop that records each question for the care team | Asked again. |

By text, a follow-up (a question with `when`) that a report requires stays on the question for a few tries, then the questionnaire stops and files what it has.

**Named parsers** (`parser:`) replace the type's parser where reading the answer needs reviewed code. They live in `packages/voa_core/src/voa_core/parsers.py` (`NAMED_PARSERS`); adding one is a developer change.

| Parser | Reads | Notes |
|---|---|---|
| `trend` | better / same / worse | Synonyms ("improving", "getting worse") and negation ("not worse" is not "worse"). |
| `blood_sugar` | high / low / in_range | Meter readings in mg/dL ("120 to 250") or words ("a bit high"); the most abnormal wins. An unclear answer is `unknown`; the patient's own words are kept as `blood_sugar_reading`. |

## Rules (triage)

Every rule whose condition holds adds a **finding** with its tier and reason. The most urgent tier is the call's follow-up (`urgent_same_day` > `routine_next_business_day` > `informational`); the findings are listed most urgent first, in rule order within a tier. `emergency` is never a rule's: it is the call frame's (red flags and 911).

```yaml
rules:
  - when: "pain_severity >= 8"
    tier: urgent_same_day
    reason: "pain {pain_severity}/10"
    fields: [pain_severity]          # the answers the nurse is pointed at
  - when: "legs_swollen == yes and not check(swelling_is_urgent, swelling_description)"
    tier: routine_next_business_day
    reason: "leg or foot swelling"
  - any_of: [fever_reported, legs_swollen]   # one finding, for the ids that match
    when: "_ == unknown"
    tier: routine_next_business_day
    reason: "some answers unclear"
```

**The `when` language** (no code runs; it is read by a small parser in `voa_core/rules.py`):

- Compare an answer: `fever_reported == yes`, `pain_trend != worse`, `pain_severity >= 4 and pain_severity <= 7`. A bare word is text; quote values with spaces (`'in range'`). Comparisons are exact (`Yes` is not `yes`), and `<` `<=` `>` `>=` hold only between numbers (a `scale` answer counts only as a whole number).
- Lists: `blood_sugar_status in [high, low]`, `x not in [yes, unknown]`.
- `and`, `or`, `not`, parentheses.
- `answered(x)`: there is an answer (a number for number types; non-blank text otherwise).
- `mentions(x, [cough, 'short of breath'])`: the answer's text contains one of the words, not negated ("no cough" does not count).
- `patient.<field>`: a patient field (`patient.is_diabetic`). Never put patient facts in a `reason`: reasons say only what the patient reported.
- `check(name, args...)`: a named check (below).

**Named checks** (`check:` on a rule, or `check(...)` in `when`) are reviewed Python for what the language cannot say safely. They live in `packages/voa_core/src/voa_core/checks.py`; adding or changing one is a developer change with clinical review, and using one in a file is not.

| Check | Arguments | True when |
|---|---|---|
| `swelling_is_urgent` | the description's question id | The description says one side, worsening, or painful, red or warm, and not negated ("no redness"). |
| `severe_language` | - | The patient's own words (free-text answers and every raw reply) call the pain severe. Points at the message it was in. |
| `asks_for_a_person` | - | The patient's own words ask for a nurse, a doctor or a call. |
| `expressed_worry` | - | The patient's own words express worry or confusion. |
| `questions_for_care_team` | `same_day` or `next_day` | One finding per topic line of the recorded questions at that priority ("questions asked (medication)"). |
| `questions_recorded` | - | At least one question for the care team was recorded. |

Post-op's rules reproduce the pre-engine triage exactly: `packages/voa_core/tests/parity` compares them with outputs recorded before the engine, over several thousand cases.

**Any new free-text check must also work on the translated text** (docs/LANGUAGES-PLAN.md §3.3). A patient may answer in another language. The prose checks above are English keyword rules, so they fail safe on words they cannot read (a non-English description is urgent swelling; non-English words add an "answer in another language" finding), and after the call `voa_core/language_safety.py` runs them again on the English translation and raises the tier when that is more urgent. A new check therefore needs: a fail-safe result for non-English input (`parsers.not_english`), an entry in `language_safety.english_findings` so the post-call re-check runs it on the translation, and a row in `config/languages/eval/safety.yaml`.

### `required_with` (what "complete" means)

A completed report needs an answer to every top-level question that is not `optional` (questions with `when_patient` are asked by the voice agent but not required by the report check), and, for each `required_with`, its sub-answers once the parent has that answer:

```yaml
  - id: pain_present
    type: yes_no
    required_with:
      answer: "yes"
      fields: [pain_severity, pain_location, pain_trend]
      presence: key        # key: present at all; non_empty (default): present and not blank
      message: "pain_present=yes requires pain sub-fields: {missing}. Ask the pain severity, location, and trend sub-questions before submitting."
```

A `scale` sub-answer must also be a whole number in range. The voice agent reads the message, so it says what to ask.

### Text-message rules

- Use plain characters only: straight quotes `"` and `'`, a plain hyphen `-`, and three dots typed as `...`. One "smart" quote or long dash switches the message to a format that costs more than twice as much. The check below catches this.
- Nothing before the date-of-birth check may hint at a medical reason. That copy is deliberately not in this file.

## What you can change, and who needs to be involved

| Change | Who |
|---|---|
| Rewording `ask`, `sms`, `sms_if_*`, `note`, `scripts` or `sms_scripts` | **You.** Run the check, then deploy. |
| Reordering questions | **You**, but tell the care team, because the report reads in the new order. |
| Adding, removing or renaming a question, or changing its `type`, `when` or `when_patient` | **A developer, and a clinician if a rule reads it.** No code change is needed (parsing is by type), but the rules, `required_with`, the tests, and anything downstream that reads the answer (the nurse UI and analytics, until step 2) must agree. The check fails when a rule or `required_with` names a question that no longer exists. |
| Triage `rules`: what makes a patient same-day, next-day or "noted"; thresholds | **A clinician** (and the scripted `tests` updated with them). For post-op, the parity suite fails on any change of outcome: a change there is a deliberate clinical change in its own reviewed commit. |
| A new named check, parser or `reuse` helper; patient-question priorities (`patient_questions.py`) | **A developer and a clinician.** Reviewed code, so every subtle clinical decision can be traced to one function. |
| Adding a red flag (`red_flags_extra`) | **A clinician**, and a developer to check the SMS emergency keywords match. The shared red flags (`SHARED_RED_FLAGS` in `voa_core/campaign.py`) can never be removed by a file; changing them is a code and clinical change. |
| The SMS invite, the 1/2/3 menu, the date-of-birth messages, STOP/HELP replies | **A developer, with a compliance review.** This copy stays in `packages/voa_core/src/voa_core/copy.py`. It is sent before identity is verified, or it is registered with the carrier. |

## How to check your edit

```
uv run pytest -q -p no:warnings packages/voa_core packages/voa_agents
```

If something is wrong, a failing test names the question and the problem (`voa_core.campaign_lint.campaign_problems` checks every file, including its `tests`). Examples:

- `sleeping_well: missing sms`
- `preamble: unknown placeholder {count}`
- `not plain GSM-7 text`
- `rules[3]: `when` neither side refers to an answer or a patient field (check the question id's spelling)`
- `test 'severe pain': expected tier urgent_same_day, got routine_next_business_day (...)`

Some tests compare every text message with its exact expected wording, so they fail on any rewording you make on purpose. Those tests are in `packages/voa_core/tests/test_sms_golden.py`. When you change wording deliberately, update the expected text there in the same change, or ask a developer to.

## How to deploy

The campaign file is built into the service images, so an edit takes effect only after a deploy. Commit the change first: the deploy refuses uncommitted work. Then run:

```
scripts/deploy.sh demo gateway api
```

The gateway runs the phone calls and the API runs the text messages. Deploy both, so the two channels never disagree.

## Carrier registration (A2P 10DLC)

US carriers register sample messages for business texting. If you change the wording of patient-facing text messages, the registered A2P campaign may need its sample messages updated before texting is switched on. Texting is currently in dry-run. Check with whoever owns the Twilio account before changing copy on a live number.

## Choosing the use case for a call

Every call, call-list entry, run and ingest tranche can name a use case with `campaign_id` (ingest: `"campaign"`); without one it is post-op. The call stores `campaign_id` and `spec_version`, the gateway runs that spec, and a missed call's text follow-up uses it too. Nothing in the dashboard offers another use case yet.

## Language packs (calls in another language)

A use case is written in English. A **language pack** gives the same use case's wording in one other language: `config/campaigns/i18n/<campaign>.<language>.yaml`, for example `post_op.es-US.yaml`. Plan: `docs/LANGUAGES-PLAN.md` §3.2. Code: `packages/voa_core/src/voa_core/language_packs.py`.

**A pack is wording only.** The question ids, types, rules, triage, red flags and the call frame all come from the English spec, and a pack cannot change them. The voice agent records every answer as the same English value as on an English call ("yes", 7, "worse"), so triage, reports and the nurse screens need nothing per language.

The file:

```yaml
language: es-US                 # one of the 24 codes; must match the file name
campaign: post_op               # must match the file name
status: draft                   # draft | in_review | approved | retired
translated_by: "machine draft (Claude)"
reviewed_by: ""                 # the qualified medical translator
approved_by: ""                 # the named approver
approved_at: ""                 # YYYY-MM-DD
notes: "..."                    # what the reviewer should look at
scripts:                        # every key of the English spec's scripts
  opener: "Hola, esta es una llamada automatizada de {org_name}. ..."
  ...
questions:                      # every question id of the English spec
  pain_present:
    ask: "¿Tiene algún dolor?"  # what the voice agent says
  pain_severity:
    ask: "..."
    note: "For a range, record the higher number."  # optional; only where the English has a note
custom_vocabulary: [incisión, ...]  # optional: words the caller's speech recognition should expect
# Optional until texts in other languages are built (LANGUAGES-PLAN step 5):
# sms_scripts: {...} and a question's sms / sms_if_* wording.
```

**To add a language or a pack:**
1. Copy an existing pack, rename it `<campaign>.<code>.yaml`, and set `language`, `campaign`, `status: draft`, `translated_by`.
2. Translate every line of `scripts` and every question's `ask`. Write numbers in words: the voice agent reads them aloud. A `note` is guidance for the model, in English, with any line to say quoted in the pack's language.
3. A developer adds the language's emergency words to `EMERGENCY_WORDS` in `language_packs.py` (Spanish and English exist). Without them the lint fails, so a pack in a new language cannot be approved by mistake.
4. Run the check below, then the persona evals in that language (`evals/voice_personas.py`; Spanish: `--set es` with `VOA_ENV=dev VOA_ALLOW_DRAFT_PACKS=1`).
5. A qualified medical translator reviews it. Then it is approved: see `docs/HANDOFF.md`, "How to approve a pack".

**To edit a pack:** change the wording only. A change to an approved pack's wording should go back through the translator.

**The lint** (`language_packs.pack_problems`, run by `packages/voa_core/tests/test_language_packs.py` on every pack in the repo) fails on:
- a missing script or question wording;
- a `{placeholder}` that differs from the English line's (keep `{org_name}`, `{patient_first_name}`, `{patient_last_name}` exactly as in English);
- an emergency line without 911 (or "nueve uno uno") or without the instruction to hang up and call;
- any extra key: a script, question, note or field the English spec does not have (a pack cannot carry `type`, `rules` or anything else);
- `status: approved` without `reviewed_by`, `approved_by` and `approved_at`.

**What a pack's status does:**
- `draft`, `in_review`, `retired`: nothing. Patients in that language stay **held** (not called), unless an admin ticks "Call in English" for them. The patient form shows the language as "(not available yet)".
- `approved` (and lint-clean): the language becomes available for **voice** calls of that use case. Patients in it are called in their language, and the voicemail is said in it.
- **Texts stay English-only in this step.** A patient whose language is not English (and who is not set to "Call in English") gets no missed-call text: it is skipped with the reason "no approved Spanish (US) text wording" on the call.
- The admin console shows each pack and its status under Display → Languages (read-only).

**Evals only:** `VOA_ALLOW_DRAFT_PACKS=1` lets the gateway speak a draft pack, and only when `VOA_ENV` is set to `dev` or `test`. It is refused in demo and prod, and it never makes a patient callable.

## Not built yet

Changes apply only after a redeploy. A later step would load the campaign from Cloud Storage or Firestore, with the same checks run on upload, so that a rewording goes live without one.
