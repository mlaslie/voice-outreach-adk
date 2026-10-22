# Voice persona eval, 20261022

Model `gemini-3.8-live`, AMD strategy `speak_first`, 3 runs per persona, concurrency 3. Overall **6/6** passed. Spanish personas: the post-op es-US pack (status `draft`, used with VOA_ALLOW_DRAFT_PACKS=1 in VOA_ENV=dev).

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy_es | 3/3 | 1598 | 1471 | 1727 | 0 | - |
| dob_retry_es | 3/3 | 1048 | 1433 | 1649 | 1 | - |

Mean call duration: 154 s. Raw results: `evals/out/` (gitignored).
