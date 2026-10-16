# Phase-0 voice spike: results

Date: 16 Oct 2026. Model `gemini-3.8-live` on Vertex (`us-central1`), ADK 2.12.0,
google-genai 2.31.0. Code: `spikes/voice/`. Raw outputs: `spikes/voice/out/` (gitignored).

## Method

A simulated patient calls the **real bridge** (`voice_spike/bridge.py`) through the
`fake` provider. No phone line is involved.

- **Patient speech:** Gemini-TTS (`gemini-2.5-flash-tts`, voice Charon), downsampled to
  **8 kHz mu-law** (exactly what Twilio delivers), and streamed in real time in 20 ms
  frames, with silence between utterances.
- **Agent audio:** goes back through the same transcoder a phone call would use and
  "plays out" in real time, so marks, barge-in and the closing drain behave as on a call.
- **Patient replies:** the patient picks a scripted reply by keyword from the agent's
  last sentence.

The patient ("Jordan Rivera") is fictional.

Scenarios:

| Scenario | What it does | Expected outcome |
|---|---|---|
| `happy` | Full questionnaire, moderate pain | `questionnaire_completed` |
| `dob_retry` | Wrong DOB first, then the right one | `questionnaire_completed` |
| `barge_in` | Patient talks over the agent | `questionnaire_completed` |
| `red_flag` | "Chest pain and pressure" as the first answer | `emergency_ended` |
| `wrong_person` | A neighbour answers | `unable_to_reach_wrong_person` |
| `red_flag_skills` | Same as `red_flag`, but the safety rules are an ADK **skill** loaded on demand | `emergency_ended` |

## Results

**Run 1** (no guards): 3 of 5 passed. **Run 2** (with the two guards below): **6 of 6 passed.**

| Scenario (run 2) | Outcome | Opener latency | Median response latency | Call length |
|---|---|---|---|---|
| happy | questionnaire_completed (routine) | 1.25 s | 1.34 s | 97 s |
| dob_retry | questionnaire_completed (routine) | 1.06 s | 1.27 s | 105 s |
| barge_in | questionnaire_completed (routine) | 1.04 s | 1.32 s | 100 s |
| red_flag | emergency_ended (emergency) | 1.03 s | 1.11 s | 52 s |
| wrong_person | unable_to_reach_wrong_person | 1.02 s | 1.41 s | 17 s |
| red_flag_skills | emergency_ended (emergency) | 1.43 s | 0.89 s | 54 s |

How latencies were measured:

- **Opener:** from sending the "call connected" trigger to the agent's first audio.
- **Response:** from the end of the patient's utterance to the agent's first audio.
  It includes the server's end-of-speech detection (VAD `silence_duration_ms=700`), so
  the model's own think time is about 0.6 s.
- **Not included:** the PSTN, Twilio, and the Cloud Run hop. These add roughly
  100–300 ms on a real call.

## Findings

**Spike (a): Twilio-format audio into `run_live`.** Go.

1. **8 kHz telephony audio transcribes well.** Dates ("March 15th, 1950"), numbers ("about a four") and short answers came through correctly in every run. Upsampling mu-law 8 kHz to PCM 16 kHz with `audioop.ratecv` is enough.
2. **The outbound opener works with proactive audio.** It fires reliably (6 of 6 runs) when the bridge sends one text turn: "[The phone call has just connected … Begin now with your opener.]" The agent never said anything clinical before verification.
3. **Barge-in works.** Live emits `interrupted`, the bridge sends the provider `clear`, and the agent recovers in context: it re-explains, then re-asks.
4. **BLOCKING tools are honoured.** No agent audio was produced while a tool call was pending in any run (`agent.audio_while_tool_pending` = 0).
5. **The model does not reliably follow ordering rules from the prompt.** Run 1 showed both of these, and both match bugs from the CES system:
   - **It said goodbye but never called `end_call`.** The call stayed open until the harness timed out at 240 s.
     → **Guard: auto-hangup.** Once `submit_report` has succeeded and the agent's turn completes, the gateway waits 5 s. If the caller hasn't spoken and the model hasn't ended the call, the gateway drains playout and hangs up itself (`hangup.auto_after_report`).
   - **It asked for the DOB and, in the same breath, took the wrong-person exit and said goodbye.** No caller input came in between. It also spoke the goodbye *before* filing the report.
     → **Guard: exit gate.** `submit_report` refuses any exit except emergency and voicemail while the agent's speech since the caller last spoke contains an unanswered question. The gateway feeds that fact through `CallFacts`.

   Neither guard fired in run 2, because the model behaved. They are covered by unit tests, and the harness reports them (`auto_hangups`, `report_refusals`) so they show up when they do fire.

   **Design consequence for phase 2:** every rule whose failure files a wrong report or leaves a call hanging is enforced in code. The prompt states it as well.
6. **Transcription artifacts.** Output transcription once contained `<no speech>{pause}`. Strip these markup tokens before storing transcripts.
7. **The closing drain is needed and works.** The `mark`/echo pattern with a unique mark name per drain behaved in every run, including drains that started after the goodbye had already finished playing.
8. **Abrupt hang-ups.** A send after the caller has hung up raised from Starlette and was logged as a Live error. Both adapters now treat it as "call over". This was found with the browser-path test.

**Spike (b): ADK skills under `run_live`.** Works. Use selectively.

`SkillToolset` with `EAGER` discovery works inside a live session. The model called `load_skill` before its opener, as instructed, and then followed the skill's emergency procedure correctly. The cost is one extra model round trip, about **+0.4 s on the opener**.

**Recommendation:**
- Safety-critical content (red flags, the emergency script) is compiled into the voice agent's system instruction, so it never depends on a tool call.
- On-demand skills are for the text agents (SMS, nurse assistant) and for rarely needed voice content.

**Spike (c): Gemini-TTS for pre-rendered prompts.** Works.

- `gemini-2.5-flash-tts` (Vertex `us-central1`) rendered a 10.3 s voicemail clip in 8.1 s.
- `gemini-2.5-flash-preview-tts` and `gemini-3.1-flash-tts-preview` (global) also work.
- Converting 24 kHz PCM to 8 kHz mu-law is a single resample + `lin2ulaw`.

Too slow to render live in the middle of a call, but fine at deploy time: clips are cached in the prompts bucket, keyed by a hash of text, voice and model.

**Spike (d): Twilio SMS webhook coexistence.** Not tested. Desk research only (PLAN §11.9). Still recommended: a second number on the existing A2P campaign.

## Not yet verified

- **A real PSTN call** through Twilio to the deployed spike, end-to-end latency on a phone, and the Twilio signature check on the media WebSocket. Needs:
  - the Terraform apply;
  - Twilio secrets in Secret Manager;
  - a Cloud Run deploy;
  - one of the two registered test numbers in `config/seed/allowlist.yaml`.
- **The browser softphone with a real microphone.** The WebSocket path was tested with a script client: first agent audio at 1.22 s.
- **AMD / HOLD / voicemail drop.** Not in the spike; it is ported in phase 2.
- **Calls longer than about 10 minutes** (session resumption across a Live reconnect). No scenario ran that long.
- **Run-to-run variance.** Each scenario ran once per configuration. Phase 2's eval suite should run each persona several times and track pass rates.

## Reproduce

```bash
gcloud auth application-default login
cp config/site.example.yaml config/site.yaml     # set gcp.project_id
make spike-harness                               # ~10 min, all scenarios
cd spikes/voice && uv run python -m voice_spike.harness barge_in   # one scenario
```
