# Voice persona eval, 20261022

Model `gemini-3.8-live`, AMD strategy `speak_first`, 2 runs per persona, concurrency 3. Overall **16/16** passed.

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy | 2/2 | 2673 | 1355 | 1504 | 1 | - |
| dob_retry | 2/2 | 1313 | 1334 | 1687 | 0 | - |
| barge_in | 2/2 | 1182 | 1349 | 1496 | 1 | - |
| red_flag | 2/2 | 1375 | 904 | 1443 | 0 | - |
| negated_red_flag | 2/2 | 1205 | 1352 | 1550 | 0 | - |
| wrong_person | 2/2 | 1264 | 1296 | 1465 | 0 | - |
| patient_questions | 2/2 | 1188 | 1410 | 1619 | 0 | - |
| voicemail | 2/2 | 1122 | None | None | 0 | - |

Mean call duration: 99 s. Raw results: `evals/out/` (gitignored).
