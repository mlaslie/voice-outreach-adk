# Voice persona eval, 20261022

Model `gemini-3.8-live`, AMD strategy `speak_first`, 3 runs per persona, concurrency 3. Overall **20/21** passed. Spanish personas: the post-op es-US pack (status `draft`, used with VOA_ALLOW_DRAFT_PACKS=1 in VOA_ENV=dev).

Latencies in ms. *First audio* is from media start to the agent's first audio (includes the opener's generation; AMD verdict arrives at 2.5 s). *Response* is end of patient speech to agent audio (includes 700 ms end-of-speech detection).

| Persona | Pass | First audio p50 | Response p50 | Response p90 | Auto-hangups | Failed checks |
|---|---|---|---|---|---|---|
| happy_es | 3/3 | 1641 | 1503 | 1770 | 0 | - |
| dob_retry_es | 2/3 | 1024 | 1480 | 1716 | 1 | answers_english |
| red_flag_es | 3/3 | 1045 | 1069 | 1548 | 0 | - |
| negated_red_flag_es | 3/3 | 1015 | 1483 | 1830 | 1 | - |
| patient_questions_es | 3/3 | 1168 | 1597 | 1856 | 0 | - |
| switch_to_english | 3/3 | 941 | 1417 | 1804 | 0 | - |
| voicemail_es | 3/3 | 1051 | None | None | 0 | - |

Mean call duration: 142 s. Raw results: `evals/out/` (gitignored).
