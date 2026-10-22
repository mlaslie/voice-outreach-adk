# Voice persona eval, 20261022

Model `gemini-3.8-live`, AMD strategy `speak_first`, 3 runs per persona, concurrency 3. Overall **24/24** passed.

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy | 3/3 | 1675 | 1366 | 1690 | 1 | - |
| dob_retry | 3/3 | 961 | 1370 | 1684 | 1 | - |
| barge_in | 3/3 | 1057 | 1426 | 1819 | 2 | - |
| red_flag | 3/3 | 941 | 1107 | 1424 | 0 | - |
| negated_red_flag | 3/3 | 961 | 1472 | 1707 | 0 | - |
| wrong_person | 3/3 | 1023 | 1331 | 1432 | 0 | - |
| patient_questions | 3/3 | 932 | 1512 | 1939 | 0 | - |
| voicemail | 3/3 | 1181 | None | None | 0 | - |

Mean call duration: 98 s. Raw results: `evals/out/` (gitignored).
