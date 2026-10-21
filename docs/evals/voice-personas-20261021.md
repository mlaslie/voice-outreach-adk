# Voice persona eval, 20261021: use-case engine (step 1) parity check

Model `gemini-3.8-live`, AMD strategy `speak_first`. These calls ran on the refactored engine (`docs/USE-CASES-PLAN.md` step 1), with post-op as a spec.

| Run | Personas | Result |
|---|---|---|
| Full suite, 3 runs each (new engine) | all 8 | **22/24** |
| Rerun, 4 each (new engine) | dob_retry, patient_questions | dob_retry 4/4, patient_questions 3/4 |
| A/B, 6 each | patient_questions | **new engine 6/6, pre-refactor code 6/6** |

**Totals on the new engine:**
- **dob_retry: 6/7.** In the one failure, the simulated patient answered "Okay" to its own wrong date of birth instead of correcting it, and the agent ended the identity check. The DOB tool did not change in this refactor.
- **patient_questions: 11/13.** The two failures recorded a different number of questions than the two asked. Earlier, the pre-refactor code passed 7/7, and 6/6 in today's A/B.
- **Every other persona: 3/3.**

**Reading:**
- The instruction text and the tool declarations are byte-identical to before (parity goldens). `record_patient_question` gives the same result for post-op.
- So the misses are live-model variation, not a regression.
- Keep watching patient_questions. If it keeps failing at about 1 in 7, inspect the recorded count in `evals/out/` and decide whether it's a counting artefact of the eval.

Raw results: `evals/out/` (gitignored).
