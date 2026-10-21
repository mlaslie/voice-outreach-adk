# Analytics tab: plan

**21 Oct 2026:** Analytics lives in the **admin console** (`voa-admin`), not the nurse UI; only the admin console's identity may call `voa-analytics`. It now opens with **Call outcomes**: every call in the period in exactly one bucket (completed by voice, completed by text, voicemail left (+ texted), rang with no answer (+ texted), not completed (by reason), busy, could not dial), adding up to the total.

**Status:** proposal (20 Oct 2026). Nothing is built yet.

**Goal:** add an Analytics view to the nurse console that answers the questions a clinical lead, an operations owner and an engineer ask after a demo. The design has to survive the move to a production project unchanged.

## 1. What comparable products show

| Source | What it offers | What we take from it |
|---|---|---|
| **GE-CX app** (the other system) | No analytics screen of its own. Its numbers come from the platform under it. | Room to do better than the comparison system. |
| **CX Agent Studio / Dialogflow CX** (GE-CX's platform) | Conversation history metrics: sessions, escalation rate, turns per session, tool failures and latency, abandonment. Outcome counts over time. Filters by source. | Outcome funnel, escalation rate, tool reliability and latency. |
| **CX Insights / Quality AI** | Sentiment per side, topic modelling, QA scorecards with human review, AI summaries, audio playback alongside the transcript. | Later phase: AI scoring of calls against a rubric, with a human able to override it. |
| **Twilio Voice Insights** | Connection rate, answering-machine detection outcomes, call length and answer rate over time, who hung up, audio-quality flags, CSV export. | Connectivity and AMD panel; CSV export. |
| **Post-discharge outreach programmes** (e.g. UCSF's care-transitions programme, CipherHealth) | Reach rate, overall and by patient group; escalation share; an "unreached" list for fallback outreach; response and escalation trends. | Reach rate as the headline number, an unreached list, and the warning that an average can hide gaps between patient groups. |

Sources:
- [CX Agent Studio conversation history](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/conversation-history)
- [Dialogflow CX analytics](https://docs.cloud.google.com/dialogflow/cx/docs/concept/analytics)
- [Conversational Insights](https://docs.cloud.google.com/contact-center/ccai-platform/docs/conversational-insights)
- [Twilio Voice Insights dashboards](https://www.twilio.com/docs/voice/insights/dashboards)
- [Twilio AMD filtering in Voice Insights](https://www.twilio.com/en-us/changelog/amd_filtering_in_voice_insights_dashboard)
- [UCSF CTOP poster (2023)](https://pophealth.ucsf.edu/sites/g/files/tkssra4556/f/wysiwyg/UCSF%20Health%20Symposium%20Poster%20CTOP%20SMS%20HE%20Poject%202023%20-%20Final.pdf)
- [CipherHealth outreach](https://cipherhealth.com/outreach/)

## 2. What we already record (no new collection needed for v1)

| Store | Fields useful for analytics |
|---|---|
| `calls` | status, outcome, channel (voice/SMS), answered by (human/machine), duration, initiated/answered/ended times, follow-up tier and **every reason** (`follow_up_reasons`), nurse override (who, when, from, to), sign-off and review times, DOB check result, `sms_follow_up` (sent / not sent and why), survey answers, patient questions with category and priority. |
| `call_events` | A millisecond timeline per call: media start, first agent audio, every tool call and response, interruptions, AMD verdict, report saved, drain, hang-up. This gives first-audio latency, response latency, barge-in count and tool timings. |
| `sms_sessions`, `sms_messages` | Invite sent, reply received, choice (call me, pick a time, answer by text), completion, opt-outs, callbacks booked. |
| `call_list`, `call_runs` | Attempts per patient, recalls, run sizes and timing, cancellations. |
| Cloud Logging metrics | Live errors, drain timeouts, dial failures, invalid reports, HTTP errors (already alerting). |

Not recorded today: demographics (needed for any equity breakdown), and sentiment or topics (phase 3).

## 3. The Analytics tab (v1)

**Placement and layout**
- A fourth item in the console sidebar, after Call status.
- Filters along the top: date range (7 / 30 / 90 days, custom), channel (voice, text, both), campaign.
- Every number is clickable and opens the matching calls in Call status, so a figure can always be traced back to the calls behind it.

**A. Outreach (headline)**
- **Reach rate:** share of patients completed by voice or text within N days of their first attempt. It is the headline number, with a trend line.
- **Funnel:** dialled → connected → human answered → identity verified → questionnaire completed. Text runs alongside it: invite sent → reply → chose "answer by text" → completed.
- **Attempts:** attempts per reached patient, and how effective auto-recall is.
- **Unreached list:** patients not reached after all attempts, ready for manual outreach.

**B. Clinical picture**
- **Follow-up tiers:** distribution of emergency / same day / next business day / noted / none over time.
- **Top reasons:** from `follow_up_reasons`, counting every reason rather than only the top tier.
- **Patient questions:** by category (medication, symptom, appointment and so on) and by priority.
- **Symptom prevalence:** fever, incision concerns, swelling (one side vs both), poor sleep, pain score distribution, blood-sugar status for diabetics.
- **Red flags:** count of emergencies, each linked to its call.

**C. Nurse workload and response**
- **Open and overdue follow-ups** by tier.
- **Time to action:** report filed → nurse sign-off, median and 90th percentile per tier, against the tier's target (same day, next business day).
- **Override rate:** how often nurses change the agent's tier, and in which direction. This is the best proxy for triage accuracy, and the feed for clinical tuning.

**D. Agent quality (operations and engineering)**
- **Voice responsiveness:** median and 90th-percentile time to first audio, and response latency after the patient stops speaking.
- **Interruptions:** barge-ins per call.
- **Reliability:** report validation failures, tool refusals (exit gate, unanswered question), live-model errors, drain timeouts.
- **Connectivity and AMD:** answered by human vs machine, voicemail drops, AMD verdict timing, DOB failure rate.
- **Text delivery:** sent vs skipped and why (cooldown, no consent, the other system has the number, provider errors).

**E. Export:** CSV of any table (de-identified), plus a weekly summary e-mail in phase 2.

## 4. Architecture

```
Firestore (operational, PHI)
      │  every 15 min + nightly full pass
      ▼
voa-analytics-export   Cloud Run Job (Cloud Scheduler)
      │  flattens calls, events, texts; strips names, phones, DOB and free text;
      │  replaces patient id with a keyed hash
      ▼
BigQuery  dataset voa_analytics  (de-identified, partitioned by day)
      │  fact_calls, fact_call_events, fact_sms, fact_reasons, fact_questions
      ▼
voa-analytics   Cloud Run service (private, read-only BigQuery role)
      │  /analytics/* : fixed, parameterised queries with a 60 s cache
      ▼
voa-admin  Analytics section  (BFF with an ID token, the same pattern as the API)
```

**Why this design**
- **Separate store.** Analytics never queries Firestore directly: aggregate queries over Firestore are slow, costly, and would compete with live calls for the same database.
- **De-identified by design.** BigQuery holds no names, phone numbers, dates of birth or transcript text. Patient questions keep their category and priority, not the words. The nurse who needs the words clicks through to the call in the console, where access is already controlled. In production this lets a much wider audience (operations, quality, executives) see the dashboard without PHI access.
- **Separate service and job.** They scale on their own schedule, carry their own narrow IAM (BigQuery read only; the export job alone reads Firestore), and can fail or be redeployed without touching calls. They are new Terraform modules, the same `cloud_run_service` module plus a job module.
- **"Today" tiles stay live.** The existing KPI tiles keep reading Firestore through the API. BigQuery serves trends, so freshness is about 15 minutes for charts and instant for today's work queue.
- **Engineering metrics stay in Cloud Monitoring.** The tab shows a few health numbers and links to the Cloud Monitoring dashboard that Terraform already creates, rather than rebuilding it.
- **Charts:** one small, pinned charting library (e.g. Recharts) in the dashboard, styled with the console's tokens. Nothing calls the browser out to third parties.
- **Ad hoc analysis:** BigQuery tables connect directly to Looker Studio for anyone who needs questions the tab does not answer.

## 5. Phases

| Phase | Scope | Size |
|---|---|---|
| **1** | Export job and BigQuery tables (Terraform); `voa-analytics` service; tab with sections A (reach, funnel, unreached), B (tiers, reasons, questions) and C (open, overdue, time to action, overrides). Click-through to calls. Tests on the de-identification (no PHI columns) and on query results against a fixture dataset. | M |
| **2** | Section D (latency, barge-ins, reliability, AMD, text delivery); CSV export; weekly summary e-mail; a Gemini Enterprise card ("How did this week go?") using the same endpoints. | M |
| **3** | AI review of calls (Gemini batch over transcripts in the operational project): sentiment, topics, a QA scorecard (all questions asked, identity checked, no medical advice, closing said), with a human override. Results join the analytics tables as scores only. Equity breakdown, if demographics are added to the call list. | L |

## 6. Production: is "separate Cloud Run services" the right lever?

Yes, along the lines that differ in exposure, scaling, failure impact and data access. That is what the system already does, and analytics follows the same rule:

| Service | Why separate |
|---|---|
| gateway | Public, real-time audio, always warm; must not share fate with anything else. |
| api | Private core: dashboard API, dialing, texts, tasks. |
| ui | Behind IAP; only talks to the API and analytics. |
| nurse | A2A endpoint for Gemini Enterprise, its own identity and invokers. |
| **analytics + export job** (new) | Read-only, de-identified data, different users and access rules. |

**Don't split further by default.** Each new service adds IAM, deploys, cold starts and a network hop. A good candidate later is the SMS conversation (inside the API today), if text volume or its release cadence ever diverge.

**What makes the move to production simple matters more than the number of services:**
1. **One Terraform environment per project.**
   - Add `infra/envs/prod` beside `infra/envs/demo`, using the same modules with different values.
   - No code or config change goes into production that hasn't run in demo.
2. **Build once, promote the same image.**
   - CI builds an image per commit and deploys it to demo.
   - Production deploys the same image by digest after approval. No rebuilds per environment.
3. **Configuration only by environment.**
   - Everything identifying or environment-specific already lives in `tfvars` and `site.yaml`.
   - Production adds its own values and secrets, nothing more.
4. **Data and compliance boundaries.** Production needs:
   - BAAs with Google and Twilio;
   - VPC Service Controls around Firestore, Storage and BigQuery;
   - customer-managed keys, if required;
   - Data Access audit logs, which are already on;
   - IAP groups instead of individual users.
5. **Things to remove or keep off in production:**
   - the browser softphone and the SMS simulator (already off unless dry-run);
   - demo-only switches such as the text cooldown override;
   - recording links: keep them or replace them with dashboard-only playback (review A-9).
6. **Operations.**
   - On-call alert routing (the alerts already exist).
   - A load test of concurrent calls.
   - A tested restore from Firestore point-in-time recovery.
   - Retention policies for recordings and analytics tables.

## 7. Questions to settle before phase 1

1. **Audience:** nurses, clinical leads, executives or engineers? It decides which section leads, and whether the tab sits in the nurse console or behind a separate role.
2. **Targets:** what reach rate and time-to-action goals should the charts show as lines?
3. **Freshness:** is 15 minutes acceptable for trends?
4. **Retention:** how long should the de-identified analytics data be kept?
5. **Equity breakdowns:** add demographics (age band, language, other) to the call list?
