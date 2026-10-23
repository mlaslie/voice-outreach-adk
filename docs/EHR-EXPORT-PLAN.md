# EHR export plan: a configurable FHIR exporter

Status: **steps 1–3 done and deployed to demo (23 Oct 2026).** The mapping engine (`packages/voa_ehr`), the API (§4.2c) and the admin UI (graphical mapper, side-by-side collapsible preview with traceability, simulate, versions, outbox, upload/download) are in place. No real EHR has been called: the `voa-ehr-export` service (step 5) is not built, so Test connection is disabled.

The request: *"the FHIR structure needs to be configurable such that parameters/info can be easily mapped by admins; perhaps give a simple to use web UI that drives a yaml/json configuration file to map app parameters to EHR FHIR parameters."*

## Contents

1. Goal and non-goals
2. The mapping model: the export profile
3. Where it runs
4. The admin UI (step 2)
5. Testing strategy
6. Order of work
7. Open questions

---

## 1. Goal and non-goals

**Goal.** Every call a nurse signs off writes one record to Firestore `ehr_outbox/{call_id}` (`voa_core/review.py` `ehr_payload`). Today nothing reads that record. It is also not valid FHIR:

- it has no subject reference;
- it has no `questionnaire` canonical;
- its answers are raw values (`"yes"`, `8`), not typed `answer[].value[x]`;
- its linkIds are the app's question ids, not the EHR's.

The exporter turns each record into valid FHIR R4 for one EHR. **How** it does that is set by an admin-editable **export profile**, not by code. The resources are:

- a QuestionnaireResponse;
- a Task for the nurse's follow-up;
- optional Observations;
- an optional summary or transcript document.

**Non-goals.**

- **Not an alerting path.** Emergencies are handled during the call: the agent sends the patient to 911, and the nurse console puts the call at the top. The export runs after a nurse's sign-off, minutes to days later, and may be retried or fail. Nothing clinical may depend on it arriving.
- **No patient creation or matching heuristics.** The exporter finds the patient by MRN, or it doesn't export. It never creates a Patient, and it never picks one of several matches.
- **No arbitrary code in a profile.** Transforms are a fixed, reviewed set (§2.7).
- **Not a terminology service.** The codes in a profile are the deploying organisation's choice. The example's codes are illustrations to verify.
- **PHI decisions belong to the deploying organisation, not to this product.** The defaults send the minimum:
  - answers, the disposition and the nurse's note;
  - no date of birth;
  - no transcript;
  - no summary;
  - no patient name.

  Sending more takes an explicit switch. Free-text documents also need `phi_acknowledged: true`, recorded once the organisation's privacy office has agreed.
- **Not HL7 v2.** An organisation that integrates through an engine (ORU^R01 or MDM^T02) can point that engine at the FHIR output, or ask for a v2 target later (§7).

## 2. The mapping model: the export profile

A profile is a versioned YAML document (JSON works too, since JSON is YAML). The schema is `packages/voa_ehr/src/voa_ehr/profile.py`: pydantic models with `extra="forbid"` throughout, so a misspelt key is an error, not a setting silently ignored. The worked example is `config/ehr/profiles/post_op.example.yaml`.

### 2.1 Shape (abridged)

```yaml
profile_schema: 1                  # schema version (the code's)
id: post_op_example                # a safe id
name: Example Health - post-op results
version: 3                         # this profile's revision (draft -> active)
target:
  fhir_version: R4
  base_url: https://fhir.example.org/R4       # https only (http for localhost)
  auth: {type: bearer, secret: secret://ehr-fhir-token}
  timeout_s: 30
  submit: transaction              # transaction | individual
  write_mode: conditional_update   # conditional_update | conditional_create
patient:
  identifier_system: https://fhir.example.org/identifiers/mrn
  not_found: retry                 # retry | fail | skip
  search_with_birthdate: false
  include_display_name: false
identifiers:
  system: https://voice-outreach.example.org/identifiers/call
validate_output: true
use_cases:
  - campaign_id: post_op
    spec_versions: []              # [] = every version
    questionnaire_response: {questionnaire: <canonical>, unmapped: skip, exclude: [], items: {...}}
    task: {emit_when: resolved, status: {...}, priority: {...}, owner: {...}}
    observations: [{source: pain_severity, code: {...}, type: integer}]
    summary: {enabled: false}
    transcript: {enabled: false}
```

### 2.2 Target and authentication

- **`base_url`:** the FHIR base. It is not secret and lives in the profile. It must be https; plain http is accepted only for `localhost`, for the fake server. A URL with a query string or credentials in it is refused.
- **`auth`:** the credential is always a reference, `secret://<name>[#version]`, using the repo-wide Secret Manager convention (`voa_core.config.resolve_secret`). An inline value is refused by the model.

| Auth type | Status | Used for |
|---|---|---|
| `none` | built | the fake server and public test servers |
| `bearer` | built | a static token in Secret Manager |
| `client_credentials` | in the schema; the client refuses it until built | OAuth2 client secret |
| `smart_backend` | in the schema; the client refuses it until built | SMART Backend Services: a JWT signed with a private key from Secret Manager. Epic and Oracle Health system-to-system use this. |

- **`submit: transaction`:** one Bundle per call, all or nothing. The Task and Observations reference the QuestionnaireResponse by `urn:uuid`, and the server resolves it.
- **`submit: individual`:** one request per resource, for servers without transactions, which is common in vendor EHRs. The QuestionnaireResponse goes first, and its server id is filled into the others.

### 2.3 Patient lookup

- **Search:** `GET Patient?identifier=<identifier_system>|<mrn>`, URL-encoded. The MRN comes from the outbox record's `patient.mrn`.
- **Exactly one match:** the reference is `Patient/<id>`. With `include_display_name: true` it also carries the name as `display`. This is off by default.
- **No match** (`not_found`):

  | Setting | What happens |
  |---|---|
  | `retry` (default) | The record stays pending and counts an attempt. The EHR may not have registered the patient yet. After `max_attempts` it is `failed`. |
  | `fail` | `failed` at once. |
  | `skip` | `skipped`. |

- **More than one match:** always `failed` (`patient_ambiguous`). The exporter never guesses.
- **No MRN on the record:** `failed` (`no_mrn`), and no search is sent. A call with no MRN is matched by phone inside this app (review.py B-6), but that is never good enough for an EHR.
- **`search_with_birthdate: true`:** adds `&birthdate=<dob>` to the search. Only then does the exporter read `patient_dob` from the call doc, and the DOB goes only into the search, never into a resource. Off by default (tested).

### 2.4 Resources to emit

Each resource is optional per use case.

**QuestionnaireResponse** (`questionnaire_response`)

- `questionnaire`: the EHR's canonical URL, optionally `|version`.
- `items`: keyed by the app's question id (from `config/campaigns/<id>.yaml`, plus the engine fields `blood_sugar_reading` and `patient_questions`). Each item has:
  - `link_id`: the EHR Questionnaire's linkId;
  - optional `text`;
  - `type`: `boolean` | `integer` | `decimal` | `string` | `coding` | `date` | `quantity`;
  - for `coding`: `system` and `codes` (app value -> `{code, display[, system]}`);
  - for `quantity`: `unit` / `unit_system` (UCUM by default) / `unit_code`;
  - optional transforms (§2.7).
- **Answers that cannot be typed** (`"unknown"` for a boolean, `"about seven"` for an integer) are never guessed. They are reported in `skipped`, and they follow the `unmapped` setting.
- **`unmapped: skip`** (default): an answer with no mapping is left out and listed in the preview's `skipped`.
- **`unmapped: text`:** it is sent as a `valueString` under `unmapped_prefix + question id`. Use this only if the EHR's Questionnaire has those items. A strict server rejects unknown linkIds.
- **`exclude`:** never sent, mapped or not. For example, free-text `overall_feeling`, if the organisation prefers.
- Fixed elements:
  - `identifier` = `{identifiers.system}|{call_id}`. It is a single Identifier: in R4 this element is 0..1.
  - `status: completed`;
  - `subject`;
  - `authored`: the call's end, else the sign-off time;
  - `author.display`: the default is "Automated outreach (patient-reported)".

**Task** (`task`): the nurse follow-up.

- **`emit_when`:**
  - `resolved` (default; today's outbox `task`): the sign-off resolved a follow-up;
  - `needs_follow_up`: the tier needed a callback;
  - `always`.
- **`status`:** `{resolved: completed, open: requested}`, configurable.
- **`priority`:** set from the call's tier. The keys are the app's tiers, checked; the values must be FHIR request priorities.

  | Tier | Default priority |
  |---|---|
  | emergency | stat |
  | urgent_same_day | urgent |
  | routine_next_business_day | routine |
  | informational | routine |
  | none | routine |

- **`owner`:** the signer.
  - `display` (default): a display string;
  - `identifier`: a logical reference `{identifier: {system, value}}`, with no Practitioner lookup needed;
  - `none`.
- Also:
  - `focus` is the QuestionnaireResponse;
  - `for` is the patient;
  - `note` is the nurse's note (`include_nurse_note`);
  - `reasonCode.text` is the triage reason (`include_reason`);
  - `code` is optional;
  - `intent` defaults to `order`.

**Observations** (`observations`): selected answers as Observations, e.g. a pain score with a LOINC code or a fever flag.

- **Fields:** `source` (the question id), `code` (system, code, display), `category` (`survey` by default), and the same value spec as an item. A coding becomes a `valueCodeableConcept`, a decimal becomes a `valueQuantity`.
- **Fixed elements:**
  - `status: final`;
  - `subject`;
  - `performer` = the patient (patient-reported);
  - `effectiveDateTime`;
  - `derivedFrom` = the QuestionnaireResponse.
- **No answer, no Observation.**

**Summary and transcript** (`summary`, `transcript`): a DocumentReference (text/plain, base64) or a Communication.

- **Off by default.** Turning one on needs `phi_acknowledged: true`, or the model refuses the profile.
- **The summary** is built by code from the disposition: channel, date, tier, reason, signer, note.
- **The transcript** is read from `transcripts/{call_id}` only when it is on. It can be the `original` or the `english` translation.
- **Recordings are not exported.** Their links expire after 15 minutes, and they are deleted after the retention period. Copying audio into the EHR is a separate decision (§7).

### 2.5 Identifiers and idempotency

- **Identifiers:** every resource carries `identifiers.system | <value>`:

  | Resource | Value |
  |---|---|
  | QuestionnaireResponse | `{call_id}` |
  | Task | `{call_id}-task` |
  | Observation | `{call_id}-obs-{question}` |
  | Summary document | `{call_id}-summary` |
  | Transcript document | `{call_id}-transcript` |

- **`write_mode: conditional_update`** (default): `PUT Type?identifier=...`. A retry, or a re-sign-off after a reopen, updates the same resources. Tested: the ids stay the same, and `versionId` goes to 2.
- **`write_mode: conditional_create`:** `POST` with `If-None-Exist`. For servers that refuse a conditional update. A re-sign-off then cannot update. See §7.
- **Bundles are deterministic.** Each fullUrl is a `uuid5` of the identifier, so the preview and the send are byte-identical (tested).
- **Withdrawals:** the API sets `status: withdrawn` when a review is reopened (`review.withdrawn_export`). The exporter then:
  1. searches for every resource the mapping could have sent, by identifier, plus any `ehr_refs` already on the record;
  2. PUTs each one back with `status: entered-in-error`, which every emitted type has.

  A record never exported finds nothing and sends nothing.

### 2.6 Per use case and per spec version

`use_cases` is a list. Each entry names a `campaign_id` and optionally `spec_versions`. The use case and version come from the call doc's `campaign_id` / `spec_version`, which the filing layer stamps. The outbox record itself does not carry them yet; see §6, step 4.

- **Matching:** the most specific entry wins. An entry that lists the version beats an entry with an empty list, which means every version.
- **Overlap:** two entries for the same use case that cover the same version are an error.
- **No entry:** the record is `skipped` (`no_mapping`). It is not retried, because a profile change is needed.

### 2.7 Value transforms (fixed, safe)

This follows the same spirit as `voa_core/rules.py`: no `eval`, nothing that runs code. The transforms run in this order:

1. **`constant`:** send this value whenever the question was answered.
2. **`concat`:** join several answers into one string, with `separator`. Missing ones are left out. Only for type string.
3. **`value_map`:** enum to enum, e.g. `{better: improving}` or `{worse: 1}`. Keys are matched after the app's normalisation (`norm_answer`: lower case; yes/true/yeah become yes).
4. **The type conversion:**
   - `coding` uses `codes` (enum to coding);
   - `date` tries `date_formats` in order, restricted to the date directives `%Y %m %d %y %b %B %j`;
   - `quantity` adds the unit.

Lists, such as `patient_questions`, become one answer per element.

### 2.8 Validation

**On load** (`load_profile`), it checks:

- **The model.** Types, required fields, forbidden extra keys, secret-reference shape, https, and units only on quantity. Also:
  - a coding needs codes and a system;
  - date formats are restricted;
  - link ids are unique;
  - tiers and priorities must be known;
  - the PHI acknowledgement;
  - version overlap.
- **Against the use-case spec** (`profile_problems`, through `voa_core.campaign.load_campaign`):
  - **Errors:** the use case exists; every mapped id, concat id, excluded id and Observation source is a question in that spec, or an engine field. Each type must fit its question, so `integer` for a yes/no is an error.

    | Question type | Allowed answer types |
    |---|---|
    | yes/no | boolean, coding, string |
    | scale | integer, decimal, quantity, coding, string |
    | choice | coding, string |
    | text | string (coding when the question has a parser) |
    | date | date, string |

    A `value_map` widens the allowed types, because the admin is mapping the values explicitly.
  - **Warnings:** a code map that misses one of the question's answers (e.g. no code for `same`); a boolean for a yes/no/unknown question; unmapped questions; a spec version outside `spec_versions`.

**Before sending** (`validate_output: true`), `fhir_check.fhir_problems` checks the Bundle:

- its structure, with `fhir.resources` (§5);
- the required status, intent and priority codes.

An invalid bundle is `failed` (`invalid_fhir`) and not retried.

**Dry-run preview:** `exporter.preview()` and `scripts/ehr_export_preview.py`. Both give the exact Bundle, the `skipped` answers, warnings and errors, with no network.

### 2.9 The worked example (post_op)

`config/ehr/profiles/post_op.example.yaml`. **Everything about the EHR is fictional:** `fhir.example.org`, its Questionnaire `post-op-phone-check|1`, its linkIds and its local code systems (`symptom-trend`, `glucose-self-report`, `task-type`). Codes from real systems are marked **EXAMPLE - VERIFY** in the file:

| Used for | Code | Verify |
|---|---|---|
| Yes / No / Unknown answers | LOINC LA33-6 / LA32-8 / LA4489-6 | against the EHR's answer lists |
| Pain score Observation | LOINC 72514-3 "Pain severity - 0-10 verbal numeric rating [Score] - Reported" | against the EHR's flowsheet mapping |
| Fever flag Observation | SNOMED CT 386661006 "Fever (finding)" | the organisation may prefer a temperature Observation (LOINC 8310-5) if it ever collects a number |

Excerpt:

```yaml
pain_severity: {link_id: "2.1", text: "Pain score (0-10)", type: integer}
pain_trend:
  link_id: "2.3"
  type: coding
  system: https://fhir.example.org/CodeSystem/symptom-trend   # fictional
  codes:
    better: {code: improving, display: Improving}
    same: {code: unchanged, display: Unchanged}
    worse: {code: worsening, display: Worsening}
...
observations:
  - source: pain_severity
    code: {system: http://loinc.org, code: 72514-3, display: Pain severity 0-10 reported}  # EXAMPLE - VERIFY
    type: integer
```

`config/ehr/samples/post_op.outbox.json` is a fictional signed-off record and its call doc, generated with `review.ehr_payload`. Previewing it produces 4 entries:

- the QuestionnaireResponse, with 11 items;
- the Task (`urgent`, completed, with the nurse's note);
- two Observations.

## 3. Where it runs

**Recommended: a Cloud Run service `voa-ehr-export`, triggered every minute by Cloud Scheduler, plus Eventarc on `ehr_outbox` writes for low latency.**

- **One code path:** `exporter.run_once(store, profile, client)`. It is safe to run concurrently (§3.1) and safe to re-run (idempotent writes), so a trigger and the schedule can overlap.
- **Its own service account**, with:
  - Firestore read/write (`ehr_outbox`, plus read on `calls` and `transcripts`);
  - Secret Manager accessor on the profile's secrets only;
  - nothing else.

  The API's service account gets no EHR credential.
- **Egress:** only to the EHR's base URL. Where the EHR requires it, through a static egress IP (Serverless VPC connector + Cloud NAT) that the EHR can allowlist.
- **The profile** is read from Firestore `settings/ehr_export`, the active version (§4.4), at the start of each run. A run uses one profile version end to end and stamps it on each record (`export_profile: {id, version}`).
- **Write-back** on each record:
  - `status`: `exported`, `failed`, `skipped` or `withdrawn_done`;
  - `export_attempts`;
  - `last_error`: HTTP status and OperationOutcome codes only, never a request body;
  - `exported_at`;
  - `ehr_refs`: e.g. `Task/12`;
  - `entered_in_error`, for a withdrawal.
- **Logs:** call id, status and ref count. Never answers.

### 3.1 State machine and races

```
pending_export ──> exported
      │    ├──> skipped   (no mapping; patient not found + skip)
      │    └──> failed    (max attempts; patient not found + fail; ambiguous; no MRN; invalid FHIR)
(API) withdrawn ──> withdrawn_done
```

- **Claim:** each record is claimed with a short lease (`export_lease_until`, 5 minutes) in a store transaction, so two runs never send the same record at once.
- **Write-back** is a transaction that re-reads the record. If the API withdrew it or re-signed it while we were sending (the status or `created_at` changed), our result is not written over theirs, and the next run sees their state. A withdrawal during a send then marks what we sent `entered-in-error` (tested).
- **Requeue:** a `failed` or `skipped` record is requeued by an admin from the console (set back to `pending_export`). Admin "Requeue" is step 2.

### 3.2 Alternatives considered

| Option | Why not (now) |
|---|---|
| Inside the API (on sign-off) | It couples sign-off latency and availability to the EHR. The API's service account would hold EHR credentials. Retries would have to be built anyway. |
| Cloud Run **job** on Scheduler only | Simple, and fine at demo scale. But it has minute-plus latency and no Eventarc trigger. Use it as a first deploy if Eventarc adds friction: the code is the same. |
| Cloud Functions on the Firestore trigger only | No batch retry of failures without a second trigger. A function per write is noisier to operate. |
| Google Cloud Healthcare API FHIR store as a staging area | It adds a store, and it doesn't solve mapping or EHR auth. Worth it only if the organisation already uses it as its integration hub (§7). |
| An integration engine (Mirth, Rhapsody, Cloverleaf) | It is the right answer for many hospitals. The profile's FHIR output can feed one: point `base_url` at the engine. |
| Pull API (`GET /api/v1/results?since=`) | Still a good option for EHR teams that prefer polling (INTEGRATION-GUIDE §3). It is complementary, and it would serve the same mapped bundles. |

## 4. The admin UI (step 2)

**Admin → Integrations → EHR export.** It is admin-only (`auth.py` route map). Every write is audit-logged like other settings.

### 4.1 Profile editor

- **Connection:**
  - FHIR base URL;
  - auth type;
  - **secret names only**: a dropdown of `secret://` names the service account can read, never a value field;
  - transaction or individual;
  - conditional update or create.
- **Patient:**
  - MRN identifier system;
  - when not found (retry / fail / skip);
  - "Also match on date of birth" (off). Turning it on shows a PHI note.
- **Use case tabs**, one per mapped `campaign_id`, with an "Add use case" picker from `campaign_ids()`.
- **Questions table**, built from the use-case spec, one row per question, in spec order, plus the engine fields:

  | App question (id, ask text, type, choices) | EHR linkId | Answer type (only the fitting types offered) | Code map / unit | Send |
  |---|---|---|---|---|
  | pain_trend · "Is it getting better…" · choice | 2.3 | coding | better→improving, same→unchanged, worse→worsening | ✓ |

  - The code-map editor lists the question's own choices as rows. A missing code shows the warning inline.
  - The Send column holds the `exclude` toggle.
  - An "Unmapped answers" select offers `skip` or `text`, plus the prefix.
- **Resource toggles:**
  - QuestionnaireResponse (canonical URL);
  - Task (emit when, the status map, the tier → priority table, owner mode);
  - Observations (an "Add observation" row: question, code system, code, display, type);
  - Summary and Transcript. Each shows a PHI warning, and switching it on requires ticking "Our privacy office approved sending this to the EHR", which sets `phi_acknowledged`.
- **Problems panel:** errors block "Activate"; warnings don't. It is `profile_problems` live: the API runs it on every save and returns `Problem.as_dict()`.

### 4.2 Preview: app output and mapped FHIR, side by side

The admin sees exactly what the app produced and what the profile turns it into, with every link between the two.

```
┌ Source: [Recent signed-off call ▾] [Fictional sample ▾]  [Expand all] [Collapse all] ──────────────┐
│ App output (outbox record)              │ Mapped FHIR (transaction Bundle)                        │
│ ▾ patient                               │ ▾ entry[0] QuestionnaireResponse                        │
│     mrn: "A1234567"                     │   ▾ item                                                │
│ ▾ questionnaire_response.item           │     ▸ [0] linkId "1"   overall_feeling                  │
│   ▾ [4] pain_trend: "worse"  ◀────────────▶ ▾ [3] linkId "2.3"                                    │
│   ░ [9] blood_sugar_status (skipped:      │       ▾ answer[0].valueCoding                         │
│        not mapped)                      │           code: "worsening"  ← pain_trend               │
│ ▸ disposition                           │ ▸ entry[1] Task (priority: urgent ← follow_up)          │
│ ▸ review                                │ ▸ entry[2] Observation (72514-3 ← pain_severity)        │
└─────────────────────────────────────────┴─────────────────────────────────────────────────────────┘
  Skipped (2) · Warnings (1) · FHIR errors (0)                         [Copy JSON] [Download JSON]
```

**Two panes,** each a fully collapsible JSON tree: objects and arrays expand and collapse at every level, with expand-all and collapse-all.
- **Left: app output.** The outbox record as the app writes it, with the answers keyed by the app's question ids.
- **Right: mapped FHIR.** The transaction Bundle the profile produces (`exporter.preview`), byte-identical to what would be sent.

**Traceability.** The engine returns a **source map**: a list of `{source, target, question_id, note}`, both paths as JSON Pointers.
- Selecting a node on either side highlights its counterpart(s) on the other.
- Mapped targets carry a small badge naming their source, e.g. `← pain_trend`.
- Unmapped or excluded answers are greyed on the left, with the reason.
- FHIR structure errors are flagged on the right node that caused them.

**Live.** The preview runs against the **unsaved draft** as it is edited in the mapper, debounced. Editing a code map updates the right pane at once.

**Sources:**
- the last 20 signed-off calls (outbox records, any status);
- or the fictional sample per use case (`config/ehr/samples/<use case>.outbox.json`).

**Simulate export.** Runs the real exporter loop against the built-in **fake FHIR server**, twice. The second run shows the idempotency: no new resources. The result lists the requests (method, URL, status) and the resources as stored. No network.

**PHI.** A real call's preview shows what the console already shows the admin. Nothing is stored. The patient reference is a placeholder (`Patient/example-patient`), because there is no EHR lookup in a preview.

### 4.2a The mapper (graphical editor)

**Layout:** one screen, **Admin → Integrations → EHR export**, with tabs **Mapping · Preview · Versions · Outbox**. The Mapping tab has the §4.1 sections as cards. Its key widgets:

- **Questions table** (per use case): one row per app question, in spec order:
  - the app question (id, wording, type, choices);
  - **Send** toggle;
  - **EHR linkId** (text);
  - **Answer type**: a dropdown offering only the types that fit the question type;
  - **Code map**: for `coding`, an inline grid with one row per app choice and columns for code, display and system;
  - **Unit** (for `quantity`).

  The row shows its problems inline.
- **Resource toggle cards:**
  - **QuestionnaireResponse:** the canonical URL.
  - **Task:** when it is emitted; the status map; a tier → priority grid with dropdowns.
  - **Observations:** add or remove rows. Each row has the source question, code system (LOINC, SNOMED or custom), code, display and value type.
  - **Summary / Transcript:** off by default. Turning one on needs the PHI acknowledgement checkbox.
- **Patient and Identifiers card,** and a **Connection** card. The Connection card takes secret *names* only. Test connection is disabled until the export service exists (step 5).
- **Problems panel** (live from `profile_problems`): errors block **Activate**; warnings don't.
- **Toolbar:**
  - **Save draft**;
  - **Activate** (confirm, with a diff against the active version);
  - **Download YAML**, **Upload YAML/JSON**;
  - **Reset draft to active**.
- **First run:** with no profile yet, offer "Start from the example" (`config/ehr/profiles/post_op.example.yaml`).

### 4.2b API contract (step 2)

All routes are **ADMIN** in `auth.py` `ROUTE_POLICY`, and writes are audited. The profile travels as JSON (the same shape as the YAML).

| Route | Body | Returns |
|---|---|---|
| `GET /api/ehr-export` | | `{active_version, draft_version, active: Version\|null, draft: Version\|null}` |
| `GET /api/ehr-export/questions?campaign_id=` | | `{campaign_id, spec_version, questions: [{id, ask, type, choices, allowed_types: [...]}]}` |
| `POST /api/ehr-export/validate` | `{profile}` | `{problems: [Problem]}`, nothing saved |
| `PUT /api/ehr-export/draft` | `{profile, note?}` | `Version` (with problems); also accepts `{yaml: "..."}` |
| `POST /api/ehr-export/draft/reset` | | the draft is replaced by a copy of the active profile |
| `POST /api/ehr-export/activate` | `{note?}` | `Version`. 409 with problems if there are errors. |
| `GET /api/ehr-export/versions` | | `{versions: [Version without profile]}` |
| `POST /api/ehr-export/versions/{n}/restore` | | the restored copy becomes the new draft |
| `GET /api/ehr-export/yaml?version=draft\|active\|{n}` | | `text/yaml` attachment |
| `GET /api/ehr-export/example` | | `{profile}` from `config/ehr/profiles/post_op.example.yaml` |
| `GET /api/ehr-export/preview-sources` | | `{samples: [{campaign_id, label}], calls: [{call_id, campaign_id, signed_off_at, status, label}]}`, the last 20 outbox records (label = first name + last initial) |
| `POST /api/ehr-export/preview` | `{profile?, source: {sample: campaign_id} \| {call_id}}` | `{ok, source_record, bundle, source_map: [{source, target, question_id?, note?}], skipped: [{question_id, reason, source}], warnings: [str], errors: [{path?, message}]}` |
| `POST /api/ehr-export/simulate` | `{profile?, source}` | `{runs: [{requests: [{method, url, status}], created, updated}], resources: [...]}` |
| `GET /api/ehr-export/outbox?status=` | | `{records: [{call_id, status, created_at, exported_at?, last_error?, attempts?}]}` |
| `POST /api/ehr-export/outbox/{call_id}/requeue` | | the record is back to `pending_export` |

- **`Version`:** `{version, status: draft|active|retired, profile, problems, saved_by, saved_at, activated_by?, activated_at?, note?}`.
- **`Problem`:** `{level: error|warning, path, message}`.
- **The profile when `profile` is omitted:** `preview` and `simulate` use the saved draft, else the active profile, else the example.

### 4.2c As built (step 2)

`services/api/src/voa_api/routes_ehr_export.py`, the engine's source map in `voa_ehr/transform.py`, storage helpers in `voa_ehr/versions.py`. The contract above holds; these are the additions and the choices it left open:

- **Storage** is §4.4's: the pointer doc `settings/ehr_export` (`{active_version, draft_version, next_version}`) and the subcollection `settings/ehr_export/versions/{n}`. A draft keeps its number until activated; the next save after an activation starts a new number. Saving stamps `n` into the profile's own `version`.
- **A draft is saved even with errors,** including model errors (the raw JSON is kept), so work is never lost. Activate refuses with 409 `{detail: {message, problems}}`. `preview`/`simulate` refuse a profile that does not parse with 422 `{detail: {message, problems}}`.
- **`source_map[].source` is `null`** when the value does not come from the outbox record: the transcript (from `transcripts/{id}`) and answers read from the call doc for an old record with no items. The QuestionnaireResponse identifier points at `/call_sid`.
- **`skipped`** also lists excluded answers (`reason: "excluded by the profile"`).
- **`errors[].path`** is a JSON Pointer into the bundle: the deepest node that exists (a missing required element points at its parent). A no-mapping preview has an error without a path and `bundle: null`.
- **Additive fields:** `GET /api/ehr-export` adds `use_cases` (the spec ids); `questions[]` adds `parser` and `engine` (engine fields such as `patient_questions`); preview and simulate add `profile_source` (`body|draft|active|example`); each simulate run adds `created_refs`, `updated_refs` and `outcome {status, error, refs}`; `created`/`updated` are counts; the outbox rows add `label`.
- **Simulate's second run** puts the in-memory copy back to `pending_export` (a retry or re-sign-off), so it re-sends: 0 created, every resource updated.
- **Requeue** clears the lease, `last_error` and `export_attempts`; a `withdrawn`/`withdrawn_done` record is refused (409), because re-exporting would undo the withdrawal.
- **Limits:** the profile JSON and the YAML are at most 200 KB each (413); the whole body at most 216 KB (413).
- **Audit:** the existing per-route line (who, when, route, target). It does not carry the diff that §4.4 mentions.

### 4.3 Test connection

`GET {base}/metadata` through the service, with the profile's auth (`client.check_connection`). The result shows:

- ok or the error;
- the FHIR version;
- the server software;
- whether the server lists QuestionnaireResponse, Task and Observation, and transactions.

It never shows the token. The call runs from the export service, not the browser, so the egress IP and secrets are the real ones.

### 4.4 Versioning and storage

- **Storage:** Firestore `settings/ehr_export`:
  - `{active_version, draft_version}`;
  - `settings/ehr_export/versions/{n}`: `{profile (JSON), status: draft|active|retired, saved_by, saved_at, activated_by, activated_at, note}`.
- **Draft → active:**
  - Saving creates or replaces the draft.
  - **Activate** needs zero errors. The Activate dialog shows a diff against the active version.
  - The previous active version becomes `retired` and can be restored as a new draft.
  - The exporter reads only the active version.
  - Every save and activation goes to the audit log, with the diff.
- **Download / upload YAML:** download the active or draft version as YAML (`profile_to_yaml`). Upload a YAML or JSON file as the new draft, checked the same way. This lets a profile live in git and be applied by a script, or by a skill driving the API: `PUT /api/ehr-export/draft` with the YAML, then `POST /api/ehr-export/activate`.
- **API sketch (step 2):**

  | Endpoint | Purpose |
  |---|---|
  | `GET/PUT /api/ehr-export/draft` | read or save the draft |
  | `POST /api/ehr-export/activate` | activate the draft |
  | `GET /api/ehr-export/versions` | the version list |
  | `POST /api/ehr-export/preview` | `{call_id \| sample}` |
  | `POST /api/ehr-export/test-connection` | proxied to the service |
  | `GET /api/ehr-export/outbox?status=` | the outbox list |
  | `POST /api/ehr-export/outbox/{id}/requeue` | requeue a record |

## 5. Testing strategy

- **Unit tests of the mapping engine** (`packages/voa_ehr/tests/test_ehr_transform.py`):
  - each answer type;
  - untypeable values, which are reported, not guessed;
  - transforms;
  - code maps;
  - unmapped answers (skip, text, exclude);
  - Task priority by tier, emission, owner modes;
  - Observations;
  - DOB never sent;
  - transcript and summary only when on;
  - identifiers and conditional requests;
  - determinism;
  - R4 validity of every resource, and of its entered-in-error form.
- **Profile tests** (`test_ehr_profile.py`):
  - the example loads clean;
  - YAML and JSON round trip;
  - an unknown question id;
  - a bad type (parametrised);
  - forbidden keys;
  - inline secrets;
  - https;
  - coding, unit and date-format rules;
  - duplicate linkIds;
  - PHI acknowledgement;
  - tiers;
  - unknown use case;
  - version overlap and specificity;
  - warnings.
- **Integration tests against a fake FHIR server** (`voa_ehr/fake_fhir.py`, `test_ehr_exporter.py`). The fake is an in-memory server with:
  - search by identifier and birthdate;
  - conditional update and create;
  - read and update by id;
  - all-or-nothing transactions with urn:uuid rewriting;
  - injected failures;
  - a request log;
  - strict structure checks (422).

  It also runs as an httpx `MockTransport`, so the real `HttpFhirClient` is tested with no network. The tests cover:
  - export and write-back;
  - idempotent re-runs and re-sign-offs (no duplicates);
  - DOB absent from every request;
  - birthdate only in the search, when on;
  - the transcript not even read unless on;
  - patient not found (retry, fail, skip);
  - ambiguous patients;
  - no MRN;
  - no mapping;
  - transient failure then success;
  - leases;
  - a withdrawal racing a send;
  - withdrawn → entered-in-error;
  - withdrawn before export;
  - re-sign-off after withdrawal;
  - individual mode (both write modes) on a server without transactions;
  - transaction refused;
  - preview == sent;
  - bearer auth through the HTTP client;
  - the connection check;
  - unbuilt auth types refused;
  - the preview script.
- **Optional live run against the public HAPI FHIR R4 test server** (`https://hapi.fhir.org/baseR4`): opt-in only, marked `live` and skipped unless `VOA_RUN_LIVE=1` (the repo's convention). Not written in step 1.
  - **Fictional data only:** a Patient created by the test with a random MRN under a test-only identifier system, and the sample record.
  - **Public:** the server is public and anything sent is world-readable.
  - **Checks:** it would run `run_once` end to end, plus a withdrawal, and then `$validate` on each resource.
- **FHIR R4 structure validation: `fhir.resources` 8.3** (with `fhir-core`), a runtime dependency of `voa_ehr`.
  - **License:** BSD-3-Clause.
  - **Size:** pure Python, a ~2.3 MB wheel. Its only dependency is `fhir-core` (~34 KB), which depends on pydantic v2, already in the workspace. No native code and no network.
  - **What it checks:** element names, cardinality (it caught that `QuestionnaireResponse.identifier` is 0..1 in R4, not a list), data types and required elements (e.g. `Task.intent`).
  - **R4B, not R4:** the 8.x line ships R5 by default, plus R4B. It no longer ships 4.0.1 models, so we use R4B. For the six resource types we emit, R4B is structurally identical to R4.
  - **What it doesn't check:** required value-set codes. `fhir_check.py` adds the status, intent, priority and docStatus code sets.
  - **What neither checks:** profile conformance (US Core, vendor rules). That is the live test's `$validate`, and the EHR's own sandbox.
  - **Alternatives:** the HL7 Java validator (heavy, a JVM in the image) and a hand-written required-field check (it misses cardinality and type errors). `fhir.resources` covers the gap at little cost.

## 6. Order of work

| # | Step | Size | Status |
|---|---|---|---|
| 1 | Plan, profile schema, mapping engine, fake server, exporter loop, example profile, preview script, tests | M | **done** |
| 2 | API (§4.2b): versions, draft/activate, audit, live validate, preview **with source map**, simulate against the fake server, YAML import/export, outbox list and requeue | M | **done**, deployed (see §4.2c) |
| 3 | Admin UI: the graphical mapper (§4.1, §4.2a), the **side-by-side collapsible preview with traceability** (§4.2), simulate, versions, outbox, upload/download | L | **done**, deployed |
| 4 | `ehr_payload`: also stamp `campaign_id` / `spec_version` on the outbox record. A one-line additive change. Today the exporter reads them from the call doc. | S | |
| 5 | `voa-ehr-export` Cloud Run service + Terraform: its service account, the Scheduler (+ Eventarc), Secret Manager access, egress. Test connection endpoint. | M | |
| 6 | `client_credentials` and `smart_backend` (JWT) auth, with token caching | S–M | |
| 7 | Opt-in HAPI live test (+ `$validate`); then the target EHR's sandbox with fictional patients | S, then vendor-dependent | |
| 8 | Orphan clean-up: on a re-export, mark `entered-in-error` the resources the new mapping no longer produces (e.g. an Observation whose answer was removed) | S | |
| 9 | Optional: Pub/Sub event on export and failure; a console badge for failed exports | S | |

## 7. Open questions (for the deploying organisation and the EHR team)

1. **Which EHR, and which API?**
   - **Epic:** QuestionnaireResponse write is limited, and often goes through flowsheets (Observation.Create, vital-signs and flowsheet IDs) or Communication or DocumentReference. Task write support varies by version, and transactions are generally not supported, so `submit: individual` is needed. SMART Backend Services auth.
   - **Oracle Health (Cerner) Millennium:** QuestionnaireResponse write is not generally available. DocumentReference (clinical notes) and Observation have better support.

   The answer decides which resources the profile turns on.
2. **Write access.** Does the organisation have a registered backend app with write scopes (`system/QuestionnaireResponse.write`, `system/Task.write`, `system/Observation.write`, `system/DocumentReference.write`)? What is the approval process, and how long does it take?
3. **Questionnaire availability.** Is there a Questionnaire resource in the EHR for this check (a canonical URL and linkIds)? Or should answers land as Observations / flowsheet rows instead (QuestionnaireResponse off)?
4. **Identifier systems.**
   - The MRN's identifier system (OID or URI) in the EHR's Patient resources.
   - Whether to use an enterprise identifier instead.
   - What system the organisation wants for our call identifier.
5. **Conditional update support.** If it is missing, `conditional_create` cannot update on a re-sign-off. Is a correction then sent as a new resource, with the old one `entered-in-error`, acceptable?
6. **Task semantics.** Should the EHR receive a *completed* Task (a record that the nurse called back), or an *open* Task (work to do in the EHR's own queue), which would duplicate this console's worklist? Who owns it: the signer as a Practitioner reference, which needs a lookup, or a display or identifier?
7. **PHI scope.** Summary and/or transcript to the EHR? Patient name in references? DOB in the patient search? Free-text answers (`overall_feeling`, descriptions) in the QuestionnaireResponse, or excluded?
8. **Codes.** Confirm or replace the example LOINC / SNOMED CT codes and the local code systems, and the yes/no/unknown answer coding.
9. **Unreviewed results.** Should patient-reported answers go to the EHR *before* nurse sign-off, marked unreviewed (INTEGRATION-GUIDE "raw answers before review")? Out of scope here; it would be a second trigger on the same profile.
10. **HL7 v2 instead.** Does the organisation route through an integration engine that wants ORU^R01 or MDM^T02 rather than FHIR?
11. **Recordings.** Should audio ever be copied into the EHR? It would need storage on the EHR side and a retention decision.
