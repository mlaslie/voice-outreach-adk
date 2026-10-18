# Voice persona eval, 20261018

Model `gemini-3.8-live`, AMD strategy `speak_first`, 3 runs per persona, concurrency 3. Overall **21/21** passed.

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy | 3/3 | 2117 | 1417 | 1562 | 0 | - |
| dob_retry | 3/3 | 943 | 1274 | 1645 | 0 | - |
| barge_in | 3/3 | 1077 | 1335 | 1669 | 0 | - |
| red_flag | 3/3 | 965 | 1003 | 1497 | 0 | - |
| negated_red_flag | 3/3 | 1015 | 1427 | 1636 | 0 | - |
| wrong_person | 3/3 | 939 | 1425 | 1441 | 0 | - |
| voicemail | 3/3 | 952 | None | None | 0 | - |

Mean call duration: 73 s. Raw results: `evals/out/` (gitignored).
