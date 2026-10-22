# Patient languages: plan

**Status (23 Oct 2026):** steps 1–5 are built and deployed.
1. A preferred language for every patient (24 languages), with a safe hold.
2. Transcripts and texts in the original language and in English.
3. The safety layer for other languages.
4. Calls in Spanish (US).
5. Texts in Spanish (US).

**In production, only English is approved.** The Spanish wording (`config/campaigns/i18n/post_op.es-US.yaml`) and the per-language keyword lists are **machine drafts awaiting a qualified medical translator**.

**On demo,** `languages_auto_approve` makes all 24 languages available, with unreviewed wording, clearly labelled. See [Language availability and approval](#language-availability-and-approval).

Step 6 (more languages) is a proposal.

## Language availability and approval

Read this before turning on a language. It says where the words a patient hears or reads come from, when a patient is held, and how a language is approved.

**The three wording sources.** For each use case and each channel (calls, texts), a language's wording comes from one of:

| Source | What it is | Reviewed? | When it is used |
|---|---|---|---|
| **Approved pack** (`approved`) | `config/campaigns/i18n/<campaign>.<lang>.yaml` with `status: approved`, complete and lint-clean for the channel | Yes: a qualified medical translator, then a named approver | Always (production and demo) |
| **Draft pack** (`draft_pack`) | The same file with `status: draft` or `in_review` (es-US today, a machine draft) | No | Only with the demo switch below (and in dev/test evals with `VOA_ALLOW_DRAFT_PACKS=1`, gateway only) |
| **Machine translation** (`machine`) | No pack at all: the English wording, translated live. Calls: the voice model translates each scripted line as it speaks. Texts: each outbound text is translated by Gemini at send time. Voicemail: translated once per language and rendered in it | No | Only with the demo switch below |

English (en-US, en-IN) uses the English spec itself and is always available.

**What "held" means.** A patient whose language has no usable wording for their use case is **held**: never auto-dialled, "Call now" answers 409, and never called or texted in English silently. They stay `pending` in the call list and are picked up by the next run once the language becomes available. An admin can tick **"Call in English"** for that patient instead; that always forces English (calls and texts), with the demo switch on or off. A held patient whose call was missed gets **no** text ("no approved <language> text wording").

**The demo switch: `languages.auto_approve`.** For the demo environment only, every one of the 24 languages is available for calls and texts and nobody is held:
- Set by `languages_auto_approve = true` in `infra/envs/demo/terraform.tfvars` (env var `VOA__LANGUAGES__AUTO_APPROVE`). Default `false`.
- **The services refuse to start with it on unless `VOA_ENV` is `dev`, `test` or `demo`** (`config.AUTO_APPROVE_ENVS`; a config error at startup, and `languages.configure` checks again).
- Wording per language: an approved pack, else a draft pack, else machine translation. Each language reports its source (`GET /api/languages`: `auto_approved: true`, per language `source` / `sms_source`, and `sources_by_campaign`).
- The call doc and the SMS session record `language_source` (`approved`, `draft_pack` or `machine`), and every machine-translated text records `wording: {language, source, english, error?}`, so reviewers and analytics can find conversations that used unreviewed wording.
- Labels: the admin console's Languages card and Testing page show "Demo: all languages auto-approved. Wording in languages without an approved pack is machine-translated and unreviewed. Not for real patients." The patient language select shows "(auto-approved, demo)" instead of "(not available yet)". The nurse drawer and the nurse agent's transcript card show "Spoke Vietnamese (auto-approved, unreviewed wording)".
- Unchanged: the safety layer (§3.3) for every non-English call and text; tool answers in English; the knowledge base off for non-English calls; "Call in English".
- Machine-translated texts keep STOP, 911, tel:911 and the reply letter C; the translation is checked, and if one is missing, or translation fails, the **whole English text** is sent instead (never a mix) and the reason is stamped on the message and the session. The emergency text therefore always goes out, in English with 911 if need be. A machine-translated voicemail falls back to the English clip.
- **It must never be on in production** or anywhere real patients are called. Machine translation of critical patient content without qualified human review does not meet Section 1557 expectations.

**The approval process, step by step** (the only way a language becomes available in production):
1. **Generate or edit the pack:** `config/campaigns/i18n/<campaign>.<lang>.yaml` (format and fields: `docs/EDITING-QUESTIONS.md`, "Language packs"). A machine draft is fine as a starting point, with `status: draft`.
2. **A qualified medical translator reviews it** line by line against the English spec (HHS Section 1557). Wording fixes go into the pack. Also get the language's STOP/HELP and red-flag lists in `config/languages/keywords.yaml` reviewed.
3. **The approver** (the clinic's language-access or compliance owner) sets `status: approved`, `reviewed_by`, `approved_by` and `approved_at`, **in a reviewed commit**. The commit history is the audit trail.
4. **`pack_lint` passes:** `uv run pytest -q -p no:warnings packages/voa_core` (the pack lint, `language_packs.pack_problems` and `sms_problems`, runs on every pack in the repo).
5. **Redeploy the gateway and the API together** (`make deploy SERVICES="gateway api"`): the API decides who is held, the gateway decides the spoken language, and the pack ships inside both images.
6. **Check the language in Admin → Display → Languages:** voice (and texts, if translated) say "approved, in use".
7. **Before texts go out, check that the carrier registration covers the language** (A2P 10DLC sample messages in that language, opt-out and help wording; see `docs/HANDOFF.md`, "Spanish texts", carrier registration).

To withdraw a language: set `status: retired` (or `draft`) and redeploy; its patients are held again.

**The requirement:**
1. A preferred language on each patient in the call list.
2. Calls and texts in that language.
3. Nurses can see a transcript in the patient's own language or in English, English by default.

**The decision** (from the user, 21 Oct): the preferred language is one of the **24 languages the live voice model supports**, so a patient's calls and texts are always in the same language.

**Related:**
- `docs/USE-CASES-PLAN.md`: wording lives in use-case specs.
- `docs/EDITING-QUESTIONS.md`.
- `docs/KNOWLEDGE-PLAN.md`.

## 1. What the models can do

**Live voice (`gemini-3.8-live`)**
- Speaks and understands 24 languages, and can switch between them mid-call.
- Language control is twofold:
  - `speech_config.language_code`;
  - a firm instruction ("RESPOND IN SPANISH. YOU MUST RESPOND UNMISTAKABLY IN SPANISH.").
- Transcription of both sides is built in, takes a custom vocabulary, and works in each language.
- Source: Gemini Enterprise Agent Platform, *Configure language and voice*; *Gemini 3.8 Live* guide.

| Language | Code | | Language | Code |
|---|---|---|---|---|
| Arabic (Egyptian) | ar-EG | | Korean | ko-KR |
| Bengali (Bangladesh) | bn-BD | | Marathi | mr-IN |
| Dutch | nl-NL | | Polish | pl-PL |
| English (India) | en-IN | | Portuguese (Brazil) | pt-BR |
| English (US) | en-US | | Romanian | ro-RO |
| French (France) | fr-FR | | Russian | ru-RU |
| German | de-DE | | Spanish (US) | es-US |
| Hindi | hi-IN | | Tamil | ta-IN |
| Indonesian | id-ID | | Telugu | te-IN |
| Italian | it-IT | | Thai | th-TH |
| Japanese | ja-JP | | Turkish | tr-TR |
| | | | Ukrainian, Vietnamese | uk-UA, vi-VN |

**Not on the list, though common in US hospitals:** Mandarin, Cantonese, Tagalog, Haitian Creole. By the decision above these can't be chosen, and those patients aren't enrolled for automated calls. Revisit when the live model adds them.

**The other models:**
- **Voicemail clip (`gemini-2.5-flash-tts`):** the same set at GA, given a `language_code`. Spanish is listed as es-ES; check es-US before relying on it, or keep es-ES pronunciation for the recorded voicemail only.
- **Text models (Gemini flash):** translate and read all 24 well. Used for SMS replies and transcript translation.

## 2. What is English-only today

A code survey found these. The first five are **patient-safety** issues if a non-English call or text went out with today's code.

| # | Where | What goes wrong in another language |
|---|---|---|
| 1 | `intent.is_red_flag` (the only red-flag screen for texts) | "dolor en el pecho, no puedo respirar" is **not** caught: no 911 reply, no emergency filing. |
| 2 | `parsers.py` yes/no, scale and trend | "no sé" ("I don't know") is read as **"no"**. "sí" is read as **"same"** for the pain trend. `_norm` deletes accented letters. |
| 3 | `checks.swelling_is_urgent`, `severe_language`, `asks_for_a_person`, `expressed_worry` | A Spanish one-sided, worsening swelling isn't urgent; severe-pain words are never seen. |
| 4 | `patient_questions.classify` | An English stem inside a Spanish question misroutes it: "¿Qué dieta… si tengo fiebre y sangrado?" becomes **diet, next day**. Non-Latin text questions are dropped. |
| 5 | Knowledge-base keyword veto and item approval | English only, so for other languages the LLM is the only gate. |
| 6 | Gateway `clean_transcript` (Latin-only for en) | Non-Latin speech becomes an **empty transcript**, and the call can't file a report. |
| 7 | `dob.py` | Day-first "05/03/1950" is read as May 3, so identity fails. A spoken Spanish date is unreadable and re-asked endlessly. |
| 8 | `intent` STOP/HELP | "ALTO" and "PARAR" are not opt-outs (a compliance issue). |
| 9 | Everything patient-facing | Use-case scripts, questions and SMS texts; `copy.py` (invite, menu, DOB gate, STOP/HELP); the voicemail; the instruction frame. All English, and SMS is limited to GSM-7. |
| 10 | Plumbing | Ingest stores `language`, but `PlaceCallRequest` drops it. Forms, CSV and nurse tools have no field. `voice.language_code` is global. |
| 11 | Transcript storage and display | No language or translation slot. The UI and nurse-agent colouring use English word lists. |

**Already language-safe:**
- follow-up reasons (English templates over typed values);
- analytics (codes only, no free text);
- the live model's own red-flag judgement on calls.

## 3. The design

**One rule: a patient's language must never make a call less safe.** Anything that can't be read safely goes to a nurse, and the more urgent reading always wins.

### 3.1 Speak the patient's language; decide and record in English; keep the original

```
patient (es-US) ──► live model speaks Spanish ──► tools receive:
                                                    answer   = English, typed ("yes", 7, "worse")
                                                    original = the patient's Spanish words
                                                  ──► parsers, rules, triage run on English (unchanged)
                                                  ──► nurses see English by default, Spanish on request
```

- **Typed answers** (yes/no, scales, choices, dates) are recorded as the same English values as today, so triage, rules, analytics and the nurse UI need no per-language code.
- **The patient's own words are kept** in `original` and in the transcript, in their language, for the nurse and the audit trail.
- **Free text** (pain location, swelling description, patient questions, "how are you feeling") is stored in the original language, plus an English translation. Every English keyword check runs on the translation.
- **Never word-by-word code:** keyword lists are not translated into 24 languages for parsing. Translation to English is the normalisation step. Per-language keyword lists are used only where a miss is dangerous (§3.3).

### 3.2 Approved wording per language ("language packs")

- **Where it lives.** Every patient-facing string gets a translation in a **language pack** next to the use-case spec, e.g. `config/campaigns/i18n/post_op.es-US.yaml`. It covers:
  - the voice scripts and question wording;
  - the SMS texts and question texts;
  - the voicemail;
  - the shared SMS copy now in `copy.py` (invite, menu, DOB prompt, STOP/HELP, emergency).
- **Drafted by machine, approved by a person.** Gemini or the use-case skill drafts a pack. A **qualified medical translator** reviews it, and a named approver marks it approved (the same lifecycle as use-case specs).
  - **Compliance note:** under the HHS Section 1557 rule (2024), machine translation of critical patient content is expected to be reviewed by a qualified human translator. Confirm with your compliance team.
- **Linted:**
  - every key translated;
  - placeholders preserved;
  - the SMS length counted in the right encoding (§3.5);
  - 911 and STOP left untranslated where carriers or emergency services require it.
- **Availability.** A language is **available** for a use case only when its pack is approved. The patient form shows all 24, but marks "not available yet" when there is no approved pack.
- **No pack yet.** (See "Language availability and approval" above for the demo switch.) A patient whose language has no pack is **held** in the queue ("waiting for <language> support"), not called in English. An admin can override per patient with "call in English".

### 3.3 Safety layer for other languages

| Check | How it works for a non-English call or text |
|---|---|
| **Red flags, voice** | The live model judges red flags in any language (today's behaviour). **Added:** after the call, the transcript is translated to English and re-triaged. If that finds something more urgent (red flag, severe pain, urgent swelling), the call's tier is **raised**, with the reason "found on translated transcript". It is never lowered. |
| **Red flags, text** | Each inbound text is checked on its **original** with that language's reviewed red-flag list (a pack item, like STOP), **and** on its English translation with today's `is_red_flag`. Either one triggers the emergency reply. If translation fails, an unreadable message is treated as unclear and routed to a nurse, never as "no". |
| **Yes/no and scales** | Voice: the model passes the English typed value and the original; the tool rejects a non-English `answer`. Text: the reply is translated, then today's parser runs. An "unknown"-style reply ("no sé", "không biết") must come out as `unknown`, covered by the evaluation set. |
| **Free-text checks** (swelling, severe language, wants a person, worry) | Run on the English translation. |
| **Patient questions** | Classified on the English translation, then the LLM classifier. The more urgent of the two wins. |
| **Knowledge base** | Off for non-English calls in the first release: every question goes to a nurse. Later, answers come from approved content translated with review. |
| **STOP / HELP** | Per-language keywords in the pack ("ALTO", "PARAR", "AYUDA"…), plus the English ones, which always work. |
| **DOB** | Voice: the model converts the spoken date to ISO (YYYY-MM-DD) for `verify_identity`; a non-ISO value is refused for non-English calls. Text: the prompt shows the local format, and parsing is day-first or month-first per language pack. |
| **Language switch mid-call** | The live model follows the patient into another supported language. The call records `language_used`. If the patient can't continue in any supported language: new outcome `language_assistance_needed`, routine follow-up, "call with an interpreter". |

### 3.4 Calls

- **Per call:** the call's `language` flows entry → dialer → `pending_calls` → gateway. The gateway sets the speech config per session (today it is set once per agent), plus transcription hints and the custom vocabulary from the pack.
- **Instruction:** the English instruction frame is unchanged, plus a language block ("Speak only Spanish (US)…"), plus the pack's approved wording for scripts and questions. English calls stay **byte-identical**, so the parity goldens keep passing.
- **Tools:** for non-English calls the voice tools take `answer` (English, typed) and `original`. This is a separate toolset, so English tool declarations don't change (the same pattern as the knowledge toolset).
- **Voicemail:** the TTS clip uses the pack's voicemail text and `language_code`, cached per language.
- **Transcript cleaning** follows the call's language: Latin-only for English and other Latin-script languages, script-aware otherwise. The "unanswered question" guard also recognises `？` and `؟`.

### 3.5 Texts

- **Wording:** the invite, menu, DOB prompt, questionnaire, closings, emergency and STOP/HELP texts all come from the language pack. `copy.py` becomes the English pack.
- **Inbound:** STOP/HELP and the red flag are checked on the original. The text is then translated to English (flash, about 1 s) and goes through today's state machine unchanged.
- **Encoding and cost:**
  - Accented Spanish (á, í, ó, ú, ¿) and every non-Latin script send as UCS-2: 70 characters per segment, against 160.
  - The lint counts segments per pack, and the budget counts segments, not messages.
- **Consent and registration:**
  - The consent wording must exist in each language.
  - Check the carrier (A2P 10DLC) registration covers non-English message content before sending.

### 3.6 Nurses: transcript in English or the original

- **Storage:** each transcript turn stores `lang` and `text` (original). After the call, a translation task adds `text_en`. A turn already in English is copied, not translated.
- **Nurse console:** the transcript opens **in English**, with a "machine-translated from Spanish" label, and a toggle: English / Original / side by side. The answers and patient questions show the English value with the original underneath. The recording is unchanged.
- **Gemini Enterprise card:** English, with "Translated from Spanish"; the original is available on request.
- **Download:** the transcript downloads in either language, with the language on the title line.
- **Lists:** the call list, the queue and the drawer show the patient's language as a chip, so a nurse knows before calling back.

## 4. Data changes

| Where | Field |
|---|---|
| Call-list entry, ingest call, CSV `language` column, patient forms, nurse-agent add/redial | `language`: one of the 24 BCP-47 codes, default `en-US`; anything else is a 422 |
| Call doc, `pending_calls`, report | `language` (asked for), `language_used` (spoken) |
| Transcript turn | `lang`, `text`, `text_en` |
| Recorded answers | `_raw` stays the typed English value; new `_original[question_id]` |
| Patient question | `text` (English), `text_original`, `lang` |
| SMS session and messages | `language`; each inbound message keeps `body` (original) and `body_en` |
| Analytics `fact_calls` | `language`, `language_used` |

## 5. Order of work

| Step | Scope | Visible result |
|---|---|---|
| 1. Language field, safe hold | `language` end to end (entry, ingest, CSV, forms, nurse tools, call doc), validated against the 24. Patients in a language without an approved pack are held, not called in English. Language chip in the lists. | Nurses see each patient's language. Nobody is called in the wrong language. |
| 2. Transcripts in two languages | Language-aware transcript cleaning (fixes the empty non-Latin transcript), per-turn `lang`, a post-call translation task, the English/Original toggle in the console and on the card. | Nurses read any call in English or the original. |
| 3. Safety layer | Translation-to-English normalisation for SMS, per-language red-flag and STOP/HELP lists, the `original` + English tool variant, ISO DOB from the model, post-call re-triage on the translated transcript (raise only). A **new non-English evaluation set**: red flags, "don't know", day-first dates, mixed language, Spanish symptoms inside other topics. | Every row in §2's safety table has a test that fails today and passes after. |
| 4. Spanish (US) voice pilot | Language-pack format and lint, the Spanish post-op pack (translator-reviewed), per-session speech config, the instruction language block, the voicemail in Spanish, Spanish persona evals (happy path, red flag, DOB retry, questions, voicemail, switching to English mid-call). | Spanish-speaking patients get the post-op call in Spanish. |
| 5. Spanish texts | The Spanish SMS pack, UCS-2-aware lint and budget, inbound translation, localized keywords, consent wording, a registration check. | The missed-call text and the text questionnaire in Spanish. |
| 6. More languages | Each needs a reviewed pack, a red-flag/STOP list and a persona run. Suggested order by typical US need: Vietnamese, Korean, Russian, Arabic (Egyptian), Portuguese (Brazil), French, then others on request. | About a week each, mostly review. |

Steps 1–3 come before any non-English call goes out. Step 3 is what makes the others safe.

## 6. Testing

- **Parity:** the English goldens (`packages/voa_core/tests/parity`) must stay byte-identical. All language work is additive: per-language tables chosen by the call's language, with the English path untouched.
- **A non-English corpus,** checked into the repo with fictional content:
  - red flags;
  - "don't know" phrasings;
  - numbers in words;
  - day-first dates;
  - free-text swelling and pain;
  - patient questions that mix topics;
  - STOP/HELP;
  - written in each pack language by the translator.

  The CI gates: every red flag caught (recall 1.0); no "don't know" read as yes or no.
- **Persona evals** per language, with the same personas as English, spoken in the language, plus "switches to English" and "speaks an unsupported language".
- **Translation spot-check:** for the first weeks of each language, a bilingual nurse reviews a sample of translated transcripts against the original audio.

## 7. Questions to settle

1. **Which languages after Spanish,** given the clinic's patient population?
2. **Who are the qualified translators,** and who approves each language pack?
3. **Where translation runs:** keep it on Gemini (simplest, same data path), or use Cloud Translation for transcripts, with a glossary for clinical terms?
4. **For a patient without an approved pack:** hold them (proposed), or call in English after the patient agrees?
5. **Should English (India) be offered,** or only US English for English speakers?
