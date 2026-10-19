# Voice persona eval, 20261019

Model `gemini-3.8-live`, AMD strategy `speak_first`, 3 runs per persona, concurrency 3. Overall **24/24** passed.

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy | 3/3 | 2501 | 1463 | 1755 | 0 | - |
| dob_retry | 3/3 | 1326 | 1498 | 4168 | 0 | - |
| barge_in | 3/3 | 1350 | 1364 | 1831 | 0 | - |
| red_flag | 3/3 | 1220 | 949 | 1401 | 0 | - |
| negated_red_flag | 3/3 | 1232 | 1424 | 1676 | 1 | - |
| wrong_person | 3/3 | 1242 | 1468 | 1532 | 0 | - |
| patient_questions | 3/3 | 1379 | 1450 | 1839 | 1 | - |
| voicemail | 3/3 | 1279 | None | None | 0 | - |

Mean call duration: 102 s. Raw results: `evals/out/` (gitignored).

## Notes

- First full run on the new questionnaire was 23/24: one `happy` run stalled after 13 exchanges and hit the 300 s timeout with no report (no live error, no refusals). It did not reproduce in 4 targeted reruns or in this full rerun. Failed runs now keep their transcript in `evals/out/` so the next one can be diagnosed.
- `happy` first-audio is higher because it starts first (cold Live connection in a fresh process).
- New `patient_questions` persona: asks a medication question and a driving question. It checks that both are recorded, that the call files `urgent_same_day`, and that the medication-list reminder is spoken. Every completed persona now also checks for the reminder.
