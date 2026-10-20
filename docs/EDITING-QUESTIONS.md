# Editing the post-op questions

The questions, and what the voice agent and the text messages say, are in one file:

**`config/campaigns/post_op.yaml`**

You can reword a question there without touching any code. The same file drives the phone call and the text-message (SMS) questionnaire.

## What each field means

| Field | Where | What it does |
|---|---|---|
| `ask` | each question | What the voice agent asks on the phone. The agent may rephrase it slightly to sound natural. |
| `sms` | each question | The text message for that question. Every question needs one, follow-ups included. |
| `sms_if_known` | `pain_location` only | Sent instead of `sms` when the patient already said where it hurts. `{site}` is the place they named (for example "incision"). |
| `sms_if_side_known`, `sms_if_trend_known` | `swelling_description` only | Sent instead of `sms` when the patient already said which side is swollen (ask only whether it is getting worse), or already said it is getting worse (ask only which side). |
| `note` | each question | Extra guidance for the voice agent only, for example "For a range, record the higher number". The agent reads it as an instruction and never says it aloud. Texts ignore it. |
| `when` | follow-up questions | Ask this question only after a particular answer, for example `when: {pain_present: "yes"}`. By text, follow-ups are sent straight after that answer and are not numbered. |
| `when_patient` | some questions | Ask only some patients, for example `when_patient: {is_diabetic: true}` for the blood-sugar question. |
| `type`, `choices` | each question | What kind of answer is recorded. Do not change these yourself (see below). |
| `scripts` | top level | The voice agent's fixed lines: opener, identity check, consent, closings, voicemail, emergency line. |
| `sms_scripts` | top level | The text questionnaire's own lines: the "I'll send N questions" preamble, the numbering, "Sorry, I didn't catch that", the questions-for-the-care-team loop, the closings, the medication reminder and the failure messages. |
| `red_flags` | top level | Symptoms that make the voice agent stop and tell the caller to dial 911. |

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
- **`sms_scripts.closing_routine` and `closing_urgent`:** `{questions_follow_up}` and `{medication_reminder}`. Both must stay. The clinic asked for the medication reminder on every completed questionnaire.

`ask`, `note` and `sms` take no placeholders.

### Text-message rules

- Use plain characters only: straight quotes `"` and `'`, a plain hyphen `-`, and three dots typed as `...`. One "smart" quote or long dash switches the message to a format that costs more than twice as much. The check below catches this.
- Nothing before the date-of-birth check may hint at a medical reason. That copy is deliberately not in this file.

## What you can change, and who needs to be involved

| Change | Who |
|---|---|
| Rewording `ask`, `sms`, `sms_if_*`, `note`, `scripts` or `sms_scripts` | **You.** Run the check, then deploy. |
| Reordering questions | **You**, but tell the care team, because the report reads in the new order. |
| Adding, removing or renaming a question, or changing its `type`, `when` or `when_patient` | **A developer.** Each question needs an answer parser and a triage entry in `packages/voa_core/src/voa_core/survey.py`, and the filed report is checked field by field. The check fails if you try. |
| Triage: what makes a patient same-day, next-day or "noted"; pain thresholds; swelling rules; patient-question priorities | **A developer and a clinician.** These rules are code on purpose, so that every clinical decision can be traced to one line (`survey.triage`, `patient_questions.py`). Nothing in the YAML file changes who gets escalated. |
| Adding or removing a red flag | **A clinician**, and a developer to check the SMS emergency keywords match. Rewording an existing flag without changing its meaning is fine. |
| The SMS invite, the 1/2/3 menu, the date-of-birth messages, STOP/HELP replies | **A developer, with a compliance review.** This copy stays in `packages/voa_core/src/voa_core/copy.py`. It is sent before identity is verified, or it is registered with the carrier. |

## How to check your edit

```
uv run pytest -q -p no:warnings packages/voa_core packages/voa_agents
```

If something is wrong, a failing test names the question and the problem. Examples:

- `sleeping_well: missing sms`
- `preamble: unknown placeholder {count}`
- `not plain GSM-7 text`

Some tests compare every text message with its exact expected wording, so they fail on any rewording you make on purpose. Those tests are in `packages/voa_core/tests/test_sms_golden.py`. When you change wording deliberately, update the expected text there in the same change, or ask a developer to.

## How to deploy

The campaign file is built into the service images, so an edit takes effect only after a deploy. Commit the change first: the deploy refuses uncommitted work. Then run:

```
scripts/deploy.sh demo gateway api
```

The gateway runs the phone calls and the API runs the text messages. Deploy both, so the two channels never disagree.

## Carrier registration (A2P 10DLC)

US carriers register sample messages for business texting. If you change the wording of patient-facing text messages, the registered A2P campaign may need its sample messages updated before texting is switched on. Texting is currently in dry-run. Check with whoever owns the Twilio account before changing copy on a live number.

## Not built yet

Changes apply only after a redeploy. A later step would load the campaign from Cloud Storage or Firestore, with the same checks run on upload, so that a rewording goes live without one.
