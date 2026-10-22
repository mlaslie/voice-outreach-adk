# Voice persona eval, 20261022

Model `gemini-3.8-live`, AMD strategy `speak_first`, 3 runs per persona, concurrency 3. Overall **23/24** passed.

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy | 3/3 | 2179 | 1366 | 1641 | 1 | - |
| dob_retry | 2/3 | 1161 | 1380 | 1686 | 1 | med_reminder, outcome |
| barge_in | 3/3 | 1191 | 1414 | 1839 | 0 | - |
| red_flag | 3/3 | 1447 | 932 | 1475 | 0 | - |
| negated_red_flag | 3/3 | 1323 | 1384 | 1676 | 0 | - |
| wrong_person | 3/3 | 1259 | 1377 | 1395 | 0 | - |
| patient_questions | 3/3 | 1169 | 1431 | 1625 | 1 | - |
| voicemail | 3/3 | 1234 | None | None | 0 | - |

Mean call duration: 92 s. Raw results: `evals/out/` (gitignored).
