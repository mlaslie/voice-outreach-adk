# Voice persona eval, 20261022

Model `gemini-3.8-live`, AMD strategy `speak_first`, 3 runs per persona, concurrency 3. Overall **24/24** passed.

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy | 3/3 | 3036 | 1425 | 1642 | 0 | - |
| dob_retry | 3/3 | 1181 | 1410 | 1643 | 0 | - |
| barge_in | 3/3 | 1181 | 1425 | 1736 | 2 | - |
| red_flag | 3/3 | 1214 | 896 | 1548 | 0 | - |
| negated_red_flag | 3/3 | 1327 | 1440 | 1677 | 0 | - |
| wrong_person | 3/3 | 1283 | 1437 | 1445 | 0 | - |
| patient_questions | 3/3 | 1244 | 1418 | 1826 | 1 | - |
| voicemail | 3/3 | 1167 | None | None | 0 | - |

Mean call duration: 98 s. Raw results: `evals/out/` (gitignored).
