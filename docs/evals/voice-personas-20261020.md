# Voice persona eval, 20261020

Model `gemini-3.8-live`, AMD strategy `speak_first`, 1 runs per persona, concurrency 4. Overall **8/8** passed.

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy | 1/1 | 3516 | 1293 | 1457 | 1 | - |
| dob_retry | 1/1 | 3350 | 1372 | 1643 | 1 | - |
| barge_in | 1/1 | 3374 | 1280 | 1490 | 0 | - |
| red_flag | 1/1 | 3444 | 1070 | 1505 | 0 | - |
| negated_red_flag | 1/1 | 1507 | 1411 | 1536 | 0 | - |
| wrong_person | 1/1 | 1270 | 1362 | 1362 | 0 | - |
| patient_questions | 1/1 | 1254 | 1389 | 1594 | 1 | - |
| voicemail | 1/1 | 1436 | None | None | 0 | - |

Mean call duration: 100 s. Raw results: `evals/out/` (gitignored).
